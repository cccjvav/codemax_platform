"""Transactional settlement and append-only attempt evidence; no external money movement.

Caller must supply a verified payment source or explicit administrator evidence. This module
owns commit/rollback for settlement. Its row lock also works on SQLite through a no-op UPDATE.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, PaymentEvent, PaymentReceipt, User


class PaymentConflict(ValueError):
    """A receipt cannot be safely attributed, or a retry conflicts with recorded evidence."""


async def lock_order(db: AsyncSession, order: Order) -> None:
    """Refresh under a write lock; do not autoflush stale ORM snapshots over the winning receipt."""
    with db.no_autoflush:
        await db.execute(update(Order).where(Order.id == order.id).values(status=Order.status, update_time=Order.update_time)
                         .execution_options(synchronize_session=False))
        await db.refresh(order)


async def settle(
    db: AsyncSession, order: Order, *, source: str, transaction_id: str,
    merchant_id: str | None = None, app_id: str | None = None, amount: int | None = None,
    actor: User | None = None, evidence: str | None = None, paid_at: datetime | None = None,
    audit_event: PaymentEvent | None = None,
) -> bool:
    """Record one receipt and paid state atomically; exact repeats are no-ops, conflicts fail closed.

    DB uniqueness handles same-transaction/different-order races. Cross-mode confirmations and
    already-paid legacy rows without evidence are refused, never silently adopted as real money.
    """
    actor_id, actor_name = (actor.id, actor.username) if actor else (None, None)
    try:
        await lock_order(db, order)
        if audit_event is not None:
            if audit_event.order_id != order.id:
                raise PaymentConflict('核查事件不属于此订单')
            db.add(audit_event)
        if source not in ('wechat', 'manual', 'mock') or order.payment_mode != source:
            raise PaymentConflict('订单支付渠道未核准或不匹配，不能按当前配置猜测历史订单')
        if (order.merchant_id, order.app_id) != (merchant_id, app_id):
            raise PaymentConflict('订单商户或应用快照不匹配')
        if not transaction_id or len(transaction_id) > 64 or '\x00' in transaction_id:
            raise PaymentConflict('收款凭证标识无效')
        if source == 'manual' and (actor_id is None or not evidence):
            raise PaymentConflict('人工收款需要管理员和核账依据')
        if order.amount <= 0 or order.currency != 'CNY' or (amount is not None and amount != order.amount):
            raise PaymentConflict('核账金额或币种与订单不符')
        receipt = await db.scalar(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id))
        if receipt is not None:
            expected = (source, transaction_id, order.amount, order.currency, merchant_id, app_id)
            actual = (receipt.source, receipt.transaction_id, receipt.amount, receipt.currency,
                      receipt.merchant_id, receipt.app_id)
            if expected != actual or order.status not in ('paid', 'downloaded'):
                raise PaymentConflict('重复确认与已记录的收款凭证不一致')
            await db.commit()  # release the lock; no second receipt and no actor overwrite
            return False
        if order.status not in ('pending', 'closed'):
            raise PaymentConflict('已付款历史订单缺少可核验凭证，需单独核账而不是重记收入')
        db.add(PaymentReceipt(
            order_id=order.id, source=source, transaction_id=transaction_id,
            amount=order.amount, currency=order.currency, merchant_id=merchant_id, app_id=app_id,
            actor_id=actor_id, actor_name=actor_name, evidence=evidence, paid_at=paid_at,
        ))
        order.status = 'paid'
        order.transaction_id = transaction_id
        order.paid_at = paid_at
        await db.commit()
        return True
    except IntegrityError as exc:
        await db.rollback()
        raise PaymentConflict('收款流水已归属其他订单或凭证发生并发冲突') from exc
    except Exception:
        await db.rollback()
        raise
