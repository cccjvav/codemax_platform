"""动态渲染接口（当前安全停用）。

render 保留 URL/robots/节流预检查；实际 _goto 不启动 Chromium，抛 BrowserUnavailable。
安装 Playwright 仅改变依赖诊断，不能解除停用。恢复前需隔离网络出口并验证真实浏览器行为。
保留的 _abort_non_public 只能预检查请求，不会固定 Chromium 的实际连接地址。"""
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
    """仅检查 Playwright Python 包能否导入；不代表浏览器二进制存在或动态功能可用。"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


async def render(url: str, *, transport: httpx.BaseTransport | None = None) -> Page:
    """预检查动态抓取并调用 _goto；当前实际路径最终抛 BrowserUnavailable。

    接口预期返回 Page，以最终 URL 和 HTML 与静态解析对接。transport 只用于 robots 请求测试。
    测试可替换 _goto 验证外层约束，但这不证明生产浏览器已启动或网络隔离已完成。"""
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
    """保留的请求预检查 helper；校验失败 abort，否则 continue。

    当前停用路径不注册它。允许 continue 不证明连接地址已固定，不能代替网络级隔离。"""
    try:
        await assert_public_url(route.request.url)
    except CrawlError:
        await route.abort()
        return
    await route.continue_()
