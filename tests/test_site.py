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
