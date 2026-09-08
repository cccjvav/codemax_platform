"""爬虫礼貌性约束（TD-133）：robots.txt + 按域抓取间隔 + 全局并发上限。

## 为什么这三件事要一起做

只加 robots.txt 而不加限速，等于「打了招呼然后使劲刷」；只限速不读 robots，
等于「慢慢刷人家明确说了不让抓的东西」。两者都是会被封 IP、也确有合规风险的。

## robots.txt 的判定按业界通行约定，不是自己发明的

- **200** → 按内容判定（`urllib.robotparser` 负责解析 `User-agent` / `Disallow` / `Allow` / `Crawl-delay`）
- **404 / 410** → 视为无限制，可以抓（没有 robots 文件是绝大多数站点的常态）
- **401 / 403** → 视为**全站禁止**。站方用鉴权挡住 robots.txt，意图很明确
- **5xx / 超时** → 视为**暂时不可知，本次不抓**。宁可漏抓一篇，不要在规则未知时硬闯

最后一条比「拿不到规则就当没规则」保守，代价是目标站抽风时会漏抓；
对一个内容冷启动爬虫来说，漏抓可以重跑，被封 IP 不行。

## robots.txt 结果缓存 1 小时

同一批冷启动往往要抓同一个站的几十篇文章，每篇都去拉一次 robots.txt
本身就变成了对目标站的额外压力 —— 那就违背这个模块的初衷了。

## 抓取间隔按域计算，不是全局

`Crawl-delay` 是**每个站各自**的要求。A 站要 5 秒、B 站没要求，
用全局间隔会让抓 B 站时无谓地慢，抓 A 站时又不够礼貌。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

# robots.txt 缓存多久（秒）。1 小时是通行做法。
ROBOTS_TTL = 3600.0
# 目标站没写 Crawl-delay 时，我们对同一域名两次请求之间的最小间隔（秒）
DEFAULT_MIN_INTERVAL = 2.0
# 全局同时在飞的请求数上限。防的是「一百个域名各抓一篇」时把本机带宽打满
MAX_CONCURRENCY = 4
# robots.txt 本身很小，给个短超时和小体积上限，别被一个巨大的 robots.txt 拖住
ROBOTS_TIMEOUT = 10.0
ROBOTS_MAX_BYTES = 512_000


class RobotsDisallowed(Exception):
    """目标站的 robots.txt 不允许抓这个 URL（或规则暂时不可知）。"""


@dataclass
class _DomainState:
    allowed_all: bool = True  # 401/403 时置 False
    parser: RobotFileParser | None = None
    crawl_delay: float | None = None
    fetched_at: float = 0.0
    last_request: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# 按 (scheme, netloc) 缓存。刻意不做 LRU 淘汰：冷启动只涉及少数几个站，
# 且进程重启就清空，攒不到需要淘汰的量级。
_states: dict[tuple[str, str], _DomainState] = {}
_semaphore: asyncio.Semaphore | None = None


def _origin(url: str) -> tuple[str, str]:
    p = urlsplit(url)
    return (p.scheme, p.netloc)


def _robots_url(url: str) -> str:
    scheme, netloc = _origin(url)
    return f"{scheme}://{netloc}/robots.txt"


def _get_semaphore() -> asyncio.Semaphore:
    """懒创建：`asyncio.Semaphore` 会绑定到创建时的事件循环，
    而 pytest-asyncio 每个用例开一个新循环，模块级创建会跨循环复用而报错。"""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    return _semaphore


def reset_cache() -> None:
    """清空 robots 缓存与并发闸。测试用；生产没有清缓存的需求。"""
    global _semaphore
    _states.clear()
    _semaphore = None


async def _load_robots(url: str, user_agent: str, fetch_text) -> _DomainState:
    """拉取并解析该域的 robots.txt，带缓存。`fetch_text` 由调用方注入，
    以便复用爬虫自己那套 SSRF 校验与测试注入的 transport。"""
    state = _states.get(_origin(url))
    now = time.monotonic()
    if state is not None and now - state.fetched_at < ROBOTS_TTL:
        return state

    if state is None:
        state = _DomainState()
        _states[_origin(url)] = state

    async with state.lock:
        # 双重检查：等锁期间可能已经有别的协程加载完了
        if state.fetched_at and now - state.fetched_at < ROBOTS_TTL:
            return state
        state.allowed_all = True
        state.parser = None
        state.crawl_delay = None
        # 延迟导入：crawler 依赖本模块，模块级反向导入会成环。
        # 这里只是要认出它抛的异常类型，运行时 crawler 早已加载完毕。
        from .crawler import CrawlError

        try:
            status, text = await fetch_text(_robots_url(url))
        except CrawlError:
            # **SSRF 校验失败必须原样抛出**，不能被下面的兜底吞成 RobotsDisallowed：
            # 否则「这个地址不许访问」会被伪装成「robots 不让抓」，
            # 排障时看不出是 SSRF 拦截，安全告警也就丢了。
            raise
        except Exception:
            # 超时、连接失败、DNS 失败……规则不可知，按保守处理（本次不抓）
            state.allowed_all = False
            state.parser = None
            state.fetched_at = now
            return state

        if status in (401, 403):
            state.allowed_all = False
        elif status in (404, 410):
            state.allowed_all = True  # 没有 robots 文件 = 无限制
        elif 200 <= status < 300:
            parser = RobotFileParser()
            parser.parse((text or "").splitlines())
            state.parser = parser
            # **必须传真实 UA，不能传 None**：`Entry.applies_to` 会对 useragent 调
            # `.split("/")`，robots 里若没有 `User-agent: *` 条目就会 AttributeError。
            # 语义上也本该如此 —— Crawl-delay 是按 agent 分别声明的。
            delay = parser.crawl_delay(user_agent)
            state.crawl_delay = float(delay) if delay else None
        else:
            state.allowed_all = False  # 5xx / 3xx 等：不可知
        state.fetched_at = now
        return state


async def check_allowed(url: str, user_agent: str, fetch_text) -> None:
    """不允许抓就抛 `RobotsDisallowed`。"""
    state = await _load_robots(url, user_agent, fetch_text)
    if not state.allowed_all:
        raise RobotsDisallowed(f"robots.txt 不允许抓取（或规则暂不可知）：{url}")
    if state.parser is not None and not state.parser.can_fetch(user_agent, url):
        raise RobotsDisallowed(f"robots.txt 明确禁止抓取：{url}")


def min_interval_for(state: _DomainState) -> float:
    """该域的最小抓取间隔：优先听目标站的 Crawl-delay，其次用我们的默认值。"""
    if state.crawl_delay and state.crawl_delay > 0:
        return state.crawl_delay
    return DEFAULT_MIN_INTERVAL


async def throttle(url: str, state: _DomainState) -> None:
    """按域排队：保证对同一域名的两次请求至少间隔 `min_interval_for(state)` 秒。

    必须在锁内更新 `last_request`，否则并发的两个协程会算出同一个等待时长、
    然后同时醒来一起发出去 —— 限速就白做了。
    """
    interval = min_interval_for(state)
    async with state.lock:
        now = time.monotonic()
        wait = state.last_request + interval - now
        # 先把「预定发出时刻」记下来再睡，这样排在后面的协程会往后顺延，
        # 而不是全都按同一个 last_request 计算、然后一起冲出去。
        state.last_request = now + max(wait, 0.0)
    if wait > 0:
        await asyncio.sleep(wait)


def state_for(url: str) -> _DomainState:
    return _states.setdefault(_origin(url), _DomainState())
