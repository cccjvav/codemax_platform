"""Admin workbench HTTP/DB regression tests, including durable I/O boundaries and atomic audits."""
import asyncio

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, PaymentEvent, PaymentReceipt, User
from app.payment_ledger import PaymentConflict, settle
from app.routers import payments_admin
from app.wechat_pay import query_order
from tests.conftest import TestSession, engine
from tests.test_download import auth_headers
from tests.test_payment_ledger import admin_headers, receipts
from tests.test_wechat_notify import build_notify, fetch, make_order, post_notify, txn
from tests.test_wechat_notify import notify_ready as notify_ready
from tests.test_wechat_pay import CFG, signed_response


@pytest.fixture
async def workbench(client, monkeypatch):
    headers, number = await admin_headers(client), await make_order()
    monkeypatch.setattr(payments_admin, 'pay_config', lambda: CFG)
    def provider(handler):
        async def call(cfg, **kwargs):
            return await query_order(cfg, **kwargs, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(payments_admin, 'query_order', call)
    provider(lambda req: signed_response(txn(number)))
    return headers, number, provider


async def reconcile(client, headers, number, **proof):
    return await client.post(f'/shop/admin/orders/{number}/reconcile', headers=headers,
                             json={'confirm_order_no': number, 'evidence': '核对原单补查支付结果', **proof})


async def events(number):
    async with TestSession() as db:
        return list((await db.scalars(select(PaymentEvent).join(Order).where(Order.order_no == number)
                                      .order_by(PaymentEvent.id))).all())


async def test_admin_shell_is_empty_no_store_not_in_sitemap(client):
    page = await client.get('/admin/payments')
    assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
    assert page.headers['x-robots-tag'] == 'noindex, nofollow'
    assert '/static/js/payments-admin.js' in page.text
    assert '/admin/payments' not in (await client.get('/sitemap.xml')).text


async def test_inventory_and_mutation_require_live_admin_role(client, workbench):
    headers, number, provider = workbench
    client.cookies.clear()
    assert (await client.get('/shop/admin/orders')).status_code == 401
    basic = await auth_headers(client, 'ordinary')
    assert (await client.get('/shop/admin/orders', headers=basic)).status_code == 403
    assert (await reconcile(client, basic, number)).status_code == 403
    async with TestSession() as db:
        admin = await db.scalar(select(User).where(User.username == 'auditor'))
        admin.role = 0
        await db.commit()
    assert (await reconcile(client, headers, number)).status_code == 403
    assert await events(number) == [] and await receipts() == []


async def test_inventory_is_bounded_keyset_filtered_and_sql_read_only(client, workbench):
    headers, number, _ = workbench
    async with TestSession() as db:
        owner = await db.scalar(select(Order.user_id).where(Order.order_no == number))
        for i in range(53):
            db.add(Order(order_no=f'LIST{i}', user_id=owner, product_name='frozen', amount=100,
                         status='closed', payment_mode='manual'))
        db.add(Order(order_no='LEGACY', user_id=owner, product_name='old', amount=100, status='paid'))
        await db.commit()
    statements = []
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(engine.sync_engine, 'before_cursor_execute', record)
    try:
        first = await client.get('/shop/admin/orders?bucket=manual', headers=headers)
        second = await client.get(f"/shop/admin/orders?bucket=manual&before={first.json()['next_cursor']}", headers=headers)
        detail = await client.get(f'/shop/admin/orders/{number}/ledger', headers=headers)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', record)
    assert 'UPDATE' not in statements and 'INSERT' not in statements and 'DELETE' not in statements
    assert first.headers['cache-control'] == detail.headers['cache-control'] == 'no-store'
    assert len(first.json()['orders']) == 50 and len(second.json()['orders']) == 3
    assert {o['id'] for o in first.json()['orders']}.isdisjoint(o['id'] for o in second.json()['orders'])
    assert detail.json()['order']['amount'] == 19900
    assert (await client.get('/shop/admin/orders?bucket=legacy', headers=headers)).json()['orders'][0]['order_no'] == 'LEGACY'
    assert (await client.get(f'/shop/admin/orders?order_no={number}', headers=headers)).json()['orders'][0]['order_no'] == number
    for suffix in ['?before=0', '?bucket=typo', '?order_no=x%00']:
        assert (await client.get('/shop/admin/orders' + suffix, headers=headers)).status_code == 422


async def test_query_started_is_durable_before_network_and_success_is_idempotent(client, workbench):
    headers, number, provider = workbench
    async def handler(req):
        trail = await events(number)  # independent connection sees committed start before any provider response
        assert trail[-1].kind == 'query_started'
        async with TestSession() as db:
            order = await db.scalar(select(Order).where(Order.order_no == number))
            order.update_time = order.create_time  # prove there is no held database write lock during I/O
            await db.commit()
        return signed_response(txn(number))
    provider(handler)
    first = await reconcile(client, headers, number)
    assert first.status_code == 200 and first.json()['changed'] is True
    second = await reconcile(client, headers, number)
    assert second.status_code == 200 and second.json()['changed'] is False
    assert len(await receipts()) == 1 and (await fetch(number)).status == 'paid'
    trail = await events(number)
    assert [e.kind for e in trail] == ['query_started', 'query_success'] * 2
    assert trail[0].attempt_id == trail[1].attempt_id != trail[2].attempt_id == trail[3].attempt_id
    assert all(e.actor_name == 'auditor' for e in trail)
    assert (await client.get('/shop/admin/orders?bucket=wechat', headers=headers)).json()['orders'] == []


@pytest.mark.parametrize('kind', ['unsigned', 'http404', 'timeout', 'wrong-order'])
async def test_failed_query_preserves_state_and_records_unknown(client, workbench, kind):
    headers, number, provider = workbench
    def handler(req):
        if kind == 'timeout':
            raise httpx.ReadTimeout('PRIVATE SECRET')
        if kind == 'unsigned':
            return httpx.Response(200, json=txn(number))
        return signed_response(txn('OTHER' if kind == 'wrong-order' else number), 404 if kind == 'http404' else 200)
    provider(handler)
    response = await reconcile(client, headers, number)
    assert response.status_code == 502 and 'PRIVATE' not in response.text
    assert (await fetch(number)).status == 'pending' and await receipts() == []
    assert [e.kind for e in await events(number)] == ['query_started', 'query_unknown']
    assert len((await client.get('/shop/admin/orders?bucket=issues', headers=headers)).json()['orders']) == 1


@pytest.mark.parametrize('state', ['NOTPAY', 'CLOSED', 'REFUND'])
@pytest.mark.parametrize('paid', [False, True])
async def test_observation_never_closes_or_revokes_local_rights(client, workbench, state, paid):
    headers, number, provider = workbench
    if paid:
        assert (await reconcile(client, headers, number)).status_code == 200
    provider(lambda req: signed_response(txn(number, trade_state=state)))
    response = await reconcile(client, headers, number)
    assert response.status_code == 200 and response.json()['observed_state'] == state
    assert (await fetch(number)).status == ('paid' if paid else 'pending')
    assert len(await receipts()) == int(paid)
    assert (await events(number))[-1].kind == ('query_conflict' if paid and state != 'REFUND' else 'query_' + state.lower())


async def test_conflicting_transaction_keeps_original_and_audits_failure(client, workbench):
    headers, number, provider = workbench
    assert (await reconcile(client, headers, number)).status_code == 200
    provider(lambda req: signed_response(txn(number, txid='DIFFERENT')))
    assert (await reconcile(client, headers, number)).status_code == 409
    assert len(await receipts()) == 1 and (await receipts())[0].transaction_id == 'TX0001'
    assert (await events(number))[-1].kind == 'query_conflict'
    assert len([e for e in await events(number) if e.kind == 'query_success']) == 1


@pytest.mark.parametrize('change', ['role', 'status', 'credential_version'])
async def test_permissions_rechecked_after_network_before_settlement(client, workbench, change):
    headers, number, provider = workbench
    async def handler(req):
        async with TestSession() as db:
            admin = await db.scalar(select(User).where(User.username == 'auditor'))
            setattr(admin, change, 0 if change != 'credential_version' else admin.credential_version + 1)
            await db.commit()
        return signed_response(txn(number))
    provider(handler)
    assert (await reconcile(client, headers, number)).status_code == 403
    assert await receipts() == [] and (await fetch(number)).status == 'pending'
    assert (await events(number))[-1].kind == 'query_aborted'


@pytest.mark.parametrize('kind', ['mode', 'merchant', 'legacy', 'confirm', 'blank'])
async def test_guard_rejects_before_provider_or_started_event(client, workbench, kind):
    headers, number, provider = workbench
    provider(lambda req: pytest.fail('provider must not be called'))
    if kind in ('mode', 'merchant', 'legacy'):
        async with TestSession() as db:
            order = await db.scalar(select(Order).where(Order.order_no == number))
            if kind == 'merchant':
                order.merchant_id = 'OTHER'
            else:
                order.payment_mode = 'manual' if kind == 'mode' else None
            await db.commit()
    proof = {'confirm_order_no': 'OTHER'} if kind == 'confirm' else {'evidence': '   '} if kind == 'blank' else {}
    assert (await reconcile(client, headers, number, **proof)).status_code == (422 if kind == 'blank' else 409)
    assert await events(number) == []


async def test_success_audit_and_receipt_rollback_together_on_commit_failure(client, workbench, monkeypatch):
    headers, number, _ = workbench
    original = AsyncSession.commit
    async def broken(db):
        await db.flush()
        if await db.scalar(select(PaymentReceipt.id)) is not None:
            raise RuntimeError('injected failure after all three changes were flushed')
        await original(db)
    monkeypatch.setattr(AsyncSession, 'commit', broken)
    with pytest.raises(RuntimeError, match='injected failure'):
        await reconcile(client, headers, number)
    assert await receipts() == [] and (await fetch(number)).status == 'pending'
    assert [e.kind for e in await events(number)] == ['query_started']


async def test_callback_and_query_share_single_atomic_receipt(client, workbench, notify_ready):
    headers, number, provider = workbench
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(req):
        entered.set()
        await release.wait()
        return signed_response(txn(number))
    provider(handler)
    pending = asyncio.create_task(reconcile(client, headers, number))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        assert (await post_notify(client, *build_notify(txn(number)))).status_code == 200
    finally:
        release.set()
    response = await pending
    assert response.status_code == 200 and response.json()['changed'] is False
    assert len(await receipts()) == 1 and (await receipts())[0].actor_name is None
    assert (await events(number))[-1].kind == 'query_success'


async def test_audit_cannot_be_attached_to_another_order(client):
    first, second = await make_order(), await make_order()
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == first))
        other = await db.scalar(select(Order.id).where(Order.order_no == second))
        with pytest.raises(PaymentConflict):
            await settle(db, order, source='wechat', transaction_id='tx', merchant_id=CFG.mchid, app_id=CFG.appid,
                         audit_event=PaymentEvent(order_id=other, attempt_id='x', kind='query_success'))
    assert await receipts() == [] and await events(second) == []


async def test_cookie_query_rejects_cross_origin_without_creating_attempt(client, workbench):
    _, number, provider = workbench
    provider(lambda req: pytest.fail('CSRF must be blocked before provider'))
    response = await reconcile(client, {'Origin': 'https://foreign.invalid'}, number)
    assert response.status_code == 403 and await events(number) == []


async def test_query_is_explicit_post_and_uses_its_own_rate_bucket(client, workbench, monkeypatch):
    from app.config import settings
    from app.ratelimit import limiter
    headers, number, _ = workbench
    assert (await client.get(f'/shop/admin/orders/{number}/reconcile', headers=headers)).status_code == 405
    monkeypatch.setattr(settings, 'RATE_LIMIT_ENABLED', True)
    monkeypatch.setattr(settings, 'RATE_LIMIT_TOOLS', 1)
    limiter.reset()
    try:
        assert (await reconcile(client, headers, number)).status_code == 200
        assert (await reconcile(client, headers, number)).status_code == 429
        assert len(await events(number)) == 2
    finally:
        limiter.reset()


@pytest.mark.parametrize('path', ['reconcile', 'confirm', 'legacy-binding'])
@pytest.mark.parametrize('origin', ['https://sibling.test', 'null', 'http://test:bad', 'http://test/foreign'])
async def test_all_financial_cookie_writes_reject_foreign_or_malformed_origin(client, workbench, path, origin):
    _, number, _ = workbench
    url = f'/shop/admin/orders/{number}/reconcile' if path == 'reconcile' else f'/shop/orders/{number}/{path}'
    response = await client.post(url, headers={'Origin': origin}, json={'confirm_order_no': number, 'evidence': 'test'})
    assert response.status_code == 403 and await events(number) == []


async def test_finance_origin_normalization_metadata_and_bearer_channel(client, workbench):
    headers, number, _ = workbench
    same = await reconcile(client, {'Origin': 'http://test:80', 'Sec-Fetch-Site': 'same-origin'}, number)
    assert same.status_code == 200
    assert (await reconcile(client, {'Sec-Fetch-Site': 'same-site'}, number)).status_code == 403
    assert (await reconcile(client, {**headers, 'Origin': 'https://api-client.invalid'}, number)).status_code == 200
    assert (await reconcile(client, {'Authorization': 'Bearer INVALID', 'Origin': 'https://api-client.invalid'}, number)).status_code == 401
