"""Opt-in system receipts from signed GET only; no real provider or business database."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin
from app import refund_verification as queue
from app.config import Settings
from app.models import Order, PaymentEvent, PaymentReceipt, RefundVerificationJob, User
from app.refunds import record_refund
from tests.conftest import TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import legacy_0016, rows
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_download import path_of
from tests.test_refund_notifications import plain, send
from tests.test_refund_verification import configuration as configuration
from tests.test_refund_verification import jobs, setup, stub
from tests.test_refunds import provider, query, refunds
from tests.test_refunds import refund_case as refund_case
from tests.test_verification_controls import post, proof
from tests.test_wechat_pay import CFG


async def test_signed_system_receipt_revokes_only_original_order_and_preserves_first_identity(client, refund_case, monkeypatch):
    buyer, admin, create, _ = refund_case
    number, completed = await setup(client, refund_case)
    other = await create('wechat')
    url = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    calls = await stub(monkeypatch, number, completed)
    assert await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    receipt = (await refunds())[0]
    assert receipt.actor_id is None and receipt.actor_name == 'system:refund-verifier'
    assert receipt.verification_event_id is not None and receipt.source == 'wechat'
    assert len(calls) == 1 and (await jobs())[0].outcome == 'success_recorded'
    assert (await client.get(path_of(url))).status_code == 403
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    assert (await client.post(f'/shop/download/{other}', headers=buyer)).status_code == 200
    async with TestSession() as db:
        start = await db.get(PaymentEvent, receipt.verification_event_id)
        assert start.kind == 'refund_verify_started' and start.actor_id is None
        assert await db.scalar(select(PaymentReceipt.amount).where(PaymentReceipt.order_id == receipt.order_id)) == 19900
    data = (await client.get(f'/shop/admin/orders/{number}/ledger?before=1', headers=admin)).json()
    assert data['refund']['recorded_by'] == 'system' and data['refund']['verification_event_id'] == start.id
    await provider(monkeypatch, number, completed)
    assert not (await query(client, admin, number)).json()['changed']
    after = (await refunds())[0]
    assert (after.id, after.actor_id, after.actor_name, after.received_at) == (receipt.id, None, receipt.actor_name, receipt.received_at)
    assert not await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)


async def test_default_off_does_not_retroactively_settle_terminal_jobs(client, refund_case, monkeypatch):
    assert Settings(_env_file=None).WX_REFUND_AUTO_RECORD_ENABLED is False
    _, _, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    calls = await stub(monkeypatch, number, completed)
    assert not await queue.run_once(TestSession, CFG, auto_record=True)
    assert not calls
    assert await queue.run_once(TestSession, CFG, enabled=True)
    assert await refunds() == [] and (await jobs())[0].outcome == 'success_needs_admin'
    assert not await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    assert await refunds() == [] and len(calls) == 1


@pytest.mark.parametrize('change', [
    {'status': 'PROCESSING'}, {'status': 'ABNORMAL'}, {'status': 'CLOSED'}, {'refund_id': '999'},
    {'success_time': '2099-01-01T00:00:00Z'}, {'success_time': '2000-01-01T00:00:00Z'},
    {'amount': {'total': 19900, 'refund': 1, 'currency': 'CNY'}},
    {'amount': {'total': 19900, 'refund': 19900, 'currency': 'USD'}}, {'channel': 'OTHER_BALANCE'},
])
async def test_only_valid_full_original_success_can_record(client, refund_case, monkeypatch, change):
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed, **change)
    await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    assert await refunds() == []
    assert (await jobs())[0].state in ('retry', 'attention')


async def test_hold_before_get_result_blocks_receipt_not_network(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    calls = await stub(monkeypatch, number, completed)
    fetch = queue.query_full_refund
    entered, release = asyncio.Event(), asyncio.Event()
    async def wait(*a, **kw):
        entered.set()
        await release.wait()
        return await fetch(*a, **kw)
    monkeypatch.setattr(queue, 'query_full_refund', wait)
    task = asyncio.create_task(queue.run_once(TestSession, CFG, enabled=True, auto_record=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await post(client, admin, number, await proof(client, admin, number))).status_code == 200
    finally:
        release.set()
        await task
    assert len(calls) == 1 and await refunds() == [] and (await jobs())[0].outcome == 'manual_hold'


async def test_expired_ticket_cannot_create_receipt_and_fresh_claim_can(client, refund_case, monkeypatch):
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    ticket = await queue.claim(TestSession)
    payload, _, _ = await queue.inputs(TestSession, ticket, CFG)
    result = await queue.query_full_refund(CFG, **payload)
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    assert not await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin', settlement=(CFG, payload, result))
    assert await refunds() == []
    assert await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    assert len(await refunds()) == 1 and (await jobs())[0].attempts == 2


@pytest.mark.parametrize('lost_ack', [False, True])
async def test_receipt_job_audit_atomic_commit_ack_recovery(client, refund_case, monkeypatch, lost_ack):
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    ticket = await queue.claim(TestSession)
    payload, _, _ = await queue.inputs(TestSession, ticket, CFG)
    result = await queue.query_full_refund(CFG, **payload)
    original = AsyncSession.commit
    async def fail(db):
        if lost_ack:
            await original(db)
        raise OperationalError('synthetic', {}, Exception('unknown commit'))
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'commit', fail)
        with pytest.raises(OperationalError):
            await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin', settlement=(CFG, payload, result))
    assert len(await refunds()) == int(lost_ack)
    assert (await jobs())[0].state == ('verified' if lost_ack else 'running')
    async with TestSession() as db:
        events = list((await db.scalars(select(PaymentEvent).where(PaymentEvent.kind == 'refund_verify_observed'))).all())
        assert len(events) == int(lost_ack)
    assert await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin', settlement=(CFG, payload, result)) is (not lost_ack)
    assert len(await refunds()) == 1


async def test_audit_failure_rollback_does_not_revoke_download(client, refund_case, monkeypatch):
    buyer, _, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    original = AsyncSession.add
    def fail(db, obj, *a, **kw):
        if isinstance(obj, PaymentEvent) and obj.kind == 'refund_verify_observed':
            raise OperationalError('synthetic', {}, Exception('audit failed'))
        return original(db, obj, *a, **kw)
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'add', fail)
        with pytest.raises(OperationalError):
            await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    assert await refunds() == [] and (await jobs())[0].state == 'running'
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200


@pytest.mark.parametrize('human_first', [True, False])
async def test_system_and_human_same_facts_preserve_first_and_conflict_never_overwrites(client, refund_case, monkeypatch, human_first):
    _, admin, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    await provider(monkeypatch, number, completed)
    if human_first:
        assert (await query(client, admin, number)).status_code == 200
        before = (await refunds())[0]
    await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    if not human_first:
        before = (await refunds())[0]
    assert (await query(client, admin, number)).status_code == 200
    saved = (await refunds())[0]
    assert (saved.id, saved.actor_id, saved.verification_event_id) == (before.id, before.actor_id, before.verification_event_id)
    assert (saved.actor_id is not None) is human_first
    assert (await send(client, await plain(number, completed), identity='NEW-NOTICE')).status_code == 204
    await stub(monkeypatch, number, completed, success_time=datetime.now(timezone.utc).isoformat())
    assert await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    assert (await jobs())[-1].outcome == 'receipt_conflict'
    assert len(await refunds()) == 1 and (await refunds())[0].completed_at == before.completed_at


async def test_competing_human_and_system_one_receipt(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    await provider(monkeypatch, number, completed)
    worker, human = await asyncio.gather(queue.run_once(TestSession, CFG, enabled=True, auto_record=True), query(client, admin, number))
    assert worker and human.status_code == 200 and len(await refunds()) == 1
    assert (await jobs())[0].state == 'verified'


@pytest.mark.parametrize('corrupt', ['merchant', 'payload', 'actor'])
async def test_finalize_rebinds_original_context(client, refund_case, monkeypatch, corrupt):
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    ticket = await queue.claim(TestSession)
    payload, _, _ = await queue.inputs(TestSession, ticket, CFG)
    result = await queue.query_full_refund(CFG, **payload)
    cfg = replace(CFG, mchid='wrong') if corrupt == 'merchant' else CFG
    if corrupt == 'payload':
        payload = {**payload, 'total': 1}
    if corrupt == 'actor':
        async with TestSession() as db:
            admin = await db.scalar(select(User).where(User.username == 'auditor'))
            await db.execute(update(PaymentEvent).where(PaymentEvent.kind == 'refund_verify_started').values(actor_id=admin.id, actor_name=admin.username))
            await db.commit()
    assert await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin', settlement=(cfg, payload, result))
    assert await refunds() == [] and (await jobs())[0].outcome == 'receipt_conflict'


async def test_human_entry_never_accepts_missing_administrator(client, refund_case):
    number, completed = await setup(client, refund_case)
    from app.payment_ledger import PaymentConflict
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        with pytest.raises(PaymentConflict):
            await record_refund(db, order, source='wechat', refund_id='500123', out_refund_no='REFUND1', amount=19900,
                                completed_at=datetime.fromisoformat(completed), actor=None, evidence='not a human',
                                audit_event=PaymentEvent(order_id=order.id, attempt_id='a'*32, kind='refund_query_success'))
    assert await refunds() == []


def test_manifest_requires_system_authority_migration(tmp_path):
    for version, (path, _) in db_admin.migration_manifest().items():
        if int(version) < 16:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.migration_manifest(tmp_path)


def test_real_pg_upgrade_preserves_human_and_rejects_fake_system_authority(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    legacy_0016(conn)
    rows(conn, "DROP TRIGGER check_system_refund_actor ON refund_receipt; DROP FUNCTION codemax_check_system_refund_actor(); "
               "ALTER TABLE refund_receipt DROP CONSTRAINT ck_refund_authority; ALTER TABLE refund_receipt DROP COLUMN verification_event_id; "
               "ALTER TABLE refund_receipt ALTER COLUMN actor_id SET NOT NULL; DELETE FROM schema_migration WHERE version='0016'; "
               "INSERT INTO sys_user(id,username,password,role) VALUES(1,'admin','synthetic',1); "
               "INSERT INTO sys_order(id,order_no,user_id,product_name,amount,payment_mode,status,transaction_id,merchant_id,app_id) "
               "VALUES(1,'ORDER1',1,'fixed',100,'wechat','paid','TX1','MCH','APP'),(2,'ORDER2',1,'fixed',100,'wechat','paid','TX2','MCH','APP'); "
               "INSERT INTO payment_receipt(id,order_id,source,transaction_id,amount,currency,merchant_id,app_id) "
               "VALUES(1,1,'wechat','TX1',100,'CNY','MCH','APP'),(2,2,'wechat','TX2',100,'CNY','MCH','APP'); "
               "INSERT INTO refund_receipt(order_id,payment_receipt_id,source,merchant_id,refund_id,out_refund_no,amount,currency,actor_id,actor_name,evidence,completed_at) "
               "VALUES(1,1,'wechat','MCH','501','R1',100,'CNY',1,'admin','human first',now())")
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT actor_id,actor_name,verification_event_id,evidence FROM refund_receipt') == [(1,'admin',None,'human first')]
    rows(conn, "INSERT INTO payment_event(id,order_id,attempt_id,kind) VALUES(1,2,repeat('a',32),'refund_notify_signal'),(2,2,repeat('b',32),'refund_verify_started'); "
               "INSERT INTO refund_verification_job(notice_event_id,order_id,state,attempts,token,lease_until) VALUES(1,2,'running',1,repeat('b',32),now()+interval '90 seconds')")
    sql = "INSERT INTO refund_receipt(order_id,payment_receipt_id,source,merchant_id,refund_id,out_refund_no,amount,currency,actor_id,actor_name,evidence,completed_at,verification_event_id) VALUES(2,2,'wechat','MCH','502','R2',100,'CNY',NULL,'system:refund-verifier','system proof',now(),2)"
    for bad in [sql.replace("NULL,'system:", "1,'system:"), sql.replace('now(),2)', 'now(),1)'),
                sql.replace('now(),2)', 'now(),NULL)'), sql.replace("100,'CNY'", "1,'CNY'")]:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    rows(conn, "UPDATE refund_verification_job SET state='attention',token=NULL,lease_until=NULL")
    with pytest.raises(psycopg2.Error):
        rows(conn, sql)
    rows(conn, "UPDATE refund_verification_job SET state='running',token=repeat('b',32),lease_until=now()-interval '1 second'")
    with pytest.raises(psycopg2.Error):
        rows(conn, sql)
    rows(conn, "UPDATE refund_verification_job SET lease_until=now()+interval '90 seconds'")
    rows(conn, sql)
    for bad in ['UPDATE refund_receipt SET actor_id=1', 'DELETE FROM refund_receipt', 'DELETE FROM payment_event WHERE id=2']:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    db_admin.migrate(conn)
    assert len(rows(conn, 'SELECT * FROM refund_receipt')) == 2


async def test_worker_passes_separate_authority_flag_only_when_explicit(monkeypatch):
    from app import refund_worker
    from app.config import settings
    class Result:
        def all(self):
            return []
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, _):
            return Result()
    calls = []
    monkeypatch.setattr(refund_worker, 'SessionLocal', Session)
    monkeypatch.setattr(refund_worker, 'verify_ledger', lambda *a, **kw: None)
    monkeypatch.setattr(refund_worker, 'pay_config', lambda: CFG)
    monkeypatch.setattr(settings, 'WX_REFUND_VERIFY_ENABLED', True)
    async def cycle(factory, cfg, **kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(refund_worker, 'run_once', cycle)
    for value in [False, True]:
        monkeypatch.setattr(settings, 'WX_REFUND_AUTO_RECORD_ENABLED', value)
        await refund_worker.run(once=True)
    assert calls == [{'enabled': True, 'auto_record': False}, {'enabled': True, 'auto_record': True}]


async def test_untrusted_signature_never_becomes_system_receipt(client, refund_case, monkeypatch):
    from app.wechat_pay import WeChatPayError
    await setup(client, refund_case)
    async def denied(*a, **kw):
        raise WeChatPayError('synthetic untrusted signature')
    monkeypatch.setattr(queue, 'query_full_refund', denied)
    await queue.run_once(TestSession, CFG, enabled=True, auto_record=True)
    assert await refunds() == [] and (await jobs())[0].state == 'retry'


async def test_lease_expiring_during_receipt_staging_rolls_back_everything(client, refund_case, monkeypatch):
    number, completed = await setup(client, refund_case)
    await stub(monkeypatch, number, completed)
    ticket = await queue.claim(TestSession)
    payload, _, _ = await queue.inputs(TestSession, ticket, CFG)
    result = await queue.query_full_refund(CFG, **payload)
    deadline = (await jobs())[0].lease_until
    from app.timeutil import as_utc
    readings = iter([as_utc(deadline) - timedelta(seconds=1), as_utc(deadline) + timedelta(seconds=1)])
    async def clock(_):
        return next(readings)
    monkeypatch.setattr(queue, 'clock', clock)
    assert not await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin', settlement=(CFG, payload, result))
    assert await refunds() == [] and (await jobs())[0].state == 'running'
    async with TestSession() as db:
        assert not await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == 'refund_verify_observed'))
