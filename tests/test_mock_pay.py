"""模拟支付通道（答辩演示用）的测试。

这个通道本身就是个风险：开着就等于免费发货。所以测试的重点不只是"能用"，
更是**关掉的时候彻底不存在** —— 见 `test_wechat_mode_hides_mock_endpoints`。

另一个重点是：模拟支付走的是与真实回调**完全相同**的状态机与幂等逻辑，
所以演示跑通的路径和上生产是同一条，不会漏测。
"""
import pytest
from sqlalchemy import select

import app.routers.shop as shop
from app.config import settings
from app.models import Order
from tests.conftest import TestSession


@pytest.fixture
def mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")

    async def no_wechat(cfg, **kw):
        raise AssertionError("模拟模式不该调用微信支付")

    monkeypatch.setattr(shop, "native_prepay", no_wechat)


async def auth_headers(client, username="buyer", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def order_row(order_no: str) -> Order:
    async with TestSession() as s:
        return (await s.execute(select(Order).where(Order.order_no == order_no))).scalar_one()


async def test_mock_order_points_at_local_cashier(client, mock_mode):
    h = await auth_headers(client)
    r = await client.post("/shop/orders", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["pay_mode"] == "mock"
    assert body["code_url"].endswith(f"/shop/mock-pay?order_no={body['order_no']}")
    assert body["code_url"].startswith("http://test"), "用请求的 base_url，本地演示才能直接点开"


async def test_mock_pay_page_renders_warning(client, mock_mode):
    r = await client.get("/shop/mock-pay", params={"order_no": "CM123"})
    assert r.status_code == 200
    assert "模拟支付通道" in r.text
    assert "CM123" in r.text


async def test_mock_confirm_marks_paid(client, mock_mode):
    h = await auth_headers(client)
    order_no = (await client.post("/shop/orders", headers=h)).json()["order_no"]
    r = await client.post("/shop/mock-pay/confirm", json={"order_no": order_no}, headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "paid"
    assert r.json()["transaction_id"].startswith("MOCK-")

    row = await order_row(order_no)
    assert row.status == "paid"
    assert row.paid_at is not None


async def test_mock_confirm_requires_login(client, mock_mode):
    r = await client.post("/shop/mock-pay/confirm", json={"order_no": "CM1"})
    assert r.status_code == 401


async def test_mock_confirm_rejects_other_users_order(client, mock_mode):
    alice = await auth_headers(client, "alice", "secret123")
    bob = await auth_headers(client, "bob", "secret456")
    order_no = (await client.post("/shop/orders", headers=alice)).json()["order_no"]
    r = await client.post("/shop/mock-pay/confirm", json={"order_no": order_no}, headers=bob)
    assert r.status_code == 404
    assert (await order_row(order_no)).status == "pending", "别人的订单不能被支付"


async def test_mock_confirm_is_idempotent(client, mock_mode):
    h = await auth_headers(client)
    order_no = (await client.post("/shop/orders", headers=h)).json()["order_no"]
    first = await client.post("/shop/mock-pay/confirm", json={"order_no": order_no}, headers=h)
    again = await client.post("/shop/mock-pay/confirm", json={"order_no": order_no}, headers=h)
    assert (first.status_code, again.status_code) == (200, 200)
    assert (await order_row(order_no)).status == "paid"


async def test_wechat_mode_hides_mock_endpoints(client):
    """默认（生产）模式下模拟通道必须彻底不存在 —— 这是本功能最重要的一条测试。"""
    assert settings.SHOP_PAY_MODE == "wechat"
    h = await auth_headers(client)
    assert (await client.get("/shop/mock-pay")).status_code == 404
    r = await client.post("/shop/mock-pay/confirm", json={"order_no": "CM1"}, headers=h)
    assert r.status_code == 404


async def test_unknown_pay_mode_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "alipay")
    h = await auth_headers(client)
    assert (await client.post("/shop/orders", headers=h)).status_code == 500


# ------------------------------------------------- base.html 公共上下文（S5-05）
#
# 复审抓到的第二个真 bug：mock_pay_page() 早先只传了 request / title / order_no /
# auth_ui，而 base.html 还要 site_name / tools / shop_path / product_name 与 SEO 三件套。
# 结果页面渲染出 `<h1><a href="/"></a></h1>` 空品牌、`<nav></nav>` 空导航、
# CTA 的 `href=""` —— 但它照样返回 200，而原先的测试只断言「页面能开 + 有订单号」，
# 所以一直没被发现。这是**支付链路上的页面**，空壳头部直接影响可信度。


async def test_mock_pay_page_renders_full_site_chrome(client, mock_mode):
    """收银台页必须与普通页面一样有完整的站点头部与导航。"""
    html = (await client.get("/shop/mock-pay", params={"order_no": "CM1"})).text

    assert "<h1><a href=\"/\">CodeMax 在线工具</a></h1>" in html, "品牌名不该是空的"
    assert "<nav></nav>" not in html, "工具导航不该是空的"
    assert 'href="/tools/er"' in html, "导航里应当有工具入口"
    assert 'href=""' not in html, "不该出现空 href 的链接（CTA 会点不动）"


async def test_mock_pay_page_has_the_same_chrome_as_a_normal_page(client, mock_mode):
    """直接与普通页面对照 —— 这比逐个断言字段更稳，也更能表达「上下文已收口」。"""
    cashier = (await client.get("/shop/mock-pay", params={"order_no": "CM1"})).text
    normal = (await client.get("/shop")).text
    for probe in ('<h1><a href="/">CodeMax 在线工具</a></h1>', 'href="/tools/er"', 'id="btn-auth"'):
        assert probe in cashier, f"收银台页缺了普通页面有的 {probe!r}"
        assert probe in normal, f"前提校验：普通页面应当有 {probe!r}"


def test_page_context_is_the_single_source_of_base_template_fields():
    """结构上防止同类问题再犯：base.html 用到的每个变量都必须由 page_context 提供。

    这条测试的意义在于**契约**而不是当前值 —— 以后谁往 base.html 加一个公共变量，
    忘了在 page_context 里补，这里就会红。否则又会退化成「每个渲染点自己拼一份」。
    """
    import re
    from pathlib import Path

    from app.site import page_context

    root = Path(__file__).resolve().parents[1]
    base = (root / "app/templates/base.html").read_text(encoding="utf-8")
    used = set(re.findall(r"{{\s*([a-z_]+)", base))
    # 循环变量与 request 不算公共上下文
    used -= {"t", "request"}

    class _Req:  # page_context 只把它塞进字典，不调用它的方法
        pass

    provided = set(page_context(_Req(), title="x"))
    missing = used - provided
    assert not missing, f"base.html 用到但 page_context 未提供的变量：{sorted(missing)}"
