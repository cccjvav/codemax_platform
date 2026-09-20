"""ASGI 安全响应头、请求体预算、请求日志与 422 应答脱敏。

业务脚本默认只允许 self；Mermaid/D3 已本地构建。开发 docs/redoc 路径有单独 CDN/样式例外，
生产不注册这些 API 文档端点。OAuth 同意页可提供精确回调 form-action。
HSTS 与对外链接生成均使用可信直接代理规则；安全头与日志不等于完整应用安全证明。

请求体预算（TD-260）只限制**应用读到的字节**：默认 1 MiB，`/diagrams` 2 MiB，支付/退款回调
沿用路由内 64 KiB 流式上限。它不替代反向代理的 `client_max_body_size`，也不限制响应大小。"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
import uuid

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import get_route_path

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
    """生产链接固定使用启动时校验的 SITE_BASE_URL；开发默认 request.base_url，不主动访问网络。

    仅当 TRUST_PROXY_HEADERS 开启且直接对端匹配可信 CIDR 时采信转发头。
    scheme 只接受 http/https；代理必须清理来源头并正确设置 Host，不能把该 helper 当成任意 Host 的验证器。"""
    from .config import settings

    if settings.ENV == "production":
        return settings.SITE_BASE_URL.rstrip("/")
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


# 请求体预算（字节）。数值是 2026-09-20 接手复核与用户确认的取值，不做运行时配置项：
# 改小会让合法的 drawio XML（DiagramIn 允许 50 万字符，UTF-8 可到 ~1.5 MB）保存失败。
BODY_LIMIT_DEFAULT = 1 * 1024 * 1024
BODY_LIMIT_DIAGRAMS = 2 * 1024 * 1024
# 这两个回调自己按 64 KiB 流式读取并用渠道规定的 FAIL 报文应答，不能换成通用 {"detail"} 413。
_SELF_BUDGETED_PATHS = frozenset({"/shop/pay/notify", "/shop/refunds/notify"})


def body_limit_for(path: str) -> int | None:
    """路径 → 请求体上限字节数；返回 None 表示该路由自管预算，中间件不介入。"""
    if path in _SELF_BUDGETED_PATHS:
        return None
    if path == "/diagrams" or path.startswith("/diagrams/"):
        return BODY_LIMIT_DIAGRAMS
    return BODY_LIMIT_DEFAULT


class BodyTooLarge(HTTPException):
    """请求体超出预算。继承 HTTPException 是有意的：FastAPI 读取请求体时会把其他异常统一包成
    400 "There was an error parsing the body"，只有 HTTPException 原样上抛并由既有处理器变成
    `{"detail": ...}` 应答，所以路由内读体越界也能得到 413 而不是伪装的 400。"""

    def __init__(self, limit: int):
        super().__init__(413, f"请求体超过上限（{limit} 字节）", headers={"connection": "close"})
        self.limit = limit


class RequestBodyBudgetMiddleware:
    """按路径给请求体设上限（TD-260）。

    - `Content-Length` 已超限：不读一个字节，直接 413 并要求关闭连接；
    - 分块/无长度/谎报长度：包装 `receive` 累计实际字节，越界即抛 BodyTooLarge，之后不再消费；
    - 回调路径（`body_limit_for` 返回 None）完全透传，由路由自己的 64 KiB 流式预算负责。

    `connection: close` 让服务器在应答后关闭连接，而不是为了复用连接把剩余上传全部读完丢弃。
    这不是反向代理的 `client_max_body_size`，生产仍应在网关再设一层。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = get_route_path(scope)  # 去掉 root_path 前缀，与路由匹配用的是同一个路径
        limit = body_limit_for(path)
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = _header(scope, b"content-length").strip()
        if declared:
            try:
                length = int(declared)
            except ValueError:
                length = None  # 畸形头：协议层通常已拒绝；这里退回按实际字节计数
            if length is not None and length < 0:
                await _reject(scope, receive, send, 400, "Content-Length 无效")
                return
            if length is not None and length > limit:
                await _reject(scope, receive, send, 413, f"请求体超过上限（{limit} 字节）")
                return

        received = 0
        response_started = False

        async def budgeted_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, budgeted_receive, tracking_send)
        except BodyTooLarge as exc:
            # 正常情况下 FastAPI 已把它变成 413 应答，不会到这里；只有非 FastAPI 消费者
            # （或应答已开始后才越界）才会漏出来。能补发就补发，不能就只记录，不再抛成 500。
            if response_started:
                logger.warning("%s 请求体在应答开始后超过 %d 字节", path, exc.limit)
                return
            await _reject(scope, receive, send, 413, exc.detail)


async def _reject(scope, receive, send, status: int, detail: str) -> None:
    response = JSONResponse({"detail": detail}, status_code=status, headers={"connection": "close"})
    await response(scope, receive, send)


async def validation_error_without_input(request, exc: RequestValidationError) -> JSONResponse:
    """422 应答只保留 type/loc/msg/ctx，不回显 `input`/`url`。

    FastAPI 默认把出错字段的原值整个放进 `input`：一个 60 万字符的超长正文会被原样回显，
    等于让 1 字节的请求成本换来同等大小的响应。前端 `auth.js`/`support-page.js` 只读 `msg`。
    """
    errors = [
        {key: value for key, value in error.items() if key not in ("input", "url")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


class RequestLoggingMiddleware:
    """每个请求一行有界文本日志（不是持久审计账本），并把 request id 回写给客户端。

    request id 的意义：用户报障时让他把响应头里的 `X-Request-ID` 报上来，
    就能在日志里精确定位那一次请求 —— 没有它，线上排障只能靠时间戳猜。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied_id = _header(scope, b"x-request-id")
        request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied_id) else uuid.uuid4().hex
        # ascii escapes CR/LF/control characters; never log query-string capabilities.
        method = ascii(scope.get("method", "-")[:16])[1:-1]
        path = ascii(scope.get("path", "-")[:1024])[1:-1]
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
