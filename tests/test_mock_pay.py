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
