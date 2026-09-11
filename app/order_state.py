"""订单状态机（S3-01-2）：待支付 → 已支付 → 已下载。

迁移边只在这里定义一处，支付回调 / 发货 / 下载三处都走同一套判断，
不各自写 if —— 否则迟早出现"未支付也能下载"这类漏洞。

两条设计约定（详见 TECH_DECISIONS.md 的 TD-100 ~ TD-102）：
1. **幂等**：微信支付会重复通知，重复通知不能让回调失败，所以"已经是目标状态"
   返回 False 而不是抛错。
2. **原子**：迁移用 `UPDATE ... WHERE status=<期望值>` 做 CAS，避免并发回调
   把同一单处理两次（与 /oauth/token 消费授权码同一个套路）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order
from .timeutil import as_utc

PENDING = "pending"  # 待支付
PAID = "paid"  # 已支付
DOWNLOADED = "downloaded"  # 已下载
CLOSED = "closed"  # 超时关闭（S5-01-1）：待支付太久，二维码大概率已失效

STATES = (PENDING, PAID, DOWNLOADED, CLOSED)

# 唯一允许的迁移边
ALLOWED: dict[str, tuple[str, ...]] = {
    PENDING: (PAID, CLOSED),
    # CLOSED → PAID 是**故意**留的：关单只是我们这边不再等它，但用户完全可能
    # 已经扫了旧二维码把钱付了。钱收了就必须发货，否则是收钱不发货（TD-156）。
    CLOSED: (PAID,),
    PAID: (DOWNLOADED,),
    DOWNLOADED: (),
}


class IllegalTransition(Exception):
    """非法状态迁移，例如未支付就想标记已下载。"""


def check_transition(current: str, target: str) -> None:
    """校验迁移是否合法，不合法就抛 IllegalTransition。"""
    if target not in ALLOWED.get(current, ()):
        raise IllegalTransition(f"订单状态不允许从 {current!r} 迁移到 {target!r}")


async def mark_paid(
    db: AsyncSession, order: Order, *, transaction_id: str | None = None, paid_at: datetime | None = None,
) -> bool:
    """待支付 → 已支付。返回本次是否真的发生了迁移。

    `CLOSED` 也要能收：订单被超时关闭不代表用户没付钱 —— 他完全可能已经扫了
    旧二维码。钱到账就必须发货，否则是收钱不发货（TD-156）。这条边漏掉的后果
    由 `test_late_payment_on_closed_order_still_delivers` 守着。
    """
    if order.status in (PENDING, CLOSED):
        # 钱到账时，pending/closed 都是允许的起点，不能只匹配读到的旧快照。
        # 元数据必须和状态一起写，避免 ORM autoflush 让竞争失败者覆盖首笔流水。
        receipt = {}
        if transaction_id is not None:
            receipt["transaction_id"] = transaction_id
        if paid_at is not None:
            receipt["paid_at"] = paid_at
        changed = await _cas(db, order, (PENDING, CLOSED), PAID, **receipt)
        if order.status not in (PAID, DOWNLOADED):
            raise IllegalTransition(f"支付后订单状态异常：{order.status!r}")
        return changed
    if order.status in (PAID, DOWNLOADED):
        return False  # 重复通知：幂等，不报错
    raise IllegalTransition(f"订单状态 {order.status!r} 无法标记为已支付")


def is_expired(order: Order, ttl_minutes: int, now: datetime | None = None) -> bool:
    """待支付订单是否已超过 ttl_minutes。只对待支付单有意义，其它状态一律 False。

    刻意**不加 `expire_at` 字段**：过期时间 = `create_time` + 配置值就能算出来，
    多存一列只会多一个要与配置保持同步的东西（TD-100 当初也是这么建议的）。
    """
    if order.status != PENDING or order.create_time is None:
        return False
    moment = now or datetime.now(timezone.utc)
    return moment - as_utc(order.create_time) > timedelta(minutes=ttl_minutes)


async def mark_closed(db: AsyncSession, order: Order) -> bool:
    """待支付 → 超时关闭。返回本次是否真的发生了迁移（已关闭则 False，幂等）。"""
    if order.status == CLOSED:
        return False
    check_transition(order.status, CLOSED)
    return await _cas(db, order, PENDING, CLOSED)


async def mark_downloaded(db: AsyncSession, order: Order) -> bool:
    """已支付 → 已下载。未支付的订单会抛 IllegalTransition。"""
    if order.status == DOWNLOADED:
        return False  # 重复下载请求：幂等
    check_transition(order.status, DOWNLOADED)
    return await _cas(db, order, PAID, DOWNLOADED)


async def _cas(
    db: AsyncSession, order: Order, expected: str | tuple[str, ...], target: str, **values,
) -> bool:
    allowed = (expected,) if isinstance(expected, str) else expected
    result = await db.execute(
        update(Order).where(Order.id == order.id, Order.status.in_(allowed)).values(status=target, **values)
    )
    await db.commit()
    await db.refresh(order)
    return result.rowcount == 1
