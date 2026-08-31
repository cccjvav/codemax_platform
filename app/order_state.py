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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order

PENDING = "pending"  # 待支付
PAID = "paid"  # 已支付
DOWNLOADED = "downloaded"  # 已下载

STATES = (PENDING, PAID, DOWNLOADED)

# 唯一允许的迁移边
ALLOWED: dict[str, tuple[str, ...]] = {
    PENDING: (PAID,),
    PAID: (DOWNLOADED,),
    DOWNLOADED: (),
}


class IllegalTransition(Exception):
    """非法状态迁移，例如未支付就想标记已下载。"""


def check_transition(current: str, target: str) -> None:
    """校验迁移是否合法，不合法就抛 IllegalTransition。"""
    if target not in ALLOWED.get(current, ()):
        raise IllegalTransition(f"订单状态不允许从 {current!r} 迁移到 {target!r}")


async def mark_paid(db: AsyncSession, order: Order) -> bool:
    """待支付 → 已支付。返回本次是否真的发生了迁移。"""
    if order.status == PENDING:
        return await _cas(db, order, PENDING, PAID)
    if order.status in (PAID, DOWNLOADED):
        return False  # 重复通知：幂等，不报错
    raise IllegalTransition(f"订单状态 {order.status!r} 无法标记为已支付")


async def mark_downloaded(db: AsyncSession, order: Order) -> bool:
    """已支付 → 已下载。未支付的订单会抛 IllegalTransition。"""
    if order.status == DOWNLOADED:
        return False  # 重复下载请求：幂等
    check_transition(order.status, DOWNLOADED)
    return await _cas(db, order, PAID, DOWNLOADED)


async def _cas(db: AsyncSession, order: Order, expected: str, target: str) -> bool:
    result = await db.execute(
        update(Order).where(Order.id == order.id, Order.status == expected).values(status=target)
    )
    await db.commit()
    await db.refresh(order)
    return result.rowcount == 1
