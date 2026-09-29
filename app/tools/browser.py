"""动态渲染接口（当前安全停用）。

render 保留 URL/robots/节流预检查与返回 Page 的接口；_goto 不启动 Chromium，总是抛 BrowserUnavailable。
安装 Playwright 只改变报错文字（缺包时给安装命令），不能解除停用。

恢复前需要网络级隔离的渲染服务：DNS 预检查与 Playwright 的请求拦截（route.continue_）都不能
固定 Chromium 实际连接的地址。原来留在这里的请求拦截 helper 与渲染超时常量没有任何调用方，
TD-327 删除，免得被当成「已有隔离、已有等待策略」的依据。"""
from __future__ import annotations

import httpx

from . import politeness
from .crawler import MAX_BYTES, USER_AGENT, CrawlError, Page, assert_public_url, robots_fetcher


class BrowserUnavailable(RuntimeError):
    """动态渲染用不了：当前一律如此（安全停用）；没装 playwright 时报错里另给安装命令。"""


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

    # 2) robots + 3) 全局并发闸与按域限速：动态抓取也是抓取，不能因为换了引擎就绕过站方的意愿
    #    （TD-133）；渲染比 httpx 贵得多，更不能放开刷。与静态抓取共用 polite_access（TD-290）。
    async with politeness.polite_access(url, USER_AGENT, robots_fetcher(transport)):
        # 4) 到这里才碰浏览器。
        page = await _goto(url)

    # 5) **校验重定向之后的最终落点**。浏览器自己会跟随重定向，所以入口 URL 是公网、
    #    渲染完落在内网是完全可能的 —— 只校验入口 URL 等于给 SSRF 留了后门
    #    （与 crawler._request 逐跳校验同一个理由，见 TD-196）。
    #    放在 `_goto` 外面是刻意的：`_goto` 只管取页面（恢复后浏览器的各种失败都应报成
    #    BrowserUnavailable → 503），而 SSRF 是安全问题，必须原样抛 CrawlError 让上层返回 400。
    await assert_public_url(page.url)

    # 体积上限放在 `render` 而不是 `_goto` 里：这样它不需要真浏览器就能被测到
    # （与 crawler.fetch 的 MAX_BYTES 同一个上限，渲染后的 DOM 往往比原始 HTML 更大）。
    if len(page.html) > MAX_BYTES:
        raise CrawlError(f"渲染后的页面过大：{len(page.html)} 字节，超过上限 {MAX_BYTES}")
    return page


async def _goto(url: str) -> Page:
    """取渲染后页面的唯一一步（测试替换它来模拟浏览器）。当前不启动浏览器，总是抛 BrowserUnavailable：
    没装 playwright 时给安装命令，装了也只说明已安全停用。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:  # 连包都没装
        raise BrowserUnavailable(
            # 原文是「装上它并下载浏览器后才能用 dynamic 抓取」，读来像装上就能用；实际装上后只会得到停用说明（TD-327）
            "未安装 playwright。动态渲染目前已安全停用，安装后也不能使用，需先部署网络隔离的渲染服务；"
            "届时的依赖安装命令：\n"
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
