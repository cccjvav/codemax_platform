"""TD-133：爬虫礼貌性约束 —— robots.txt、按域抓取间隔、全局并发上限。"""
import asyncio
import time

import httpx
import pytest

from app.tools import politeness
from app.tools.crawler import USER_AGENT, CrawlError, fetch
from app.tools.politeness import RobotsDisallowed

FAKE_IP = "93.184.216.34"  # 公网字面量 IP：SSRF 校验真跑，请求被 MockTransport 截下
BASE = f"http://{FAKE_IP}"
OTHER_IP = "93.184.216.35"
OTHER = f"http://{OTHER_IP}"

PAGE = "<html><body><article>正文</article></body></html>"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """robots 缓存与并发闸是模块级的，用例之间必须清干净，否则互相污染。"""
    politeness.reset_cache()
    # 默认间隔 2 秒，测试里等不起；压到 50ms 仍能验证「确实排了队」
    monkeypatch.setattr(politeness, "DEFAULT_MIN_INTERVAL", 0.05)
    yield
    politeness.reset_cache()


def transport(robots_status: int = 404, robots_body: str = "", *, extra: dict | None = None):
    """造一个 MockTransport：`/robots.txt` 按参数返回，其余路径返回正常页面。

    `extra` 可覆盖特定路径，用于造多域名场景。
    """
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots_body)
        if extra and str(request.url) in extra:
            return httpx.Response(200, text=extra[str(request.url)])
        return httpx.Response(200, text=PAGE)

    return httpx.MockTransport(handle), seen


# ============================================================ robots.txt 判定


@pytest.mark.asyncio
async def test_robots_disallow_blocks_the_fetch():
    t, seen = transport(200, "User-agent: *\nDisallow: /private\n")
    with pytest.raises(RobotsDisallowed):
        await fetch(f"{BASE}/private/a", transport=t)
    # 关键：被 robots 拦下时，**不应该**去请求那个页面
    assert not any("/private/a" in u for u in seen), f"不该抓被禁的路径，实际请求：{seen}"


@pytest.mark.asyncio
async def test_robots_allow_passes():
    t, seen = transport(200, "User-agent: *\nDisallow: /private\n")
    page = await fetch(f"{BASE}/blog/a", transport=t)
    assert "正文" in page.html


@pytest.mark.asyncio
async def test_missing_robots_file_means_no_restriction():
    """404 是绝大多数站点的常态，不能因此就不抓。"""
    t, _ = transport(404)
    page = await fetch(f"{BASE}/blog/a", transport=t)
    assert page.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_protected_robots_means_disallow_all(status):
    """站方用鉴权挡住 robots.txt，意图很明确：全站别抓。"""
    t, seen = transport(status)
    with pytest.raises(RobotsDisallowed):
        await fetch(f"{BASE}/blog/a", transport=t)
    assert not any("/blog/a" in u for u in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_unavailable_robots_means_do_not_crawl(status):
    """规则不可知时**不抓**。宁可漏抓一篇（可以重跑），被封 IP 不行。

    这比「拿不到规则就当没规则」保守，是刻意的取舍。
    """
    t, _ = transport(status)
    with pytest.raises(RobotsDisallowed):
        await fetch(f"{BASE}/blog/a", transport=t)


@pytest.mark.asyncio
async def test_robots_matched_by_product_token_not_full_ua():
    """规则按**产品名 token** 判定，不是整条 UA。

    标准库 `Entry.applies_to` 会先把我们的 UA 在第一个 `/` 处截断成
    `codemax-platform`，再看 robots 里写的 agent 是不是它的子串。所以站点必须写
    `User-agent: codemax-platform`；**写我们完整的 UA 串反而匹配不上**
    （那条更长，不可能是短串的子串）。这是 robots 规范的通行做法，不是 bug。
    """
    t, _ = transport(200, "User-agent: codemax-platform\nDisallow: /\n")
    with pytest.raises(RobotsDisallowed):
        await fetch(f"{BASE}/blog/a", transport=t)

    politeness.reset_cache()
    t2, _ = transport(200, "User-agent: some-other-bot\nDisallow: /\n")
    page = await fetch(f"{BASE}/blog/a", transport=t2)  # 规则不是给我们的，可以抓
    assert page.status == 200


def test_full_ua_in_robots_would_not_match_stdlib_parser():
    """把上面那条注释里的结论钉住：完整 UA 串确实匹配不上。

    将来若换了 robots 解析实现，这条会红 —— 那时要重新确认站方该怎么写。
    """
    from urllib.robotparser import RobotFileParser

    rp = RobotFileParser()
    rp.parse(f"User-agent: {USER_AGENT}\nDisallow: /\n".splitlines())
    assert rp.can_fetch(USER_AGENT, f"{BASE}/blog/a") is True, "完整 UA 串竟然匹配上了？"

    rp2 = RobotFileParser()
    rp2.parse("User-agent: codemax-platform\nDisallow: /\n".splitlines())
    assert rp2.can_fetch(USER_AGENT, f"{BASE}/blog/a") is False


@pytest.mark.asyncio
async def test_robots_fetched_once_per_domain_not_per_page():
    """同域抓 5 篇只该拉一次 robots.txt —— 每篇都拉本身就是对目标站的额外压力。"""
    t, seen = transport(200, "User-agent: *\nDisallow: /nope\n")
    for i in range(5):
        await fetch(f"{BASE}/blog/{i}", transport=t)
    assert sum(1 for u in seen if u.endswith("/robots.txt")) == 1, seen


@pytest.mark.asyncio
async def test_robots_of_each_domain_is_fetched_separately():
    t, seen = transport(200, "User-agent: *\nDisallow: /nope\n")
    await fetch(f"{BASE}/blog/a", transport=t)
    await fetch(f"{OTHER}/blog/a", transport=t)
    robots = [u for u in seen if u.endswith("/robots.txt")]
    assert len(robots) == 2, "两个域名各自的 robots 都要读"
    assert FAKE_IP in robots[0] and OTHER_IP in robots[1]


# ============================================================ SSRF 不能被伪装


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/x", "http://169.254.169.254/latest/meta-data/"])
async def test_ssrf_rejection_is_not_masked_as_robots_error(url):
    """**回归**：SSRF 校验失败必须原样抛 `CrawlError`。

    第一版实现里 `_load_robots` 用 `except Exception` 兜底，把 SSRF 拒绝
    一起吞掉、转成了 `RobotsDisallowed` —— 「这个地址不许访问」被伪装成
    「robots 不让抓」，排障时看不出是 SSRF 拦截，安全告警也就丢了。
    """
    t, _ = transport(200, "User-agent: *\nDisallow:\n")
    with pytest.raises(CrawlError):
        await fetch(url, transport=t)


# ============================================================ 抓取间隔


@pytest.mark.asyncio
async def test_same_domain_requests_are_spaced_out():
    """同一域名连续抓取必须排队，不能一口气全发出去。"""
    t, _ = transport(404)
    start = time.perf_counter()
    for i in range(4):
        await fetch(f"{BASE}/blog/{i}", transport=t)
    elapsed = time.perf_counter() - start
    # 4 次请求之间有 3 个间隔，每个 >= 0.05s
    assert elapsed >= 3 * politeness.DEFAULT_MIN_INTERVAL * 0.9, (
        f"4 次同域请求只花了 {elapsed * 1000:.0f} ms，没在限速"
    )


@pytest.mark.asyncio
async def test_crawl_delay_from_robots_is_picked_up(monkeypatch):
    """目标站写了 Crawl-delay 就用它的值，而不是我们的默认值。

    只断言**读到的值**，不真等：标准库的 Crawl-delay 只能是整数（见下条测试），
    最小的有效值就是 1 秒，等一轮太慢，而且等待行为已由
    `test_same_domain_requests_are_spaced_out` 覆盖。
    """
    t, _ = transport(200, "User-agent: *\nDisallow:\nCrawl-delay: 3\n")
    await fetch(f"{BASE}/blog/a", transport=t)
    state = politeness.state_for(f"{BASE}/blog/a")
    assert state.crawl_delay == 3.0
    assert politeness.min_interval_for(state) == 3.0


def test_fractional_crawl_delay_is_silently_ignored_by_stdlib():
    """**标准库限制**：`Crawl-delay` 只接受整数（`isdigit()` → `int()`），
    `0.5` 这类小数值会被静默丢弃、回落到我们的默认间隔。

    记在这里是因为它是个**看不见的**行为差异：robots.txt 里明明写了值，
    我们却按默认间隔跑。若将来要支持小数，得自己解析 robots.txt 而不是用
    `urllib.robotparser`（TD-169）。
    """
    from urllib.robotparser import RobotFileParser

    rp = RobotFileParser()
    rp.parse("User-agent: *\nDisallow:\nCrawl-delay: 0.5\n".splitlines())
    assert rp.crawl_delay("codemax-platform") is None
    rp2 = RobotFileParser()
    rp2.parse("User-agent: *\nDisallow:\nCrawl-delay: 5\n".splitlines())
    assert rp2.crawl_delay("codemax-platform") == 5


@pytest.mark.asyncio
async def test_different_domains_are_not_throttled_against_each_other():
    """限速是**按域**的：抓 A 站不该拖慢抓 B 站。"""
    t, _ = transport(404)
    start = time.perf_counter()
    await asyncio.gather(
        fetch(f"{BASE}/a", transport=t),
        fetch(f"{OTHER}/a", transport=t),
    )
    elapsed = time.perf_counter() - start
    assert elapsed < politeness.DEFAULT_MIN_INTERVAL, (
        f"两个不同域名不该互相等待，却花了 {elapsed * 1000:.0f} ms"
    )


# ============================================================ 全局并发上限


@pytest.mark.asyncio
async def test_global_concurrency_is_capped(monkeypatch):
    """同时在飞的请求数不得超过上限。"""
    monkeypatch.setattr(politeness, "DEFAULT_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(politeness, "MAX_CONCURRENCY", 2)
    politeness.reset_cache()

    inflight = 0
    peak = 0

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=PAGE)

    async def slow_handle(request: httpx.Request) -> httpx.Response:
        nonlocal inflight, peak
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.02)
        inflight -= 1
        return httpx.Response(200, text=PAGE)

    t = httpx.MockTransport(slow_handle)
    await asyncio.gather(*[fetch(f"{BASE}/blog/{i}", transport=t) for i in range(8)])
    assert peak <= 2, f"同时在飞的请求峰值 {peak}，超过上限 2"


# ============================================================ 缓存与状态


def test_reset_cache_clears_both_states_and_semaphore():
    politeness.state_for(f"{BASE}/x")
    politeness._get_semaphore()
    politeness.reset_cache()
    assert politeness._states == {}
    assert politeness._semaphore is None


def test_min_interval_prefers_crawl_delay():
    s = politeness._DomainState(crawl_delay=7.5)
    assert politeness.min_interval_for(s) == 7.5
    assert politeness.min_interval_for(politeness._DomainState()) == politeness.DEFAULT_MIN_INTERVAL
    # Crawl-delay 为 0 是无效值，不能当成「不用等」
    assert politeness.min_interval_for(politeness._DomainState(crawl_delay=0.0)) == (
        politeness.DEFAULT_MIN_INTERVAL
    )
