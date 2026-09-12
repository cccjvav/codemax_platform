"""TD-191：动态页面渲染（Playwright）—— 给 httpx 抓不到正文的 SPA 站点兜底。

**为什么是 Playwright 而不是 Selenium**：用户选型（2026-09-03）。补充一条实测事实：
Python 版 Playwright 的 wheel 里**自带** `playwright/driver/node`（123.7 MB，权限 100755），
`_driver.py` 用 `os.getenv("PLAYWRIGHT_NODEJS_PATH", str(driver_path / "node"))` 默认就调它 ——
空 PATH 下实测能独立运行（v24.18.1）。所以**不需要另外安装 Node.js**，pip 装完即可。

**但它不是 requirements.txt 里的必需依赖**，理由：wheel 47 MB、还要再下 ~150 MB 浏览器，
而绝大多数目标站点用 `httpx` + `BeautifulSoup` 就够了。没装时端点返回 503 并给出安装命令，
而不是在启动时炸掉整个应用。

## 顺序是有讲究的（也是本模块可测的原因）

`render()` 里 **SSRF 校验 → robots 判定 → 限速 → 才启动浏览器**。前三步不碰浏览器，
所以它们在沙箱里就能真跑真测（沙箱下不到浏览器二进制：`cdn.playwright.dev` 与
`playwright.azureedge.net` 实测均 HTTP=000）。真正需要浏览器的只有最后的 `_goto()`。

⚠️ 浏览器**同样会去访问调用方给的地址**，所以 `assert_public_url` 绝不能省 ——
少了它，`dynamic=True` 就等于开了一个能访问 169.254.169.254 的 SSRF 口子。
"""
from __future__ import annotations

import httpx

from . import politeness
from .crawler import MAX_BYTES, TIMEOUT, USER_AGENT, CrawlError, Page, _request, assert_public_url

# 渲染等待策略：networkidle 对 SPA 最有效，但有些站点会一直有心跳请求导致永不 idle，
# 所以给一个上限（Playwright 的 timeout），超时就按当前 DOM 取 —— 拿到半页也比拿不到强。
RENDER_TIMEOUT_MS = int(TIMEOUT * 1000)


class BrowserUnavailable(RuntimeError):
    """浏览器用不了：没装 playwright，或装了但没下载浏览器二进制。"""


def browser_available() -> bool:
    """playwright 这个包**装了没有**。

    ⚠️ 这**不代表浏览器二进制已下载** —— 那要另外跑 `playwright install chromium`。
    二进制缺失要到真正 launch 时才暴露，由 `_goto` 转成 `BrowserUnavailable`。
    这里只做便宜的 import 探测，避免为了探测而启动一次 node 驱动进程。
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


async def render(url: str, *, transport: httpx.BaseTransport | None = None) -> Page:
    """用无头浏览器渲染页面，返回与 `crawler.fetch` 同形状的 `Page`。

    复用 `Page` 是刻意的：动态路径要带上**重定向之后的最终 URL**，
    否则入库的 `url` 与 `source_site` 会与页面实际来源不符。

    `transport` 只为测试注入 `httpx.MockTransport` 而存在，与 `crawler.fetch` 同一套路；
    它只影响 robots.txt 的抓取，不影响浏览器本身。
    """
    # 1) SSRF：必须在启动浏览器**之前**。浏览器不会替你做这个判断。
    await assert_public_url(url)

    # 2) robots：动态抓取也是抓取，不能因为换了引擎就绕过站方的意愿（TD-133）。
    async def fetch_text(robots_url: str) -> tuple[int, str]:
        r = await _request(robots_url, transport=transport, max_bytes=politeness.ROBOTS_MAX_BYTES)
        return r.status_code, r.text

    await politeness.check_allowed(url, USER_AGENT, fetch_text)

    # 3) 限速 + 全局并发闸：渲染比 httpx 贵得多，更不能放开刷。
    state = politeness.state_for(url)
    async with politeness._get_semaphore():
        await politeness.throttle(url, state)
        # 4) 到这里才碰浏览器。
        page = await _goto(url)

    # 5) **校验重定向之后的最终落点**。浏览器自己会跟随重定向，所以入口 URL 是公网、
    #    渲染完落在内网是完全可能的 —— 只校验入口 URL 等于给 SSRF 留了后门
    #    （与 crawler._request 逐跳校验同一个理由，见 TD-196）。
    #    放在 `_goto` 外面是刻意的：`_goto` 的 except 会把异常洗成 BrowserUnavailable，
    #    而 SSRF 是安全问题，必须原样抛 CrawlError 让上层返回 400 而不是 503。
    await assert_public_url(page.url)

    # 体积上限放在 `render` 而不是 `_goto` 里：这样它不需要真浏览器就能被测到
    # （与 crawler.fetch 的 MAX_BYTES 同一个上限，渲染后的 DOM 往往比原始 HTML 更大）。
    if len(page.html) > MAX_BYTES:
        raise CrawlError(f"渲染后的页面过大：{len(page.html)} 字节，超过上限 {MAX_BYTES}")
    return page


async def _goto(url: str) -> Page:
    """真正启动浏览器取渲染后的 HTML。这是本模块唯一需要浏览器二进制的一步。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:  # 连包都没装
        raise BrowserUnavailable(
            "未安装 playwright。装上它并下载浏览器后才能用 dynamic 抓取：\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from e

    # Direct Chromium egress is intentionally disabled until an isolated network renderer
    # is deployed. DNS prechecks and route.continue_ do not pin Chromium connections.
    # Keep the import above so an absent optional dependency still gives its precise diagnosis.
    del async_playwright
    raise BrowserUnavailable(
        "playwright 动态渲染已安全停用：请先部署具备网络级私网隔离的渲染服务；"
        "当前可使用静态抓取。仅安装浏览器不能解除此限制。"
    )


async def _abort_non_public(route) -> None:
    """Playwright 路由拦截器：非公网目标直接 abort，不建立连接。

    刻意不把 `CrawlError` 往外抛 —— 拦截器里抛出的异常 Playwright 会吞掉，
    抛了也没用。放行/拦截的结果由 `render()` 里对最终 URL 的那次校验统一表达成 400。
    """
    try:
        await assert_public_url(route.request.url)
    except CrawlError:
        await route.abort()
        return
    await route.continue_()
