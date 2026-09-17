"""Synthetic RSA/AES callback inbox and real DB races; never send real refunds or call WeChat."""
import asyncio
import base64
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import refund_notifications as inbox
from app.models import Order, PaymentEvent, PaymentReceipt
from app.routers import refund_notify
from tests.conftest import TestSession
from tests.test_download import path_of
from tests.test_payment_review import payload, state, write_review
from tests.test_refunds import provider, query, refunds
from tests.test_refunds import refund_case as refund_case
from tests.test_wechat_notify import _OTHER_KEY, PUBKEY_PEM
from tests.test_wechat_pay import CFG, signed_response

URL = '/shop/refunds/notify'


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: CFG)


async def plain(number, completed, **change):
    async with TestSession() as db:
        tx = await db.scalar(select(Order.transaction_id).where(Order.order_no == number))
    return {'mchid': CFG.mchid, 'out_trade_no': number, 'transaction_id': tx, 'out_refund_no': 'REFUND1',
            'refund_id': '500123', 'refund_status': 'SUCCESS', 'success_time': completed,
            'amount': {'total': 19900, 'refund': 19900, 'payer_total': 19000, 'payer_refund': 19000},
            'user_received_account': 'SECRET_BANK_ACCOUNT', **change}


def packet(data, *, identity='NOTICE1', outer=None, resource=None, key=None, sig=None):
    """Correct refund envelope; authenticate EXACT final bytes, with independently mutable layers."""
    nonce = uuid.uuid4().hex[:12]
    encrypted = AESGCM((key or CFG.api_v3_key).encode()).encrypt(nonce.encode(), json.dumps(data).encode(), b'refund')
    body = {'id': identity, 'create_time': data.get('success_time') or '2026-09-01T00:00:00Z', 'resource_type': 'encrypt-resource',
            'event_type': 'REFUND.' + data.get('refund_status', 'SUCCESS'),
            'resource': {'algorithm': 'AEAD_AES_256_GCM', 'original_type': 'refund', 'nonce': nonce,
                         'associated_data': 'refund', 'ciphertext': base64.b64encode(encrypted).decode(), **(resource or {})},
            **(outer or {})}
    signed = signed_response(body, **(sig or {}))
    return signed.content, dict(signed.headers)


async def send(client, data, **kwargs):
    raw, headers = packet(data, **kwargs)
    return await client.post(URL, content=raw, headers=headers)


async def notices():
    async with TestSession() as db:
        return list((await db.scalars(select(PaymentEvent).where(PaymentEvent.kind == inbox.NOTICE_KIND))).all())


async def test_durable_notice_only_signed_query_revokes_existing_and_new_links(client, refund_case, monkeypatch):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    url = (await client.post(f'/shop/download/{number}', headers=buyer)).json()['download_url']
    before = await state(client, admin, number)
    data = await plain(number, completed)
    async def forbidden(*args, **kwargs):
        pytest.fail('notification must never call provider')
    monkeypatch.setattr('app.routers.refunds_admin.query_full_refund', forbidden)
    response = await send(client, data)
    assert response.status_code == 204 and not response.content and response.headers['cache-control'] == 'no-store'
    assert await refunds() == []
    assert (await client.get(path_of(url))).status_code == 200
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200
    after = await state(client, admin, number)
    assert after['state'] == 'open' and after['issues'] == before['issues'] + 1
    event = (await notices())[0]
    assert event.actor_id is None and event.actor_name is None and 'SECRET_BANK_ACCOUNT' not in event.evidence
    view = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()['refund_notice']
    assert view['refund_no'] == 'REFUND1' and view['state'] == 'SUCCESS' and not view['partial']
    assert (await client.get(f'/shop/admin/orders/{number}/ledger', headers=buyer)).status_code == 403
    await provider(monkeypatch, number, completed)
    assert (await query(client, admin, number)).json()['changed']
    assert (await client.get(path_of(url))).status_code == 403
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    assert len(await refunds()) == 1


async def test_reencrypted_exact_retry_preserves_first_record_and_closed_review(client, refund_case):
    _, admin, create, completed = refund_case
    number = await create('wechat')
    data = await plain(number, completed)
    assert (await send(client, data)).status_code == 204
    first = (await notices())[0]
    assert (await write_review(client, admin, number, await payload(client, admin, number))).status_code == 200
    review = await state(client, admin, number)
    # Encryption nonce, signature and insignificant PII/summary can differ, business facts cannot.
    assert (await send(client, {**data, 'user_received_account': 'CHANGED_UNUSED_PII'}, outer={'summary': 'retry'})).status_code == 204
    again = (await notices())[0]
    assert len(await notices()) == 1 and (again.id, again.create_time, again.evidence) == (first.id, first.create_time, first.evidence)
    assert await state(client, admin, number) == review and review['state'] == 'reviewed'
    assert (await send(client, data, identity='NOTICE2')).status_code == 204
    assert (await state(client, admin, number))['state'] == 'open'


@pytest.mark.parametrize('change', [{'out_refund_no': 'OTHER'}, {'refund_id': '500124'}, {'refund_status': 'CLOSED'},
                                   {'success_time': '2026-09-17T00:00:00Z'},
                                   {'amount': {'total': 19900, 'refund': 100, 'payer_total': 19000, 'payer_refund': 100}}])
async def test_same_notification_id_changed_facts_conflict(client, refund_case, change):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    assert (await send(client, data)).status_code == 204
    assert (await send(client, {**data, **change})).status_code == 409
    assert len(await notices()) == 1 and await refunds() == []


@pytest.mark.parametrize('same_order', [True, False])
async def test_concurrent_notification_id_has_one_owner(client, refund_case, same_order):
    _, _, create, completed = refund_case
    one = await create('wechat')
    two = one if same_order else await create('wechat')
    results = await asyncio.gather(send(client, await plain(one, completed)), send(client, await plain(two, completed)))
    assert sorted(r.status_code for r in results) == ([204, 204] if same_order else [204, 409])
    assert len(await notices()) == 1 and await refunds() == []


@pytest.mark.parametrize('status', ['SUCCESS', 'ABNORMAL', 'CLOSED'])
async def test_partial_notification_is_only_followup_signal(client, refund_case, status):
    buyer, admin, create, completed = refund_case
    number = await create('wechat')
    data = await plain(number, completed, refund_status=status,
                       amount={'total': 19900, 'refund': 100, 'payer_total': 19000, 'payer_refund': 50})
    assert (await send(client, data)).status_code == 204
    detail = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()
    assert detail['refund_notice']['partial'] and detail['refund_notice']['state'] == status
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200 and await refunds() == []


async def test_late_closed_signal_neither_revokes_early_nor_restores_refunded_rights(client, refund_case, monkeypatch):
    buyer, _, create, completed = refund_case
    number = await create('wechat')
    data = await plain(number, completed)
    for n, status in enumerate(['SUCCESS', 'CLOSED', 'ABNORMAL']):
        assert (await send(client, {**data, 'refund_status': status}, identity=f'BEFORE{n}')).status_code == 204
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200
    await provider(monkeypatch, number, completed)
    assert (await query(client, refund_case[1], number)).status_code == 200
    for n, status in enumerate(['CLOSED', 'ABNORMAL', 'SUCCESS']):
        assert (await send(client, {**data, 'refund_status': status}, identity=f'AFTER{n}')).status_code == 204
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 403
    assert len(await refunds()) == 1


@pytest.mark.parametrize('layer,change', [
    ('outer', {'event_type': 'REFUND.PROCESSING'}), ('outer', {'event_type': 'REFUND.CLOSED'}),
    ('outer', {'resource_type': 'plain'}), ('outer', {'id': ''}), ('outer', {'create_time': '2026-09-17T00:00:00'}),
    ('resource', {'original_type': 'transaction'}), ('resource', {'algorithm': 'NONE'}),
    ('resource', {'nonce': None}), ('resource', {'associated_data': []}), ('resource', {'ciphertext': 'broken'}),
    ('key', '1' * 32), ('sig', {'key': _OTHER_KEY}), ('sig', {'timestamp': '1'}), ('sig', {'serial': 'FOREIGN'}),
])
async def test_invalid_signature_envelope_and_aes_are_not_acknowledged(client, refund_case, layer, change):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    response = await send(client, data, **{layer: change})
    assert response.status_code == 400 and response.json()['code'] == 'FAIL'
    assert await notices() == [] and await refunds() == []


@pytest.mark.parametrize('field,value', [('refund', True), ('refund', 0), ('refund', 19901), ('total', '19900'),
                                        ('payer_total', -1), ('payer_refund', 19001), ('payer_refund', True), ('refund', 19900.0)])
async def test_strict_money_types_and_bounds(client, refund_case, field, value):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    data['amount'][field] = value
    assert (await send(client, data)).status_code == 400
    assert await notices() == []


@pytest.mark.parametrize('change,status', [({'mchid': 'FOREIGN'}, 400), ({'out_trade_no': 'UNKNOWN'}, 409),
    ({'transaction_id': 'OTHER'}, 409), ({'refund_id': 'x' * 33}, 400), ({'out_refund_no': '<script>'}, 400),
    ({'success_time': None}, 400), ({'success_time': '2999-01-01T00:00:00Z'}, 400),
    ({'success_time': '2020-01-01T00:00:00Z'}, 409), ({'success_time': '2026-09-17T00:00:00'}, 400),
    ({'amount': {'total': 19800, 'refund': 19800, 'payer_total': 19800, 'payer_refund': 19800}}, 409),
])
async def test_bad_identity_time_and_original_total(client, refund_case, change, status):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed, **change)
    assert (await send(client, data)).status_code == status
    assert await notices() == [] and await refunds() == []


@pytest.mark.parametrize('header', ['Wechatpay-Serial', 'Wechatpay-Signature', 'Wechatpay-Timestamp', 'Wechatpay-Nonce'])
async def test_duplicate_signature_headers_fail_closed(client, refund_case, header):
    _, _, create, completed = refund_case
    raw, headers = packet(await plain(await create('wechat'), completed))
    response = await client.post(URL, content=raw, headers=[*headers.items(), (header, headers[header.lower()])])
    assert response.status_code == 400 and await notices() == []


async def test_raw_tamper_size_compression_and_missing_configuration(client, refund_case, monkeypatch):
    _, _, create, completed = refund_case
    raw, headers = packet(await plain(await create('wechat'), completed))
    assert (await client.post(URL, content=raw + b' ', headers=headers)).status_code == 400
    assert (await client.post(URL, content=b'x' * 65537, headers=headers)).status_code == 413
    assert (await client.post(URL, content=raw, headers={**headers, 'content-encoding': 'gzip'})).status_code == 415
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: replace(CFG, appid=''))
    assert (await client.post(URL, content=raw, headers=headers)).status_code == 503
    assert await notices() == []


async def test_missing_original_receipt_is_not_reconstructed(client, refund_case):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    async with TestSession() as db:
        await db.execute(delete(PaymentReceipt))
        await db.commit()
    assert (await send(client, data)).status_code == 409 and await notices() == []


async def test_wrong_frozen_app_and_manual_source_reject(client, refund_case, monkeypatch):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: replace(CFG, appid='OTHER'))
    assert (await send(client, data)).status_code == 409
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: CFG)
    data = await plain(await create('manual'), completed)
    assert (await send(client, data)).status_code == 409 and await notices() == []


async def test_commit_failure_not_acknowledged_and_retry_is_recoverable(client, refund_case, monkeypatch):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    commit = AsyncSession.commit
    async def fail(db):
        await db.flush()  # Prove even a flushed row rolls back.
        raise OperationalError('synthetic commit failure', None, Exception('synthetic'))
    monkeypatch.setattr(AsyncSession, 'commit', fail)
    assert (await send(client, data)).status_code == 503 and await notices() == []
    monkeypatch.setattr(AsyncSession, 'commit', commit)
    assert (await send(client, data)).status_code == 204 and len(await notices()) == 1


async def test_lost_commit_ack_replay_and_timeout_do_not_lose_signal(client, refund_case, monkeypatch):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    commit = AsyncSession.commit
    async def uncertain(db):
        await commit(db)
        raise OperationalError('synthetic lost ACK', None, Exception('synthetic'))
    monkeypatch.setattr(AsyncSession, 'commit', uncertain)
    assert (await send(client, data)).status_code == 503 and len(await notices()) == 1
    monkeypatch.setattr(AsyncSession, 'commit', commit)
    assert (await send(client, data)).status_code == 204 and len(await notices()) == 1
    async def blocked(db):
        await db.flush()
        await asyncio.sleep(10)
    monkeypatch.setattr(AsyncSession, 'commit', blocked)
    monkeypatch.setattr(refund_notify, 'NOTIFY_BUDGET', .05)
    assert (await send(client, data, identity='TIMEOUT')).status_code == 503
    assert len(await notices()) == 1


async def test_raw_public_key_mode_and_utc_equivalent_replay(client, refund_case, monkeypatch):
    _, _, create, completed = refund_case
    data = await plain(await create('wechat'), completed)
    monkeypatch.setattr(refund_notify, 'pay_config', lambda: replace(CFG, platform_cert=PUBKEY_PEM, platform_key_id='PUB_KEY_ID_TEST'))
    assert (await send(client, data, sig={'serial': 'PUB_KEY_ID_TEST'})).status_code == 204
    data['success_time'] = datetime.fromisoformat(completed).astimezone(timezone(timedelta(hours=8))).isoformat()
    assert (await send(client, data, sig={'serial': 'PUB_KEY_ID_TEST'})).status_code == 204 and len(await notices()) == 1


def test_summary_maximum_and_malformed_display():
    notice = inbox.RefundNotice('a' * 36, '2026-09-18T00:00:00.123456+00:00', 'm' * 32, 'o' * 32, 't' * 32,
                                'r' * 64, '1' * 32, 'SUCCESS', 2147483647, 2147483647, 0, 0, '2026-09-18T00:00:00.123456+00:00')
    evidence = inbox.notice_evidence(notice)
    assert len(evidence) <= 500
    event = PaymentEvent(kind=inbox.NOTICE_KIND, evidence=evidence)
    assert inbox.notice_view(event)['refund_no'] == notice.out_refund_no
    for bad in ['[]', '{}', 'null', 'x' * 501, json.dumps({**json.loads(evidence), 'refund_no': '<script>'})]:
        event.evidence = bad
        assert inbox.notice_view(event) is None


async def test_latest_notice_independent_of_event_pagination_and_malformed_no_fallback(client, refund_case):
    _, admin, create, completed = refund_case
    number = await create('wechat')
    data = await plain(number, completed)
    assert (await send(client, data)).status_code == 204
    assert (await send(client, {**data, 'out_refund_no': 'LATEST'}, identity='NOTICE2')).status_code == 204
    view = (await client.get(f'/shop/admin/orders/{number}/ledger?before=1', headers=admin)).json()
    assert view['events'] == [] and view['refund_notice']['refund_no'] == 'LATEST'
    async with TestSession() as db:
        order_id = await db.scalar(select(Order.id).where(Order.order_no == number))
        db.add(PaymentEvent(order_id=order_id, kind=inbox.NOTICE_KIND, attempt_id='corrupt-summary', evidence='invalid'))
        await db.commit()
    view = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()
    assert view['refund_notice'] is None


@pytest.mark.parametrize('header,value', [('Wechatpay-Signature', 'WECHATPAY/SIGNTEST/'), ('Wechatpay-Nonce', 'n' * 129),
                                         ('Wechatpay-Timestamp', '1' * 13), ('Wechatpay-Serial', 's' * 129)])
async def test_oversized_and_signtest_headers(client, refund_case, header, value):
    _, _, create, completed = refund_case
    raw, headers = packet(await plain(await create('wechat'), completed))
    headers[header.lower()] = value
    assert (await client.post(URL, content=raw, headers=headers)).status_code == 400 and await notices() == []


async def test_slow_body_is_bounded_without_false_ack(client, monkeypatch):
    monkeypatch.setattr(refund_notify, 'NOTIFY_BUDGET', .02)
    async def slow():
        yield b'{'
        await asyncio.sleep(10)
    assert (await client.post(URL, content=slow())).status_code == 503 and await notices() == []
