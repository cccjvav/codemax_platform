"""Durable, leased GET-only refund follow-up. No refund POST; system receipt authority is separately default-off.

Without explicit system authority, SUCCESS remains an observation awaiting an administrator.
Callback and repair callers hold the order lock before enqueue. Each worker releases its DB
transaction before HTTP; a token and DB-clock deadline fence late results after recovery.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from .models import Order, PaymentEvent, RefundVerificationJob
from .payment_ledger import PaymentConflict, lock_order
from .refund_notifications import NOTICE_KIND, notice_view
from .refunds import _stage_receipt, original_receipt, refund_for
from .timeutil import as_utc
from .wechat_pay import WeChatPayError, assert_notify_configuration, query_full_refund

MAX_ATTEMPTS = 8
CONTROL_KIND = 'refund_verify_control'
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


async def finish(factory, ticket, state, code, *, settlement=None):
    """Token AND unexpired deadline fence observations. Queue and audit commit atomically.

    Optional trusted SUCCESS context stages receipt inside the same transaction/savepoint.
    Stale/held tickets cannot create receipts. No raw payloads or pretend administrator identities.
    """
    async with factory() as db:
        order = await db.get(Order, ticket.order_id)
        await lock_order(db, order)
        job = await db.scalar(select(RefundVerificationJob).where(RefundVerificationJob.id == ticket.id)
                              .with_for_update().execution_options(populate_existing=True))
        now = await clock(db)
        if (job is None or job.state != 'running' or job.token != ticket.token
                or job.order_id != ticket.order_id or job.notice_event_id != ticket.event_id
                or job.attempts != ticket.attempt or job.lease_until is None or as_utc(job.lease_until) <= now):
            await db.rollback()
            return False
        if settlement is not None and state == 'verified' and code == 'success_needs_admin':
            try:
                async with db.begin_nested():
                    changed = await stage_system_receipt(db, order, ticket, *settlement)
                code = 'success_recorded' if changed else 'success_already_recorded'
            except (PaymentConflict, IntegrityError):
                state, code = 'attention', 'receipt_conflict'
        if state == 'retry' and ticket.attempt >= MAX_ATTEMPTS:
            state, code = 'attention', 'exhausted'
        now = await clock(db)  # Staging/lock waits may have consumed the lease; fence at final CAS again.
        result = await db.execute(update(RefundVerificationJob).where(
            RefundVerificationJob.id == ticket.id, RefundVerificationJob.state == 'running',
            RefundVerificationJob.token == ticket.token, RefundVerificationJob.lease_until > now,
            RefundVerificationJob.order_id == ticket.order_id, RefundVerificationJob.notice_event_id == ticket.event_id,
            RefundVerificationJob.attempts == ticket.attempt
        ).values(state=state, outcome=code, token=None, lease_until=None, updated_at=now,
                 next_at=now + timedelta(seconds=min(3600, 30 * 2 ** (ticket.attempt - 1))))
          .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            await db.rollback()
            return False
        db.add(observation(ticket, 'refund_verify_observed', code))
        await db.commit()
        return True


async def run_once(factory, cfg, *, enabled=False, auto_record=False):
    """One bounded repair/claim/query/finalize cycle. No privilege impersonation or money API."""
    if not enabled:
        return False
    await repair_missing(factory)
    ticket = await claim(factory)
    if ticket is None:
        return False
    settlement = None
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
                if auto_record:
                    settlement = (cfg, payload, result)
        elif result.state == 'PROCESSING':
            state, code = 'retry', 'processing'
        else:
            state, code = 'attention', result.state.lower()
    except PaymentConflict:
        state, code = 'attention', 'invalid_or_partial_notice'
    except WeChatPayError:
        state, code = 'retry', 'untrusted_or_unavailable'
    await finish(factory, ticket, state, code, settlement=settlement)
    return True


async def jobs_view(db, order_id):
    """Latest 50 queue facts independent of event pagination. No tokens or secret errors exposed."""
    rows = (await db.execute(select(RefundVerificationJob, PaymentEvent).join(
        PaymentEvent, PaymentEvent.id == RefundVerificationJob.notice_event_id).where(
        RefundVerificationJob.order_id == order_id).order_by(RefundVerificationJob.id.desc()).limit(51))).all()
    latest = await last_control(db, order_id)
    version = latest.id if latest else 0
    return {'latest_control': control_view(latest), 'jobs': [{'snapshot': control_snapshot(j, version), 'id': j.id, 'notice_event_id': j.notice_event_id, 'state': j.state, 'attempts': j.attempts,
                      'refund_no': (notice_view(event) or {}).get('refund_no'),
                      'outcome': j.outcome, 'next_at': j.next_at, 'lease_until': j.lease_until,
                      'updated_at': j.updated_at} for j, event in rows[:50]], 'has_more': len(rows) > 50}


async def last_control(db, order_id):
    """Order-wide immutable sequence also prevents same-second hold/retry ABA snapshots."""
    return await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order_id,
                           PaymentEvent.kind == CONTROL_KIND).order_by(PaymentEvent.id.desc()).limit(1))


def control_snapshot(job, version):
    """Opaque optimistic proof, not authority. Include lease and control sequence, never expose token."""
    values = [job.id, job.order_id, job.notice_event_id, job.state, job.attempts, job.token,
              *(as_utc(v).isoformat() if v else None for v in (job.lease_until, job.next_at, job.updated_at)),
              job.outcome, version]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


def control_view(event):
    """First action attribution, not a claim that the queue is still in that action's resulting state."""
    if event is None:
        return None
    try:
        note = json.loads(event.evidence)
        if (event.kind != CONTROL_KIND or event.actor_id is None or not event.actor_name
                or type(note.get('v')) is not int or note['v'] != 1
                or note.get('action') not in ('hold', 'retry') or type(note.get('job_id')) is not int):
            return None
        return {'request_id': event.attempt_id, 'job_id': note['job_id'], 'action': note['action'],
                'actor': event.actor_name, 'evidence': note['evidence'], 'created_at': event.create_time}
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


async def control_job(db, order, *, actor, key, job_id, action, snapshot, evidence):
    """Caller locks/rechecks active user first. Then order -> job; no network or count reset.

    Hold fences results; already-claimed work can still issue GET after hold returns. Retry uses remaining lifetime
    budget; verified/exhausted jobs require the existing independent administrator query instead.
    Exact request replay reads first attribution before checking current mutable state.
    """
    try:
        await lock_order(db, order)
        job = await db.scalar(select(RefundVerificationJob).where(RefundVerificationJob.id == job_id,
                              RefundVerificationJob.order_id == order.id).with_for_update().execution_options(populate_existing=True))
        if job is None or actor.role != 1 or actor.status != 1 or action not in ('hold', 'retry'):
            raise PaymentConflict('核验任务归属、操作者或动作不符')
        note = json.dumps({'v': 1, 'job_id': job_id, 'action': action, 'snapshot': snapshot, 'evidence': evidence},
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if len(note) > 500:
            raise PaymentConflict('核验操作依据过长')
        old = await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == CONTROL_KIND, PaymentEvent.attempt_id == key))
        if old:
            if (old.order_id, old.actor_id, old.evidence) != (order.id, actor.id, note):
                raise PaymentConflict('原请求ID归属或内容不同，不可覆盖首次操作')
            view = control_view(old)
            await db.commit()
            return view, False
        latest = await last_control(db, order.id)
        if control_snapshot(job, latest.id if latest else 0) != snapshot:
            raise PaymentConflict('任务已有新进展，请刷新核对后重新确认')
        if action == 'hold':
            if job.state == 'verified' or job.outcome == 'manual_hold':
                raise PaymentConflict('已核验成功请按原号人工查询；已接管任务无需重复接管')
            job.state, job.outcome = 'attention', 'manual_hold'
        else:
            if job.state != 'attention' or job.attempts >= MAX_ATTEMPTS:
                raise PaymentConflict('只能重排待人工任务的剩余次数；耗尽/已核验任务请按原号人工查询')
            event = await db.get(PaymentEvent, job.notice_event_id)
            view = notice_view(event)
            receipt = await original_receipt(db, order, 'wechat')
            if (view is None or event.order_id != order.id or view['partial'] or view['refund'] != receipt.amount
                    or await refund_for(db, order.id)):
                raise PaymentConflict('通知无效/部分退款或已有成功凭证，不能重排全额核验')
            job.state, job.outcome = 'retry', 'operator_retry'
        job.token, job.lease_until = None, None
        job.updated_at = job.next_at = await clock(db)
        event = PaymentEvent(order_id=order.id, attempt_id=key, kind=CONTROL_KIND,
                             actor_id=actor.id, actor_name=actor.username, evidence=note)
        db.add(event)
        await db.flush()
        view = control_view(event)
        await db.commit()
        return view, True
    except BaseException:
        await db.rollback()
        raise


async def stage_system_receipt(db, order, ticket, cfg, payload, result):
    """Only finish calls this with a fresh signed query result under order/job lease locks.

    Rebind the immutable receipt and notice to the exact outbound query context. The result type
    alone is not authority: no route or stored SUCCESS observation may call this as a shortcut.
    """
    receipt = await original_receipt(db, order, 'wechat')
    notice = notice_view(await db.get(PaymentEvent, ticket.event_id))
    start = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order.id,
                            PaymentEvent.kind == 'refund_verify_started', PaymentEvent.attempt_id == ticket.token))
    if (notice is None or notice['partial'] or notice['refund'] != receipt.amount
            or (receipt.merchant_id, receipt.app_id) != (cfg.mchid, cfg.appid)
            or payload != {'out_refund_no': notice['refund_no'], 'out_trade_no': order.order_no,
                           'transaction_id': receipt.transaction_id, 'total': receipt.amount}
            or result.state != 'SUCCESS' or result.refund_id != notice['refund_id']
            or start is None or start.actor_id is not None or start.actor_name is not None):
        raise PaymentConflict('系统凭证与原核验身份不匹配')
    return await _stage_receipt(db, order, source='wechat', refund_id=result.refund_id,
                     out_refund_no=notice['refund_no'], amount=receipt.amount, completed_at=result.completed_at,
                     actor_id=None, actor_name='system:refund-verifier', verification_event_id=start.id,
                     evidence='独立验签全额原路CNY退款查询；系统核验任务 ' + str(ticket.id))
