"""S3-01-2 测试：订单状态机（待支付 → 已支付 → 已下载）。

状态迁移的**幂等**与**原子**两条约定是重点：微信支付会重复通知，
重复通知既不能让回调失败，也不能让同一单被处理两次。
"""
import pytest
import pytest_asyncio
from sqlalchemy import update

from app.models import Order, User
from app.order_state import (
    DOWNLOADED,
    PAID,
    PENDING,
    IllegalTransition,
    check_transition,
    mark_downloaded,
    mark_paid,
)
from tests.conftest import TestSession

_seq = 0


@pytest_asyncio.fixture(autouse=True)
async def _schema(client):
    """本文件不走 HTTP，只需要 conftest 那套建表/清库逻辑，故借它的 client fixture。"""


async def make_order(status: str = PENDING) -> int:
    """建一个用户 + 一张订单，返回订单 id。"""
    global _seq
    _seq += 1
    async with TestSession() as s:
        user = User(username=f"buyer{_seq}", password="x")
        s.add(user)
        await s.flush()
        order = Order(
            order_no=f"NO{_seq:06d}", user_id=user.id, product_name="毕设服务", amount=9900, status=status
        )
        s.add(order)
        await s.commit()
        return order.id


async def status_of(order_id: int) -> str:
    async with TestSession() as s:
        return (await s.get(Order, order_id)).status


def test_state_values_are_persisted_strings():
    """这三个字符串是要落库的值：改常量必须同时改 full_init.sql 与既有数据。"""
    assert (PENDING, PAID, DOWNLOADED) == ("pending", "paid", "downloaded")


def test_only_forward_transitions_allowed():
    check_transition(PENDING, PAID)
    check_transition(PAID, DOWNLOADED)
    for current, target in [
        (PENDING, DOWNLOADED),  # 未支付不能直接下载
        (PAID, PENDING),  # 不能回退
        (DOWNLOADED, PAID),
        (DOWNLOADED, PENDING),
        ("unknown", PAID),
    ]:
        with pytest.raises(IllegalTransition):
            check_transition(current, target)


async def test_mark_paid_from_pending():
    oid = await make_order()
    async with TestSession() as s:
        assert await mark_paid(s, await s.get(Order, oid)) is True
    assert await status_of(oid) == PAID


async def test_mark_paid_twice_is_idempotent():
    """重复通知：第二次返回 False，不报错、不改状态。"""
    oid = await make_order()
    async with TestSession() as s:
        assert await mark_paid(s, await s.get(Order, oid)) is True
    async with TestSession() as s:
        assert await mark_paid(s, await s.get(Order, oid)) is False
    assert await status_of(oid) == PAID


async def test_late_paid_notification_after_download_is_noop():
    """已下载之后才到的重复支付通知，不能把状态改回去。"""
    oid = await make_order(status=DOWNLOADED)
    async with TestSession() as s:
        assert await mark_paid(s, await s.get(Order, oid)) is False
    assert await status_of(oid) == DOWNLOADED


async def test_mark_downloaded_from_paid():
    oid = await make_order(status=PAID)
    async with TestSession() as s:
        assert await mark_downloaded(s, await s.get(Order, oid)) is True
    assert await status_of(oid) == DOWNLOADED


async def test_mark_downloaded_twice_is_idempotent():
    oid = await make_order(status=DOWNLOADED)
    async with TestSession() as s:
        assert await mark_downloaded(s, await s.get(Order, oid)) is False
    assert await status_of(oid) == DOWNLOADED


async def test_cannot_download_unpaid_order():
    oid = await make_order()  # pending
    async with TestSession() as s:
        with pytest.raises(IllegalTransition):
            await mark_downloaded(s, await s.get(Order, oid))
    assert await status_of(oid) == PENDING, "非法迁移不能留下副作用"


async def test_unknown_status_rejected():
    oid = await make_order(status="refunded")
    async with TestSession() as s:
        with pytest.raises(IllegalTransition):
            await mark_paid(s, await s.get(Order, oid))
    assert await status_of(oid) == "refunded"


async def test_cas_only_matches_expected_status():
    """迁移靠 UPDATE ... WHERE status=<期望值>：状态已被别人改掉时必须影响 0 行。"""
    oid = await make_order(status=PAID)
    async with TestSession() as s:
        r = await s.execute(
            update(Order).where(Order.id == oid, Order.status == PENDING).values(status=PAID)
        )
        await s.commit()
    assert r.rowcount == 0
    assert await status_of(oid) == PAID
