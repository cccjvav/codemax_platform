"""接口限流（TD-15，上线阻塞项之一）。

**内存滑动窗口**，按「作用域 + 客户端 IP」计数。本项目是单进程部署，为一个限流引入
Redis 属于过度设计（AGENTS.md 第 1 条）；代价见 TD-141：多进程 / 多实例部署时每个进程
各算各的，实际配额会变成 N 倍。

为什么必须有它：`/tools/*` 是**刻意不设鉴权**的引流端点（要 SEO 收录、要游客能直接用），
所以任何人都能无限调用 —— DDL 解析与 Word 导出吃 CPU，`/tools/mermaid` 每次调用**直接花钱**，
`/auth/login` 不限流则可以被在线爆破。

一个容易踩的坑：**不能无条件信任 `X-Forwarded-For`**。那是客户端可以随便填的头，
无条件信任等于把限流交给攻击者控制（每次换一个 XFF 就是无限配额）。所以默认只取
socket 对端地址；只有明确部署在可信反向代理之后才打开 `TRUST_PROXY_HEADERS`（TD-142）。
"""
from __future__ import annotations

import ipaddress
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from .config import settings
from .middleware import trusted_proxy

_PRUNE_THRESHOLD = 1024  # key 多到这个数就顺手清一次，避免字典随 IP 无限增长


@dataclass
class Limiter:
    """滑动窗口计数器。

    `clock` 可注入，测试就不必真的等窗口过期。`limit` 与 `window` 每次调用传入，
    这样改了 settings 立刻生效（不必重建实例）。
    """

    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque] = field(default_factory=dict)

    def allow(self, key: str, *, limit: int, window: float) -> tuple[bool, int]:
        """返回 (是否放行, 建议重试秒数)。"""
        now = self.clock()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= limit:
            return False, max(1, int(window - (now - hits[0])) + 1)
        hits.append(now)
        if len(self._hits) > _PRUNE_THRESHOLD:
            self.prune(window)
        return True, 0

    def prune(self, window: float) -> None:
        """丢掉窗口外已经没有命中的 key，防止字典随不同 IP 无限增长。

        注意不能只删空 deque —— `allow` 弹出旧命中后紧接着就会 append，
        deque 在实践中永远非空，那样删等于什么都没删。要按「最后一次命中的时间」判断。
        """
        now = self.clock()
        for key in [k for k, dq in self._hits.items() if not dq or now - dq[-1] >= window]:
            self._hits.pop(key, None)

    def reset(self) -> None:
        self._hits.clear()


limiter = Limiter()


def client_key(request: Request) -> str:
    """限流用的客户端标识。默认取 socket 对端地址，见模块 docstring 关于 XFF 的说明。"""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy(request.scope):
        chain = request.headers.get("x-forwarded-for", "").split(",")
        try:
            networks = [ipaddress.ip_network(c.strip()) for c in settings.TRUSTED_PROXY_CIDRS.split(",")]
            for item in reversed(chain):
                address = ipaddress.ip_address(item.strip())
                if not any(address in net for net in networks):
                    return str(address)
        except ValueError:
            return peer
    return peer


def rate_limit(scope: str, limit_attr: str):
    """生成一个限流依赖。

    `limit_attr` 是 settings 上的属性名，运行时才读，方便测试改配额。
    """

    async def dependency(request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        limit = getattr(settings, limit_attr)
        ok, retry_after = limiter.allow(
            f"{scope}:{client_key(request)}", limit=limit, window=settings.RATE_LIMIT_WINDOW
        )
        if not ok:
            raise HTTPException(
                429,
                f"请求过于频繁，请 {retry_after} 秒后再试",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
