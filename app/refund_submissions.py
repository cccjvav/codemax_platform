"""Explicit full-refund authorization and durable attempts. No worker, auto-retry or settlement.

The caller locks the current actor before these order locks. Outbound I/O happens only after
commit, with no DB lock held. Exact attempt replay only reads; a fresh explicit retry keeps the
same frozen bytes/reference, waits 60 seconds and refuses any intervening refund observation.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, PaymentEvent, RefundAuthorization, User
from .order_state import as_utc
from .payment_ledger import PaymentConflict, lock_order
from .refund_requests import prior_refund_activity, request_for
from .refunds import original_receipt, refund_for

AUTHORIZED = 'refund_authorized'
STARTED = 'refund_send_started'
OBSERVED = 'refund_send_observed'
RETRY_SECONDS = 60  # exceeds the bounded APIv3 exchange (20 seconds); not a worker lease.


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(body: str) -> str:
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def build_body(receipt, prepared, reason: str, origin: str) -> str:
    """Separate customer-visible UTF-8 reason from internal audit evidence. Fixed callback path."""
    try:
        url = urlsplit(origin)
        _ = url.port  # reject malformed/non-numeric ports before freezing the callback
    except ValueError:
        raise PaymentConflict('退款通知来源格式无效') from None
    if (not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/?', origin)
            or url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or any(ord(c) <= 32 or ord(c) >= 127 for c in origin)):
        raise PaymentConflict('退款通知来源必须是无凭据/路径/参数的HTTPS正式域名')
    callback = origin.rstrip('/') + '/shop/refunds/notify'
    if (len(callback.encode()) > 256 or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', receipt.transaction_id)
            or not reason or reason != reason.strip() or len(reason.encode('utf-8')) > 80
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in reason)):
        raise PaymentConflict('退款原因须为1–80 UTF-8字节单行；原流水或回调地址不适用')
    return canonical({'transaction_id': receipt.transaction_id, 'out_refund_no': prepared.out_refund_no,
                      'reason': reason, 'notify_url': callback,
                      'amount': {'refund': prepared.amount, 'total': receipt.amount, 'currency': 'CNY'}})


async def authorization_for(db: AsyncSession, prepared_id: int) -> RefundAuthorization | None:
    return await db.scalar(select(RefundAuthorization).where(RefundAuthorization.preparation_id == prepared_id))


async def attempt_view(db: AsyncSession, start: PaymentEvent) -> dict:
    outcome = await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == OBSERVED,
                                                       PaymentEvent.attempt_id == start.attempt_id))
    note = json.loads(outcome.evidence) if outcome else {'state': 'unknown'}
    return {'attempt_id': start.attempt_id, 'actor': start.actor_name, 'created_at': start.create_time,
            'state': note['state'], 'provider_status': note.get('provider_status'),
            'refund_id': note.get('refund_id')}


async def submission_view(db: AsyncSession, prepared) -> dict | None:
    if prepared is None:
        return None
    row = await authorization_for(db, prepared.id)
    if row is None:
        return None
    latest = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == prepared.order_id,
                             PaymentEvent.kind == STARTED).order_by(PaymentEvent.id.desc()).limit(1))
    return {'authorization_id': row.request_id, 'digest': row.digest, 'body': json.loads(row.body),
            'actor': row.actor_name, 'evidence': row.evidence, 'created_at': row.created_at,
            'attempt': await attempt_view(db, latest) if latest else None}


async def authorize(db: AsyncSession, order: Order, *, actor: User, key: str, refund_no: str,
                    amount: int, reason: str, evidence: str, origin: str) -> bool:
    """Caller validates schema/actor. Own transaction; immutable full contract plus audit atomic."""
    try:
        await lock_order(db, order)
        receipt = await original_receipt(db, order, 'wechat')
        prepared = await request_for(db, order.id)
        if prepared is None or (prepared.out_refund_no, prepared.amount) != (refund_no, amount):
            raise PaymentConflict('须手动确认原准备号和原全额')
        existing = await db.scalar(select(RefundAuthorization).where(RefundAuthorization.request_id == key))
        # Exact retry uses the frozen callback even if configuration changed after the first save.
        if existing:
            if (existing.preparation_id, existing.actor_id, existing.evidence, json.loads(existing.body)['reason']) != (
                    prepared.id, actor.id, evidence, reason):
                raise PaymentConflict('同授权ID的归属/内容不同，不能覆盖')
            await db.commit()
            return False
        if (await authorization_for(db, prepared.id) or await refund_for(db, order.id)
                or await prior_refund_activity(db, order.id)):
            raise PaymentConflict('已有授权或退款活动，须核对原记录，不能重新构造请求')
        body = build_body(receipt, prepared, reason, origin)
        row = RefundAuthorization(preparation_id=prepared.id, request_id=key, actor_id=actor.id,
                                  actor_name=actor.username, evidence=evidence, body=body, digest=digest(body))
        db.add(row)
        db.add(PaymentEvent(order_id=order.id, attempt_id=key, kind=AUTHORIZED,
                            actor_id=actor.id, actor_name=actor.username, evidence=evidence))
        await db.commit()
        return True
    except BaseException:
        await db.rollback()
        raise


async def begin_send(db: AsyncSession, order: Order, *, actor: User, key: str, authorization_id: str,
                     expected_digest: str, refund_no: str, amount: int, evidence: str, cfg, enabled: bool):
    """Replay never sends; fresh explicit attempt commits before returning frozen network input.

    No automatic resurrection of in-flight work. Even signed HTTP failures remain unknown.
    Any independent notify/query requires operator follow-up, not blind resubmission here.
    """
    try:
        await lock_order(db, order)
        receipt = await original_receipt(db, order, 'wechat')
        prepared = await request_for(db, order.id)
        row = await authorization_for(db, prepared.id) if prepared else None
        if (row is None or row.request_id != authorization_id or row.digest != expected_digest
                or digest(row.body) != row.digest or prepared.out_refund_no != refund_no or prepared.amount != amount):
            raise PaymentConflict('发送确认不匹配已冻结授权/准备；不重新编号或改正文')
        note = canonical({'v': 1, 'authorization_id': authorization_id, 'digest': row.digest, 'evidence': evidence})
        existing = await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == STARTED, PaymentEvent.attempt_id == key))
        if existing:
            if (existing.order_id, existing.actor_id, existing.evidence) != (order.id, actor.id, note):
                raise PaymentConflict('同发送尝试ID的归属/内容不同')
            view = await attempt_view(db, existing)
            await db.commit()
            return view, None
        if not enabled:
            raise PaymentConflict('真实退款发送开关关闭；历史授权不会自动发送')
        if (prepared.merchant_id, prepared.app_id) != (cfg.mchid, cfg.appid):
            raise PaymentConflict('当前发送凭据不属于原商户/应用')
        if await refund_for(db, order.id) or await prior_refund_activity(db, order.id):
            raise PaymentConflict('已有退款凭证或通知/查询，请继续核验，不重复申请')
        latest = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order.id,
                                PaymentEvent.kind == STARTED).order_by(PaymentEvent.id.desc()).limit(1))
        if latest:
            previous = await attempt_view(db, latest)
            accepted = await db.scalar(select(PaymentEvent.id).where(PaymentEvent.order_id == order.id,
                                        PaymentEvent.kind == OBSERVED, PaymentEvent.evidence.like('%"state":"accepted"%')).limit(1))
            if (accepted or previous['state'] != 'unknown'
                    or (datetime.now(timezone.utc) - as_utc(latest.create_time)).total_seconds() < RETRY_SECONDS):
                raise PaymentConflict('已受理或尝试仍可能进行中；刷新并核验，不并发发送')
        start = PaymentEvent(order_id=order.id, attempt_id=key, kind=STARTED, actor_id=actor.id,
                             actor_name=actor.username, evidence=note)
        db.add(start)
        # Return only immutable scalars after commit; no ORM access may start a transaction across I/O.
        packet = (row.body, order.order_no, receipt.transaction_id, receipt.amount)
        await db.commit()
        return {'attempt_id': key, 'state': 'unknown'}, packet
    except BaseException:
        await db.rollback()
        raise


async def finish_send(db: AsyncSession, order: Order, *, key: str, result) -> dict:
    """Durable observation, not financial completion; retain even if actor loses role during I/O."""
    try:
        await lock_order(db, order)
        start = await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == STARTED, PaymentEvent.attempt_id == key,
                                                         PaymentEvent.order_id == order.id))
        if start is None:
            raise PaymentConflict('缺少持久发送尝试，不能补造渠道结果')
        prior = await db.scalar(select(PaymentEvent.id).where(PaymentEvent.kind == OBSERVED, PaymentEvent.attempt_id == key))
        if prior is None:
            note = {'state': 'unknown'} if result is None else {
                'state': 'accepted', 'provider_status': result.state, 'refund_id': result.refund_id}
            db.add(PaymentEvent(order_id=order.id, attempt_id=key, kind=OBSERVED,
                                actor_id=start.actor_id, actor_name=start.actor_name, evidence=canonical(note)))
        await db.commit()
        return await attempt_view(db, start)
    except BaseException:
        await db.rollback()
        raise
