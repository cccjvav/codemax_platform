"""Read-only durable worker, crash recovery and signed synthetic HTTP; no real merchant calls."""
import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin
from app import refund_verification as queue
from app.models import Order, PaymentEvent, RefundVerificationJob, User
from app.routers import refund_notify
from app.wechat_pay import query_full_refund
from tests.conftest import TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_db_admin import rows
from tests.test_download import path_of
from tests.test_payment_review import state
from tests.test_refund_notifications import plain, send
from tests.test_refunds import provider, query, refunds, response_body
from tests.test_refunds import refund_case as refund_case
from tests.test_wechat_pay import _AUTH_RE, CFG, signed_response, verify


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: CFG)


async def jobs():
    async with TestSession() as db:
        return list((await db.scalars(select(RefundVerificationJob).order_by(RefundVerificationJob.id))).all())


async def setup(client, case, **changes):
    _, _, create, completed = case
    number = await create('wechat')
    assert (await send(client, await plain(number, completed, **changes))).status_code == 204
    return number, completed


async def stub(monkeypatch, number, completed, **changes):
    async with TestSession() as db:
        transaction = await db.scalar(select(Order.transaction_id).where(Order.order_no == number))
    body = response_body(number, transaction, completed, **changes)
    calls = []
    async def handler(request):
        auth = _AUTH_RE.fullmatch(request.headers['Authorization']).groupdict()
        verify(auth['signature'], request.method, request.url.raw_path.decode(), auth['timestamp'], auth['nonce_str'], request.content.decode())
        assert request.method == 'GET' and request.url.path.endswith('/' + body['out_refund_no']) and not request.content
        # Another connection can acquire both user/order locks while provider is awaited.
        async with TestSession() as db:
            await db.execute(update(User).values(update_time=User.update_time))
            await db.execute(update(Order).where(Order.order_no == number).values(status=Order.status))
            await db.commit()
        calls.append(request)
        return signed_response(body)
    async def fetch(cfg, **kwargs):
        return await query_full_refund(cfg, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(queue, 'query_full_refund', fetch)
    return calls


async def test_signed_success_only_observes_no_money_no_entitlement_and_admin_can_finish(client, refund_case, monkeypatch):
    buyer, admin, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    link = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    calls = await stub(monkeypatch, number, completed)
    before = await state(client, admin, number)
    assert not await queue.run_once(TestSession, CFG)
    assert not calls and (await jobs())[0].attempts == 0
    assert await queue.run_once(TestSession, CFG, enabled=True)
    job = (await jobs())[0]
    assert (job.state, job.outcome, job.attempts, job.token) == ('verified', 'success_needs_admin', 1, None)
    assert len(calls) == 1 and await refunds() == []
    assert (await client.get(path_of(link))).status_code == 200
    assert (await state(client, admin, number))['snapshot'] != before['snapshot']
    for cursor in ['', '?before=1']:
        result = await client.get(f'/shop/admin/orders/{number}/ledger{cursor}', headers=admin)
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        projection = result.json()['refund_verification']
        assert projection['jobs'][0]['state'] == 'verified' and not projection['has_more']
        assert 'token' not in projection['jobs'][0]
    assert (await client.get(f'/shop/admin/orders/{number}/ledger', headers=buyer)).status_code == 403
    assert not await queue.run_once(TestSession, CFG, enabled=True) and len(calls) == 1
    await provider(monkeypatch, number, completed)
    assert (await query(client, admin, number)).status_code == 200
    assert (await client.get(path_of(link))).status_code == 403


@pytest.mark.parametrize('change,expected', [
    ({'status': 'PROCESSING'}, ('retry', 'processing')),
    ({'status': 'ABNORMAL'}, ('attention', 'abnormal')),
    ({'status': 'CLOSED'}, ('attention', 'closed')),
    ({'refund_id': '999'}, ('attention', 'identity_conflict')),
    ({'success_time': '2099-01-01T00:00:00Z'}, ('attention', 'time_conflict')),
    ({'success_time': '2000-01-01T00:00:00Z'}, ('attention', 'time_conflict')),
    ({'amount': {'total': 19900, 'refund': 1, 'currency': 'CNY'}}, ('retry', 'untrusted_or_unavailable')),
    ({'channel': 'OTHER_BALANCE'}, ('retry', 'untrusted_or_unavailable')),
])
async def test_signed_observations_and_unsupported_results(client, refund_case, monkeypatch, change, expected):
    number, completed = await setup(client, refund_case)
    calls = await stub(monkeypatch, number, completed, **change)
    await queue.run_once(TestSession, CFG, enabled=True)
    job = (await jobs())[0]
    assert (job.state, job.outcome) == expected and await refunds() == [] and len(calls) == 1
    assert not await queue.run_once(TestSession, CFG, enabled=True) and len(calls) == 1


async def test_partial_never_queries_and_duplicate_does_not_reset_job(client, refund_case, monkeypatch):
    number, completed = await setup(client, refund_case, amount={'total': 19900, 'refund': 1, 'payer_total': 19900, 'payer_refund': 1})
    async def forbidden(*a, **k):
        pytest.fail('partial notices must not be queried as full refunds')
    monkeypatch.setattr(queue, 'query_full_refund', forbidden)
    await queue.run_once(TestSession, CFG, enabled=True)
    assert (await jobs())[0].state == 'attention'
    data = await plain(number, completed, amount={'total': 19900, 'refund': 1, 'payer_total': 19900, 'payer_refund': 1})
    assert (await send(client, data)).status_code == 204
    assert len(await jobs()) == 1 and (await jobs())[0].attempts == 1


async def test_concurrent_claim_and_expired_stale_token_cannot_finish(client, refund_case):
    await setup(client, refund_case)
    claimed = await asyncio.gather(queue.claim(TestSession), queue.claim(TestSession))
    tickets = [t for t in claimed if t]
    assert len(tickets) == 1
    first = tickets[0]
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(lease_until=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    assert not await queue.finish(TestSession, first, 'verified', 'success_needs_admin')
    second = await queue.claim(TestSession)
    assert second.token != first.token and second.attempt == 2
    assert not await queue.finish(TestSession, first, 'verified', 'success_needs_admin')
    assert await queue.finish(TestSession, second, 'retry', 'processing')
    assert not await queue.finish(TestSession, second, 'verified', 'success_needs_admin')
    assert (await jobs())[0].state == 'retry'


async def test_repair_legacy_missing_job_bounded_and_concurrent(client, refund_case):
    number, completed = await setup(client, refund_case)
    assert (await send(client, await plain(number, completed), identity='NOTICE2')).status_code == 204
    async with TestSession() as db:
        await db.execute(delete(RefundVerificationJob))  # synthetic legacy inbox, not a production deletion API
        await db.commit()
    assert await queue.repair_missing(TestSession, limit=1) == 1 and len(await jobs()) == 1
    await asyncio.gather(queue.repair_missing(TestSession), queue.repair_missing(TestSession))
    assert len(await jobs()) == 2


@pytest.mark.parametrize('lost_ack', [False, True])
async def test_notice_and_queue_atomic_ack_recovery(client, refund_case, monkeypatch, lost_ack):
    _, _, create, completed = refund_case
    number = await create('wechat')
    data = await plain(number, completed)
    original = AsyncSession.commit
    async def fail(db):
        if lost_ack:
            await original(db)
        raise OperationalError('synthetic', {}, Exception('lost acknowledgement'))
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'commit', fail)
        assert (await send(client, data)).status_code == 503
    assert len(await jobs()) == int(lost_ack)
    assert (await send(client, data)).status_code == 204
    assert len(await jobs()) == 1


async def test_finish_audit_and_queue_atomic_failure(client, refund_case, monkeypatch):
    await setup(client, refund_case)
    ticket = await queue.claim(TestSession)
    original = AsyncSession.add
    def fail(db, row, *a, **k):
        if isinstance(row, PaymentEvent) and row.kind == 'refund_verify_observed':
            raise OperationalError('synthetic', {}, Exception('audit failed'))
        return original(db, row, *a, **k)
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'add', fail)
        with pytest.raises(OperationalError):
            await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin')
    assert (await jobs())[0].state == 'running'
    assert await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin')


async def test_bounded_retries_and_configuration_mismatch_no_http(client, refund_case, monkeypatch):
    await setup(client, refund_case)
    async def forbidden(*a, **k):
        pytest.fail('foreign merchant must not query')
    monkeypatch.setattr(queue, 'query_full_refund', forbidden)
    for attempt in range(1, 9):
        assert await queue.run_once(TestSession, replace(CFG, mchid='wrong'), enabled=True)
        job = (await jobs())[0]
        assert job.attempts == attempt
        if attempt < 8:
            assert job.state == 'retry'
            async with TestSession() as db:
                await db.execute(update(RefundVerificationJob).values(next_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
                await db.commit()
    assert (await jobs())[0].state == 'attention' and (await jobs())[0].outcome == 'exhausted'
    assert not await queue.run_once(TestSession, CFG, enabled=True)


async def test_last_attempt_crash_becomes_attention_without_new_query(client, refund_case):
    await setup(client, refund_case)
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(attempts=7))
        await db.commit()
    await queue.claim(TestSession)
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(lease_until=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    assert await queue.claim(TestSession) is None
    assert (await jobs())[0].state == 'attention' and (await jobs())[0].attempts == 8


def test_worker_disabled_cli_has_no_database_operation():
    result = subprocess.run([sys.executable, '-m', 'app.refund_worker', '--once'],
                            env={**os.environ, 'WX_REFUND_VERIFY_ENABLED': 'false', 'DATABASE_URL': 'postgresql+asyncpg://invalid@invalid/test'},
                            capture_output=True, text=True)
    assert result.returncode == 1 and 'disabled' in result.stderr and 'Traceback' not in result.stderr


def test_manifest_requires_verification_migration(tmp_path):
    for version, (path, _) in db_admin.migration_manifest().items():
        if int(version) < 15:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.migration_manifest(tmp_path)


def test_real_pg_upgrade_preserves_inbox_and_guards_identity(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    rows(conn, 'DROP TRIGGER check_system_refund_actor ON refund_receipt; DROP FUNCTION codemax_check_system_refund_actor(); ALTER TABLE refund_receipt DROP CONSTRAINT ck_refund_authority; ALTER TABLE refund_receipt DROP COLUMN verification_event_id; ALTER TABLE refund_receipt ALTER COLUMN actor_id SET NOT NULL; DELETE FROM schema_migration WHERE version::integer=16; DROP TABLE refund_verification_job; DROP FUNCTION codemax_check_refund_verification_job(); '
         "DELETE FROM schema_migration WHERE version='0015'; "
         "INSERT INTO sys_user(id,username,password) VALUES(1,'test','synthetic'); "
         "INSERT INTO sys_order(id,order_no,user_id,product_name,amount) VALUES(1,'ORDER',1,'test',100); "
         "INSERT INTO payment_event(id,order_id,attempt_id,kind,evidence) VALUES(1,1,repeat('a',32),'refund_notify_signal','synthetic')")
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT evidence FROM payment_event') == [('synthetic',)]
    assert rows(conn, 'SELECT * FROM refund_verification_job') == []
    rows(conn, 'INSERT INTO refund_verification_job(notice_event_id,order_id) VALUES(1,1)')
    for bad in ['INSERT INTO refund_verification_job(notice_event_id,order_id) VALUES(1,1)',
                'UPDATE refund_verification_job SET order_id=99',
                "UPDATE refund_verification_job SET created_at=created_at+interval '1 day'",
                "UPDATE refund_verification_job SET state='running'",
                'UPDATE refund_verification_job SET attempts=9',
                'DELETE FROM refund_verification_job']:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    rows(conn, "UPDATE refund_verification_job SET state='running',token=repeat('b',32),lease_until=now()+interval '90 seconds',attempts=1")
    db_admin.migrate(conn)


async def test_stop_does_not_cancel_independent_get_worker(client, refund_case, monkeypatch):
    from tests.test_refund_stops import authorized, post, stop_proof
    _, admin, number, proof, _ = await authorized(client, refund_case)
    completed = refund_case[3]
    assert (await post(client, admin, number, 'stop', stop_proof(proof))).status_code == 200
    assert (await send(client, await plain(number, completed, out_refund_no=proof['out_refund_no']))).status_code == 204
    calls = await stub(monkeypatch, number, completed, out_refund_no=proof['out_refund_no'])
    assert await queue.run_once(TestSession, CFG, enabled=True)
    assert len(calls) == 1 and (await jobs())[0].state == 'verified' and await refunds() == []


async def test_invalid_signature_redacted_and_recoverable(client, refund_case, monkeypatch):
    number, completed = await setup(client, refund_case)
    async with TestSession() as db:
        tx = await db.scalar(select(Order.transaction_id).where(Order.order_no == number))
    body = response_body(number, tx, completed)
    async def fetch(cfg, **kwargs):
        response = signed_response(body)
        response.headers['Wechatpay-Signature'] = 'WECHATPAY/SIGNTEST/private-error'
        return await query_full_refund(cfg, **kwargs, transport=httpx.MockTransport(lambda r: response))
    monkeypatch.setattr(queue, 'query_full_refund', fetch)
    await queue.run_once(TestSession, CFG, enabled=True)
    assert (await jobs())[0].state == 'retry' and await refunds() == []
    async with TestSession() as db:
        notes = list((await db.scalars(select(PaymentEvent.evidence))).all())
        assert not any('private-error' in (s or '') for s in notes)


async def test_cancel_after_claim_leaves_recoverable_lease(client, refund_case, monkeypatch):
    await setup(client, refund_case)
    entered = asyncio.Event()
    async def hold(*a, **k):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(queue, 'query_full_refund', hold)
    task = asyncio.create_task(queue.run_once(TestSession, CFG, enabled=True))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await jobs())[0].state == 'running'
    assert await queue.claim(TestSession) is None
    assert await refunds() == []
