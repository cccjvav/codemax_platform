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
   所以路由里用 `await request.body()` 取原文。

沙箱里没有商户号、API 证书与公网回调地址，真实下单与真实回调在此跑不通。
能真正验证的是签名/验签算法（自签密钥 + 自签证书 + 公钥验签）、AES-GCM 解密
（冻结测试向量 + 篡改必须失败）与 HTTP 请求构造（`httpx.MockTransport`），
见 `tests/test_wechat_pay.py` 与 `tests/test_wechat_notify.py`。
"""
from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
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

    @property
    def configured(self) -> bool:
        """下单所需六项缺一不可。没配齐就直接拒绝下单，不要发出注定失败的请求。"""
        return all((self.appid, self.mchid, self.serial_no, self.private_key, self.api_v3_key, self.notify_url))

    @property
    def notify_ready(self) -> bool:
        """回调只需解密用的 APIv3 密钥与验签用的平台证书。"""
        return bool(self.api_v3_key and self.platform_cert)


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
    raw = body.encode("utf-8")
    headers = {
        "Authorization": auth_header(cfg, "POST", NATIVE_PATH, body),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "codemax-platform/1.0",
    }
    async with httpx.AsyncClient(base_url=BASE_URL, transport=transport, timeout=TIMEOUT) as c:
        r = await c.post(NATIVE_PATH, content=raw, headers=headers)
    if r.status_code != 200:
        raise WeChatPayError(f"微信支付下单失败：HTTP {r.status_code} {r.text[:200]}")
    code_url = r.json().get("code_url")
    if not code_url:
        raise WeChatPayError(f"微信支付下单响应缺少 code_url：{r.text[:200]}")
    return code_url


# ---------------------------------------------------------------- 支付结果回调（S3-01-3）


def _public_key(pem: str):
    """平台证书与「微信支付公钥」两种 PEM 都支持：新商户拿到的往往是后者。"""
    if "BEGIN CERTIFICATE" in pem:
        return x509.load_pem_x509_certificate(pem.encode()).public_key()
    return serialization.load_pem_public_key(pem.encode())


def verify_notify_signature(
    platform_cert_pem: str, *, timestamp: str, nonce: str, body: str, signature: str
) -> None:
    """用平台证书公钥验回调签名，不通过抛 WeChatPayError。

    验签串只有三行：`应答时间戳\\n 应答随机串\\n 应答报文主体\\n`。
    `body` 必须是**原始报文主体**（路由里用 `await request.body()` 取原文），
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
    except json.JSONDecodeError as e:
        raise WeChatPayError(f"回调报文不是合法 JSON：{e}") from e
