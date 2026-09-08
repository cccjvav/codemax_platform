"""TD-191：动态页面抓取（Playwright）。

## 这个文件能在沙箱里测什么、不能测什么

沙箱**下不到浏览器二进制**（`cdn.playwright.dev` 与 `playwright.azureedge.net` 实测
均 HTTP=000），所以真正 `page.goto()` 的那一步在这里跑不了。

但 `browser.render()` 的顺序是 **SSRF 校验 → robots 判定 → 限速 → 才启动浏览器**，
前三步不碰浏览器，因此**它们在这里是真跑的、不是打桩**。本文件里只有 `_goto`
（唯一需要浏览器的一步）被替换掉。

真正启动浏览器的那条测试放在最后，默认跳过，用 `RUN_BROWSER_TESTS=1` 打开 ——
它必须在装了浏览器的机器上跑（Windows 上 `playwright install chromium` 通常直接可用）。
"""
import json
import os

import httpx
import pytest
from sqlalchemy import select

from app.models import Article
from app.routers import admin as admin_router
from app.tools import browser, crawler, politeness
from app.tools.browser import BrowserUnavailable, _goto, render
from app.tools.crawler import CrawlError
from app.tools.llm import get_llm
from main import app
from tests.conftest import TestSession

FAKE_IP = "93.184.216.34"  # 公网字面量 IP：is_global=True，DNS 解析不需要联网
BASE = f"http://{FAKE_IP}"
URL = f"{BASE}/spa-article"

_REAL_ASYNC_CLIENT = httpx.AsyncClient  # 基类要固定，不能跟着 monkeypatch 漂

SELECTORS = {"title": "h1.t", "author": "", "published_at": "", "content": "div.body"}

# 一个真实的 SPA 空壳：静态抓回来只有一个 #root，正文全在前端 JS 里。
# 静态路径拿到它**提不出正文**（422），动态路径渲染后才有内容（200）——
# 这个对比正是本模块存在的理由，所以两条路径的假响应必须成对设计。
STATIC_HTML = """
<html><body><div id="root"></div><script>/* 正文由 JS 注入 */</script></body></html>
"""

RENDERED_HTML = """
<html><body>
  <article>
    <h1 class="t">只有渲染后才出现的标题</h1>
    <div class="body"><p>这段正文由前端 JS 注入。</p></div>
  </article>
</body></html>
"""


class FakeLLM:
    def __init__(self, reply: str | None = None):
        self.reply = reply if reply is not None else json.dumps(SELECTORS, ensure_ascii=False)

    async def chat(self, system: str, user: str) -> str:
        return self.reply


@pytest.fixture
def net(monkeypatch):
    """把 crawler 里创建的 AsyncClient 接到 MockTransport 上（只影响 robots 抓取）。"""

    def install(robots: tuple[int, str] = (404, "")):
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(robots[0], text=robots[1])
            return httpx.Response(200, text=STATIC_HTML)

        transport = httpx.MockTransport(handle)

        class MockedClient(_REAL_ASYNC_CLIENT):
            def __init__(self, **kw):
                kw["transport"] = transport
                super().__init__(**kw)

        monkeypatch.setattr(crawler.httpx, "AsyncClient", MockedClient)

    # robots 判定按 origin 缓存 1 小时且是模块级的，不清会跨测试串（TD-133）
    politeness.reset_cache()
    install()
    yield install
    politeness.reset_cache()


@pytest.fixture(autouse=True)
def fake_llm():
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    yield
    app.dependency_overrides.pop(get_llm, None)


@pytest.fixture
def no_browser(monkeypatch):
    """把唯一需要浏览器的那一步换成返回预设页面，其余逻辑（SSRF/robots/限速）保持真跑。"""
    calls: list[str] = []

    async def fake_goto(url: str):
        calls.append(url)
        return crawler.Page(url=url, status=200, html=RENDERED_HTML)

    monkeypatch.setattr(browser, "_goto", fake_goto)
    return calls


async def _admin(client, username: str = "boss"):
    await client.post("/auth/register", json={"username": username, "password": "pw123456"})
    r = await client.post("/auth/login", data={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    from app.models import User

    async with TestSession() as s:
        u = await s.scalar(select(User).where(User.username == username))
        u.role = 1
        await s.commit()
    return client


# ============================================== render 的安全前置（不需要浏览器）


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.9/x",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",  # 云厂商元数据
        "file:///etc/passwd",
    ],
)
async def test_render_blocks_bad_url_before_touching_browser(net, no_browser, url):
    """**最关键的一条**：浏览器同样会去访问调用方给的地址，SSRF 校验必须在启动浏览器之前。

    断言 `no_browser` 一次都没被调用 —— 证明拦截发生在启动浏览器**之前**，
    而不是「浏览器打开了但失败了」。
    """
    with pytest.raises(CrawlError):
        await render(url)
    assert no_browser == [], "SSRF 校验必须在启动浏览器之前完成"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://10.0.0.9/x", "http://169.254.169.254/latest/meta-data/"])
async def test_render_ssrf_does_not_depend_on_robots_layer(net, no_browser, monkeypatch, url):
    """`render` 里的 SSRF 校验必须**自己**站得住，不能靠 robots 那层顺带做。

    为什么单独加这条：把 `render` 里的 `assert_public_url` 删掉，上一条测试**照样通过**
    —— 因为 `politeness.check_allowed` 会去抓 robots.txt，而 `_request` 内部也做 SSRF 校验，
    于是拦截「碰巧」还是发生了（实测该变异体存活）。但那是**巧合性的防御**：
    将来谁改了 robots 逻辑、加了缓存预热、或让某个域跳过 robots，SSRF 就静默消失。
    这条把 robots 层整个换成空操作，专门钉住 `render` 自己那一行。
    """

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(politeness, "check_allowed", noop)
    with pytest.raises(CrawlError):
        await render(url)
    assert no_browser == []


@pytest.mark.asyncio
async def test_render_respects_robots_disallow(net, no_browser):
    """换了引擎不等于可以无视站方意愿（TD-133）。"""
    net(robots=(200, "User-agent: *\nDisallow: /\n"))
    with pytest.raises(politeness.RobotsDisallowed):
        await render(URL)
    assert no_browser == [], "robots 判定也必须在启动浏览器之前"


@pytest.mark.asyncio
async def test_render_reaches_browser_when_allowed(net, no_browser):
    """反过来也要证明：正常 URL 确实走到了浏览器那一步（否则前两条可能是空跑）。"""
    page = await render(URL)
    assert no_browser == [URL]
    assert page.html == RENDERED_HTML


@pytest.mark.asyncio
async def test_rendered_page_over_size_limit_rejected(net, monkeypatch):
    """渲染后的 DOM 常常比原始 HTML 更大，所以要有和静态抓取同一个上限。"""
    from app.tools.crawler import MAX_BYTES

    async def huge(url: str):
        return crawler.Page(url=url, status=200, html="x" * (MAX_BYTES + 1))

    monkeypatch.setattr(browser, "_goto", huge)
    with pytest.raises(CrawlError) as e:
        await render(URL)
    assert "过大" in str(e.value)


@pytest.mark.asyncio
async def test_launch_failure_is_translated_not_leaked(net, monkeypatch):
    """浏览器启动失败必须被转成 `BrowserUnavailable`，**不能漏成端点的 500**。

    用一个假的 `playwright.async_api` 模块模拟启动失败 —— 不需要网络也不需要浏览器，
    所以在 CI 上也能跑。这条钉的是「异常翻译」这个契约本身；
    真实的「没下浏览器」报错文本已在本机实测过（见 TECH_DECISIONS TD-191）。
    """
    import sys
    import types

    fake = types.ModuleType("playwright.async_api")

    def async_playwright():
        raise RuntimeError("BrowserType.launch: Executable doesn't exist at /x/chrome")

    fake.async_playwright = async_playwright
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)

    with pytest.raises(BrowserUnavailable) as e:
        await _goto(URL)
    assert "playwright install chromium" in str(e.value)


@pytest.mark.asyncio
async def test_goto_reports_missing_playwright_with_install_command():
    """没装 playwright 时要给出可执行的命令，而不是让人猜。"""
    assert browser.browser_available() is False, "本沙箱未装 playwright，前提成立"
    with pytest.raises(BrowserUnavailable) as e:
        await _goto(URL)
    assert "pip install playwright" in str(e.value)
    assert "playwright install chromium" in str(e.value)


# ============================================== 端点接线


@pytest.mark.asyncio
async def test_dynamic_flag_uses_rendered_html(client, net, no_browser):
    await _admin(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL, "dynamic": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dynamic"] is True
    assert body["title"] == "只有渲染后才出现的标题"
    assert no_browser == [URL], "dynamic=true 必须走浏览器渲染"

    async with TestSession() as s:
        row = await s.scalar(select(Article).where(Article.url == URL))
    assert row is not None
    assert row.content == "这段正文由前端 JS 注入。"


@pytest.mark.asyncio
async def test_static_path_never_touches_browser(client, net, no_browser):
    """默认不渲染 —— 渲染比一次 GET 贵一个数量级，不该是默认行为。"""
    await _admin(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    # 静态抓回来是 SPA 空壳，提不出正文 → 422；重点是**没碰浏览器**
    assert r.status_code == 422, r.text
    assert "内容提取失败" in r.json()["detail"]
    assert no_browser == [], "dynamic 未置真时不得启动浏览器"


@pytest.mark.asyncio
async def test_dynamic_records_final_url_after_redirect(client, net, monkeypatch):
    """入库的 url / source_site 要用**重定向之后的最终地址**，否则来源对不上。"""

    async def fake_goto(url: str):
        return crawler.Page(url=f"http://{FAKE_IP}/final-path", status=200, html=RENDERED_HTML)

    monkeypatch.setattr(browser, "_goto", fake_goto)
    await _admin(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL, "dynamic": True})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == f"http://{FAKE_IP}/final-path"
    assert r.json()["source_site"] == FAKE_IP


@pytest.mark.parametrize("evil", ["http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data/"])
async def test_dynamic_final_url_after_redirect_is_revalidated(client, net, monkeypatch, evil):
    """动态路径也要校验**重定向之后的最终落点**，不能只校验最初那个 URL。

    浏览器自己会跟随重定向，所以入口 URL 是公网、渲染完落在内网是完全可能的。
    沙箱下不到浏览器二进制，这里用替身把 `_goto` 的最终 URL 换成内网地址，
    验的是「拿到最终 URL 之后有没有再校验一次」这段逻辑本身。
    """

    async def fake_goto(url: str):
        return crawler.Page(url=evil, status=200, html=RENDERED_HTML)

    monkeypatch.setattr(browser, "_goto", fake_goto)
    await _admin(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL, "dynamic": True})
    assert r.status_code == 400, f"最终落点 {evil} 必须被拒，实际 {r.status_code} {r.text[:200]}"

    async with TestSession() as s:
        n = len((await s.execute(select(Article))).scalars().all())
    assert n == 0, "被拒的页面绝不能入库"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://10.0.0.9/x", "http://169.254.169.254/latest/meta-data/"])
async def test_dynamic_ssrf_still_blocked_at_endpoint(client, net, no_browser, url):
    """dynamic=true 不能变成绕过 SSRF 的后门。"""
    await _admin(client)
    r = await client.post("/admin/articles/ingest", json={"url": url, "dynamic": True})
    assert r.status_code == 400, r.text
    assert no_browser == []


@pytest.mark.asyncio
async def test_browser_unavailable_maps_to_503(client, net, monkeypatch):
    """服务端没装浏览器 → 503（本站能力缺失），不是 400 也不是 500。"""

    async def boom(url: str):
        raise BrowserUnavailable("未安装 playwright")

    monkeypatch.setattr(admin_router, "render", boom)
    await _admin(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL, "dynamic": True})
    assert r.status_code == 503, r.text
    assert "playwright" in r.json()["detail"]


@pytest.mark.asyncio
async def test_non_admin_still_blocked_on_dynamic(client, net, no_browser):
    """加了 dynamic 不能顺带把鉴权放宽。"""
    await client.post("/auth/register", json={"username": "alice", "password": "pw123456"})
    await client.post("/auth/login", data={"username": "alice", "password": "pw123456"})
    r = await client.post("/admin/articles/ingest", json={"url": URL, "dynamic": True})
    assert r.status_code == 403
    assert no_browser == []


# ============================================== 真浏览器（默认跳过）


@pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1" or not browser.browser_available(),
    reason="需要真浏览器：pip install playwright && playwright install chromium，"
    "再以 RUN_BROWSER_TESTS=1 运行。本沙箱下不到浏览器二进制（CDN 不可达）。",
)
@pytest.mark.asyncio
async def test_real_browser_renders_a_page():
    """**唯一需要真浏览器的一条**，沙箱内不跑。

    在本机验证：
        set RUN_BROWSER_TESTS=1
        .venv\bin\python -m pytest tests/test_dynamic_crawl.py -q

    ⚠️ 这条不只是「跑一下看看」：`_goto` 的内部（`pg.url` 取重定向后的最终地址、
    `pg.content()` 取渲染后 DOM）**在沙箱里没有任何覆盖** —— 端点那几条测试把整个
    `_goto` 换成了假的，所以实测把 `final_url = pg.url` 改成 `final_url = url`
    是**存活的变异体**。这条就是补这个洞的，请在合并前于本机跑一次。
    """
    # http:// 会被 example.com 301 到 https:// —— 正好用它验「取的是重定向后的最终地址」
    page = await _goto("http://example.com/")
    assert "<html" in page.html.lower()
    assert page.url.startswith("https://example.com"), (
        f"应记录重定向后的最终地址，实际 {page.url!r}"
    )
