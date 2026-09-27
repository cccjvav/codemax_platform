"""TD-262 回归：同一用户的预支付在本进程内单飞，`POST /shop/orders` 有自己的限流桶。

替换 `tests/audit_handoff_probes.py` 里的「同单双预支付」诊断（复核 F-03 / ROADMAP A-03）：
修复前两个并发下单请求各自拿着同一张 pending 单调用一次 `native_prepay`；修复后第二个请求
等第一个完成，直接复用它写入的 code_url，提供方只被调用一次。

全部在进程内 ASGI + 可控替身上验证：事件屏障让第一次预支付停在半路，第二个请求在这段时间
到达。不是多实例共享单飞（TD-141），也不是渠道幂等或资金动作的证明。
"""
import asyncio

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models import Order, PaymentEvent
from app.order_state import mark_closed
from app.ratelimit import limiter
from app.routers import shop
from tests.conftest import TestSession
from tests.test_download import auth_headers, make_order
from tests.test_wechat_pay import _ENV


@pytest.fixture
def wechat_env(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "wechat")


class BlockingProvider:
    """第一次调用停在 `release` 之前；记录每次 out_trade_no，供断言只调了一次。"""

    def __init__(self):
        self.calls: list[str] = []
        self.first_in_flight = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, cfg, **kw):
        self.calls.append(kw["out_trade_no"])
        if len(self.calls) == 1:
            self.first_in_flight.set()
            await self.release.wait()
        return f"weixin://synthetic/{kw['out_trade_no']}"


async def events_of(order_no: str) -> list[str]:
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == order_no))
        rows = (await db.scalars(select(PaymentEvent).where(PaymentEvent.order_id == order.id).order_by(PaymentEvent.id))).all()
        return [row.kind for row in rows]


async def test_concurrent_checkout_calls_the_provider_once_and_shares_the_result(client, product, wechat_env, monkeypatch):
    provider = BlockingProvider()
    monkeypatch.setattr(shop, "native_prepay", provider)
    headers = await auth_headers(client, "single_flight")

    first = asyncio.create_task(client.post("/shop/orders", headers=headers))
    await asyncio.wait_for(provider.first_in_flight.wait(), 5)
    second = asyncio.create_task(client.post("/shop/orders", headers=headers))
    await asyncio.sleep(0.2)  # 第二个请求此时必须在锁上等待，而不是发起第二次预支付
    assert len(provider.calls) == 1 and not second.done(), "第二个请求应等待，不得并发调用提供方"
    provider.release.set()
    r1, r2 = await asyncio.gather(first, second)

    assert (r1.status_code, r2.status_code) == (200, 200)
    assert r1.json()["order_no"] == r2.json()["order_no"] == provider.calls[0]
    assert r1.json()["code_url"] == r2.json()["code_url"] == f"weixin://synthetic/{provider.calls[0]}"
    assert (r1.json()["reused"], r2.json()["reused"]) == (False, True)
    assert len(provider.calls) == 1, "同一单号只允许一次在途预支付"
    async with TestSession() as db:
        assert len((await db.scalars(select(Order))).all()) == 1
    assert await events_of(provider.calls[0]) == ["prepay_started", "prepay_ready"], "只有一次尝试的开始/就绪事件"
    assert not shop._PREPAY_LOCKS, "没有在途请求时锁表必须回收"


async def test_waiter_sees_the_unknown_result_and_retries_with_the_same_order(client, product, wechat_env, monkeypatch):
    """第一次预支付失败（结果未知）时，等待中的第二个请求不复用空 code_url，而是用同一单号再试一次。"""
    calls: list[str] = []
    first_in_flight, release = asyncio.Event(), asyncio.Event()

    async def provider(cfg, **kw):
        calls.append(kw["out_trade_no"])
        if len(calls) == 1:
            first_in_flight.set()
            await release.wait()
            raise shop.WeChatPayError("微信支付网络请求失败；结果未知，请核查原订单")
        return "weixin://synthetic/retry"

    monkeypatch.setattr(shop, "native_prepay", provider)
    headers = await auth_headers(client, "single_flight_retry")
    first = asyncio.create_task(client.post("/shop/orders", headers=headers))
    await asyncio.wait_for(first_in_flight.wait(), 5)
    second = asyncio.create_task(client.post("/shop/orders", headers=headers))
    await asyncio.sleep(0.2)
    release.set()
    r1, r2 = await asyncio.gather(first, second)

    assert r1.status_code == 502 and r2.status_code == 200
    assert calls == [calls[0], calls[0]], "重试必须沿用同一稳定单号，不造孤儿单"
    assert r2.json()["code_url"] == "weixin://synthetic/retry" and r2.json()["reused"] is False
    assert await events_of(calls[0]) == ["prepay_started", "prepay_unknown", "prepay_started", "prepay_ready"]
    assert not shop._PREPAY_LOCKS


async def test_cancelled_waiter_does_not_leak_the_lock_entry(client, product, wechat_env, monkeypatch):
    provider = BlockingProvider()
    monkeypatch.setattr(shop, "native_prepay", provider)
    headers = await auth_headers(client, "single_flight_cancel")
    first = asyncio.create_task(client.post("/shop/orders", headers=headers))
    await asyncio.wait_for(provider.first_in_flight.wait(), 5)
    second = asyncio.create_task(client.post("/shop/orders", headers=headers))
    await asyncio.sleep(0.2)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    provider.release.set()
    assert (await first).status_code == 200
    assert len(provider.calls) == 1
    assert not shop._PREPAY_LOCKS, "被取消的等待者必须归还引用计数"


async def test_prepay_flight_is_per_user():
    """不同用户互不阻塞；同一用户串行；锁表在最后一个使用者离开后为空。"""
    order: list[str] = []

    async def hold(user_id: int, tag: str, gate: asyncio.Event | None = None):
        async with shop._prepay_flight(user_id):
            order.append(f"{tag}:in")
            if gate is not None:
                await gate.wait()
            order.append(f"{tag}:out")

    gate = asyncio.Event()
    a1 = asyncio.create_task(hold(1, "a1", gate))
    await asyncio.sleep(0)
    a2 = asyncio.create_task(hold(1, "a2"))
    b1 = asyncio.create_task(hold(2, "b1"))
    await asyncio.sleep(0.05)
    assert order == ["a1:in", "b1:in", "b1:out"], "用户 2 不等用户 1；用户 1 的第二个调用在等"
    gate.set()
    await asyncio.gather(a1, a2, b1)
    assert order[-3:] == ["a1:out", "a2:in", "a2:out"]
    assert not shop._PREPAY_LOCKS


async def test_checkout_has_its_own_rate_limit_bucket(client, product, mock_mode, monkeypatch):
    """`POST /shop/orders` 按 RATE_LIMIT_AUTH 计数，与 `download`/工具桶无关；限流先于业务逻辑。"""
    limiter.reset()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOOLS", 100)
    try:
        headers = await auth_headers(client, "quota_user")  # 注册 + 登录各占 register/login 桶，不占 order 桶
        codes = [(await client.post("/shop/orders", headers=headers)).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = await client.post("/shop/orders", headers=headers)
        assert r.status_code == 429 and r.headers["retry-after"].isdigit()
        async with TestSession() as db:
            assert len((await db.scalars(select(Order))).all()) == 1, "被限流的请求不建单，前三次复用同一张"
    finally:
        limiter.reset()


# ---------------------------------------------------------------- R-02 / TD-302：变异检查补的缺口


async def test_response_reflects_a_payment_that_landed_while_prepay_was_in_flight(client, product, wechat_env, monkeypatch):
    """预支付在途时回调已把订单记为已付款：响应必须是库里的最新状态，而不是内存里的旧 pending。"""
    async def provider_then_paid(cfg, **kw):
        async with TestSession() as db:
            await db.execute(update(Order).where(Order.order_no == kw["out_trade_no"]).values(status="paid"))
            await db.commit()
        return f"weixin://synthetic/{kw['out_trade_no']}"

    monkeypatch.setattr(shop, "native_prepay", provider_then_paid)
    headers = await auth_headers(client, "late_callback")
    r = await client.post("/shop/orders", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "paid"


async def test_prepay_is_not_sent_for_an_order_that_left_pending_while_waiting(client, product, wechat_env, monkeypatch):
    """等单飞锁期间订单被关闭（或付款）：拿到锁后复查状态，409，不发起预支付、不记开始事件。"""
    calls = []

    async def provider(cfg, **kw):
        calls.append(kw["out_trade_no"])
        return "weixin://synthetic/never"

    class ClosedWhileWaiting(shop._prepay_flight):
        async def __aenter__(self):
            await super().__aenter__()
            async with TestSession() as db:
                await db.execute(update(Order).where(Order.user_id == self.user_id).values(status="closed"))
                await db.commit()

    monkeypatch.setattr(shop, "native_prepay", provider)
    monkeypatch.setattr(shop, "_prepay_flight", ClosedWhileWaiting)
    headers = await auth_headers(client, "closed_while_waiting")
    r = await client.post("/shop/orders", headers=headers)
    assert r.status_code == 409 and calls == []
    async with TestSession() as db:
        order = await db.scalar(select(Order))
    assert await events_of(order.order_no) == []


async def test_closing_a_stale_pending_object_cannot_overwrite_a_payment(client, product):
    """内存里的订单对象还是 pending，库里已被另一个事务记为已付款：关单的条件更新不能生效。
    状态机检查只看内存对象，拦不住这种交错；唯一的保护是 UPDATE 的 status 条件。"""
    number = await make_order("pending", "stale_close")
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        async with TestSession() as other:
            await other.execute(update(Order).where(Order.order_no == number).values(status="paid"))
            await other.commit()
        assert await mark_closed(db, order) is False
        assert order.status == "paid"
