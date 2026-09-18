"""Refund preparation is immutable/idempotent, NOT a sender; independent sessions and real PG DDL."""
import asyncio
import uuid

import psycopg2
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin, refund_requests
from app.config import settings
from app.models import Order, PaymentEvent, PaymentReceipt, RefundRequest, User
from app.routers import refund_notify, refunds_admin
from tests.conftest import TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_db_admin import rows
from tests.test_download import path_of
from tests.test_payment_ledger import admin_headers
from tests.test_payment_review import payload as review_payload
from tests.test_payment_review import state, write_review
from tests.test_refund_notifications import plain, send
from tests.test_refunds import provider, query, refunds
from tests.test_refunds import refund_case as refund_case
from tests.test_wechat_pay import CFG


def proof(number, **changes):
    return {'confirm_order_no': number, 'request_id': uuid.uuid4().hex, 'amount': 19900,
            'evidence': '合成订单仅准备，不发送退款', **changes}


async def prepare(client, headers, number, body):
    return await client.post(f'/shop/admin/orders/{number}/refunds/requests', headers=headers, json=body)


async def ledger(client, headers, number, suffix=''):
    result = await client.get(f'/shop/admin/orders/{number}/ledger' + suffix, headers=headers)
    assert result.status_code == 200
    return result.json()


async def records():
    async with TestSession() as db:
        return list((await db.scalars(select(RefundRequest))).all())


async def audits():
    async with TestSession() as db:
        return list((await db.scalars(select(PaymentEvent).where(PaymentEvent.kind == refund_requests.PREPARED_KIND))).all())


async def test_prepare_persists_full_contract_and_audit_without_money_or_rights_change(client, refund_case, monkeypatch):
    buyer, admin, create, _ = refund_case
    number = await create('wechat')
    url = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    async def forbidden(*args, **kwargs):
        pytest.fail('preparation must not call a provider')
    monkeypatch.setattr(refunds_admin, 'query_full_refund', forbidden)
    before = await ledger(client, admin, number)
    body = proof(number)
    response = await prepare(client, admin, number, body)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    value = response.json()['refund_request']
    assert response.json()['changed'] and value['state'] == 'prepared' and value['preparation_only']
    assert value['out_refund_no'].startswith('CMR') and len(value['out_refund_no']) == 35
    assert value['amount'] == 19900 and (value['merchant_id'], value['app_id']) == (CFG.mchid, CFG.appid)
    request, event = (await records())[0], (await audits())[0]
    assert (request.actor_id, request.actor_name, request.evidence) == (event.actor_id, event.actor_name, event.evidence)
    assert request.request_id == event.attempt_id == body['request_id']
    after = await ledger(client, admin, number, '?before=1')
    assert after['events'] == [] and after['refund_request'] == value and not after['refund_prepare_allowed']
    assert after['receipt'] == before['receipt'] and after['order'] == before['order']
    assert after['review']['state'] == 'open' and await refunds() == []
    assert (await client.get(path_of(url))).status_code == 200
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200


async def test_exact_retry_preserves_reference_actor_time_and_review(client, refund_case):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    body = proof(number)
    first = (await prepare(client, admin, number, body)).json()['refund_request']
    assert (await write_review(client, admin, number, await review_payload(client, admin, number))).status_code == 200
    before = await state(client, admin, number)
    result = await prepare(client, admin, number, body)
    assert result.status_code == 200 and not result.json()['changed'] and result.json()['refund_request'] == first
    assert len(await records()) == len(await audits()) == 1 and await state(client, admin, number) == before


@pytest.mark.parametrize('same_id', [True, False])
async def test_same_order_concurrency_has_one_reference(client, refund_case, same_id):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    one = proof(number)
    two = one if same_id else proof(number)
    results = await asyncio.gather(prepare(client, admin, number, one), prepare(client, admin, number, two))
    assert sorted(r.status_code for r in results) == ([200, 200] if same_id else [200, 409])
    assert len(await records()) == len(await audits()) == 1
    if same_id:
        assert results[0].json()['refund_request'] == results[1].json()['refund_request']


async def test_cross_order_or_actor_cannot_reuse_request_identity(client, refund_case):
    _, admin, create, _ = refund_case
    one, two = await create('wechat'), await create('wechat')
    other = await admin_headers(client, 'other-preparer')
    body = proof(one)
    results = await asyncio.gather(prepare(client, admin, one, body), prepare(client, other, two, {**body, 'confirm_order_no': two}))
    assert sorted(r.status_code for r in results) == [200, 409] and len(await records()) == 1
    owner, number, nonowner = (admin, one, other) if results[0].status_code == 200 else (other, two, admin)
    assert (await prepare(client, nonowner, number, {**body, 'confirm_order_no': number})).status_code == 409
    assert (await prepare(client, owner, number, {**body, 'confirm_order_no': number, 'evidence': '不同的准备理由'})).status_code == 409
    assert (await prepare(client, owner, number, proof(number))).status_code == 409
    assert len(await audits()) == 1


@pytest.mark.parametrize('field,value,status', [('amount', True, 422), ('amount', '19900', 422), ('amount', 19900.0, 422),
    ('amount', 19899, 409), ('amount', 19901, 409), ('amount', 0, 422), ('request_id', 'x' * 32, 422),
    ('request_id', 'f' * 33, 422), ('evidence', ' ', 422), ('evidence', 'x' * 161, 422),
    ('evidence', 'bad\x7fcontrol', 422), ('confirm_order_no', 'OTHER', 409)])
async def test_invalid_preparation_never_mints_reference(client, refund_case, field, value, status):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    assert (await prepare(client, admin, number, proof(number, **{field: value}))).status_code == status
    assert await records() == await audits() == []


@pytest.mark.parametrize('source', ['manual', 'mock'])
async def test_no_manual_or_mock_preparations(client, refund_case, source):
    _, admin, create, _ = refund_case
    number = await create(source)
    assert (await prepare(client, admin, number, proof(number))).status_code == 409 and await records() == []


@pytest.mark.parametrize('kind', ['refund_query_started', 'refund_notify_signal'])
async def test_prior_unknown_refund_activity_blocks_new_reference(client, refund_case, kind):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    async with TestSession() as db:
        oid = await db.scalar(select(Order.id).where(Order.order_no == number))
        db.add(PaymentEvent(order_id=oid, attempt_id=uuid.uuid4().hex, kind=kind))
        await db.commit()
    assert not (await ledger(client, admin, number))['refund_prepare_allowed']
    assert (await prepare(client, admin, number, proof(number))).status_code == 409 and await records() == []


@pytest.mark.parametrize('missing_receipt', [True, False])
async def test_missing_receipt_or_unpaid_cannot_prepare(client, refund_case, missing_receipt):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    async with TestSession() as db:
        if missing_receipt:
            await db.execute(delete(PaymentReceipt))
        else:
            await db.execute(update(Order).where(Order.order_no == number).values(status='pending'))
        await db.commit()
    assert not (await ledger(client, admin, number))['refund_prepare_allowed']
    assert (await prepare(client, admin, number, proof(number))).status_code == 409
    assert (await prepare(client, admin, 'UNKNOWN', proof('UNKNOWN'))).status_code == 404 and await records() == []


async def test_permissions_origin_rate_limit_and_actor_recheck(client, refund_case, monkeypatch):
    buyer, admin, create, _ = refund_case
    number = await create('wechat')
    body = proof(number)
    assert (await prepare(client, buyer, number, body)).status_code == 403
    # Explicit cookie path, not the valid Bearer exception.
    cookie = {'Cookie': 'access_token=' + admin['Authorization'].split()[1], 'Origin': 'https://attacker.invalid'}
    assert (await prepare(client, cookie, number, body)).status_code == 403
    from app.ratelimit import limiter
    limiter.reset()
    monkeypatch.setattr(settings, 'RATE_LIMIT_ENABLED', True)
    monkeypatch.setattr(settings, 'RATE_LIMIT_TOOLS', 1)
    assert (await prepare(client, admin, number, body)).status_code == 200
    assert (await prepare(client, admin, number, body)).status_code == 429
    monkeypatch.setattr(settings, 'RATE_LIMIT_ENABLED', False)
    number = await create('wechat')
    lock = refunds_admin.lock_user
    async def revoke(db, uid):
        actor = await lock(db, uid)
        await db.execute(update(User).where(User.id == uid).values(credential_version=actor.credential_version + 1))
        await db.refresh(actor)
        return actor
    monkeypatch.setattr(refunds_admin, 'lock_user', revoke)
    assert (await prepare(client, admin, number, proof(number))).status_code == 403
    assert len(await records()) == 1


@pytest.mark.parametrize('after_commit', [False, True])
async def test_failed_commit_and_lost_ack_retry_original_key(client, refund_case, monkeypatch, after_commit):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    body = proof(number)
    commit = AsyncSession.commit
    async def uncertain(db):
        await (commit(db) if after_commit else db.flush())
        raise OperationalError('synthetic failure', None, Exception('synthetic'))
    monkeypatch.setattr(AsyncSession, 'commit', uncertain)
    assert (await prepare(client, admin, number, body)).status_code == 503
    assert len(await records()) == len(await audits()) == int(after_commit)
    original = (await records())[0].out_refund_no if after_commit else None
    monkeypatch.setattr(AsyncSession, 'commit', commit)
    result = await prepare(client, admin, number, body)
    assert result.status_code == 200 and result.json()['changed'] is not after_commit
    assert len(await records()) == len(await audits()) == 1
    if original:
        assert result.json()['refund_request']['out_refund_no'] == original


@pytest.mark.parametrize('same_reference', [True, False])
async def test_query_completion_is_separate_and_original_request_remains_replayable(client, refund_case, monkeypatch, same_reference):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    body = proof(number)
    original = (await prepare(client, admin, number, body)).json()['refund_request']
    reference = original['out_refund_no'] if same_reference else 'EXTERNAL_REFUND'
    await provider(monkeypatch, number, completed, out_refund_no=reference)
    assert (await query(client, admin, number, out_refund_no=reference)).status_code == 200
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    expected = 'confirmed' if same_reference else 'completed_elsewhere'
    result = (await prepare(client, admin, number, body)).json()
    assert not result['changed'] and result['refund_request']['state'] == expected
    assert result['refund_request']['out_refund_no'] == original['out_refund_no']
    assert (await prepare(client, admin, number, proof(number))).status_code == 409
    assert len(await records()) == len(await audits()) == len(await refunds()) == 1


async def test_notification_never_promotes_preparation_to_completion(client, refund_case, monkeypatch):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    body = proof(number)
    value = (await prepare(client, admin, number, body)).json()['refund_request']
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: CFG)
    assert (await send(client, await plain(number, completed, out_refund_no=value['out_refund_no']))).status_code == 204
    assert (await prepare(client, admin, number, body)).json()['refund_request']['state'] == 'prepared'
    assert await refunds() == [] and (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200


async def test_prior_completed_refund_cannot_create_preparation(client, refund_case, monkeypatch):
    _, admin, create, completed = refund_case
    number = await create('wechat')
    await provider(monkeypatch, number, completed)
    assert (await query(client, admin, number)).status_code == 200
    assert (await prepare(client, admin, number, proof(number))).status_code == 409 and await records() == []


def test_real_migration_preserves_payment_and_checks_immutable_preparations(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    rows(conn, "DROP TABLE refund_request; DROP FUNCTION codemax_check_refund_request(); DELETE FROM schema_migration WHERE version='0012'")
    rows(conn, "INSERT INTO sys_user(username,password,role) VALUES ('preparer','x',1); "
               "INSERT INTO sys_order(order_no,user_id,product_name,amount,payment_mode,merchant_id,app_id,status,transaction_id) "
               "SELECT 'ORDER1',id,'fixed',100,'wechat','MCH','APP','paid','TX1' FROM sys_user; "
               "INSERT INTO payment_receipt(order_id,source,transaction_id,amount,currency,merchant_id,app_id) "
               "SELECT id,'wechat','TX1',100,'CNY','MCH','APP' FROM sys_order")
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT * FROM refund_request') == []
    insert = "INSERT INTO refund_request(order_id,payment_receipt_id,request_id,out_refund_no,merchant_id,app_id,amount,currency,actor_id,actor_name,evidence) SELECT order_id,id,repeat('a',32),'CMR'||repeat('b',32),'MCH','APP',100,'CNY',1,'preparer','synthetic proof' FROM payment_receipt"
    for bad in [insert.replace(",100,'CNY'", ",50,'CNY'"), insert.replace("'MCH','APP'", "'FOREIGN','APP'"),
                insert.replace('order_id,id,', 'order_id,999,'), insert.replace("'preparer','synthetic", "'wrong-name','synthetic"),
                insert.replace("repeat('a',32)", "'bad-id'")]:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    rows(conn, "INSERT INTO payment_event(order_id,attempt_id,kind) SELECT id,'unknown','refund_query_started' FROM sys_order")
    with pytest.raises(psycopg2.Error):
        rows(conn, insert)
    # Test-only removal of the blocker; production has no deletion endpoint.
    rows(conn, "ALTER TABLE payment_event DISABLE TRIGGER freeze_payment_event; DELETE FROM payment_event; ALTER TABLE payment_event ENABLE TRIGGER freeze_payment_event")
    rows(conn, insert)
    for bad in [insert, 'UPDATE refund_request SET amount=50', 'DELETE FROM refund_request']:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    assert rows(conn, 'SELECT amount FROM refund_request') == [(100,)]
    assert rows(conn, 'SELECT status,transaction_id FROM sys_order') == [('paid', 'TX1')]
    db_admin.migrate(conn)
    assert db_admin.status(conn) == []


async def test_request_fact_independently_changes_snapshot_and_remains_discoverable(client, refund_case):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    before = await state(client, admin, number)
    assert (await prepare(client, admin, number, proof(number))).status_code == 200
    async with TestSession() as db:
        # ORM fixture has no append-only triggers; deliberately simulate missing audit evidence.
        await db.execute(delete(PaymentEvent).where(PaymentEvent.kind == refund_requests.PREPARED_KIND))
        await db.commit()
    after = await state(client, admin, number)
    assert after['snapshot'] != before['snapshot'] and after['state'] == 'open'
    result = (await client.get('/shop/admin/orders?bucket=needs_review', headers=admin)).json()
    assert number in [o['order_no'] for o in result['orders']]


@pytest.mark.parametrize('extra', [{'currency': 'USD'}, {'out_refund_no': 'MY_NUMBER'}, {'actor_id': 123}])
async def test_caller_cannot_silently_override_server_owned_contract(client, refund_case, extra):
    _, admin, create, _ = refund_case
    number = await create('wechat')
    assert (await prepare(client, admin, number, proof(number, **extra))).status_code == 422
    assert await records() == []


async def test_audit_failure_rolls_back_preparation_as_one_transaction(client, refund_case):
    from sqlalchemy import event
    _, admin, create, _ = refund_case
    number = await create('wechat')
    body = proof(number)
    def fail_audit(mapper, connection, target):
        if target.kind == refund_requests.PREPARED_KIND:
            raise OperationalError('synthetic audit failure', None, Exception('synthetic'))
    event.listen(PaymentEvent, 'before_insert', fail_audit)
    try:
        assert (await prepare(client, admin, number, body)).status_code == 503
        assert await records() == await audits() == []
    finally:
        event.remove(PaymentEvent, 'before_insert', fail_audit)
    assert (await prepare(client, admin, number, body)).status_code == 200
    assert len(await records()) == len(await audits()) == 1
