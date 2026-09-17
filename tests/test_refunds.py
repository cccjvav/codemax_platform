"""Synthetic full-refund proofs, real signatures/HTTP/DB concurrency; never executes real refunds."""
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin
from app.config import settings
from app.delivery import snapshot_product
from app.models import Order, PaymentEvent, PaymentReceipt, RefundReceipt, User
from app.payment_ledger import settle
from app.routers import refunds_admin, shop
from app.storage import LocalStorage, sign_download
from app.wechat_pay import WeChatPayError, query_full_refund
from tests.conftest import PRODUCT_BYTES, PRODUCT_KEY, TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_db_admin import rows
from tests.test_download import auth_headers, path_of
from tests.test_payment_ledger import admin_headers
from tests.test_wechat_pay import _AUTH_RE, CFG, signed_response, verify


@pytest.fixture
async def refund_case(client, product):
    buyer = await auth_headers(client, 'refund-buyer')
    admin = await admin_headers(client)
    completed = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async def order(source='manual', paid_at=None):
        async with TestSession() as db:
            user = await db.scalar(select(User).where(User.username == 'refund-buyer'))
            actor = await db.scalar(select(User).where(User.username == 'auditor'))
            snap = snapshot_product(LocalStorage(str(product), 'http://test', settings.SECRET_KEY), PRODUCT_KEY)
            obj = Order(order_no=uuid.uuid4().hex, user_id=user.id, product_name='fixed synthetic file', amount=19900,
                         payment_mode=source, merchant_id=CFG.mchid if source == 'wechat' else None,
                         app_id=CFG.appid if source == 'wechat' else None, status='pending',
                         delivery_key=snap.key, delivery_digest=snap.digest, delivery_size=snap.size)
            db.add(obj)
            await db.commit()
            await settle(db, obj, source=source, transaction_id=uuid.uuid4().hex,
                         merchant_id=obj.merchant_id, app_id=obj.app_id, actor=actor, evidence='synthetic paid proof',
                         paid_at=paid_at or datetime.now(timezone.utc) - timedelta(days=1))
            return obj.order_no
    return buyer, admin, order, completed


async def manual(client, header, number, completed, **change):
    return await client.post(f'/shop/admin/orders/{number}/refunds/manual', headers=header,
                             json={'confirm_order_no': number, 'evidence': 'synthetic refunded proof',
                                   'reference': 'BANK-REFUND-1', 'amount': 19900, 'completed_at': completed, **change})


async def query(client, header, number, **change):
    return await client.post(f'/shop/admin/orders/{number}/refunds/query', headers=header,
                             json={'confirm_order_no': number, 'evidence': 'synthetic query reason', 'out_refund_no': 'REFUND1', **change})


async def refunds():
    async with TestSession() as db:
        return list((await db.scalars(select(RefundReceipt))).all())


def response_body(number, transaction_id, completed, **changes):
    return {'out_trade_no': number, 'transaction_id': transaction_id, 'out_refund_no': 'REFUND1', 'refund_id': '500123',
            'amount': {'total': 19900, 'refund': 19900, 'currency': 'CNY'}, 'status': 'SUCCESS', 'channel': 'ORIGINAL',
            'success_time': completed, **changes}


async def provider(monkeypatch, number, completed, handler=None, **changes):
    async with TestSession() as db:
        transaction_id = await db.scalar(select(Order.transaction_id).where(Order.order_no == number))
    body = {**response_body(number, transaction_id, completed), **changes}
    async def call(cfg, **kwargs):
        return await query_full_refund(cfg, **kwargs, transport=httpx.MockTransport(handler or (lambda req: signed_response(body))))
    monkeypatch.setattr(refunds_admin, 'pay_config', lambda: CFG)
    monkeypatch.setattr(refunds_admin, 'query_full_refund', call)
    return body


async def test_manual_refund_revokes_existing_link_and_reissue_only_this_order(client, refund_case):
    buyer, admin, create, completed = refund_case
    number, other = await create(), await create()
    first = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    second = (await client.post(f'/shop/download/{other}', headers=buyer)).json()['download_url']
    assert (await client.get(path_of(first))).content == PRODUCT_BYTES
    assert (await manual(client, admin, number, completed)).json()['changed'] is True
    assert (await client.get(path_of(first))).status_code == 403
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    assert (await client.get(path_of(second))).content == PRODUCT_BYTES
    assert (await client.post(f'/shop/download/{other}', headers=buyer)).status_code == 200
    status = (await client.get(f'/shop/orders/{number}', headers=buyer)).json()
    assert status['refunded'] and status['status'] == 'downloaded'
    history = (await client.get('/shop/orders', headers=buyer)).json()['orders']
    assert {o['order_no']: o['refunded'] for o in history} == {number: True, other: False}
    assert len(await refunds()) == 1


async def test_manual_retries_preserve_first_receipt_actor_and_time(client, refund_case):
    _, admin, create, completed = refund_case
    number = await create()
    assert (await manual(client, admin, number, completed)).status_code == 200
    original = (await refunds())[0]
    assert not (await manual(client, admin, number, completed, evidence='second operation note')).json()['changed']
    assert (await manual(client, admin, number, completed, reference='DIFFERENT')).status_code == 409
    after = (await refunds())[0]
    assert (original.id, original.evidence, original.received_at, original.actor_name) == (after.id, after.evidence, after.received_at, after.actor_name)


@pytest.mark.parametrize('field,value,status', [('amount', 100, 409), ('amount', True, 422), ('amount', '19900', 422),
    ('confirm_order_no', 'OTHER', 409), ('evidence', '   ', 422), ('completed_at', '2026-09-01T00:00:00', 422),
    ('completed_at', '2099-09-01T00:00:00Z', 409), ('completed_at', '2001-09-01T00:00:00Z', 409)])
async def test_manual_invalid_proof_never_revokes(client, refund_case, field, value, status):
    _, admin, create, completed = refund_case
    number = await create()
    changes = {field: value}
    if field == 'completed_at':
        completed = value
        changes = {}
    assert (await manual(client, admin, number, completed, **changes)).status_code == status
    assert await refunds() == []


@pytest.mark.parametrize('source', ['wechat', 'mock'])
async def test_manual_cannot_refund_other_sources(client, refund_case, source):
    _, admin, create, completed = refund_case
    assert (await manual(client, admin, await create(source), completed)).status_code == 409
    assert await refunds() == []


async def test_refunds_need_live_admin_and_same_origin(client, refund_case):
    buyer, admin, create, completed = refund_case
    number = await create()
    assert (await manual(client, buyer, number, completed)).status_code == 403
    assert (await manual(client, {'Origin': 'https://foreign.invalid'}, number, completed)).status_code == 403
    async with TestSession() as db:
        await db.execute(update(User).where(User.username == 'auditor').values(role=0))
        await db.commit()
    assert (await manual(client, admin, number, completed)).status_code == 403
    assert await refunds() == []


async def test_concurrent_refunds_same_order_and_cross_order_reference(client, refund_case):
    _, admin, create, completed = refund_case
    number, other = await create(), await create()
    results = await asyncio.gather(manual(client, admin, number, completed), manual(client, admin, number, completed))
    assert sorted(r.json()['changed'] for r in results) == [False, True]
    assert (await manual(client, admin, other, completed)).status_code == 409
    assert len(await refunds()) == 1


async def test_wechat_query_signed_get_and_atomic_full_refund(client, refund_case, monkeypatch):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    body = await provider(monkeypatch, number, completed)
    async def handler(req):
        assert req.method == 'GET' and req.content == b''
        assert str(req.url) == 'https://api.mch.weixin.qq.com/v3/refund/domestic/refunds/REFUND1'
        fields = _AUTH_RE.fullmatch(req.headers['Authorization'])
        verify(fields['signature'], 'GET', req.url.raw_path.decode(), fields['timestamp'], fields['nonce_str'], '')
        async with TestSession() as db:
            assert list((await db.scalars(select(PaymentEvent.kind))).all()) == ['refund_query_started']
            assert await db.scalar(select(RefundReceipt.id)) is None
        return signed_response(body)
    await provider(monkeypatch, number, completed, handler)
    url = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    assert (await query(client, admin, number)).json()['changed']
    assert (await client.get(path_of(url))).status_code == 403
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        # A late exact payment confirmation remains idempotent and cannot resurrect entitlement.
        assert not await settle(db, order, source='wechat', transaction_id=order.transaction_id,
                                merchant_id=order.merchant_id, app_id=order.app_id)
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    ledger = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()
    assert ledger['receipt'] and ledger['refund']['refund_id'] == '500123'
    assert [e['kind'] for e in ledger['events']] == ['refund_query_success', 'refund_query_started']


@pytest.mark.parametrize('state', ['PROCESSING', 'CLOSED', 'ABNORMAL'])
async def test_non_success_retains_rights_and_enters_review(client, refund_case, monkeypatch, state):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    await provider(monkeypatch, number, completed, status=state, success_time=None)
    assert (await query(client, admin, number)).json()['observed_state'] == state
    assert await refunds() == []
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200
    ledger = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()
    assert ledger['review']['state'] == 'open' and ledger['review']['orphans'] == 0


@pytest.mark.parametrize('changes', [{'out_trade_no': 'OTHER'}, {'transaction_id': 'OTHER'}, {'out_refund_no': 'OTHER'},
    {'amount': {'total': 19900, 'refund': 100, 'currency': 'CNY'}}, {'amount': {'total': 19900, 'refund': True, 'currency': 'CNY'}},
    {'channel': 'OTHER_BANKCARD'}, {'status': 'REFUND'}, {'refund_id': ''}, {'success_time': None},
    {'success_time': '2026-09-17T00:00:00'}])
async def test_mismatched_provider_proof_is_unknown_not_refunded(client, refund_case, monkeypatch, changes):
    _, admin, create, completed = refund_case
    number = await create('wechat')
    await provider(monkeypatch, number, completed, **changes)
    assert (await query(client, admin, number)).status_code == 502
    assert await refunds() == []


@pytest.mark.parametrize('bad', ['unsigned', 'tampered', 'stale'])
async def test_refund_requires_actual_response_signature(bad):
    body = response_body('ORDER1', 'TX1', '2026-09-17T00:00:00Z')
    response = signed_response(body, timestamp='1000000000') if bad == 'stale' else signed_response(body)
    if bad == 'unsigned':
        response.headers.clear()
    if bad == 'tampered':
        response = httpx.Response(200, content=response.content + b' ', headers=response.headers)
    with pytest.raises(WeChatPayError):
        await query_full_refund(CFG, out_refund_no='REFUND1', out_trade_no='ORDER1', transaction_id='TX1', total=19900,
                                transport=httpx.MockTransport(lambda req: response))


async def test_query_permission_change_after_network_aborts(client, refund_case, monkeypatch):
    _, admin, create, completed = refund_case
    number = await create('wechat')
    body = await provider(monkeypatch, number, completed)
    async def handler(req):
        async with TestSession() as db:
            await db.execute(update(User).where(User.username == 'auditor').values(credential_version=User.credential_version + 1))
            await db.commit()
        return signed_response(body)
    await provider(monkeypatch, number, completed, handler)
    assert (await query(client, admin, number)).status_code == 403
    assert await refunds() == []
    async with TestSession() as db:
        assert (await db.scalars(select(PaymentEvent.kind).order_by(PaymentEvent.id))).all() == ['refund_query_started', 'refund_query_aborted']


async def test_refund_and_audit_are_not_committed_separately(client, refund_case, monkeypatch):
    _, admin, create, completed = refund_case
    number = await create()
    real = AsyncSession.commit
    async def fail(db):
        if any(isinstance(o, RefundReceipt) for o in db.new):
            await db.flush()
            async with TestSession() as observer:
                assert await observer.scalar(select(RefundReceipt.id)) is None
                assert await observer.scalar(select(PaymentEvent.id)) is None
            raise RuntimeError('injected after refund and audit flush')
        await real(db)
    monkeypatch.setattr(AsyncSession, 'commit', fail)
    with pytest.raises(RuntimeError, match='injected'):
        await manual(client, admin, number, completed)
    assert await refunds() == []
    async with TestSession() as db:
        assert await db.scalar(select(PaymentEvent.id)) is None


async def test_legacy_links_rejected_and_order_identity_bound(client, refund_case):
    buyer, _, create, _ = refund_case
    number, other = await create(), await create()
    url = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    assert (await client.get(path_of(url))).headers['cache-control'] == 'private, no-store'
    assert (await client.get(path_of(url.replace(number, other)))).status_code == 403
    parts = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    parts.pop('order_no')
    parts['signature'] = sign_download(settings.SECRET_KEY, parts['key'], int(parts['expires']))
    assert (await client.get('/shop/dl', params=parts)).status_code == 403
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200


async def test_refund_during_file_hash_blocks_grant_and_existing_token(client, refund_case, monkeypatch):
    buyer, admin, create, completed = refund_case
    number = await create()
    url = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    original = shop.run_in_threadpool
    async def interleave(fn, *args):
        result = await original(fn, *args)
        if fn is shop.verify_snapshot:
            assert (await manual(client, admin, number, completed)).status_code == 200
        return result
    monkeypatch.setattr(shop, 'run_in_threadpool', interleave)
    assert (await client.get(path_of(url))).status_code == 403


async def test_order_key_must_match_frozen_contract_even_with_valid_signature(client, refund_case):
    _, _, create, _ = refund_case
    number = await create()
    expires = int(time.time()) + 100
    key = PRODUCT_KEY
    signature = sign_download(settings.SECRET_KEY, key, expires, order_no=number)
    assert (await client.get('/shop/dl', params={'order_no': number, 'key': key, 'expires': expires, 'signature': signature})).status_code == 403


def test_pg_refund_migration_validates_full_original_receipt_and_freezes_records(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    rows(conn, "DROP TABLE refund_receipt; DROP FUNCTION codemax_check_full_refund(); DELETE FROM schema_migration WHERE version='0011'")
    rows(conn, "INSERT INTO sys_user(username,password,role) VALUES ('refund-admin','x',1); "
               "INSERT INTO sys_order(order_no,user_id,product_name,amount,payment_mode,status,transaction_id) "
               "SELECT 'REFUND-ORDER',id,'fixed',100,'manual','paid','BANK-PAY' FROM sys_user; "
               "INSERT INTO payment_receipt(order_id,source,transaction_id,amount,currency,actor_id,actor_name,evidence) "
               "SELECT id,'manual','BANK-PAY',100,'CNY',user_id,'refund-admin','synthetic' FROM sys_order")
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT * FROM refund_receipt') == []
    insert = "INSERT INTO refund_receipt(order_id,payment_receipt_id,source,merchant_id,refund_id,out_refund_no,amount,currency,actor_id,actor_name,evidence,completed_at) SELECT order_id,id,'manual','','BANK-REFUND','BANK-REFUND',100,'CNY',actor_id,actor_name,'synthetic',now() FROM payment_receipt"
    for bad in [insert.replace(",100,'CNY'", ",50,'CNY'"), insert.replace("'manual',''", "'wechat',''"), insert.replace("order_id,id,", "order_id,999,")]:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    rows(conn, insert)
    for bad in [insert, 'UPDATE refund_receipt SET amount=1', 'DELETE FROM refund_receipt']:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    assert rows(conn, 'SELECT amount FROM refund_receipt') == [(100,)]
    assert rows(conn, 'SELECT status,transaction_id FROM sys_order') == [('paid', 'BANK-PAY')]
    db_admin.migrate(conn)
    assert db_admin.status(conn) == []


async def test_success_repeat_and_later_processing_never_restores_download(client, refund_case, monkeypatch):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    await provider(monkeypatch, number, completed)
    assert (await query(client, admin, number)).json()['changed']
    before = (await refunds())[0]
    assert not (await query(client, admin, number)).json()['changed']
    await provider(monkeypatch, number, completed, status='PROCESSING')
    assert (await query(client, admin, number)).json()['observed_state'] == 'PROCESSING'
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    after = (await refunds())[0]
    assert (after.id, after.received_at, after.actor_name) == (before.id, before.received_at, before.actor_name)


async def test_refund_during_link_issuance_preserves_original_paid_status(client, refund_case, monkeypatch):
    buyer, admin, create, completed = refund_case
    number = await create()
    original = shop.run_in_threadpool
    async def interleave(fn, *args):
        result = await original(fn, *args)
        if fn is shop.verify_snapshot:
            assert (await manual(client, admin, number, completed)).status_code == 200
        return result
    monkeypatch.setattr(shop, 'run_in_threadpool', interleave)
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    async with TestSession() as db:
        assert await db.scalar(select(Order.status).where(Order.order_no == number)) == 'paid'


async def test_refund_fact_alone_changes_review_snapshot(client, refund_case):
    from app.payment_review import review_states
    _, admin, create, completed = refund_case
    number = await create()
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        before = (await review_states(db, [order]))[order.id]['snapshot']
    assert (await manual(client, admin, number, completed)).status_code == 200
    async with TestSession() as db:
        # ORM-created test DB intentionally lacks production append-only triggers. Remove just the
        # event to prove the receipt participates independently, rather than only event count/max.
        from sqlalchemy import delete
        await db.execute(delete(PaymentEvent))
        await db.commit()
        order = await db.scalar(select(Order).where(Order.order_no == number))
        assert (await review_states(db, [order]))[order.id]['snapshot'] != before


async def test_refund_started_aging_uses_same_attempt_and_protocol(client, refund_case):
    from app.payment_review import review_states
    _, _, create, _ = refund_case
    number = await create('wechat')
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        start = PaymentEvent(order_id=order.id, attempt_id='attempt', kind='refund_query_started',
                             create_time=datetime.now(timezone.utc) - timedelta(seconds=61))
        db.add_all([start, PaymentEvent(order_id=order.id, attempt_id='attempt', kind='query_success')])
        await db.commit()
        assert (await review_states(db, [order]))[order.id]['orphans'] == 1
        db.add(PaymentEvent(order_id=order.id, attempt_id='attempt', kind='refund_query_processing'))
        await db.commit()
        state = (await review_states(db, [order]))[order.id]
        assert state['orphans'] == 0 and state['state'] == 'open'


async def test_competing_admins_cannot_assign_same_refund_reference_to_two_orders(client, refund_case):
    _, first, create, completed = refund_case
    second = await admin_headers(client, 'second-auditor')
    one, two = await create(), await create()
    results = await asyncio.gather(manual(client, first, one, completed), manual(client, second, two, completed))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(await refunds()) == 1


async def test_missing_historical_payment_receipt_is_not_invented(client, refund_case):
    from sqlalchemy import delete
    _, admin, create, completed = refund_case
    number = await create()
    async with TestSession() as db:
        await db.execute(delete(PaymentReceipt))
        await db.commit()
    assert (await manual(client, admin, number, completed)).status_code == 409
    assert await refunds() == []


async def test_refund_query_wrong_merchant_never_calls_provider(client, refund_case, monkeypatch):
    from dataclasses import replace
    _, admin, create, _ = refund_case
    number = await create('wechat')
    monkeypatch.setattr(refunds_admin, 'pay_config', lambda: replace(CFG, mchid='FOREIGN'))
    async def forbidden(*args, **kwargs):
        pytest.fail('wrong merchant must fail before network')
    monkeypatch.setattr(refunds_admin, 'query_full_refund', forbidden)
    assert (await query(client, admin, number)).status_code == 409
    assert await refunds() == []


async def test_refund_reference_is_url_encoded_before_signing():
    reference = 'REFUND|*@_1'
    def handler(req):
        assert req.url.raw_path.decode() == '/v3/refund/domestic/refunds/REFUND%7C%2A%40_1'
        fields = _AUTH_RE.fullmatch(req.headers['Authorization'])
        verify(fields['signature'], 'GET', req.url.raw_path.decode(), fields['timestamp'], fields['nonce_str'], '')
        return signed_response(response_body('ORDER1', 'TX1', '2026-09-17T00:00:00Z', out_refund_no=reference))
    assert (await query_full_refund(CFG, out_refund_no=reference, out_trade_no='ORDER1', transaction_id='TX1', total=19900,
                                   transport=httpx.MockTransport(handler))).state == 'SUCCESS'


@pytest.mark.parametrize('source', ['manual', 'wechat'])
async def test_payment_and_refund_normalize_timezones_before_storage(client, refund_case, monkeypatch, source):
    from app.timeutil import as_utc
    _, admin, create, _ = refund_case
    now = datetime.now(timezone.utc)
    paid_at = (now - timedelta(minutes=3)).astimezone(timezone(timedelta(hours=8)))
    completed_at = (now - timedelta(minutes=1)).astimezone(timezone(timedelta(hours=2)))
    number = await create(source, paid_at)
    if source == 'wechat':
        await provider(monkeypatch, number, completed_at.isoformat())
    for expected in (True, False):
        result = await manual(client, admin, number, completed_at.isoformat()) if source == 'manual' else await query(client, admin, number)
        assert result.status_code == 200 and result.json()['changed'] is expected
    async with TestSession() as db:
        payment = await db.scalar(select(PaymentReceipt))
        refund = await db.scalar(select(RefundReceipt))
        order = await db.scalar(select(Order))
        assert as_utc(payment.paid_at) == as_utc(order.paid_at) == as_utc(paid_at)
        assert as_utc(refund.completed_at) == as_utc(completed_at)
