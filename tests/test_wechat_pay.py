"""S3-01-1 测试：微信支付 NATIVE 下单 + 下单接口。

沙箱里没有商户号与证书，所以真实下单不可能跑通。这里能真正验证的是：
- **签名算法本身**：用自签 RSA 密钥签名，再用公钥按官方签名串格式验签；
- **发出的字节就是签名的字节**：用 `httpx.MockTransport` 截下真实请求，
  拿请求体原文复算验签 —— 这条一旦红，线上必然验签失败；
- **下单接口的业务规则**：未配置 503、订单落库、未支付单复用、失败不留单。
"""
import base64
import json
import re
from datetime import datetime

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select, update

import app.routers.shop as shop
from app.config import settings
from app.models import Order
from app.wechat_pay import (
    NATIVE_PATH,
    PayConfig,
    WeChatPayError,
    auth_header,
    canonical_string,
    native_prepay,
    new_order_no,
    sign,
)
from tests.conftest import TestSession

# 自签密钥：只为验证签名算法，与微信无关
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PUB = _KEY.public_key()

CFG = PayConfig(
    appid="wxAPPID",
    mchid="1900000109",
    serial_no="SERIAL123",
    private_key=PRIVATE_PEM,
    api_v3_key="0" * 32,
    notify_url="https://codemax.top/shop/pay/notify",
)

_ENV = {
    "WX_APPID": "wxAPPID",
    "WX_MCHID": "1900000109",
    "WX_SERIAL_NO": "SERIAL123",
    "WX_PRIVATE_KEY": PRIVATE_PEM,
    "WX_API_V3_KEY": "0" * 32,
    "WX_NOTIFY_URL": "https://codemax.top/shop/pay/notify",
}

_AUTH_RE = re.compile(
    r'WECHATPAY2-SHA256-RSA2048 mchid="(?P<mchid>[^"]+)",nonce_str="(?P<nonce_str>[^"]+)",'
    r'signature="(?P<signature>[^"]+)",timestamp="(?P<timestamp>[^"]+)",serial_no="(?P<serial_no>[^"]+)"'
)


def verify(signature_b64: str, method: str, url_path: str, timestamp: str, nonce: str, body: str) -> None:
    """用公钥按官方签名串验签，不抛异常即通过。"""
    PUB.verify(
        base64.b64decode(signature_b64),
        canonical_string(method, url_path, timestamp, nonce, body).encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


@pytest.fixture
def pay_configured(monkeypatch):
    """配齐六项，并把 native_prepay 换成可控替身；返回调用记录。"""
    for k, v in _ENV.items():
        monkeypatch.setattr(settings, k, v)
    calls: list[dict] = []

    async def fake(cfg: PayConfig, **kw):
        calls.append({"cfg": cfg, **kw})
        return f"weixin://wxpay/bizpayurl?pr={kw['out_trade_no']}"

    monkeypatch.setattr(shop, "native_prepay", fake)
    return calls


async def auth_headers(client, username="buyer", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def orders_in_db() -> list[Order]:
    async with TestSession() as s:
        return list((await s.execute(select(Order))).scalars().all())


# ---------------------------------------------------------------- 签名与下单请求


def test_canonical_string_is_five_newline_terminated_lines():
    s = canonical_string("POST", NATIVE_PATH, "1611", "NONCE", '{"a":1}')
    assert s == f'POST\n{NATIVE_PATH}\n1611\nNONCE\n{{"a":1}}\n'
    assert s.count("\n") == 5, "第五行之后仍要有换行符"


def test_signature_verifies_with_public_key():
    body = '{"appid":"wxAPPID"}'
    sig = sign("POST", NATIVE_PATH, body, PRIVATE_PEM, timestamp="1611", nonce="NONCE")
    verify(sig, "POST", NATIVE_PATH, "1611", "NONCE", body)


def test_signature_is_over_exact_body_bytes():
    """签名串里 body 差一个空格就必须验签失败 —— 这正是线上验签失败的头号原因。"""
    body = '{"appid":"wxAPPID"}'
    sig = sign("POST", NATIVE_PATH, body, PRIVATE_PEM, timestamp="1611", nonce="NONCE")
    with pytest.raises(InvalidSignature):
        verify(sig, "POST", NATIVE_PATH, "1611", "NONCE", body + " ")
    with pytest.raises(InvalidSignature):
        verify(sig, "POST", NATIVE_PATH, "1612", "NONCE", body)


def test_auth_header_format_matches_spec():
    h = auth_header(CFG, "POST", NATIVE_PATH, "{}", timestamp="1611", nonce="NONCE")
    m = _AUTH_RE.fullmatch(h)
    assert m, f"Authorization 头格式不符规范：{h}"
    assert m["mchid"] == "1900000109"
    assert m["serial_no"] == "SERIAL123"
    assert m["timestamp"] == "1611"
    assert m["nonce_str"] == "NONCE"
    verify(m["signature"], "POST", NATIVE_PATH, m["timestamp"], m["nonce_str"], "{}")


def test_pay_config_requires_all_six_fields():
    assert CFG.configured is True
    for field in ("appid", "mchid", "serial_no", "private_key", "api_v3_key", "notify_url"):
        broken = PayConfig(**{**CFG.__dict__, field: ""})
        assert broken.configured is False, f"{field} 为空时不应算配置齐全"


def test_new_order_no_shape():
    no = new_order_no(datetime(2026, 9, 1, 12, 30, 5))
    assert no.startswith("CM20260901123005")
    assert len(no) == 28, "order_no 列上限 32，留有余量"
    assert no[16:].isalnum()


def test_new_order_no_is_unique():
    assert len({new_order_no() for _ in range(500)}) == 500


async def test_native_prepay_sends_request_whose_bytes_match_the_signature():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json={"code_url": "weixin://wxpay/bizpayurl?pr=ABC"})

    code_url = await native_prepay(
        CFG,
        out_trade_no="CM20260901123005ABCDEF01",
        description="毕设服务",
        total=19900,
        transport=httpx.MockTransport(handler),
    )
    assert code_url == "weixin://wxpay/bizpayurl?pr=ABC"

    req = captured["req"]
    assert req.method == "POST"
    assert str(req.url) == f"https://api.mch.weixin.qq.com{NATIVE_PATH}"
    assert req.headers["Content-Type"] == "application/json"

    sent = req.content.decode("utf-8")
    assert json.loads(sent) == {
        "appid": "wxAPPID",
        "mchid": "1900000109",
        "description": "毕设服务",
        "out_trade_no": "CM20260901123005ABCDEF01",
        "notify_url": "https://codemax.top/shop/pay/notify",
        "amount": {"total": 19900, "currency": "CNY"},
    }
    # 关键：拿实际发出的字节复算验签
    fields = _AUTH_RE.fullmatch(req.headers["Authorization"])
    verify(fields["signature"], "POST", NATIVE_PATH, fields["timestamp"], fields["nonce_str"], sent)


async def test_native_prepay_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "PARAM_ERROR", "message": "参数错误"})

    with pytest.raises(WeChatPayError) as e:
        await native_prepay(CFG, out_trade_no="X", description="d", total=1, transport=httpx.MockTransport(handler))
    assert "400" in str(e.value)


async def test_native_prepay_raises_when_code_url_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(WeChatPayError):
        await native_prepay(CFG, out_trade_no="X", description="d", total=1, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- 下单接口


async def test_create_order_requires_login(client):
    assert (await client.post("/shop/orders")).status_code == 401


async def test_create_order_503_when_unconfigured(client, monkeypatch):
    for k in _ENV:
        monkeypatch.setattr(settings, k, "")
    h = await auth_headers(client)
    r = await client.post("/shop/orders", headers=h)
    assert r.status_code == 503
    assert "WX_" in r.json()["detail"]
    assert await orders_in_db() == [], "没配置就不该留下待支付订单"


async def test_create_order_persists_and_returns_code_url(client, pay_configured):
    h = await auth_headers(client)
    r = await client.post("/shop/orders", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["code_url"] == f"weixin://wxpay/bizpayurl?pr={body['order_no']}"
    assert body["amount"] == 19900
    assert body["product_name"] == "毕设服务"
    assert body["status"] == "pending"
    assert body["reused"] is False
    assert len(pay_configured) == 1
    assert pay_configured[0]["total"] == 19900
    assert pay_configured[0]["cfg"].mchid == "1900000109"

    rows = await orders_in_db()
    assert len(rows) == 1
    assert rows[0].order_no == body["order_no"]
    assert rows[0].code_url == body["code_url"]


async def test_repeat_call_reuses_pending_order(client, pay_configured):
    """连点两下不该刷出两张待支付单（S3-01-1-4）。"""
    h = await auth_headers(client)
    first = (await client.post("/shop/orders", headers=h)).json()
    second = (await client.post("/shop/orders", headers=h)).json()
    assert second["order_no"] == first["order_no"]
    assert second["reused"] is True
    assert len(pay_configured) == 1, "复用未支付订单时不该再调一次微信"
    assert len(await orders_in_db()) == 1


async def test_paid_order_does_not_block_next_purchase(client, pay_configured):
    """只拦"未完成"订单：已支付的可以接着买。"""
    h = await auth_headers(client)
    first = (await client.post("/shop/orders", headers=h)).json()
    async with TestSession() as s:
        await s.execute(update(Order).where(Order.order_no == first["order_no"]).values(status="paid"))
        await s.commit()
    second = (await client.post("/shop/orders", headers=h)).json()
    assert second["order_no"] != first["order_no"]
    assert second["reused"] is False
    assert len(pay_configured) == 2


async def test_wechat_failure_returns_502_and_leaves_no_order(client, pay_configured, monkeypatch):
    async def boom(cfg, **kw):
        raise WeChatPayError("微信支付下单失败：HTTP 400 参数错误")

    monkeypatch.setattr(shop, "native_prepay", boom)
    h = await auth_headers(client)
    r = await client.post("/shop/orders", headers=h)
    assert r.status_code == 502
    assert "微信支付下单失败" in r.json()["detail"]
    assert await orders_in_db() == [], "微信下单失败不该留下待支付订单"
