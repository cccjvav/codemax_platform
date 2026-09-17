"""微信支付 APIv3：NATIVE 扫码下单（S3-01-1）+ 支付结果回调（S3-01-3）。

下单侧：请求签名、Authorization 头、商户订单号、Native 下单。
回调侧：平台证书验签、`AES-GCM` 报文解密。

**请求**签名串（官方规范，共五行，**每行都以 \\n 结尾**，最后一行也不例外）：

    HTTP请求方法\\n URL\\n 请求时间戳\\n 请求随机串\\n 请求报文主体\\n

**回调**验签串只有三行（同样是每行带 \\n）：

    应答时间戳\\n 应答随机串\\n 应答报文主体\\n

Authorization 头：

    WECHATPAY2-SHA256-RSA2048 mchid="...",nonce_str="...",signature="...",
    timestamp="...",serial_no="..."

三个必须钉死的细节（错了在本地测不出来，只会在真机上验签失败）：

1. **参与签名的 body 必须与实际发出的字节逐字节相同**。所以先把 payload
   `json.dumps` 成字符串、拿这串去签名、再把同一串 `encode` 后发出去；
   中途任何一次重新序列化（键顺序、空格、中文转义）都会让验签失败。
2. **参与签名的 URL 不含域名**，只有路径与查询串。
3. **回调验签要用"原始报文主体"**，不能先反序列化再重新序列化 —— 与第 1 条同理，
   所以路由里用 `request.stream()` 取原文。

沙箱里没有商户号、API 证书与公网回调地址，真实下单与真实回调在此跑不通。
能真正验证的是签名/验签算法（自签密钥 + 自签证书 + 公钥验签）、AES-GCM 解密
（冻结测试向量 + 篡改必须失败）与 HTTP 请求构造（`httpx.MockTransport`），
见 `tests/test_wechat_pay.py` 与 `tests/test_wechat_notify.py`。
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

BASE_URL = "https://api.mch.weixin.qq.com"
NATIVE_PATH = "/v3/pay/transactions/native"  # 参与签名的 URL 就是这个路径
AUTH_TYPE = "WECHATPAY2-SHA256-RSA2048"
TIMEOUT = 10.0


class WeChatPayError(Exception):
    """微信支付返回非 2xx，或报文缺少必需字段。"""


@dataclass(frozen=True)
class PayConfig:
    """下单与回调所需的全部凭据。frozen：支付配置不该在请求处理途中被改。"""

    appid: str
    mchid: str
    serial_no: str
    private_key: str  # apiclient_key.pem 的 PEM 文本
    api_v3_key: str
    notify_url: str
    platform_cert: str = ""  # 微信支付平台证书（或微信支付公钥）的 PEM 文本，回调验签用

    platform_key_id: str = ""  # Explicit ID for raw public-key mode, never merchant serial_no.

    @property
    def configured(self) -> bool:
        """下单所需六项缺一不可。没配齐就直接拒绝下单，不要发出注定失败的请求。"""
        return all((self.appid, self.mchid, self.serial_no, self.private_key, self.api_v3_key, self.notify_url))

    @property
    def notify_ready(self) -> bool:
        """回调绑定商户/app、解密密钥及可信平台证书或显式ID公钥。"""
        return bool(self.appid and self.mchid and self.api_v3_key and self.platform_cert
                    and ("BEGIN CERTIFICATE" in self.platform_cert or self.platform_key_id))


def pay_config() -> PayConfig:
    """每次调用现取配置，方便测试改 settings 后立刻生效。"""
    return PayConfig(
        appid=settings.WX_APPID,
        mchid=settings.WX_MCHID,
        serial_no=settings.WX_SERIAL_NO,
        private_key=settings.WX_PRIVATE_KEY,
        api_v3_key=settings.WX_API_V3_KEY,
        notify_url=settings.WX_NOTIFY_URL,
        platform_cert=settings.WX_PLATFORM_CERT,
        platform_key_id=settings.WX_PLATFORM_KEY_ID,
    )


def new_order_no(now: datetime | None = None) -> str:
    """商户订单号：`CM` + 14 位时间 + 12 位随机十六进制 = 28 字符（列上限 32）。

    随机后缀让同一秒内的并发下单不撞号；`sys_order.order_no` 的 UNIQUE 再兜一层。
    带时间前缀便于人工排查，也满足微信对同一订单号不可重复下单的语义。
    """
    now = now or datetime.now(timezone.utc)  # 只是订单号里的可读前缀，用 UTC 免歧义
    return f"CM{now:%Y%m%d%H%M%S}{secrets.token_hex(6).upper()}"


def canonical_string(method: str, url_path: str, timestamp: str, nonce: str, body: str) -> str:
    """待签名串。注意第五行之后仍有一个换行符。"""
    return f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"


def sign(method: str, url_path: str, body: str, private_key_pem: str, *, timestamp: str, nonce: str) -> str:
    """SHA256withRSA 签名，结果 Base64。"""
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("Merchant signing key must be RSA >= 2048 bits")
    message = canonical_string(method, url_path, timestamp, nonce, body).encode()
    return base64.b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()


def auth_header(
    cfg: PayConfig, method: str, url_path: str, body: str, *, timestamp: str | None = None, nonce: str | None = None
) -> str:
    """生成 Authorization 头的值。timestamp / nonce 可注入，便于测试复算签名。"""
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or secrets.token_hex(16).upper()
    signature = sign(method, url_path, body, cfg.private_key, timestamp=timestamp, nonce=nonce)
    return (
        f'{AUTH_TYPE} mchid="{cfg.mchid}",nonce_str="{nonce}",'
        f'signature="{signature}",timestamp="{timestamp}",serial_no="{cfg.serial_no}"'
    )


async def native_prepay(
    cfg: PayConfig, *, out_trade_no: str, description: str, total: int, transport: httpx.BaseTransport | None = None
) -> str:
    """NATIVE 下单，返回用于生成二维码的 `code_url`。

    `transport` 只为测试注入 `httpx.MockTransport` 而存在，生产留空即可。
    """
    body = json.dumps(
        {
            "appid": cfg.appid,
            "mchid": cfg.mchid,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": cfg.notify_url,
            "amount": {"total": total, "currency": "CNY"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    data = await _request_json(cfg, "POST", NATIVE_PATH, body, transport=transport)
    code_url = data.get("code_url") if isinstance(data, dict) else None
    if not isinstance(code_url, str) or not 1 <= len(code_url) <= 512 or any(ord(ch) < 33 for ch in code_url):
        raise WeChatPayError("微信支付下单响应缺少有效code_url")
    try:
        valid = urlsplit(code_url).scheme == "weixin"
    except ValueError:
        valid = False
    if not valid:
        raise WeChatPayError("微信支付二维码协议无效")
    return code_url


# ---------------------------------------------------------------- 支付结果回调（S3-01-3）


def _public_key(pem: str):
    """平台证书与「微信支付公钥」两种 PEM 都支持：新商户拿到的往往是后者。"""
    if "BEGIN CERTIFICATE" in pem:
        return x509.load_pem_x509_certificate(pem.encode()).public_key()
    return serialization.load_pem_public_key(pem.encode())


# P1-3：回调时间戳的最大允许偏移。微信官方建议 5 分钟。
NOTIFY_MAX_SKEW_SECONDS = 300


def assert_notify_fresh(timestamp: str, *, now: int | None = None) -> None:
    """P1-3：拒绝时间戳过期或超前的回调，不通过抛 WeChatPayError。

    `verify_notify_signature` 把 timestamp 拼进验签串，却**从不比对它和当前时间** ——
    于是攻击者只要抓到一个真实回调（HTTPS 抓包、日志泄漏、或转发链路里任何一环），
    就能**永久重放**：签名一直是合法的。让一个已关闭的订单重新变成已支付、
    或反复触发发货，都不需要伪造任何东西。

    两侧都要挡：
    - **过去**超出窗口 = 重放；
    - **未来**超出窗口 = 伪造，或对端时钟错乱（那它的其它时间字段也不可信）。

    刻意放在**路由层**而不是 `verify_notify_signature` 里：后者是纯签名验证，
    现有 5 处单测都用固定时间戳复算签名，把时间策略塞进去会让「验签对不对」和
    「时间新不新」两件事纠缠在一起，也没法各自单测。
    """
    ref = int(time.time()) if now is None else now
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        # 头是可以随便伪造的，不能假设它格式正确（空串 / 带小数 / 带空格都要挡）
        raise WeChatPayError("Wechatpay-Timestamp 不是有界合法整数")
    skew = int(timestamp) - ref
    if abs(skew) > NOTIFY_MAX_SKEW_SECONDS:
        raise WeChatPayError(
            f"回调时间戳偏离当前时间 {skew} 秒，超出 ±{NOTIFY_MAX_SKEW_SECONDS} 秒窗口"
            "（可能是重放攻击）"
        )


def verify_notify_signature(
    platform_cert_pem: str, *, timestamp: str, nonce: str, body: str, signature: str
) -> None:
    """用平台证书公钥验回调签名，不通过抛 WeChatPayError。

    验签串只有三行：`应答时间戳\\n 应答随机串\\n 应答报文主体\\n`。
    `body` 必须是**原始报文主体**（路由里用 `request.stream()` 取原文），
    反序列化后再重新序列化会让验签必然失败。
    """
    message = f"{timestamp}\n{nonce}\n{body}\n".encode()
    try:
        _public_key(platform_cert_pem).verify(
            base64.b64decode(signature, validate=True), message, padding.PKCS1v15(), hashes.SHA256()
        )
    except (InvalidSignature, InvalidTag, ValueError, TypeError) as e:
        raise WeChatPayError(f"回调验签不通过：{e or '签名值不是合法 Base64'}") from e


def decrypt_resource(api_v3_key: str, *, ciphertext: str, nonce: str, associated_data: str = "") -> dict:
    """解密回调 `resource`，返回业务 JSON（S3-01-3-2）。

    微信的 `ciphertext` 是 `Base64(密文 || 16 字节 GCM tag)`，正好是 `cryptography`
    的 AESGCM 期望的格式，不需要自己切 tag。密钥 / nonce / 附加数据都按 UTF-8 字节用。
    """
    try:
        raw = base64.b64decode(ciphertext, validate=True)
        plaintext = AESGCM(api_v3_key.encode()).decrypt(nonce.encode(), raw, associated_data.encode())
    except (InvalidTag, ValueError, TypeError) as e:  # Base64 非法 / 密钥长度不对 / tag 校验失败
        raise WeChatPayError(f"回调报文解密失败：{e}") from e
    try:
        return json.loads(plaintext)
    except (ValueError, UnicodeError, RecursionError):
        raise WeChatPayError("回调报文不是合法 UTF-8 JSON") from None


def assert_notify_identity(cfg: PayConfig, serial: str, *, now: datetime | None = None) -> None:
    """Bind the header to a locally trusted RSA key; certificate mode also enforces validity.

    This does not fetch keys from a callback URL or validate a public CA chain: the
    operator must provision the real WeChat key/certificate through trusted channels.
    """
    if not serial or len(serial) > 128 or not serial.isascii():
        raise WeChatPayError("平台证书/公钥标识无效")
    try:
        key = _public_key(cfg.platform_cert)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError('RSA key required')
        if 'BEGIN CERTIFICATE' in cfg.platform_cert:
            cert = x509.load_pem_x509_certificate(cfg.platform_cert.encode())
            moment = now or datetime.now(timezone.utc)
            valid = (serial.upper() == format(cert.serial_number, 'X')
                     and cert.not_valid_before_utc <= moment <= cert.not_valid_after_utc)
        else:
            valid = bool(cfg.platform_key_id) and serial == cfg.platform_key_id
    except (ValueError, TypeError):
        raise WeChatPayError("平台验签凭据配置无效") from None
    if not valid:
        raise WeChatPayError("平台证书/公钥标识不匹配或证书不在有效期")


def assert_notify_configuration(cfg: PayConfig) -> None:
    """Before charging, require locally usable callback credentials, not merely a private signing key."""
    if not cfg.notify_ready or len(cfg.api_v3_key.encode()) != 32:
        raise WeChatPayError("回调配置不完整或APIv3密钥不是32字节")
    try:
        serial = (format(x509.load_pem_x509_certificate(cfg.platform_cert.encode()).serial_number, 'X')
                  if 'BEGIN CERTIFICATE' in cfg.platform_cert else cfg.platform_key_id)
    except (ValueError, TypeError):
        raise WeChatPayError("平台回调证书配置无效") from None
    assert_notify_identity(cfg, serial)


async def _request_json(cfg: PayConfig, method: str, path: str, body: str = "", *, transport=None) -> dict:
    """Bounded APIv3 exchange: exact request bytes, no redirects/compression, authenticated response.

    Every response, including HTTP errors, must pass platform identity/time/signature checks.
    An unverified error is not proof that no payment exists. Never expose provider raw bodies.
    """
    assert_notify_configuration(cfg)
    try:
        authorization = auth_header(cfg, method, path, body)
    except (ValueError, TypeError):
        raise WeChatPayError("微信支付签名配置无效") from None
    headers = {"Authorization": authorization, "Content-Type": "application/json",
               "Accept": "application/json", "Accept-Encoding": "identity"}
    response_body = bytearray()
    try:
        async with (
            asyncio.timeout(TIMEOUT * 2),
            httpx.AsyncClient(base_url=BASE_URL, transport=transport, timeout=TIMEOUT, follow_redirects=False) as client,
            client.stream(method, path, content=body.encode('utf-8'), headers=headers) as response,
        ):
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise WeChatPayError('微信支付应答编码不支持')
            async for chunk in response.aiter_bytes():
                if len(response_body) + len(chunk) > 65536:
                    raise WeChatPayError('微信支付应答过大')
                response_body.extend(chunk)
            assert_response_signature(cfg, response.headers, bytes(response_body), response.status_code)
            if response.status_code != 200:
                raise WeChatPayError(f'微信支付请求失败：HTTP {response.status_code}；不得推断未付款')
    except (httpx.HTTPError, TimeoutError, UnicodeError):
        raise WeChatPayError('微信支付网络请求失败；结果未知，请核查原订单') from None
    try:
        data = json.loads(response_body)
    except (ValueError, UnicodeError, RecursionError):
        raise WeChatPayError('微信支付应答不是有效JSON') from None
    if not isinstance(data, dict):
        raise WeChatPayError('微信支付应答必须为对象')
    return data


def assert_response_signature(cfg: PayConfig, headers: httpx.Headers, raw: bytes, status: int) -> None:
    """Verify raw UTF-8 response with exactly one bounded value for each authentication header."""
    try:
        values = {}
        for name, limit in [('Wechatpay-Serial', 128), ('Wechatpay-Timestamp', 12),
                            ('Wechatpay-Nonce', 128), ('Wechatpay-Signature', 1024)]:
            candidates = headers.get_list(name)
            if len(candidates) != 1 or not 1 <= len(candidates[0]) <= limit:
                raise WeChatPayError('Missing, duplicate or oversized signature header')
            values[name] = candidates[0]
        assert_notify_fresh(values['Wechatpay-Timestamp'])
        assert_notify_identity(cfg, values['Wechatpay-Serial'])
        verify_notify_signature(cfg.platform_cert, timestamp=values['Wechatpay-Timestamp'],
                                nonce=values['Wechatpay-Nonce'], body=raw.decode('utf-8'),
                                signature=values['Wechatpay-Signature'])
    except (WeChatPayError, UnicodeError):
        raise WeChatPayError(f'微信支付应答验签失败（HTTP {status}）；结果不可信') from None


@dataclass(frozen=True)
class QueryResult:
    state: str
    transaction_id: str | None = None
    paid_at: datetime | None = None


async def query_order(cfg: PayConfig, *, out_trade_no: str, total: int, transport=None) -> QueryResult:
    """Read one Native order at the fixed merchant API; bind even unpaid observations to its identity.

    Only SUCCESS can grant a receipt. REFUND is an observation requiring a separate refund
    workflow; NOTPAY/CLOSED never withdraw an existing paid entitlement.
    """
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', out_trade_no):
        raise WeChatPayError('查单的商户订单号格式无效')
    path = '/v3/pay/transactions/out-trade-no/' + quote(out_trade_no, safe='')
    path += '?' + urlencode({'mchid': cfg.mchid})
    data = await _request_json(cfg, 'GET', path, transport=transport)
    if (data.get('out_trade_no') != out_trade_no or data.get('appid') != cfg.appid
            or data.get('mchid') != cfg.mchid):
        raise WeChatPayError('查单应答与目标订单/商户/应用不一致')
    state = data.get('trade_state')
    if state not in ('SUCCESS', 'NOTPAY', 'CLOSED', 'REFUND'):
        raise WeChatPayError('此Native查单状态尚不支持自动处理')
    if data.get('trade_type') not in (None, 'NATIVE'):
        raise WeChatPayError('查单交易类型不一致')
    amount = data.get('amount')
    # Unpaid query fields may be omitted by the provider; when present they must match.
    if amount is not None and (not isinstance(amount, dict)
            or type(amount.get('total')) is not int or amount['total'] != total or amount.get('currency') != 'CNY'):
        raise WeChatPayError('查单金额或币种不一致')
    if state != 'SUCCESS':
        return QueryResult(state)
    transaction_id, success_time = data.get('transaction_id'), data.get('success_time')
    if (data.get('trade_type') != 'NATIVE' or amount is None
            or not isinstance(transaction_id, str) or not 1 <= len(transaction_id) <= 64
            or any(ord(c) < 33 for c in transaction_id)):
        raise WeChatPayError('成功查单缺少完整交易凭证')
    try:
        if not isinstance(success_time, str) or len(success_time) > 40:
            raise ValueError('time')
        paid_at = datetime.fromisoformat(success_time)
        if paid_at.tzinfo is None:
            raise ValueError('timezone')
    except ValueError:
        raise WeChatPayError('成功查单时间无效') from None
    return QueryResult(state, transaction_id, paid_at)
