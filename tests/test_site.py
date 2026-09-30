"""S2-02-1 测试：TOOLS 清单驱动的 SSR 页面 / TDK / 导航 / sitemap / robots。

核心是"清单即真相"：页面清单改一处，路由、导航、sitemap 必须同步跟上，
所以断言全部拿 app/site.py 的 PAGES / TOOLS 去比对真实响应。
"""
import re

import pytest

from app.config import settings
from app.site import PAGES, TOOLS, page_title
from main import app
from tests.conftest import iter_app_routes

BASE = settings.SITE_BASE_URL.rstrip("/")


def test_site_base_url_is_absolute():
    assert BASE.startswith("http"), "SITE_BASE_URL 必须是绝对 URL，否则 sitemap/robots 无效"


def test_every_page_route_registered():
    """清单里每个页面都得有对应路由（路由由清单生成，防止有人手工删掉）。"""
    registered = {getattr(route, "path", None) for route in iter_app_routes(app.routes)}
    assert {p.path for p in PAGES} <= registered


async def test_home_lists_every_tool(client):
    r = await client.get("/")
    assert r.status_code == 200
    for t in TOOLS:
        assert f'href="{t.path}"' in r.text
        assert t.title in r.text


@pytest.mark.parametrize("page", PAGES, ids=[p.key for p in PAGES])
async def test_page_tdk_and_nav(client, page):
    r = await client.get(page.path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert f"<title>{page_title(page)}</title>" in r.text
    assert f'<meta name="description" content="{page.description}"' in r.text
    assert f'<meta name="keywords" content="{page.keywords}"' in r.text
    assert f'<link rel="canonical" href="{BASE}{page.path}"' in r.text
    for t in TOOLS:  # 每个页面的导航都由同一份清单渲染
        assert f'href="{t.path}"' in r.text


async def test_sitemap_matches_page_list_exactly(client):
    r = await client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    locs = re.findall(r"<loc>(.*?)</loc>", r.text)
    assert locs == [BASE + p.path for p in PAGES], "sitemap 必须与页面清单不多不少"
    registered = {getattr(route, "path", None) for route in iter_app_routes(app.routes)}
    for loc in locs:
        assert loc.removeprefix(BASE) in registered, f"{loc} 不是已注册路由"


async def test_robots_points_to_sitemap(client):
    r = await client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "User-agent: *" in r.text
    assert f"Sitemap: {BASE}/sitemap.xml" in r.text


# ---------------------------------------------------------------- TD-333：浏览器打开不存在的地址给站内 404 页

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@pytest.mark.parametrize("path", ["/nope", "/tools/nope", "/tools/er/extra"])
async def test_unknown_address_opened_in_browser_gets_site_404_page(client, path):
    """原来浏览器打开任何不存在的地址都只看到一行 `{"detail":"Not Found"}`，没有导航、回不去首页。
    现在是带顶栏导航的站内页，状态码仍是 404（搜索引擎不收录），且不回显请求路径（路径由访问者任意构造）。"""
    r = await client.get(path + "-<b>injected</b>", headers={"Accept": BROWSER_ACCEPT})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "<title>页面不存在 - " in body and '<h2 class="page-heading">页面不存在</h2>' in body
    assert '<a class="cta" href="/">返回首页</a>' in body
    for tool in TOOLS:  # 顶栏导航齐全：走的是 page_context，不是手拼的上下文
        assert f'href="{tool.path}"' in body
    assert "injected" not in body and "nope" not in body
    assert r.headers.get("x-content-type-options") == "nosniff", "404 页同样经过安全头中间件"

    head = await client.head(path, headers={"Accept": BROWSER_ACCEPT})
    assert head.status_code == 404 and head.headers["content-type"].startswith("text/html")


async def test_non_browser_404s_stay_json(client):
    """只有「GET/HEAD + Accept 含 text/html + 没有路由匹配」才换成 HTML，其余 404 的 JSON 不变：
    页面脚本的 fetch 默认 Accept 是 `*/*`；非 GET 请求；业务代码写了中文原因的 404（前端要读 detail）。"""
    for headers in ({}, {"Accept": "*/*"}, {"Accept": "application/json"}):
        r = await client.get("/nope", headers=headers)
        assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), headers
    r = await client.post("/nope", headers={"Accept": BROWSER_ACCEPT})
    assert (r.status_code, r.json()) == (404, {"detail": "Not Found"})
    # 默认 SHOP_PAY_MODE=wechat：模拟收银台是业务 404，带中文原因
    r = await client.get("/shop/mock-pay", headers={"Accept": BROWSER_ACCEPT})
    assert r.status_code == 404 and r.json()["detail"].startswith("模拟支付通道未开启")
    # 405 等其他状态码也不受影响
    r = await client.post("/", headers={"Accept": BROWSER_ACCEPT})
    assert r.status_code == 405 and r.json() == {"detail": "Method Not Allowed"}
