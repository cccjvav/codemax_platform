"""Durable, leased GET-only refund follow-up. No refund POST, receipt or entitlement writes.

A verified SUCCESS is an observation awaiting an active administrator's independent query.
Callback and repair callers hold the order lock before enqueue. Each worker releases its DB
transaction before HTTP; a token and DB-clock deadline fence late results after recovery.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update

from .models import Order, PaymentEvent, RefundVerificationJob
from .payment_ledger import PaymentConflict, lock_order
from .refund_notifications import NOTICE_KIND, notice_view
from .refunds import original_receipt
from .timeutil import as_utc
from .wechat_pay import WeChatPayError, assert_notify_configuration, query_full_refund

MAX_ATTEMPTS = 8
LEASE_SECONDS = 90  # Recovery lease, not exactly-once HTTP. Duplicate GETs are harmless.


async def clock(db):
    value = func.clock_timestamp() if db.bind.dialect.name == 'postgresql' else func.current_timestamp()
    return as_utc(await db.scalar(select(value)))


async def enqueue(db, event):
    """No commit: caller's order lock serializes callback/recovery; unique event is last defense."""
    if not await db.scalar(select(RefundVerificationJob.id).where(RefundVerificationJob.notice_event_id == event.id)):
        db.add(RefundVerificationJob(notice_event_id=event.id, order_id=event.order_id))


async def repair_missing(factory, limit=50):
    """Bounded old-inbox recovery, run only by opted-in worker. Never resets completed jobs."""
    async with factory() as db:
        ids = list((await db.scalars(select(PaymentEvent.id).outerjoin(
            RefundVerificationJob, RefundVerificationJob.notice_event_id == PaymentEvent.id).where(
                PaymentEvent.kind == NOTICE_KIND, PaymentEvent.actor_id.is_(None), PaymentEvent.actor_name.is_(None),
                RefundVerificationJob.id.is_(None)).order_by(PaymentEvent.id).limit(limit))).all())
    for identity in ids:
        async with factory() as db:
            event = await db.get(PaymentEvent, identity)
            if event is not None:
                order = await db.get(Order, event.order_id)
                await lock_order(db, order)
                await enqueue(db, event)
                await db.commit()
    return len(ids)


@dataclass(frozen=True)
class Ticket:
    id: int
    order_id: int
    event_id: int
    token: str
    attempt: int


def available(now):
    return or_(and_(RefundVerificationJob.state.in_(('pending', 'retry')), RefundVerificationJob.next_at <= now),
               and_(RefundVerificationJob.state == 'running', RefundVerificationJob.lease_until <= now))


def observation(ticket, kind, code):
    return PaymentEvent(order_id=ticket.order_id, attempt_id=ticket.token, kind=kind,
                        evidence=json.dumps({'v': 1, 'job_id': ticket.id, 'notice_event_id': ticket.event_id,
                                             'attempt': ticket.attempt, 'outcome': code}, separators=(',', ':')))


async def claim(factory):
    """CAS selects one due job. Claim plus started evidence commit before returning a ticket."""
    async with factory() as db:
        now = await clock(db)
        job = await db.scalar(select(RefundVerificationJob).where(available(now)).order_by(
            RefundVerificationJob.next_at, RefundVerificationJob.id).limit(1))
        if job is None:
            return None
        token = uuid.uuid4().hex
        exhausted = job.attempts >= MAX_ATTEMPTS
        ticket = Ticket(job.id, job.order_id, job.notice_event_id, token, min(job.attempts + 1, MAX_ATTEMPTS))
        result = await db.execute(update(RefundVerificationJob).where(
            RefundVerificationJob.id == job.id, available(now), RefundVerificationJob.attempts == job.attempts
        ).values(state='attention' if exhausted else 'running', attempts=ticket.attempt,
                 token=None if exhausted else token, lease_until=None if exhausted else now + timedelta(seconds=LEASE_SECONDS),
                 outcome='exhausted' if exhausted else 'querying', updated_at=now).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            await db.rollback()
            return None
        db.add(observation(ticket, 'refund_verify_observed' if exhausted else 'refund_verify_started',
                           'exhausted' if exhausted else 'querying'))
        await db.commit()
        return None if exhausted else ticket


async def inputs(factory, ticket, cfg):
    """Fresh original receipt/notice binding, reduced to immutable scalars before network."""
    async with factory() as db:
        event = await db.get(PaymentEvent, ticket.event_id)
        view = notice_view(event)
        order = await db.get(Order, ticket.order_id)
        if view is None or event.order_id != ticket.order_id or order is None:
            raise PaymentConflict('invalid_notice')
        receipt = await original_receipt(db, order, 'wechat')
        if view['partial'] or view['refund'] != receipt.amount:
            raise PaymentConflict('partial_or_mismatch')
        if (receipt.merchant_id, receipt.app_id) != (cfg.mchid, cfg.appid):
            raise WeChatPayError('configuration')
        if not cfg.configured:
            raise WeChatPayError('configuration')
        assert_notify_configuration(cfg)
        return {'out_refund_no': view['refund_no'], 'out_trade_no': order.order_no,
                'transaction_id': receipt.transaction_id, 'total': receipt.amount}, view['refund_id'], receipt.paid_at


async def finish(factory, ticket, state, code):
    """Token AND unexpired deadline fence observations. Queue and audit commit atomically.

    No receipt is written, even for SUCCESS. Redacted fixed outcome codes only, no raw payloads.
    """
    async with factory() as db:
        order = await db.get(Order, ticket.order_id)
        await lock_order(db, order)
        now = await clock(db)
        if state == 'retry' and ticket.attempt >= MAX_ATTEMPTS:
            state, code = 'attention', 'exhausted'
        result = await db.execute(update(RefundVerificationJob).where(
            RefundVerificationJob.id == ticket.id, RefundVerificationJob.state == 'running',
            RefundVerificationJob.token == ticket.token, RefundVerificationJob.lease_until > now,
            RefundVerificationJob.order_id == ticket.order_id, RefundVerificationJob.notice_event_id == ticket.event_id,
            RefundVerificationJob.attempts == ticket.attempt
        ).values(state=state, outcome=code, token=None, lease_until=None, updated_at=now,
                 next_at=now + timedelta(seconds=min(3600, 30 * 2 ** (ticket.attempt - 1)))))
        if result.rowcount != 1:
            await db.rollback()
            return False
        db.add(observation(ticket, 'refund_verify_observed', code))
        await db.commit()
        return True


async def run_once(factory, cfg, *, enabled=False):
    """One bounded repair/claim/query/finalize cycle. No privilege impersonation or money API."""
    if not enabled:
        return False
    await repair_missing(factory)
    ticket = await claim(factory)
    if ticket is None:
        return False
    try:
        payload, refund_id, paid_at = await inputs(factory, ticket, cfg)
        result = await query_full_refund(cfg, **payload)
        if result.refund_id != refund_id:
            state, code = 'attention', 'identity_conflict'
        elif result.state == 'SUCCESS':
            if (result.completed_at is None or result.completed_at.tzinfo is None
                    or as_utc(result.completed_at) > datetime.now(timezone.utc) + timedelta(minutes=5)
                    or (paid_at and as_utc(result.completed_at) < as_utc(paid_at))):
                state, code = 'attention', 'time_conflict'
            else:
                state, code = 'verified', 'success_needs_admin'
        elif result.state == 'PROCESSING':
            state, code = 'retry', 'processing'
        else:
            state, code = 'attention', result.state.lower()
    except PaymentConflict:
        state, code = 'attention', 'invalid_or_partial_notice'
    except WeChatPayError:
        state, code = 'retry', 'untrusted_or_unavailable'
    await finish(factory, ticket, state, code)
    return True


async def jobs_view(db, order_id):
    """Latest 50 queue facts independent of event pagination. No tokens or secret errors exposed."""
    rows = (await db.execute(select(RefundVerificationJob, PaymentEvent).join(
        PaymentEvent, PaymentEvent.id == RefundVerificationJob.notice_event_id).where(
        RefundVerificationJob.order_id == order_id).order_by(RefundVerificationJob.id.desc()).limit(51))).all()
    return {'jobs': [{'id': j.id, 'notice_event_id': j.notice_event_id, 'state': j.state, 'attempts': j.attempts,
                      'refund_no': (notice_view(event) or {}).get('refund_no'),
                      'outcome': j.outcome, 'next_at': j.next_at, 'lease_until': j.lease_until,
                      'updated_at': j.updated_at} for j, event in rows[:50]], 'has_more': len(rows) > 50}
