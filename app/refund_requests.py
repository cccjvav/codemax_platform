"""Durable refund PREPARATION, not a money-moving authorization or completion ledger.

One immutable full-WeChat preparation per order; exact request retries recover the same
reference. No sender, worker, cancellation or alternative-number retry is implemented here.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, PaymentEvent, RefundReceipt, RefundRequest, User
from .payment_ledger import PaymentConflict, lock_order
from .refunds import original_receipt, refund_for

PREPARED_KIND = 'refund_request_prepared'


async def request_for(db: AsyncSession, order_id: int) -> RefundRequest | None:
    return await db.scalar(select(RefundRequest).where(RefundRequest.order_id == order_id))


async def prior_refund_activity(db: AsyncSession, order_id: int) -> bool:
    """Even an unknown/closed query is not permission to mint a replacement provider reference."""
    return await db.scalar(select(PaymentEvent.id).where(PaymentEvent.order_id == order_id,
                           PaymentEvent.kind.in_(('refund_notify_signal', 'refund_query_started'))).limit(1)) is not None


def request_view(row: RefundRequest | None, refund: RefundReceipt | None) -> dict | None:
    """Preparation and completed receipt remain separate; never infer provider submission status."""
    if row is None:
        return None
    state = 'prepared'
    if refund:
        state = 'confirmed' if (refund.source, refund.merchant_id, refund.out_refund_no,
                                refund.payment_receipt_id, refund.amount, refund.currency) == (
                                    'wechat', row.merchant_id, row.out_refund_no, row.payment_receipt_id,
                                    row.amount, row.currency) else 'completed_elsewhere'
    return {'request_id': row.request_id, 'out_refund_no': row.out_refund_no, 'amount': row.amount,
            'currency': row.currency, 'merchant_id': row.merchant_id, 'app_id': row.app_id,
            'actor': row.actor_name, 'evidence': row.evidence, 'created_at': row.created_at,
            'state': state, 'preparation_only': True}


async def prepare_request(db: AsyncSession, order: Order, *, actor: User, request_id: str,
                           amount: int, evidence: str) -> tuple[RefundRequest, bool]:
    """Caller locks/rechecks the current actor FIRST; this function owns commit/rollback.

    Client idempotency key binds actor/order/amount/evidence; generated provider number is never
    returned before commit. Exact retry precedes later-activity checks, preserving recovery even
    after a notification or completed refund. Request + audit are atomic, no network involved.
    """
    try:
        await lock_order(db, order)
        receipt = await original_receipt(db, order, 'wechat')
        if (actor.role != 1 or actor.status != 1 or type(amount) is not int or amount != receipt.amount
                or amount <= 0 or order.currency != 'CNY' or not receipt.merchant_id or not receipt.app_id
                or not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id)
                or not isinstance(evidence, str) or not 3 <= len(evidence.strip()) <= len(evidence) <= 160
                or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in evidence)):
            raise PaymentConflict('退款准备的原收款、全额、发起人或请求依据无效')
        existing = await db.scalar(select(RefundRequest).where(RefundRequest.request_id == request_id))
        if existing:
            if (existing.order_id, existing.payment_receipt_id, existing.actor_id, existing.amount, existing.evidence,
                    existing.merchant_id, existing.app_id, existing.currency) != (
                    order.id, receipt.id, actor.id, amount, evidence, receipt.merchant_id, receipt.app_id, receipt.currency):
                raise PaymentConflict('相同准备请求ID的归属或内容不同，不能覆盖')
            await db.commit()
            return existing, False
        if await request_for(db, order.id):
            raise PaymentConflict('原订单已有退款准备，请读取原记录，不得换号再建')
        if await refund_for(db, order.id) or await prior_refund_activity(db, order.id):
            raise PaymentConflict('已有退款凭证、通知或查询记录，先核对原退款，不生成新退款号')
        row = RefundRequest(order_id=order.id, payment_receipt_id=receipt.id, request_id=request_id,
                            out_refund_no='CMR' + uuid.uuid4().hex, merchant_id=receipt.merchant_id,
                            app_id=receipt.app_id, amount=amount, currency='CNY', actor_id=actor.id,
                            actor_name=actor.username, evidence=evidence)
        db.add(row)
        await db.flush()  # PG trigger checks pre-existing observations before appending our own audit.
        db.add(PaymentEvent(order_id=order.id, attempt_id=request_id, kind=PREPARED_KIND,
                            actor_id=actor.id, actor_name=actor.username, evidence=evidence))
        await db.commit()
        return row, True
    except IntegrityError as exc:
        await db.rollback()
        raise PaymentConflict('退款准备标识或订单发生并发冲突，请读取原记录后按原请求核对') from exc
    except BaseException:
        await db.rollback()
        raise
