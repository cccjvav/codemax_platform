"""Explicit Native close attempts, not local expiry, refunds or a daily reconciliation ledger."""
import json
from datetime import datetime, timezone

from sqlalchemy import select

from .models import PaymentEvent, PaymentReceipt
from .payment_ledger import PaymentConflict, lock_order
from .timeutil import as_utc

STARTED = 'channel_close_started'
ACKNOWLEDGED = 'channel_close_acknowledged'
UNKNOWN = 'channel_close_unknown'
QUERY_MAX_AGE = 300
RETRY_SECONDS = 60


async def attempt_view(db, start):
    if start is None:
        return None
    outcome = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == start.order_id,
        PaymentEvent.attempt_id == start.attempt_id, PaymentEvent.kind.in_((ACKNOWLEDGED, UNKNOWN))))
    return {'request_id': start.attempt_id, 'actor': start.actor_name, 'evidence': json.loads(start.evidence)['evidence'],
            'created_at': start.create_time, 'state': 'acknowledged' if outcome and outcome.kind == ACKNOWLEDGED else 'unknown'}


async def eligibility(db, order, *, cfg, enabled):
    """Read-only hint, rechecked under the order lock before writing. Fresh NOTPAY is not a payment guarantee."""
    query = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order.id,
        PaymentEvent.kind.startswith('query_', autoescape=True)).order_by(PaymentEvent.id.desc()).limit(1))
    latest = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order.id,
        PaymentEvent.kind == STARTED).order_by(PaymentEvent.id.desc()).limit(1))
    now = datetime.now(timezone.utc)
    allowed = bool(enabled and order.payment_mode == 'wechat' and order.status == 'closed'
        and order.currency == 'CNY' and not order.transaction_id
        and (order.merchant_id, order.app_id) == (cfg.mchid, cfg.appid)
        and query and query.kind == 'query_notpay'
        and -5 <= (now - as_utc(query.create_time)).total_seconds() <= QUERY_MAX_AGE
        and (not latest or (query.id > latest.id and (now - as_utc(latest.create_time)).total_seconds() >= RETRY_SECONDS)))
    if allowed:
        allowed = not await db.scalar(select(PaymentReceipt.id).where(PaymentReceipt.order_id == order.id))
    if allowed:
        allowed = not await db.scalar(select(PaymentEvent.id).where(PaymentEvent.order_id == order.id,
            PaymentEvent.kind == ACKNOWLEDGED).limit(1))
    return allowed, query, latest


async def view(db, order, *, cfg, enabled):
    allowed, query, latest = await eligibility(db, order, cfg=cfg, enabled=enabled)
    return {'enabled': enabled, 'allowed': allowed, 'query_attempt_id': query.attempt_id if query else None,
            'latest': await attempt_view(db, latest)}


async def begin(db, order, *, actor, key, query_key, amount, evidence, cfg, enabled):
    """Caller locks/revalidates actor first. Commit STARTED before returning immutable outbound parameters."""
    try:
        await lock_order(db, order)
        note = json.dumps({'v': 1, 'query': query_key, 'amount': amount, 'evidence': evidence},
                          sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        existing = await db.scalar(select(PaymentEvent).where(PaymentEvent.kind == STARTED, PaymentEvent.attempt_id == key))
        if existing:
            if (existing.order_id, existing.actor_id, existing.evidence) != (order.id, actor.id, note):
                raise PaymentConflict('同关单请求ID归属或内容不同；不能覆盖首次尝试')
            result = await attempt_view(db, existing)
            await db.commit()
            return result, None
        allowed, query, _ = await eligibility(db, order, cfg=cfg, enabled=enabled)
        if not allowed or query.attempt_id != query_key or type(amount) is not int or amount != order.amount:
            raise PaymentConflict('仅本地已关闭且无收款的原微信单可关渠道；须先取得5分钟内最新可信NOTPAY并确认金额，未知重试需新查单及等待60秒')
        entry = PaymentEvent(order_id=order.id, attempt_id=key, kind=STARTED, actor_id=actor.id,
                             actor_name=actor.username, evidence=note)
        db.add(entry)
        packet = order.order_no
        await db.commit()
        return {'request_id': key, 'state': 'unknown'}, packet
    except BaseException:
        await db.rollback()
        raise


async def finish(db, order, key, acknowledged):
    """Keep the first initiating actor even after revocation; observation never mutates order/receipt/rights."""
    try:
        await lock_order(db, order)
        start = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order.id,
            PaymentEvent.kind == STARTED, PaymentEvent.attempt_id == key))
        if start is None:
            raise PaymentConflict('缺少持久关单开始记录')
        existing = await db.scalar(select(PaymentEvent.id).where(PaymentEvent.order_id == order.id,
            PaymentEvent.attempt_id == key, PaymentEvent.kind.in_((ACKNOWLEDGED, UNKNOWN))))
        if not existing:
            db.add(PaymentEvent(order_id=order.id, attempt_id=key,
                kind=ACKNOWLEDGED if acknowledged else UNKNOWN, actor_id=start.actor_id, actor_name=start.actor_name,
                evidence='已验签204空应答；仍需独立查原单，不改本地收款' if acknowledged else '关单结果未知；不得推断未付款或自动重发'))
        await db.commit()
        return await attempt_view(db, start)
    except BaseException:
        await db.rollback()
        raise
