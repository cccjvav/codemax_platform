"""ASGI 安全响应头与请求日志。

业务脚本默认只允许 self；Mermaid/D3 已本地构建。开发 docs/redoc 路径有单独 CDN/样式例外，
生产不注册这些 API 文档端点。OAuth 同意页可提供精确回调 form-action。
HSTS 与对外链接生成均使用可信直接代理规则；安全头与日志不等于完整应用安全证明。"""
from __future__ import annotations

import ipaddress
import logging
import time
import uuid

from starlette.datastructures import MutableHeaders

logger = logging.getLogger("codemax.access")

# CDN 仅用于开发 API 文档例外；业务 Mermaid 已自托管。
_CDN = "https://cdn.jsdelivr.net"
_DRAWIO = "https://embed.diagrams.net"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        f"frame-src {_DRAWIO}",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
)

# 安全头是常量，预先编码成 ASGI 要的 (bytes, bytes) 形式，省掉每次请求重复编码
_STATIC_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),  # 禁止 MIME 嗅探
    (b"x-frame-options", b"SAMEORIGIN"),  # 防点击劫持；不用 DENY 是留同源嵌套的余地
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
]


def _header(scope: dict, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def trusted_proxy(scope: dict) -> bool:
    from .config import settings

    if not settings.TRUST_PROXY_HEADERS or not scope.get("client"):
        return False
    try:
        peer = ipaddress.ip_address(scope["client"][0])
        return any(peer in ipaddress.ip_network(c.strip()) for c in settings.TRUSTED_PROXY_CIDRS.split(","))
    except ValueError:
        return False  # malformed configuration never becomes trust-all


def public_base_url(request) -> str:
    """生成浏览器可用的请求基址；默认 request.base_url，不主动访问网络。

    仅当 TRUST_PROXY_HEADERS 开启且直接对端匹配可信 CIDR 时采信转发头。
    scheme 只接受 http/https；代理必须清理来源头并正确设置 Host，不能把该 helper 当成任意 Host 的验证器。"""
    base = str(request.base_url).rstrip("/")
    if not trusted_proxy(request.scope):
        return base

    scope = request.scope
    proto = _header(scope, b"x-forwarded-proto").split(",")[0].strip().lower()
    host = _header(scope, b"x-forwarded-host").split(",")[0].strip()
    if not proto and not host:
        return base
    # 只替换确实给了的那部分；scheme 只接受 http/https，别的值一律忽略
    if proto in ("http", "https"):
        base = proto + base[base.index("://"):]
    if host:
        base = base[: base.index("://") + 3] + host
    return base


class SecurityHeadersMiddleware:
    """给所有响应加安全头。已存在的头不覆盖（让端点自己有机会定制）。"""

    def __init__(self, app, hsts_max_age: int = 0):
        self.app = app
        self.hsts_max_age = hsts_max_age

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # HSTS 只在真 https 时下发。反向代理终止 TLS 时应用看到的是 http，
        # 要靠 X-Forwarded-Proto 判断 —— 但这只在 TRUST_PROXY_HEADERS 打开时可信，
        # 否则伪造一个头就能让站点被浏览器锁死一年。
        hsts = None
        if self.hsts_max_age > 0:
            proto = scope.get("scheme", "")
            if proto != "https" and trusted_proxy(scope):
                proto = _header(scope, b"x-forwarded-proto").split(",")[0].strip().lower()
            if proto == "https":
                hsts = (
                    b"strict-transport-security",
                    f"max-age={self.hsts_max_age}; includeSubDomains".encode(),
                )

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in _STATIC_SECURITY_HEADERS:
                    headers.setdefault(k.decode(), v.decode())
                if scope.get("path") in ("/docs", "/redoc"):
                    headers["content-security-policy"] = CONTENT_SECURITY_POLICY.replace(
                        "script-src 'self'", f"script-src 'self' 'unsafe-inline' {_CDN}"
                    ).replace("style-src 'self' 'unsafe-inline'",
                              f"style-src 'self' 'unsafe-inline' {_CDN} https://fonts.googleapis.com") + "; font-src 'self' https://fonts.gstatic.com data:"
                if not scope.get("path", "").startswith("/static/"):
                    headers.setdefault("cache-control", "no-store")
                if hsts is not None:
                    headers.setdefault(hsts[0].decode(), hsts[1].decode())
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestLoggingMiddleware:
    """每个请求一行结构化日志，并把 request id 回写给客户端。

    request id 的意义：用户报障时让他把响应头里的 `X-Request-ID` 报上来，
    就能在日志里精确定位那一次请求 —— 没有它，线上排障只能靠时间戳猜。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _header(scope, b"x-request-id") or uuid.uuid4().hex[:16]
        method = scope.get("method", "-")
        path = scope.get("path", "-")
        start = time.perf_counter()
        status = 500  # 若应用抛异常没走到 response.start，就按 500 记

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # 异常也要留痕，否则 500 在日志里是一片空白
            logger.exception(
                "%s %s -> 500 (%.1fms) rid=%s",
                method, path, (time.perf_counter() - start) * 1000, request_id,
            )
            raise
        logger.info(
            "%s %s -> %d (%.1fms) rid=%s",
            method, path, status, (time.perf_counter() - start) * 1000, request_id,
            extra={"rid": request_id},
        )
