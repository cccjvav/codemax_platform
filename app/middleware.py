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

import ipaddress
import logging
import time
import uuid

from starlette.datastructures import MutableHeaders

logger = logging.getLogger("codemax.access")

# 模板与静态资源实际用到的外部源（由 test_ops.py 反向校验，不许漂）
# TD-222：d3 已改为 npm 打进产物，不再走 CDN。但 **mermaid 仍然走 jsdelivr**
# （`app/templates/mermaid.html` 里是裸 ESM `import`，不是 `<script src>`），
# 所以这个白名单**还不能删** —— 删了 mermaid 页会被 CSP 直接拦死、整页无图。
#
# mermaid 压缩后接近 2 MB，打进产物会让仓库与首屏都明显变重，故本轮刻意不动，
# 作为后续项记在 TD-222。等它也被打包进来，这里应当一并收紧。
#
# ⚠️ 这个域是真实攻击面：jsdelivr 被投毒时，投毒代码在本站等于同源执行权。
#    新增任何 CDN 依赖前，先想清楚为什么不能像 d3 一样打进产物。
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
    """这个请求在**用户浏览器眼里**的站点根，如 `https://shop.example.com`。

    ## 为什么不能直接用 `request.base_url`

    TLS 在反向代理（nginx / 云 LB）那层终止，应用看到的 `scope["scheme"]` 永远是
    `http`。而 Starlette 的 `base_url` 直接读这个 scheme、**不看转发头**。
    实测带着 `X-Forwarded-Proto: https` 请求 `/shop/download/{no}`，
    拿回来的预签名链接仍然是 `http://test/shop/dl?...`。

    后果很具体：浏览器在 https 页面上拿到一个 http 的下载链接，按**混合内容**
    直接拦掉 —— 用户付了钱点下载没反应，而控制台那行报错没人会关联到代理配置。

    ## 为什么必须与 HSTS 用同一套信任规则

    本文件的 `SecurityHeadersMiddleware` 早就定了规矩：`X-Forwarded-Proto`
    **只在 `TRUST_PROXY_HEADERS=true` 时可信**，否则伪造一个头就能让站点被浏览器
    锁死一年（TD-142）。生成链接必须沿用同一条判断 —— 两边不一致的话会出现
    自相矛盾的响应：HSTS 说「本站只有 https」，下载链接给的却是 http。

    `X-Forwarded-Host` 同理一并采信/一并忽略：只改 scheme 不改 host，
    链接照样指回内网地址，用户点不开。
    """
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
