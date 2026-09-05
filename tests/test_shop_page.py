# tests/test_shop_page.py
#
# S2-02-2「免费引流 → 商业变现」的转化路径。
#
# 这组测试的重点不是"页面能打开"，而是钉住三条容易在后续改动中被破坏的不变式：
#
# 1. **轮询订单状态绝不能烧掉一次性下载。** `POST /shop/download/{order_no}` 成功后
#    订单永久变 `downloaded`，同一单不能再下载（后端 CAS，见 shop.py 的 `won`）。
#    下单页每 3 秒轮询一次 `GET /shop/orders/{no}`，谁要是图省事拿 download 当状态查询，
#    用户的货就没了 —— 而且现象是"付了钱下载不了"，极难排查。
# 2. **同意页零脚本。** base.html 现在带了全站登录模块，但 OAuth 同意页必须一个脚本都没有
#    （`auth_ui=False`）。这条在 test_oauth_consent.py 里，这里只补它的对偶：
#    其它页面确实拿到了登录入口。
# 3. **二维码是内联 SVG，不引入任何外部请求。** CSP 的 `connect-src 'self'` 会拦掉
#    第三方请求，所以二维码不能靠 CDN 画。
"""S2-02-2：商城落地页、订单状态轮询与二维码。"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Order, User
from app.routers import shop
from tests.conftest import TestSession

# product fixture 在 conftest 里（pytest 自动发现），这里只需要这三个 helper
from tests.test_download import auth_headers, make_order, status_of

_seq = 0


@pytest.fixture
def mock_mode(monkeypatch):
    """走模拟收银台（TD-124）。

    ⚠️ 打 `/shop/orders` 的测试**必须**带这个 fixture：默认 `SHOP_PAY_MODE=wechat`
    而沙箱没有商户号，下单会直接 503，很容易被误判成代码 bug。
    """
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")

    async def no_wechat(cfg, **kw):
        raise AssertionError("模拟模式不该调用微信支付")

    monkeypatch.setattr(shop, "native_prepay", no_wechat)


# ---------------------------------------------------------------- 落地页


async def test_shop_page_renders_product(client):
    """落地页必须把商品名与价格渲染出来，且价格来自配置而不是写死。"""
    r = await client.get("/shop")
    assert r.status_code == 200
    assert settings.SHOP_PRODUCT_NAME in r.text
    # 金额以「分」存储，页面必须换算成元
    assert f"¥{settings.SHOP_PRODUCT_AMOUNT / 100:.2f}" in r.text
    assert "btn-buy" in r.text, "落地页必须有下单按钮"


async def test_shop_page_warns_about_one_time_download(client):
    """一次性下载是会让用户丢货的约束，必须在页面上说清楚。"""
    assert "一次性有效" in (await client.get("/shop")).text


async def test_shop_page_is_in_sitemap(client):
    """加进 PAGES 就该自动进 sitemap（SEO 收录）。"""
    urls = re.findall(r"<loc>(.*?)</loc>", (await client.get("/sitemap.xml")).text)
    assert any(u.endswith("/shop") for u in urls), urls


async def test_shop_page_not_in_tool_nav(client):
    """SHOP 进 PAGES 但**不进 TOOLS** —— 商业页不该混在免费工具导航里。"""
    from app.site import TOOLS

    assert all(t.path != "/shop" for t in TOOLS)
    # 首页的工具卡片区只遍历 TOOLS，所以不该出现指向 /shop 的卡片
    home = (await client.get("/")).text
    assert home.count('class="card"') == len(TOOLS)


@pytest.mark.parametrize("path", ["/", "/shop", "/tools/er", "/tools/mermaid", "/tools/drawio"])
async def test_every_page_has_global_entry_points(client, path):
    """转化路径的前提：任何页面都能走到登录与商城。

    改动前 base.html 的 header 只有工具导航 —— 从搜索进来的游客**找不到任何登录入口**，
    也没有页脚。这条测试就是把"全站都有入口"钉住。
    """
    html = (await client.get(path)).text
    assert "<footer>" in html, f"{path} 缺页脚"
    assert 'id="btn-auth"' in html, f"{path} 缺登录入口"
    assert 'href="/shop"' in html, f"{path} 缺商城入口"
    assert "/static/auth.js" in html, f"{path} 没加载共享登录模块"


async def test_auth_js_is_served_and_defines_module(client):
    """共享登录模块是外部文件（不是内联脚本），必须真的能取到。

    做成外部文件的原因：base.html 也是 OAuth 同意页的父模板，
    而那页必须零内联脚本（见 test_oauth_consent.py）。
    """
    r = await client.get("/static/auth.js")
    assert r.status_code == 200
    assert "CodeMaxAuth" in r.text
    assert "/auth/login" in r.text
    assert "/auth/register" in r.text, "注册入口也在这个模块里（此前全站没有任何页面能注册）"


# ---------------------------------------------------------------- 订单状态查询


async def test_order_status_returns_pending(client, mock_mode):
    h = await auth_headers(client)
    no = (await client.post("/shop/orders", headers=h)).json()["order_no"]
    r = await client.get(f"/shop/orders/{no}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["expired"] is False
    assert body["order_no"] == no


async def test_order_status_requires_login(client, mock_mode):
    """未登录必须 401。

    ⚠️ 订单是**直接造**的，不能先调 `auth_headers(client)` 再取掉请求头 ——
    那个 helper 是在**同一个 client** 上登录的，Set-Cookie 会留在 client 的
    cookie jar 里，之后不带 Authorization 头也照样通过（我第一版就是这么写错的，
    测出来 200 而不是 401）。
    """
    no = await make_order("pending")
    assert (await client.get(f"/shop/orders/{no}")).status_code == 401


async def test_order_status_other_users_order_is_404(client, mock_mode):
    """非本人一律 404，不用 403 —— 不暴露"这个订单号存在"。"""
    h1 = await auth_headers(client, "buyer1", "secret123")
    no = (await client.post("/shop/orders", headers=h1)).json()["order_no"]
    h2 = await auth_headers(client, "buyer2", "secret123")
    assert (await client.get(f"/shop/orders/{no}", headers=h2)).status_code == 404


async def test_order_status_unknown_order_is_404(client):
    h = await auth_headers(client)
    assert (await client.get("/shop/orders/CM00000000XX", headers=h)).status_code == 404


async def test_status_polling_does_not_burn_the_one_time_download(client, product, mock_mode):
    """**这组测试里最重要的一条。**

    下单页每 3 秒轮询状态。如果轮询走的是 `POST /shop/download`（它会把订单
    一次性烧成 `downloaded`），用户付完钱就来不及下载了。
    这里模拟"轮询 5 次然后再下载"，下载必须仍然成功。
    """
    h = await auth_headers(client)
    no = await make_order("paid")

    for _ in range(5):  # 模拟轮询
        r = await client.get(f"/shop/orders/{no}", headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == "paid", "轮询不该改变状态"

    assert await status_of(no) == "paid", "轮询把状态改了 —— GET 必须只读"

    dl = await client.post(f"/shop/download/{no}", headers=h)
    assert dl.status_code == 200, (
        "轮询之后下载失败了 —— 说明轮询路径把一次性下载额度烧掉了。"
        f"响应：{dl.text}"
    )
    assert "download_url" in dl.json()


async def test_status_reflects_downloaded(client, product, mock_mode):
    """下载后轮询要能看到 downloaded，页面才能切到"已下载"态。"""
    h = await auth_headers(client)
    no = await make_order("paid")
    await client.post(f"/shop/download/{no}", headers=h)
    r = await client.get(f"/shop/orders/{no}", headers=h)
    assert r.json()["status"] == "downloaded"


async def test_status_reports_expired_without_closing(client, mock_mode):
    """过期只**报告**，不在轮询里写库关单。

    轮询每 3 秒一次，在里面写库（哪怕只是关个过期单）会把读接口变成写接口 ——
    这既是性能问题，也违反 test_auth_cookie.py 里"需登录的 GET 必须无副作用"的约束。
    关单只在 create_order 里做。
    """
    from datetime import datetime, timedelta, timezone

    h = await auth_headers(client)
    no = await make_order("pending")
    async with TestSession() as s:  # 把创建时间推到 TTL 之外
        o = (await s.execute(select(Order).where(Order.order_no == no))).scalar_one()
        o.create_time = datetime.now(timezone.utc) - timedelta(
            minutes=settings.ORDER_EXPIRE_MINUTES + 1
        )
        await s.commit()

    body = (await client.get(f"/shop/orders/{no}", headers=h)).json()
    assert body["expired"] is True, "应该报告已过期"
    assert body["status"] == "pending", "但**不该**顺手把单子关掉"


# ---------------------------------------------------------------- 二维码


async def test_mock_mode_returns_link_not_qr(client, mock_mode):
    """mock 模式的 code_url 是本站 http 链接，画成二维码反而多一步扫码。"""
    h = await auth_headers(client)
    body = (await client.post("/shop/orders", headers=h)).json()
    assert body["code_url"].startswith("http")
    assert body["qr_svg"] is None


async def test_wechat_mode_returns_inline_qr_svg(client, monkeypatch):
    """真实微信模式下 code_url 是 `weixin://` 串，必须给出二维码。"""
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "wechat")
    # 沙箱没有商户号，`pay_config().configured` 是 False，下单会先撞 503 门禁
    # （`WX_APPID` 等六项没填）。这里把配置桩成"已配置"，才能真正走到 native_prepay。
    monkeypatch.setattr(shop, "pay_config", lambda: SimpleNamespace(configured=True))

    async def fake_prepay(cfg, **kw):
        return "weixin://wxpay/bizpayurl?pr=AbCdEfGh"

    monkeypatch.setattr(shop, "native_prepay", fake_prepay)
    h = await auth_headers(client)
    body = (await client.post("/shop/orders", headers=h)).json()
    svg = body["qr_svg"]
    assert svg and svg.startswith("<svg")
    # 内联 SVG：不含脚本、不含任何外部请求（CSP connect-src 'self' 会拦第三方）
    assert "<script" not in svg.lower()
    assert "http" not in svg
    assert "href" not in svg


async def test_qr_is_only_generated_for_own_orders(client, mock_mode):
    """二维码只在订单 payload 里给出，不存在"任意内容生成二维码"的接口。

    那种接口会被拿去做钓鱼（生成指向恶意站点的码）。这里断言路由表里没有它。
    """
    from fastapi.routing import APIRoute

    from main import app

    paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert not [p for p in paths if "qr" in p.lower()], f"不该有独立的二维码接口：{paths}"


async def test_user_model_has_no_leftover_fixture_user(client):
    """占位测试：确认 make_order 造的用户确实落库了（其它测试依赖它）。"""
    await make_order("paid", username="probe_user")
    async with TestSession() as s:
        u = (
            await s.execute(select(User).where(User.username == "probe_user"))
        ).scalar_one_or_none()
    assert u is not None
