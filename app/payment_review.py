"""Read-only projections of operator review, not financial settlement or refund certification.

Reuse append-only PaymentEvent with a versioned, bounded JSON review envelope. No schema
change or materialized queue: newly visible facts invalidate the old review automatically.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .models import Order, PaymentEvent, PaymentReceipt, RefundReceipt, RefundRequest

REVIEW_KIND = 'operator_review'
ISSUES = ('prepay_unknown', 'query_unknown', 'query_conflict', 'query_refund', 'query_aborted', 'refund_query_unknown', 'refund_query_aborted', 'refund_query_conflict',
          'refund_query_processing', 'refund_query_abnormal', 'refund_query_closed', 'refund_notify_signal', 'refund_request_prepared')
ACTIONS = ('followup', 'close', 'reopen')
ORPHAN_GRACE_SECONDS = 60


def review_payload(action: str, snapshot: str, previous: int, note: str) -> str:
    """Canonical envelope fits the existing 500-character evidence column; note is capped at 160."""
    value = json.dumps({'v': 1, 'action': action, 'snapshot': snapshot, 'previous': previous, 'note': note},
                       ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    if len(value) > 500:
        raise ValueError('复核记录过长，请缩短说明')
    return value


def decode_review(event: PaymentEvent | None) -> dict | None:
    """Malformed/unknown envelope never counts as completed review; retain raw history for inspection."""
    if (event is None or event.actor_id is None or not event.actor_name
            or not event.evidence or len(event.evidence) > 500):
        return None
    try:
        value = json.loads(event.evidence)
        if (not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1
                or value.get('action') not in ACTIONS
                or not isinstance(value.get('snapshot'), str) or len(value['snapshot']) != 64
                or any(c not in '0123456789abcdef' for c in value['snapshot'])
                or type(value.get('previous')) is not int or value['previous'] < 0
                or not isinstance(value.get('note'), str) or not 3 <= len(value['note'].strip()) <= 160
                or any(ord(c) < 32 for c in value['note'])):
            return None
        return value
    except (ValueError, RecursionError):
        return None


def overdue_start(now: datetime):
    """Correlate by BOTH order and attempt. A 60-second orphan is unknown, never proof of nonpayment."""
    completed = aliased(PaymentEvent)
    terminal = exists(select(completed.id).where(
        completed.order_id == PaymentEvent.order_id, completed.attempt_id == PaymentEvent.attempt_id,
        or_(and_(PaymentEvent.kind == 'prepay_started', completed.kind.in_(('prepay_ready', 'prepay_unknown'))),
            and_(PaymentEvent.kind == 'query_started', completed.kind.in_(
                ('query_success', 'query_unknown', 'query_aborted', 'query_notpay', 'query_closed', 'query_refund', 'query_conflict'))),
            and_(PaymentEvent.kind == 'refund_query_started', completed.kind.in_(
                ('refund_query_success', 'refund_query_unknown', 'refund_query_aborted', 'refund_query_conflict',
                 'refund_query_processing', 'refund_query_closed', 'refund_query_abnormal'))))))
    return and_(PaymentEvent.kind.in_(('prepay_started', 'query_started', 'refund_query_started')),
                PaymentEvent.create_time <= now - timedelta(seconds=ORPHAN_GRACE_SECONDS), ~terminal)


def review_candidates(now: datetime):
    """SQL prefilter only; exact workflow state must be computed from current facts below."""
    return or_(exists(select(RefundRequest.id).where(RefundRequest.order_id == Order.id)),
               exists(select(PaymentEvent.id).where(PaymentEvent.order_id == Order.id,
                      or_(PaymentEvent.kind.in_((*ISSUES, REVIEW_KIND)), overdue_start(now)))))


async def review_states(db: AsyncSession, orders: list[Order], *, now: datetime | None = None) -> dict[int, dict]:
    """Batch four read-only queries for <=50 orders; count+max detects late lower-ID commits.

    A sequence number is NOT commit order. Include count, orphan aging, receipt and order state,
    so an unseen older event or callback cannot remain covered by a previous completion marker.
    """
    if len(orders) > 50:
        raise ValueError('Review batch exceeds 50 orders')
    if not orders:
        return {}
    now = now or datetime.now(timezone.utc)
    ids = [order.id for order in orders]
    facts = {oid: (count, maximum, issues) for oid, count, maximum, issues in (await db.execute(
        select(PaymentEvent.order_id, func.count(), func.max(PaymentEvent.id),
               func.sum(case((PaymentEvent.kind.in_(ISSUES), 1), else_=0)))
        .where(PaymentEvent.order_id.in_(ids), PaymentEvent.kind != REVIEW_KIND).group_by(PaymentEvent.order_id))).all()}
    orphans = dict((await db.execute(select(PaymentEvent.order_id, func.count())
                   .where(PaymentEvent.order_id.in_(ids), overdue_start(now)).group_by(PaymentEvent.order_id))).all())
    latest = select(func.max(PaymentEvent.id)).where(PaymentEvent.order_id.in_(ids), PaymentEvent.kind == REVIEW_KIND)
    reviews = {e.order_id: e for e in (await db.scalars(select(PaymentEvent)
               .where(PaymentEvent.id.in_(latest.group_by(PaymentEvent.order_id))))).all()}
    receipt_rows = (await db.execute(select(PaymentReceipt.order_id, PaymentReceipt.id, RefundReceipt.id, RefundRequest.id)
                    .outerjoin(RefundReceipt, RefundReceipt.payment_receipt_id == PaymentReceipt.id)
                    .outerjoin(RefundRequest, RefundRequest.payment_receipt_id == PaymentReceipt.id)
                    .where(PaymentReceipt.order_id.in_(ids)))).all()
    receipts, prepared_orders = {}, set()
    for oid, rid, refund_id, prepared_id in receipt_rows:
        receipt_fact = rid if refund_id is None else [rid, refund_id]
        receipts[oid] = receipt_fact if prepared_id is None else [receipt_fact, 'request', prepared_id]
        if prepared_id is not None:
            prepared_orders.add(oid)
    result = {}
    for order in orders:
        count, maximum, issues = facts.get(order.id, (0, 0, 0))
        orphan_count = orphans.get(order.id, 0)
        fingerprint = [order.id, order.status, order.transaction_id, receipts.get(order.id), count, maximum, orphan_count]
        snapshot = hashlib.sha256(json.dumps(fingerprint, separators=(',', ':')).encode()).hexdigest()
        event = reviews.get(order.id)
        data = decode_review(event)
        current = data is not None and data['snapshot'] == snapshot
        candidate = bool(issues or orphan_count or event or order.id in prepared_orders)
        state = 'none' if not candidate else 'reviewed' if current and data['action'] == 'close' else (
            'followup' if current and data['action'] == 'followup' else 'open')
        result[order.id] = {'state': state, 'version': event.id if event else 0, 'snapshot': snapshot,
                            'new_facts': bool(event and not current), 'issues': issues, 'orphans': orphan_count,
                            'request_id': event.attempt_id if event else None,
                            'note': data['note'] if data else None, 'actor': event.actor_name if event else None,
                            'time': event.create_time if event else None}
    return result
