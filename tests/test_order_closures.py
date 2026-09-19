"""Synthetic signed Native close; durable attempts and late-payment safety, no live merchant."""
import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import order_closures as close
from app.config import settings
from app.models import Order, PaymentEvent, User
from app.order_state import mark_closed
from app.routers import payments_admin
from app.wechat_pay import WeChatPayError, close_order
from tests.conftest import TestSession
from tests.test_download import auth_headers
from tests.test_payment_ledger import admin_headers, receipts
from tests.test_payments_admin import events, reconcile
from tests.test_payments_admin import workbench as workbench
from tests.test_wechat_notify import build_notify, fetch, make_order, post_notify, txn
from tests.test_wechat_notify import notify_ready as notify_ready
from tests.test_wechat_pay import _AUTH_RE, CFG, signed_response, verify


@pytest.fixture(autouse=True)
def configuration(notify_ready):
    pass


async def setup(client, workbench, monkeypatch):
    headers, number, provider = workbench
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        await mark_closed(db, order)  # local expiry transition, not remote close
    provider(lambda request: signed_response({**txn(number), 'trade_state': 'NOTPAY'}))
    query = await reconcile(client, headers, number)
    assert query.status_code == 200
    monkeypatch.setattr(settings, 'WX_ORDER_CLOSE_ENABLED', True)
    calls = []
    async def sender(cfg, **kwargs):
        def handler(request):
            fields = _AUTH_RE.fullmatch(request.headers['Authorization'])
            verify(fields['signature'], 'POST', request.url.raw_path.decode(), fields['timestamp'], fields['nonce_str'], request.content.decode())
            assert request.content == b'{"mchid":"1900000109"}' and request.url.path.endswith('/' + number + '/close')
            calls.append(request)
            return signed_response(b'', 204)
        return await close_order(cfg, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(payments_admin, 'close_order', sender)
    proof = {'request_id': uuid.uuid4().hex, 'query_attempt_id': query.json()['attempt_id'],
             'confirm_order_no': number, 'amount': 19900, 'evidence': '核对未付款后显式关渠道'}
    return headers, number, provider, proof, calls


async def post(client, headers, number, proof):
    return await client.post(f'/shop/admin/orders/{number}/close-channel', headers=headers, json=proof)


@pytest.mark.parametrize('kind', ['unsigned', 'wrong-status', 'body', 'expired', 'wrong-key', 'business-error', 'compressed', 'large', 'timeout'])
async def test_wire_accepts_only_verified_empty_204(kind):
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('PRIVATE provider detail')
        response = signed_response(b'', 204)
        if kind == 'unsigned':
            return httpx.Response(204)
        if kind == 'wrong-status':
            return signed_response(b'', 200)
        if kind == 'body':
            return signed_response(b'{}', 204)
        if kind == 'business-error':
            return signed_response({'code': 'ORDERPAID', 'message': 'PRIVATE'}, 400)
        if kind == 'large':
            return signed_response(b'x' * 65537, 204)
        if kind == 'expired':
            response.headers['Wechatpay-Timestamp'] = '1'
        if kind == 'wrong-key':
            response.headers['Wechatpay-Serial'] = 'WRONG'
        if kind == 'compressed':
            response.headers['Content-Encoding'] = 'gzip'
        return response
    with pytest.raises(WeChatPayError) as exc:
        await close_order(CFG, out_trade_no='ORIGINAL', transport=httpx.MockTransport(handler))
    assert 'PRIVATE' not in str(exc.value)


async def test_durable_before_wire_exact_replay_and_late_success(client, workbench, monkeypatch):
    headers, number, provider, proof, calls = await setup(client, workbench, monkeypatch)
    real = payments_admin.close_order
    async def check(cfg, **kwargs):
        assert (await events(number))[-1].kind == close.STARTED
        async with TestSession() as db:
            await db.execute(update(Order).where(Order.order_no == number).values(update_time=Order.update_time))
            await db.commit()  # no write lock crosses the network
        return await real(cfg, **kwargs)
    monkeypatch.setattr(payments_admin, 'close_order', check)
    response = await post(client, headers, number, proof)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['attempt']['state'] == 'acknowledged'
    assert len(calls) == 1 and (await fetch(number)).status == 'closed' and await receipts() == []
    assert (await post(client, headers, number, {**proof, 'request_id': uuid.uuid4().hex})).status_code == 409
    # A close acknowledgement must never hide a subsequently verified payment.
    raw, signed = build_notify(txn(number))
    assert (await post_notify(client, raw, signed)).status_code == 200
    assert (await fetch(number)).status == 'paid' and len(await receipts()) == 1
    monkeypatch.setattr(settings, 'WX_ORDER_CLOSE_ENABLED', False)
    monkeypatch.setattr(payments_admin, 'pay_config', lambda: replace(CFG, mchid='other', private_key=''))
    replay = await post(client, headers, number, proof)
    assert replay.status_code == 200 and replay.json()['attempt'] == response.json()['attempt'] and len(calls) == 1
    detail = (await client.get(f'/shop/admin/orders/{number}/ledger?before=1', headers=headers)).json()
    assert detail['channel_close']['latest'] == response.json()['attempt'] and not detail['channel_close']['allowed']
    assert detail['review']['state'] == 'open'


@pytest.mark.parametrize('condition', ['disabled', 'pending', 'paid', 'manual', 'merchant', 'old-query', 'new-query', 'wrong-query'])
async def test_fresh_eligible_original_only(client, workbench, monkeypatch, condition):
    headers, number, provider, proof, calls = await setup(client, workbench, monkeypatch)
    if condition == 'disabled':
        monkeypatch.setattr(settings, 'WX_ORDER_CLOSE_ENABLED', False)
    elif condition == 'merchant':
        monkeypatch.setattr(payments_admin, 'pay_config', lambda: replace(CFG, mchid='other'))
    elif condition == 'wrong-query':
        proof['query_attempt_id'] = uuid.uuid4().hex
    elif condition == 'new-query':
        provider(lambda request: signed_response({**txn(number), 'trade_state': 'CLOSED'}))
        assert (await reconcile(client, headers, number)).status_code == 200
    else:
        async with TestSession() as db:
            if condition == 'old-query':
                await db.execute(update(PaymentEvent).where(PaymentEvent.kind == 'query_notpay').values(create_time=datetime.now(timezone.utc) - timedelta(minutes=6)))
            else:
                await db.execute(update(Order).where(Order.order_no == number).values(
                    **({'payment_mode': 'manual'} if condition == 'manual' else {'status': condition})))
            await db.commit()
    assert (await post(client, headers, number, proof)).status_code == 409
    assert not calls and not any(e.kind == close.STARTED for e in await events(number))


@pytest.mark.parametrize('change', [{'amount': True}, {'amount': 1}, {'url': 'https://evil'}, {'evidence': 'bad\nline'}, {'confirm_order_no': 'OTHER'}])
async def test_strict_confirmation(client, workbench, monkeypatch, change):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    assert (await post(client, headers, number, {**proof, **change})).status_code in (409, 422)
    assert not calls


@pytest.mark.parametrize('same_key', [False, True])
async def test_concurrent_requests_only_one_post(client, workbench, monkeypatch, same_key):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    other = proof if same_key else {**proof, 'request_id': uuid.uuid4().hex}
    results = await asyncio.gather(post(client, headers, number, proof), post(client, headers, number, other))
    assert sorted(r.status_code for r in results) == ([200, 200] if same_key else [200, 409])
    assert len(calls) == 1


@pytest.mark.parametrize('fault', ['before-start', 'ack-lost', 'finish'])
async def test_commit_failure_never_blindly_resends(client, workbench, monkeypatch, fault):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    original = AsyncSession.commit
    async def fail(db):
        start = any(isinstance(row, PaymentEvent) and row.kind == close.STARTED for row in db.new)
        finish = any(isinstance(row, PaymentEvent) and row.kind == close.ACKNOWLEDGED for row in db.new)
        if (fault in ('before-start', 'ack-lost') and start) or (fault == 'finish' and finish):
            if fault == 'ack-lost':
                await original(db)
            raise OperationalError('synthetic', {}, Exception('PRIVATE'))
        await original(db)
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'commit', fail)
        response = await post(client, headers, number, proof)
        assert response.status_code == 503 and 'PRIVATE' not in response.text
    assert len(calls) == (1 if fault == 'finish' else 0)
    recovered = await post(client, headers, number, proof)
    assert recovered.status_code == 200
    assert recovered.json()['attempt']['state'] == ('acknowledged' if fault == 'before-start' else 'unknown')
    assert len(calls) == (0 if fault == 'ack-lost' else 1)


async def test_unknown_requires_new_query_and_wait_then_explicit_new_key(client, workbench, monkeypatch):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    real = payments_admin.close_order
    async def unknown(*args, **kwargs):
        raise WeChatPayError('untrusted')
    monkeypatch.setattr(payments_admin, 'close_order', unknown)
    first = await post(client, headers, number, proof)
    assert first.status_code == 200 and first.json()['attempt']['state'] == 'unknown'
    newer = {**proof, 'request_id': uuid.uuid4().hex}
    assert (await post(client, headers, number, newer)).status_code == 409
    query = await reconcile(client, headers, number)
    newer['query_attempt_id'] = query.json()['attempt_id']
    assert (await post(client, headers, number, newer)).status_code == 409
    async with TestSession() as db:
        await db.execute(update(PaymentEvent).where(PaymentEvent.kind == close.STARTED).values(create_time=datetime.now(timezone.utc)-timedelta(seconds=61)))
        await db.commit()
    monkeypatch.setattr(payments_admin, 'close_order', real)
    assert (await post(client, headers, number, newer)).json()['attempt']['state'] == 'acknowledged'
    assert len(calls) == 1
    assert (await post(client, headers, number, proof)).json()['attempt']['state'] == 'unknown'


async def test_permissions_original_actor_content_and_other_order(client, workbench, monkeypatch):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    assert (await post(client, await auth_headers(client, 'ordinary-close'), number, proof)).status_code == 403
    assert (await post(client, headers, number, proof)).status_code == 200
    for changed in [{'evidence': 'different proof'}, {'amount': 1}, {'query_attempt_id': uuid.uuid4().hex}]:
        assert (await post(client, headers, number, {**proof, **changed})).status_code == 409
    other = await admin_headers(client, name='second-closer')
    assert (await post(client, other, number, proof)).status_code == 409
    second = await make_order(status='closed')
    assert (await post(client, headers, second, {**proof, 'confirm_order_no': second})).status_code == 409
    real = payments_admin.lock_user
    async def revoked(db, identity):
        user = await real(db, identity)
        user.credential_version += 1
        return user
    monkeypatch.setattr(payments_admin, 'lock_user', revoked)
    assert (await post(client, headers, number, proof)).status_code == 403
    assert len(calls) == 1


async def test_payment_racing_close_is_not_overwritten(client, workbench, monkeypatch):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    real = payments_admin.close_order
    async def race(cfg, **kwargs):
        raw, signed = build_notify(txn(number))
        assert (await post_notify(client, raw, signed)).status_code == 200
        async with TestSession() as db:
            await db.execute(update(User).where(User.username == 'auditor').values(role=0))
            await db.commit()
        return await real(cfg, **kwargs)
    monkeypatch.setattr(payments_admin, 'close_order', race)
    response = await post(client, headers, number, proof)
    assert response.status_code == 200 and response.json()['attempt']['actor'] == 'auditor'
    assert (await fetch(number)).status == 'paid' and len(await receipts()) == len(calls) == 1


async def test_origin_and_rate_limit_before_close(client, workbench, monkeypatch):
    headers, number, _, proof, calls = await setup(client, workbench, monkeypatch)
    login = await client.post('/auth/login', data={'username': 'auditor', 'password': 'secret123'})
    assert login.status_code == 200
    assert (await client.post(f'/shop/admin/orders/{number}/close-channel', json=proof,
        headers={'Origin': 'https://evil.example'})).status_code == 403
    monkeypatch.setattr(settings, 'RATE_LIMIT_ENABLED', True)
    monkeypatch.setattr(settings, 'RATE_LIMIT_TOOLS', 1)
    assert (await post(client, headers, number, proof)).status_code == 200
    assert (await post(client, headers, number, proof)).status_code == 429
    assert len(calls) == 1
