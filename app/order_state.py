"""订单状态与收款兼容入口。

关闭/发放下载用UPDATE条件CAS；收款必须委托payment_ledger.settle，
锁定订单后把唯一凭证和paid状态同事务提交。精确重复凭证幂等，
不同流水不是正常重复，不能只因状态已paid就静默接受。
已关闭订单仍可接收迟到成功；downloaded不消灭重领链接的购买权益。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order
from .payment_ledger import PaymentConflict, settle
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
    db: AsyncSession, order: Order, *, transaction_id: str,
    paid_at: datetime | None = None,
) -> bool:
    """Compatibility entry for internal callers; cannot bypass settlement evidence or channel checks.

    HTTP callbacks/admin confirmations use settle directly with independently verified identities.
    Missing/unknown historical channels are rejected, not guessed from global settings.
    """
    try:
        return await settle(db, order, source=order.payment_mode, transaction_id=transaction_id,
                            merchant_id=order.merchant_id, app_id=order.app_id, paid_at=paid_at)
    except PaymentConflict as e:
        raise IllegalTransition(str(e)) from e


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
