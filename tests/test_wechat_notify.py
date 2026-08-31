"""S3-01-3 测试：微信支付回调 —— 平台证书验签、AES-GCM 解密、幂等。

沙箱收不到微信的真实回调，所以这里分两层验证：
- **算法层**：解密用**冻结测试向量**（密文写死在文件里，生成方式见下方注释），
  并断言篡改密文 / 错密钥 / 错附加数据 / 非 Base64 一律失败；验签用自签证书 + 公钥。
- **接口层**：自己用同一套算法造出「微信会发来的请求」（签名 + 密文都是真的），
  打进 `/shop/pay/notify`，验证应答格式与幂等。

冻结向量的生成方式（用官方推荐的 AESGCM 加密，密文含 16 字节 GCM tag）：
    AESGCM(VEC_KEY.encode()).encrypt(VEC_NONCE.encode(), PLAINTEXT.encode(), VEC_AAD.encode())
"""
import base64
import json
from datetime import datetime, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from app.config import settings
from app.models import Order, User
from app.wechat_pay import WeChatPayError, decrypt_resource, verify_notify_signature
from tests.conftest import TestSession

# ---- 冻结测试向量：解密必须解出这些值 ----
VEC_KEY = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"  # 32 字节
VEC_NONCE = "0123456789ab"  # 12 字节
VEC_AAD = "transaction"
VEC_CIPHERTEXT = (
    "FXMu148klsofseihjVYX1EOx3STEEvYi9SqQ9iYH4IesdTCvO1ooCi8mWe5tNW49TAoOXd1+0qFF"
    "JZtcZPstvor6x7YV/nUXPILx7Vi87vWWixkzNZr+MA3q3fuBSkJsmV7n2jncm7LjkGVG3TA6jbu"
    "YOdIEnb0b8SzeTHY8IIwN06XvFXQYBmFTU2AelteWOuSpp54rAZwYnZSAuxMCjN+JDX+FWlXNwD"
    "W4R4o61X/466rdeyd5kVV2pwwzI7VYOpT5CtULvl/i9t1XCOjuDoVjrTmcyPNYMFgBXCypSncqo"
    "vNpCd8dYBq9yNyjDBeuH9PnEjgRxJMSNi2clKxnFwO6TNByzZX1fg4UoUFPQZBjPtHQuobJ"
)

# ---- 自签平台证书：只为验证「用平台证书公钥验签」这条链路 ----
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WeChatPay Test")])
CERT_PEM = (
    x509.CertificateBuilder()
    .subject_name(_NAME)
    .issuer_name(_NAME)
    .public_key(_KEY.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
    .not_valid_after(datetime(2040, 1, 1, tzinfo=timezone.utc))
    .sign(_KEY, hashes.SHA256())
    .public_bytes(serialization.Encoding.PEM)
    .decode()
)
PUBKEY_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()

API_V3_KEY = "0123456789abcdef0123456789abcdef"  # 32 字节
AES_NONCE = "abcdefghijkl"  # 12 字节
SIG_NONCE = "NONCE593BEC0C930B"  # 与 AES nonce 无关，微信明确说两者不同
TIMESTAMP = "1756700000"

_seq = 0


def txn(out_trade_no: str, total: int = 19900, trade_state: str = "SUCCESS", txid: str = "TX0001") -> dict:
    """回调解密后的业务报文（字段照官方文档）。"""
    return {
        "mchid": "1900000109",
        "appid": "wxAPPID",
        "out_trade_no": out_trade_no,
        "transaction_id": txid,
        "trade_type": "NATIVE",
        "trade_state": trade_state,
        "trade_state_desc": "支付成功",
        "success_time": "2026-09-01T12:31:00+08:00",
        "amount": {"total": total, "payer_total": total, "currency": "CNY"},
        "payer": {"openid": "oUpF8uMuAJO_M2pxb1Q9zNjWeS6o"},
    }


def build_notify(
    plain: dict, *, event_type: str = "TRANSACTION.SUCCESS", key: str = API_V3_KEY, sign_key=_KEY
) -> tuple[bytes, dict]:
    """造一个「微信支付会发来的」回调：密文与签名都是真的。"""
    ct = AESGCM(key.encode()).encrypt(
        AES_NONCE.encode(), json.dumps(plain, ensure_ascii=False).encode(), VEC_AAD.encode()
    )
    body = json.dumps(
        {
            "id": "EV-2026090112310000001",
            "create_time": "2026-09-01T12:31:00+08:00",
            "resource_type": "encrypt-resource",
            "event_type": event_type,
            "summary": "支付成功",
            "resource": {
                "original_type": "transaction",
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": base64.b64encode(ct).decode(),
                "associated_data": VEC_AAD,
                "nonce": AES_NONCE,
            },
        },
        ensure_ascii=False,
    )
    message = f"{TIMESTAMP}\n{SIG_NONCE}\n{body}\n".encode()
    sig = base64.b64encode(sign_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()
    headers = {
        "Wechatpay-Serial": "PLATFORMSERIAL0001",
        "Wechatpay-Signature": sig,
        "Wechatpay-Timestamp": TIMESTAMP,
        "Wechatpay-Nonce": SIG_NONCE,
    }
    return body.encode("utf-8"), headers


@pytest.fixture
def notify_ready(monkeypatch):
    monkeypatch.setattr(settings, "WX_API_V3_KEY", API_V3_KEY)
    monkeypatch.setattr(settings, "WX_PLATFORM_CERT", CERT_PEM)


async def make_order(amount: int = 19900, status: str = "pending") -> str:
    global _seq
    _seq += 1
    async with TestSession() as s:
        user = User(username=f"payer{_seq}", password="x")
        s.add(user)
        await s.flush()
        order = Order(
            order_no=f"CM2026090112300{_seq}TEST",
            user_id=user.id,
            product_name="毕设服务",
            amount=amount,
            status=status,
        )
        s.add(order)
        await s.commit()
        return order.order_no


async def fetch(order_no: str) -> Order:
    async with TestSession() as s:
        return (await s.execute(select(Order).where(Order.order_no == order_no))).scalar_one()


async def post_notify(client, raw: bytes, headers: dict):
    return await client.post(
        "/shop/pay/notify", content=raw, headers={**headers, "Content-Type": "application/json"}
    )


# ---------------------------------------------------------------- 解密（S3-01-3-2）


def test_decrypt_resource_known_vector():
    data = decrypt_resource(VEC_KEY, ciphertext=VEC_CIPHERTEXT, nonce=VEC_NONCE, associated_data=VEC_AAD)
    assert data["out_trade_no"] == "CM20260901123005ABCDEF01"
    assert data["transaction_id"] == "4200001234202609011234567890"
    assert data["trade_state"] == "SUCCESS"
    assert data["trade_type"] == "NATIVE"
    assert data["amount"]["total"] == 19900


def test_decrypt_resource_rejects_tampered_ciphertext():
    """GCM 的认证标签必须生效：改一个字符就要失败，不能解出错数据。"""
    tampered = VEC_CIPHERTEXT[:120] + ("A" if VEC_CIPHERTEXT[120] != "A" else "B") + VEC_CIPHERTEXT[121:]
    assert tampered != VEC_CIPHERTEXT
    with pytest.raises(WeChatPayError):
        decrypt_resource(VEC_KEY, ciphertext=tampered, nonce=VEC_NONCE, associated_data=VEC_AAD)


def test_decrypt_resource_rejects_wrong_key_or_aad():
    with pytest.raises(WeChatPayError):
        decrypt_resource("X" * 32, ciphertext=VEC_CIPHERTEXT, nonce=VEC_NONCE, associated_data=VEC_AAD)
    with pytest.raises(WeChatPayError):
        decrypt_resource(VEC_KEY, ciphertext=VEC_CIPHERTEXT, nonce=VEC_NONCE, associated_data="wrong")


def test_decrypt_resource_rejects_garbage_without_stack_trace():
    """非法 Base64 / 密钥长度不对都要变成 WeChatPayError，而不是 500 栈。"""
    for ciphertext, key in [(VEC_CIPHERTEXT, "short-key"), ("!!!not base64!!!", VEC_KEY), ("", VEC_KEY)]:
        with pytest.raises(WeChatPayError):
            decrypt_resource(key, ciphertext=ciphertext, nonce=VEC_NONCE, associated_data=VEC_AAD)


def test_decrypt_resource_rejects_non_json_plaintext():
    ct = AESGCM(VEC_KEY.encode()).encrypt(VEC_NONCE.encode(), b"not json", VEC_AAD.encode())
    with pytest.raises(WeChatPayError):
        decrypt_resource(
            VEC_KEY, ciphertext=base64.b64encode(ct).decode(), nonce=VEC_NONCE, associated_data=VEC_AAD
        )


# ---------------------------------------------------------------- 验签（S3-01-3-1）


def test_verify_notify_signature_accepts_valid_and_rejects_tampered():
    body = '{"event_type":"TRANSACTION.SUCCESS"}'
    message = f"{TIMESTAMP}\n{SIG_NONCE}\n{body}\n".encode()
    sig = base64.b64encode(_KEY.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()

    verify_notify_signature(CERT_PEM, timestamp=TIMESTAMP, nonce=SIG_NONCE, body=body, signature=sig)

    with pytest.raises(WeChatPayError):
        verify_notify_signature(CERT_PEM, timestamp=TIMESTAMP, nonce=SIG_NONCE, body=body + " ", signature=sig)
    with pytest.raises(WeChatPayError):
        verify_notify_signature(CERT_PEM, timestamp=TIMESTAMP, nonce="OTHER", body=body, signature=sig)
    with pytest.raises(WeChatPayError):
        verify_notify_signature(CERT_PEM, timestamp=TIMESTAMP, nonce=SIG_NONCE, body=body, signature="!!!")


def test_verify_accepts_bare_public_key_pem():
    """新商户拿到的是「微信支付公钥」而非平台证书，两种 PEM 都得支持。"""
    body = "{}"
    message = f"{TIMESTAMP}\n{SIG_NONCE}\n{body}\n".encode()
    sig = base64.b64encode(_KEY.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()
    verify_notify_signature(PUBKEY_PEM, timestamp=TIMESTAMP, nonce=SIG_NONCE, body=body, signature=sig)


# ---------------------------------------------------------------- 回调接口


async def test_notify_503_when_platform_cert_missing(client, monkeypatch):
    monkeypatch.setattr(settings, "WX_API_V3_KEY", "")
    monkeypatch.setattr(settings, "WX_PLATFORM_CERT", "")
    raw, headers = build_notify(txn("CMX"))
    r = await post_notify(client, raw, headers)
    assert r.status_code == 503
    assert r.json()["code"] == "FAIL"


async def test_notify_rejects_bad_signature(client, notify_ready):
    """用别的私钥签的报文必须被拒 —— 这是伪造支付成功的唯一防线。"""
    order_no = await make_order()
    raw, headers = build_notify(txn(order_no), sign_key=_OTHER_KEY)
    r = await post_notify(client, raw, headers)
    assert r.status_code == 401
    assert r.json()["code"] == "FAIL"
    assert (await fetch(order_no)).status == "pending", "验签失败绝不能改订单状态"


async def test_notify_rejects_tampered_body(client, notify_ready):
    order_no = await make_order()
    raw, headers = build_notify(txn(order_no))
    tampered = raw.replace(b'"summary"', b'"summarY"')  # 只动一个字符，签名立刻对不上
    r = await post_notify(client, tampered, headers)
    assert r.status_code == 401
    assert (await fetch(order_no)).status == "pending"


async def test_notify_marks_order_paid(client, notify_ready):
    order_no = await make_order()
    raw, headers = build_notify(txn(order_no, txid="TX-REAL-001"))
    r = await post_notify(client, raw, headers)
    assert r.status_code == 200
    assert r.json()["code"] == "SUCCESS"

    order = await fetch(order_no)
    assert order.status == "paid"
    assert order.transaction_id == "TX-REAL-001"
    assert order.paid_at is not None


async def test_notify_is_idempotent(client, notify_ready):
    """重复通知（S3-01-3-3）：第二次仍答成功，但不二次迁移、不覆盖首次支付信息。

    变异验证记录（诚实起见）：把端点里 `if order.status == PENDING` 的判断去掉后
    **本测试仍然是绿的** —— 因为 `mark_paid` 对已支付订单直接返回 `False`、根本不提交，
    那句多余的赋值会随 session 关闭被丢弃，属于等价变异，测不出来。
    真正被变异测试抓住的是：跳过验签（3 条红）、去掉金额校验（1 条红）。
    """
    order_no = await make_order()
    first, h1 = build_notify(txn(order_no, txid="TX-FIRST"))
    assert (await post_notify(client, first, h1)).status_code == 200

    again, h2 = build_notify(txn(order_no, txid="TX-SECOND"))
    r = await post_notify(client, again, h2)
    assert r.status_code == 200
    assert r.json()["code"] == "SUCCESS"

    order = await fetch(order_no)
    assert order.status == "paid"
    assert order.transaction_id == "TX-FIRST", "重复通知不该覆盖首次的微信支付订单号"


async def test_notify_unknown_order_returns_404(client, notify_ready):
    raw, headers = build_notify(txn("CM-NOT-EXIST"))
    r = await post_notify(client, raw, headers)
    assert r.status_code == 404
    assert r.json()["code"] == "FAIL"


async def test_notify_amount_mismatch_is_rejected(client, notify_ready):
    """回调金额与订单金额不一致：不能发货，且要让微信重推以便暴露问题。"""
    order_no = await make_order(amount=19900)
    raw, headers = build_notify(txn(order_no, total=1))
    r = await post_notify(client, raw, headers)
    assert r.status_code == 400
    assert "金额不符" in r.json()["message"]
    assert (await fetch(order_no)).status == "pending"


async def test_notify_ignores_non_success_events(client, notify_ready):
    """未支付 / 退款类通知：回 200 确认收到（否则会被无限重推），但不改状态。"""
    order_no = await make_order()
    for raw, headers in (
        build_notify(txn(order_no, trade_state="NOTPAY")),
        build_notify(txn(order_no), event_type="REFUND.SUCCESS"),
    ):
        r = await post_notify(client, raw, headers)
        assert r.status_code == 200
        assert r.json()["code"] == "SUCCESS"
    assert (await fetch(order_no)).status == "pending"


async def test_late_notify_does_not_revert_downloaded_order(client, notify_ready):
    order_no = await make_order(status="downloaded")
    raw, headers = build_notify(txn(order_no))
    r = await post_notify(client, raw, headers)
    assert r.status_code == 200
    assert (await fetch(order_no)).status == "downloaded"


async def test_notify_needs_no_login_token(client, notify_ready):
    """回调由微信发起，不带登录 token；身份靠验签，不是靠 JWT。"""
    order_no = await make_order()
    raw, headers = build_notify(txn(order_no))
    assert "Authorization" not in headers
    assert (await post_notify(client, raw, headers)).status_code == 200
    assert (await fetch(order_no)).status == "paid"


async def test_notify_decrypt_failure_returns_400(client, notify_ready, monkeypatch):
    """报文是合法签名但用别的 APIv3 密钥加密 —— 解密必须失败且不碰订单。"""
    order_no = await make_order()
    raw, headers = build_notify(txn(order_no), key="X" * 32)
    r = await post_notify(client, raw, headers)
    assert r.status_code == 400
    assert "解密失败" in r.json()["message"]
    assert (await fetch(order_no)).status == "pending"
