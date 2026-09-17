"""Signed response/query boundaries. Real synthetic RSA crypto, no live merchant credentials."""
import asyncio
from dataclasses import replace

import httpx
import pytest

from app.wechat_pay import WeChatPayError, native_prepay, query_order
from tests.test_wechat_notify import PUBKEY_PEM, txn
from tests.test_wechat_pay import _AUTH_RE, _KEY, CFG, signed_response, verify


async def query(response, cfg=CFG):
    return await query_order(cfg, out_trade_no='ORDER_1', total=19900,
                             transport=httpx.MockTransport(lambda request: response))


async def test_signed_get_uses_exact_query_and_empty_body():
    def handler(req):
        assert req.method == 'GET' and req.content == b''
        assert str(req.url) == 'https://api.mch.weixin.qq.com/v3/pay/transactions/out-trade-no/ORDER_1?mchid=1900000109'
        assert req.headers['Accept-Encoding'] == 'identity'
        fields = _AUTH_RE.fullmatch(req.headers['Authorization'])
        verify(fields['signature'], 'GET', req.url.raw_path.decode(), fields['timestamp'], fields['nonce_str'], '')
        return signed_response(txn('ORDER_1'))
    result = await query_order(CFG, out_trade_no='ORDER_1', total=19900, transport=httpx.MockTransport(handler))
    assert result.state == 'SUCCESS' and result.transaction_id == 'TX0001' and result.paid_at.tzinfo is not None


@pytest.mark.parametrize('kind', ['unsigned', 'stale', 'serial', 'bad-key', 'tampered', 'probe', 'duplicate', 'encoding', 'oversize'])
@pytest.mark.parametrize('operation', ['query', 'native'])
async def test_response_authentication_is_mandatory(kind, operation):
    body = txn('ORDER_1') if operation == 'query' else {'code_url': 'weixin://SAFE'}
    response = signed_response(body)
    if kind == 'unsigned':
        response.headers.clear()
    elif kind == 'stale':
        response = signed_response(body, timestamp='1000000000')
    elif kind == 'serial':
        response = signed_response(body, serial='UNTRUSTED')
    elif kind == 'bad-key':
        response = signed_response(body, key=_KEY)  # merchant key must not verify platform responses
    elif kind == 'tampered':
        response = httpx.Response(200, content=response.content + b' ', headers=response.headers)
    elif kind == 'probe':
        response.headers['Wechatpay-Signature'] = 'WECHATPAY/SIGNTEST/example'
    elif kind == 'duplicate':
        response.headers = httpx.Headers([*response.headers.multi_items(), ('Wechatpay-Nonce', 'other')])
    elif kind == 'encoding':
        response.headers['Content-Encoding'] = 'gzip'
    else:
        response = signed_response(b' ' * 65537)
    with pytest.raises(WeChatPayError):
        if operation == 'query':
            await query(response)
        else:
            await native_prepay(CFG, out_trade_no='ORDER_1', description='x', total=1,
                                transport=httpx.MockTransport(lambda req: response))


async def test_response_accepts_explicit_raw_public_key_identity():
    cfg = replace(CFG, platform_cert=PUBKEY_PEM, platform_key_id='PUB_KEY_ID_TEST')
    assert (await query(signed_response(txn('ORDER_1'), serial='PUB_KEY_ID_TEST'), cfg)).state == 'SUCCESS'
    with pytest.raises(WeChatPayError):
        await query(signed_response(txn('ORDER_1')), cfg)


@pytest.mark.parametrize('field,value', [('out_trade_no', 'OTHER'), ('appid', 'foreign'), ('mchid', 'foreign'),
    ('trade_type', 'JSAPI'), ('trade_type', None), ('trade_state', 'UNKNOWN'), ('trade_state', []),
    ('transaction_id', None), ('transaction_id', 'bad\n'), ('success_time', None),
    ('success_time', '2026-09-01T00:00:00'), ('success_time', 'invalid'), ('amount', None),
    ('amount', {'total': True, 'currency': 'CNY'}), ('amount', {'total': 19900, 'currency': 'USD'}),
    ('amount', {'total': 19901, 'currency': 'CNY'}), ('amount', []), ('amount', {'total': 19900.0, 'currency': 'CNY'})])
async def test_even_signed_success_requires_complete_matching_contract(field, value):
    data = txn('ORDER_1')
    data[field] = value
    with pytest.raises(WeChatPayError):
        await query(signed_response(data))


@pytest.mark.parametrize('state', ['NOTPAY', 'CLOSED', 'REFUND'])
async def test_non_success_can_omit_optional_fields_but_not_identity(state):
    data = {'out_trade_no': 'ORDER_1', 'appid': CFG.appid, 'mchid': CFG.mchid, 'trade_state': state}
    result = await query(signed_response(data))
    assert result.state == state and result.transaction_id is None
    data['out_trade_no'] = 'OTHER'
    with pytest.raises(WeChatPayError):
        await query(signed_response(data))


@pytest.mark.parametrize('status', [302, 400, 404, 429, 500])
async def test_signed_http_error_is_not_unpaid_proof_and_never_redirects(status):
    response = signed_response({'code': 'ORDER_NOT_EXIST', 'message': 'PRIVATE'}, status)
    response.headers['Location'] = 'https://foreign.invalid/private'
    with pytest.raises(WeChatPayError) as exc:
        await query(response)
    assert str(status) in str(exc.value) and 'PRIVATE' not in str(exc.value)


@pytest.mark.parametrize('number', ['..', '../OTHER', 'x?mchid=OTHER', 'x'*33, '中文', ''])
async def test_query_rejects_path_injection_before_network(number):
    def reject(req):
        pytest.fail('must not contact provider')
    with pytest.raises(WeChatPayError):
        await query_order(CFG, out_trade_no=number, total=1, transport=httpx.MockTransport(reject))


async def test_query_uses_whole_exchange_deadline(monkeypatch):
    monkeypatch.setattr('app.wechat_pay.TIMEOUT', 0.01)
    async def slow(req):
        await asyncio.sleep(1)
        return signed_response(txn('ORDER_1'))
    with pytest.raises(WeChatPayError):
        await query_order(CFG, out_trade_no='ORDER_1', total=19900, transport=httpx.MockTransport(slow))
