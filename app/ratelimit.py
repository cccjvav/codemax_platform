"""接口限流（TD-15，上线阻塞项之一）。

**内存滑动窗口**，按「作用域 + 客户端身份」计数。本项目是单进程部署，为一个限流引入
Redis 属于过度设计（AGENTS.md 第 1 条）；代价见 TD-141：多进程 / 多实例部署时每个进程
各算各的，实际配额会变成 N 倍。

为什么必须有它：`/tools/*` 是**刻意不设鉴权**的引流端点（要 SEO 收录、要游客能直接用），
所以任何人都能无限调用 —— DDL 解析与 Word 导出吃 CPU，`/tools/mermaid` 每次调用**直接花钱**，
`/auth/login` 不限流则可以被在线爆破。

一个容易踩的坑：**不能无条件信任 `X-Forwarded-For`**。那是客户端可以随便填的头，
无条件信任等于把限流交给攻击者控制（每次换一个 XFF 就是无限配额）。所以默认只取
socket 对端地址；只有明确部署在可信反向代理之后才打开 `TRUST_PROXY_HEADERS`（TD-142）。

键容量（TD-261）：键表有上限（`max_keys`），满了**不驱逐活跃桶**去接纳新身份 —— 那等于
让攻击者用新地址把老配额洗掉。但满桶时必须先回收已过期的桶，否则一批轮换地址把表填满后，
所有新客户端都会被 429（复核报告 F-09）。`_expires` 按「最后一次放行」排序，所有作用域共用
同一个 `RATE_LIMIT_WINDOW`，因此队头永远是最早到期的桶：满桶时只看队头就能判断有没有可回收
的键，摊销 O(1)，请求路径不做全表扫描。IPv6 按 /64 归并成一个身份：单机可以用 /64 内任意
地址，不归并的话一台机器就是无限身份。
"""
from __future__ import annotations

import ipaddress
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from fastapi import HTTPException, Request

from .config import settings
from .middleware import trusted_proxy

logger = logging.getLogger("codemax.ratelimit")

_PRUNE_BATCH = 32  # 每次最多回收这些过期键；不在请求路径全表扫描
_CAPACITY_LOG_INTERVAL = 60.0  # 满桶告警最多每分钟一条，攻击期间不能反过来刷爆日志
IPV6_PREFIX = 64  # 一个 /64 通常就是一台机器 / 一个接入用户；再细分不是独立身份


class Verdict(NamedTuple):
    """`Limiter.admit` 的结论。`reason` 只在拒绝时有区分意义：quota=本身份超配额，capacity=键表满。"""

    allowed: bool
    retry_after: int
    reason: str


@dataclass
class Limiter:
    """滑动窗口计数器。

    `clock` 可注入，测试就不必真的等窗口过期。`limit` 与 `window` 每次调用传入，
    这样改了 settings 立刻生效（不必重建实例）。

    `_hits` 记每个键窗口内的放行时刻；`_expires` 是同一批键按最后一次放行排序的到期时间，
    队头最早到期。两个容器一一对应，`reset()` 一起清。
    """

    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque] = field(default_factory=dict)
    max_keys: int = 16384
    _expires: OrderedDict[str, float] = field(default_factory=OrderedDict)
    _capacity_logged_at: float | None = None

    def admit(self, key: str, *, limit: int, window: float) -> Verdict:
        """判定一次请求：先回收队头已到期的桶，再看键容量，最后看该身份自己的滑动窗口。

        队头回收假设所有调用方共用同一个 window（生产就是 `RATE_LIMIT_WINDOW`）。若有人传入
        不同 window，回收只会变保守（长窗口的队头会挡住后面已到期的短窗口桶），仍然不会删掉
        任何 `expires > now` 的活跃桶。
        """
        now = self.clock()
        # Bounded maintenance: expired buckets sit at the head because every allowed hit
        # moves its key to the tail. Stop at the first live head; never evict a live bucket.
        for _ in range(_PRUNE_BATCH):
            if not self._expires:
                break
            oldest, expires = next(iter(self._expires.items()))
            if expires > now:
                break
            del self._expires[oldest]
            del self._hits[oldest]
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                # Every bucket is live: refusing the newcomer is the only option that keeps
                # existing quotas honest. Report when the oldest bucket can expire.
                retry_after = max(1, int(next(iter(self._expires.values())) - now) + 1) if self._expires else 1
                if self._capacity_logged_at is None or now - self._capacity_logged_at >= _CAPACITY_LOG_INTERVAL:
                    self._capacity_logged_at = now
                    logger.warning(
                        "限流器身份容量已满（%d/%d 个活跃键），新来源将被 429 拒绝，最早 %d 秒后释放；"
                        "请核查是否有来源轮换刷量",
                        len(self._hits), self.max_keys, retry_after,
                    )
                return Verdict(False, retry_after, "capacity")
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= limit:
            return Verdict(False, max(1, int(window - (now - hits[0])) + 1), "quota")
        hits.append(now)
        self._expires[key] = now + window
        self._expires.move_to_end(key)
        return Verdict(True, 0, "ok")

    def allow(self, key: str, *, limit: int, window: float) -> tuple[bool, int]:
        """返回 (是否放行, 建议重试秒数)。`admit` 的两元组视图，保留给不关心拒绝原因的调用方。"""
        verdict = self.admit(key, limit=limit, window=window)
        return verdict.allowed, verdict.retry_after

    def prune(self, window: float) -> None:
        """丢掉窗口外已经没有命中的 key，防止字典随不同 IP 无限增长。

        注意不能只删空 deque —— `allow` 弹出旧命中后紧接着就会 append，
        deque 在实践中永远非空，那样删等于什么都没删。要按「最后一次命中的时间」判断。
        这是显式维护 / 测试接口，会全表扫描；请求路径靠 `admit` 的队头回收，不调用它。
        """
        now = self.clock()
        for key in [k for k, dq in self._hits.items() if not dq or now - dq[-1] >= window]:
            self._hits.pop(key, None)
            self._expires.pop(key, None)

    def reset(self) -> None:
        self._hits.clear()
        self._expires.clear()
        self._capacity_logged_at = None


limiter = Limiter()


def _identity(raw: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """把地址归一成限流身份：IPv4 原样，IPv4 映射地址还原成 IPv4，其余 IPv6 归并到 /64。

    解析不出的值（测试客户端名、无对端）原样返回，仍然占一个键而不是绕过限流。
    """
    try:
        address = ipaddress.ip_address(raw) if isinstance(raw, str) else raw
    except ValueError:
        return raw
    if address.version == 6:
        mapped = address.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        return str(ipaddress.ip_network((address, IPV6_PREFIX), strict=False))
    return str(address)


def client_key(request: Request) -> str:
    """限流用的客户端标识。默认取 socket 对端地址，见模块 docstring 关于 XFF 与 /64 的说明。"""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy(request.scope):
        chain = request.headers.get("x-forwarded-for", "").split(",")
        try:
            networks = [ipaddress.ip_network(c.strip()) for c in settings.TRUSTED_PROXY_CIDRS.split(",")]
            for item in reversed(chain):
                address = ipaddress.ip_address(item.strip())
                if not any(address in net for net in networks):
                    return _identity(address)
        except ValueError:
            return _identity(peer)
    return _identity(peer)


def rate_limit(scope: str, limit_attr: str):
    """生成一个限流依赖。

    `limit_attr` 是 settings 上的属性名，运行时才读，方便测试改配额。
    """

    async def dependency(request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        limit = getattr(settings, limit_attr)
        verdict = limiter.admit(
            f"{scope}:{client_key(request)}", limit=limit, window=settings.RATE_LIMIT_WINDOW
        )
        if not verdict.allowed:
            # capacity：不是这位客户端请求太多，而是键表被其他来源占满；文案要能区分，运维才看得出被刷。
            detail = ("服务器当前访问来源过多，请 {} 秒后再试" if verdict.reason == "capacity"
                      else "请求过于频繁，请 {} 秒后再试").format(verdict.retry_after)
            raise HTTPException(429, detail, headers={"Retry-After": str(verdict.retry_after)})

    return dependency
