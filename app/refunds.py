"""Completed full-refund evidence and order-scoped download authorization. No money movement.

Routes must verify provider proof or explicit manual evidence and lock/recheck the active actor.
Settlement keeps the original payment intact; repeat paid callbacks cannot restore downloads.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, PaymentEvent, PaymentReceipt, RefundReceipt, User
from .payment_ledger import PaymentConflict, lock_order
from .timeutil import as_utc


async def refund_for(db: AsyncSession, order_id: int) -> RefundReceipt | None:
    """Fresh database query, not a cached ORM relationship; safe for the public token gate."""
    return await db.scalar(select(RefundReceipt).where(RefundReceipt.order_id == order_id))


async def original_receipt(db: AsyncSession, order: Order, source: str) -> PaymentReceipt:
    """Refuse missing/historical/mismatched payment evidence instead of inventing a refund."""
    receipt = await db.scalar(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id))
    if (source not in ('wechat', 'manual') or order.payment_mode != source
            or order.status not in ('paid', 'downloaded') or receipt is None
            or (receipt.source, receipt.amount, receipt.currency, receipt.transaction_id, receipt.merchant_id, receipt.app_id)
            != (source, order.amount, 'CNY', order.transaction_id, order.merchant_id, order.app_id)):
        raise PaymentConflict('必须先有匹配的真实收款凭证；不能猜测历史收入、跨渠道或处理模拟退款')
    return receipt


async def record_refund(db: AsyncSession, order: Order, *, source: str, refund_id: str,
                        out_refund_no: str, amount: int, completed_at: datetime,
                        actor: User, evidence: str, audit_event: PaymentEvent) -> bool:
    """Own commit/rollback: immutable full-refund receipt + same-order audit atomically.

    Exact financial repeats preserve first attribution/evidence/time. Different financial facts
    conflict, including a different completion time; actor/note are not a new financial fact.
    """
    actor_id, actor_name = actor.id, actor.username
    try:
        await lock_order(db, order)
        receipt = await original_receipt(db, order, source)
        if (type(amount) is not int or amount != receipt.amount or amount <= 0
                or completed_at is None or completed_at.tzinfo is None
                or as_utc(completed_at) > datetime.now(timezone.utc) + timedelta(minutes=5)
                or (receipt.paid_at and as_utc(completed_at) < as_utc(receipt.paid_at))
                or actor.role != 1 or actor.status != 1 or not evidence or len(evidence) > 500
                or any(not isinstance(v, str) or not 1 <= len(v) <= 64 or any(ord(c) < 33 for c in v)
                       for v in (refund_id, out_refund_no))):
            raise PaymentConflict('全额退款金额、成功时间、管理员或证据不合规')
        if audit_event.order_id != order.id or audit_event.actor_id != actor_id:
            raise PaymentConflict('退款审计归属不一致')
        existing = await refund_for(db, order.id)
        expected = (receipt.id, source, receipt.merchant_id or '', refund_id, out_refund_no, amount, 'CNY', as_utc(completed_at))
        if existing:
            actual = (existing.payment_receipt_id, existing.source, existing.merchant_id, existing.refund_id,
                      existing.out_refund_no, existing.amount, existing.currency, as_utc(existing.completed_at))
            if actual != expected:
                raise PaymentConflict('与已记录退款不一致，禁止覆盖或重复退款')
            # Query attempts still need their own terminal event; original receipt is unchanged.
            db.add(audit_event)
            await db.commit()
            return False
        db.add(RefundReceipt(order_id=order.id, payment_receipt_id=receipt.id, source=source,
                             merchant_id=receipt.merchant_id or '', refund_id=refund_id, out_refund_no=out_refund_no,
                             amount=amount, currency='CNY', completed_at=as_utc(completed_at).astimezone(timezone.utc),
                             actor_id=actor_id, actor_name=actor_name, evidence=evidence))
        db.add(audit_event)
        await db.commit()
        return True
    except IntegrityError as exc:
        await db.rollback()
        raise PaymentConflict('退款标识已归属其他订单或发生并发冲突') from exc
    except Exception:
        await db.rollback()
        raise
