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
from sqlalchemy.orm import aliased

from .models import Order, PaymentEvent, RefundAuthorization, RefundSendStop, User
from .order_state import as_utc
from .payment_ledger import PaymentConflict, lock_order
from .refund_requests import prior_refund_activity, request_for
from .refunds import original_receipt, refund_for

AUTHORIZED = 'refund_authorized'
STARTED = 'refund_send_started'
OBSERVED = 'refund_send_observed'
STOPPED = 'refund_send_stopped'
REAUTHORIZED = 'refund_reauthorized'
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
    child = aliased(RefundAuthorization)
    return await db.scalar(select(RefundAuthorization).where(RefundAuthorization.preparation_id == prepared_id,
        ~select(child.id).where(child.supersedes_id == RefundAuthorization.id).exists()))


async def attempt_view(db: AsyncSession, start: PaymentEvent) -> dict:
    outcome = await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == OBSERVED,
                                                       PaymentEvent.attempt_id == start.attempt_id))
    note = json.loads(outcome.evidence) if outcome else {'state': 'unknown'}
    return {'attempt_id': start.attempt_id, 'actor': start.actor_name, 'created_at': start.create_time,
            'state': note['state'], 'provider_status': note.get('provider_status'),
            'refund_id': note.get('refund_id')}


async def reauthorization_activity(db: AsyncSession, order_id: int) -> bool:
    """Any durable send or independent query evidence blocks editing, including orphan observations."""
    return await db.scalar(select(PaymentEvent.id).where(PaymentEvent.order_id == order_id,
        (PaymentEvent.kind.in_((STARTED, OBSERVED, 'refund_notify_signal'))
         | PaymentEvent.kind.startswith('refund_query_', autoescape=True)
         | PaymentEvent.kind.startswith('refund_verify_', autoescape=True))).limit(1)) is not None


def authorization_view(row, stop=None, parent_key=None):
    """Immutable authorization identity; stop applies to this version, not every successor."""
    return {'authorization_id': row.request_id, 'digest': row.digest, 'body': json.loads(row.body),
            'actor': row.actor_name, 'evidence': row.evidence, 'created_at': row.created_at,
            'supersedes_authorization_id': parent_key, 'stop': stop_view(stop)}


async def submission_view(db: AsyncSession, prepared) -> dict | None:
    """Current leaf plus bounded immutable history, independent of event pagination. GET never sends."""
    if prepared is None:
        return None
    row = await authorization_for(db, prepared.id)
    if row is None:
        return None
    parent = aliased(RefundAuthorization)
    rows = (await db.execute(select(RefundAuthorization, RefundSendStop, parent.request_id)
        .outerjoin(RefundSendStop, RefundSendStop.authorization_id == RefundAuthorization.id)
        .outerjoin(parent, parent.id == RefundAuthorization.supersedes_id)
        .where(RefundAuthorization.preparation_id == prepared.id)
        .order_by((RefundAuthorization.id == row.id).desc(), RefundAuthorization.id.desc()).limit(51))).all()
    history = [authorization_view(auth, stop, parent_key) for auth, stop, parent_key in rows[:50]]
    latest = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == prepared.order_id,
                             PaymentEvent.kind == STARTED).order_by(PaymentEvent.id.desc()).limit(1))
    return {**history[0], 'history': history, 'history_has_more': len(rows) > 50,
            'reauthorize_allowed': bool(history[0]['stop']) and latest is None
                and not await refund_for(db, prepared.order_id) and not await reauthorization_activity(db, prepared.order_id),
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
            if existing.supersedes_id is not None or (existing.preparation_id, existing.actor_id, existing.evidence, json.loads(existing.body)['reason']) != (
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
        row = await db.scalar(select(RefundAuthorization).where(RefundAuthorization.preparation_id == prepared.id,
                              RefundAuthorization.request_id == authorization_id)) if prepared else None
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
        current = await authorization_for(db, prepared.id)
        if current is None or current.id != row.id:
            raise PaymentConflict('授权已被新版本替代；旧版本不能开始发送，请刷新核对')
        if await stop_for(db, row.id):
            raise PaymentConflict('本授权已停止后续本站发送；不能撤回渠道处理中退款，请独立查询原号')
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


async def stop_for(db: AsyncSession, authorization_id: int) -> RefundSendStop | None:
    return await db.scalar(select(RefundSendStop).where(RefundSendStop.authorization_id == authorization_id))


def stop_view(row: RefundSendStop | None) -> dict | None:
    if row is None:
        return None
    return {'request_id': row.request_id, 'actor': row.actor_name, 'evidence': row.evidence,
            'created_at': row.created_at, 'scope': 'future_local_sends_only'}


async def stop_sending(db: AsyncSession, order: Order, *, actor: User, key: str, authorization_id: str,
                       expected_digest: str, refund_no: str, amount: int, evidence: str) -> tuple[dict, bool]:
    """Caller locks/checks current user first; order lock serializes stop against durable send start.

    Always allowed for an existing matching authorization, even after a send/receipt or with the
    deployment gate off. Never calls a provider, edits the request, releases its number, cancels
    an in-flight attempt or restores download rights. Exact replay preserves first facts.
    """
    try:
        await lock_order(db, order)
        prepared = await request_for(db, order.id)
        auth = await db.scalar(select(RefundAuthorization).where(RefundAuthorization.preparation_id == prepared.id,
                               RefundAuthorization.request_id == authorization_id)) if prepared else None
        if (auth is None or auth.request_id != authorization_id or auth.digest != expected_digest
                or digest(auth.body) != auth.digest or prepared.out_refund_no != refund_no
                or type(amount) is not int or prepared.amount != amount):
            raise PaymentConflict('停止确认与原授权/摘要/商户退款号/全额不符')
        existing = await db.scalar(select(RefundSendStop).where(RefundSendStop.request_id == key))
        if existing:
            if (existing.authorization_id, existing.actor_id, existing.evidence) != (auth.id, actor.id, evidence):
                raise PaymentConflict('同停止请求ID的归属或内容不同，不能覆盖首次记录')
            view = stop_view(existing)
            await db.commit()
            return view, False
        if await stop_for(db, auth.id):
            raise PaymentConflict('原授权已有停止记录，请读取首次记录，不重建授权或退款号')
        row = RefundSendStop(authorization_id=auth.id, request_id=key, actor_id=actor.id,
                             actor_name=actor.username, evidence=evidence)
        db.add(row)
        await db.flush()
        db.add(PaymentEvent(order_id=order.id, attempt_id=key, kind=STOPPED, actor_id=actor.id,
                            actor_name=actor.username, evidence=evidence))
        await db.commit()
        return stop_view(row), True
    except BaseException:
        await db.rollback()
        raise


async def reauthorize(db: AsyncSession, order: Order, *, actor: User, key: str, authorization_id: str,
                      expected_digest: str, refund_no: str, amount: int, reason: str, evidence: str, origin: str):
    """Explicit replacement of a stopped NEVER-started leaf. Keep all old bodies/stops and original reference.

    Actor lock/revision is checked by the route before this order lock. First-key replay is
    validated before mutable current-state/config checks, enabling recovery after later progress.
    """
    try:
        await lock_order(db, order)
        receipt = await original_receipt(db, order, 'wechat')
        prepared = await request_for(db, order.id)
        parent = await db.scalar(select(RefundAuthorization).where(
            RefundAuthorization.request_id == authorization_id,
            RefundAuthorization.preparation_id == prepared.id)) if prepared else None
        if (actor is None or actor.role != 1 or actor.status != 1 or parent is None
                or parent.digest != expected_digest or digest(parent.body) != expected_digest
                or prepared.out_refund_no != refund_no or type(amount) is not int or prepared.amount != amount):
            raise PaymentConflict('重新授权须核对原授权、摘要、固定退款号和全额')
        existing = await db.scalar(select(RefundAuthorization).where(RefundAuthorization.request_id == key))
        if existing:
            if (existing.supersedes_id, existing.preparation_id, existing.actor_id, existing.evidence,
                    json.loads(existing.body)['reason']) != (parent.id, prepared.id, actor.id, evidence, reason):
                raise PaymentConflict('同重新授权ID归属或内容不同，不可覆盖首次记录')
            await db.commit()
            return existing, False
        current = await authorization_for(db, prepared.id)
        started = await db.scalar(select(PaymentEvent.id).where(PaymentEvent.order_id == order.id,
                                  PaymentEvent.kind == STARTED).limit(1))
        if (current is None or current.id != parent.id or not await stop_for(db, parent.id) or started
                or await refund_for(db, order.id) or await reauthorization_activity(db, order.id)):
            raise PaymentConflict('只能替代已停止且从未开始发送、无退款观察的最新授权；未知不能当未发送')
        body = build_body(receipt, prepared, reason, origin)
        row = RefundAuthorization(preparation_id=prepared.id, supersedes_id=parent.id, request_id=key,
            actor_id=actor.id, actor_name=actor.username, evidence=evidence, body=body, digest=digest(body))
        db.add(row)
        db.add(PaymentEvent(order_id=order.id, attempt_id=key, kind=REAUTHORIZED,
            actor_id=actor.id, actor_name=actor.username,
            evidence=canonical({'v': 1, 'supersedes': parent.request_id, 'digest': row.digest, 'evidence': evidence})))
        await db.commit()
        return row, True
    except BaseException:
        await db.rollback()
        raise
