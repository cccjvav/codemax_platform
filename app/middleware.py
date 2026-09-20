"""ASGI 安全响应头、请求日志与解析前的请求体预算。

业务脚本默认只允许 self；Mermaid/D3 已本地构建。开发 docs/redoc 路径有单独 CDN/样式例外，
生产不注册这些 API 文档端点。OAuth 同意页可提供精确回调 form-action。
HSTS 与对外链接生成均使用可信直接代理规则；安全头与日志不等于完整应用安全证明。"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders

from .config import settings

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


# ---------------------------------------------------------------- 请求体预算（A-01）

# 微信支付/退款回调的处理函数自己边读边数、在 64 KiB 处按微信要求的 {"code":"FAIL"} 格式拒绝；
# 这里的兜底要留出余量，否则中间件的 {"detail":…} 413 会抢在处理函数之前，改变回调应答格式。
NOTIFY_BODY_LIMIT = 2 * 65536

# 大体路由用精确正则，不用前缀：/diagrams/1/restore、/diagramsX 都不该继承 4 MiB 预算。
_DIAGRAM_PATHS = re.compile(r"^/diagrams(/\d+)?$")
_TOOL_PATHS = re.compile(r"^/tools/(er-diagram|word-export)$")
_NOTIFY_PATHS = re.compile(r"^/shop/(pay|refunds)/notify$")


def body_budget_for(path: str) -> int:
    """按路径给出请求体字节上限。每次调用现读 settings，测试改配置后立刻生效。"""
    if _DIAGRAM_PATHS.match(path):
        return settings.MAX_DIAGRAM_BODY_BYTES
    if _TOOL_PATHS.match(path):
        return settings.MAX_TOOL_BODY_BYTES
    if _NOTIFY_PATHS.match(path):
        return NOTIFY_BODY_LIMIT
    return settings.MAX_REQUEST_BODY_BYTES


class RequestBodyBudgetMiddleware:
    """在 FastAPI 读体/解析之前，对请求体施加字节上限和读取时限。

    为什么必须在这一层：FastAPI 先 `await request.body()` 把整个体读进内存，pydantic 的
    `max_length` 之后才起作用；一个匿名 POST 带 2 MiB 就能让进程吃 2 MiB，还会在 422 里被
    原样回显（回显由 main.py 的 RequestValidationError 处理器单独去掉）。

    三条规则：
    1. `Content-Length` 声明超预算 → 立即 413，一个字节都不读；非法值 → 400。
    2. 无长度（分块）或声明与实际不符 → 包装 `receive` 逐块计数，越过预算即 413，
       之后向应用返回 `http.disconnect`、丢弃应用迟到的响应，不再消费上游字节。
    3. 从收到请求头起，读体总时长超过 REQUEST_BODY_TIMEOUT_SECONDS → 408。
    拒绝响应带 `Connection: close`：h11 会在应答后关闭连接，而不是继续吞掉剩余请求体。

    字节原样透传（不解码、不改块边界），支付回调的原始报文验签不受影响。
    本中间件不替代反向代理的上限，也不是限流；只保证单个请求不能靠体积或慢发把应用拖住。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = body_budget_for(scope.get("path", ""))
        declared = _header(scope, b"content-length").strip()
        if declared:
            if not declared.isdigit():
                await _reject(send, 400, "Content-Length 非法")
                return
            if int(declared) > limit:
                await _reject(send, 413, f"请求体超过限制（最大 {limit} 字节）")
                return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.REQUEST_BODY_TIMEOUT_SECONDS
        state = {"seen": 0, "started": False, "responded": False}

        async def stop(status: int, detail: str) -> dict:
            # 应用还没开始应答就由我们应答；已经开始（边读边发的流式场景）只能停止喂数据。
            if not state["started"]:
                state["responded"] = True
                await _reject(send, status, detail)
            state["started"] = True
            return {"type": "http.disconnect"}

        async def budgeted_receive():
            if state["responded"]:
                return {"type": "http.disconnect"}
            try:
                async with asyncio.timeout_at(deadline):
                    message = await receive()
            except TimeoutError:
                return await stop(408, "请求体接收超时")
            if message["type"] == "http.request":
                state["seen"] += len(message.get("body", b""))
                if state["seen"] > limit:
                    return await stop(413, f"请求体超过限制（最大 {limit} 字节）")
            return message

        async def guarded_send(message):
            if state["responded"]:
                return  # 我们已经替它应答过了（应用此时通常在发 400 "error parsing the body"）
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        await self.app(scope, budgeted_receive, guarded_send)


async def _reject(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
