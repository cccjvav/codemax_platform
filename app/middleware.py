"""运维中间件（S5-03-3）：安全响应头 + 结构化请求日志。

对应 TECH_DECISIONS.md 的 TD-90/91（无日志、无监控、无安全响应头）。

## 为什么是纯 ASGI 中间件，不用 `BaseHTTPMiddleware`

第一版用 `BaseHTTPMiddleware` 写，功能全对，但把 S5-02 刚优化好的延迟又吃回去了：
实测客服接口 p50 从 14 ms 涨到 **21 ms**，且「一个大 DDL 拖慢客服」的比值从
1.04 恶化到 **2.14**，两条性能回归测试当场变红。

原因是 `BaseHTTPMiddleware` 会为每个请求 spawn 任务并包装请求/响应流，
叠两层就是双份开销。纯 ASGI 中间件只包一个 `send` 回调，没有任务、没有流包装，
开销可以忽略（TD-166）。**代价**是拿不到 `Request`/`Response` 对象，
只能直接操作 `scope` 与 `message`，代码啰嗦一些 —— 这里用注释补齐可读性。

## 为什么 CSP 里保留了 'unsafe-inline'

四个页面模板**全部**含内联 `<script>`（er / mermaid / drawio / mock_pay）。
要上严格 CSP 就得把它们全改成外部文件 + nonce，那是前端重构，不是加个中间件的事。
所以现在这版 CSP 的目标是**收窄来源**而不是消灭内联：仍然挡住了从任意第三方域
加载脚本、`object-src`、`base-uri` 劫持和外部嵌套。代价与后续路径记在 TD-163。

允许哪些外部源不是拍脑袋写的：`tests/test_ops.py` 会扫描模板与静态资源里出现的
所有外部域，逐个断言它们确实在 CSP 白名单里 —— 以后谁加了新 CDN 忘了改 CSP，
测试会红，而不是等上线后页面白屏。
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.datastructures import MutableHeaders

logger = logging.getLogger("codemax.access")

# 模板与静态资源实际用到的外部源（由 test_ops.py 反向校验，不许漂）
_CDN = "https://cdn.jsdelivr.net"
_DRAWIO = "https://embed.diagrams.net"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {_CDN}",
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
            from .config import settings

            proto = scope.get("scheme", "")
            if proto != "https" and settings.TRUST_PROXY_HEADERS:
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
