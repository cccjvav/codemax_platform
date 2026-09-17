"""Verified refund notification inbox, NOT refund completion or an outgoing refund API.

Notification resources omit channel and currency: never invent ORIGINAL from SUCCESS.
Persist minimal correlated observations before ACK; administrators use signed refund queries
for the existing full-original-refund receipt/entitlement workflow. No in-memory worker.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from .models import Order, PaymentEvent
from .payment_ledger import PaymentConflict, lock_order
from .refunds import original_receipt
from .timeutil import as_utc
from .wechat_pay import (
    PayConfig,
    WeChatPayError,
    assert_notify_fresh,
    assert_notify_identity,
    decrypt_resource,
    verify_notify_signature,
)

NOTICE_KIND = 'refund_notify_signal'
STATES = ('SUCCESS', 'ABNORMAL', 'CLOSED')


@dataclass(frozen=True)
class RefundNotice:
    notification_id: str
    created_at: str
    merchant_id: str
    order_no: str
    transaction_id: str
    out_refund_no: str
    refund_id: str
    state: str
    total: int
    refund: int
    payer_total: int
    payer_refund: int
    completed_at: str | None


def _identifier(value, pattern: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise WeChatPayError('退款通知标识格式无效')
    return value


def _moment(value) -> str:
    """Normalize aware provider timestamps; old notifications may legitimately be retried today."""
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError('time')
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            raise ValueError('timezone')
        moment = moment.astimezone(timezone.utc)
        if moment > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError('future')
        return moment.isoformat()
    except (ValueError, OverflowError):
        raise WeChatPayError('退款通知时间无效') from None


def parse_notice(cfg: PayConfig, headers: Headers, raw: bytes) -> RefundNotice:
    """Exactly one bounded signature header; authenticate raw UTF-8 BEFORE JSON/AES parsing.

    Only the three documented event types are accepted. Strict integer monetary bounds apply
    even though this inbox cannot settle refunds. No payer account or full ciphertext is saved.
    """
    values = {}
    for name, limit in [('Wechatpay-Serial', 128), ('Wechatpay-Timestamp', 12),
                        ('Wechatpay-Nonce', 128), ('Wechatpay-Signature', 1024)]:
        found = headers.getlist(name)
        if len(found) != 1 or not 1 <= len(found[0]) <= limit:
            raise WeChatPayError('退款通知签名头缺失、重复或超长')
        values[name] = found[0]
    assert_notify_fresh(values['Wechatpay-Timestamp'])
    assert_notify_identity(cfg, values['Wechatpay-Serial'])
    try:
        body = raw.decode('utf-8')
        verify_notify_signature(cfg.platform_cert, timestamp=values['Wechatpay-Timestamp'],
                                nonce=values['Wechatpay-Nonce'], body=body, signature=values['Wechatpay-Signature'])
        envelope = json.loads(body)
    except (UnicodeError, ValueError, RecursionError):
        raise WeChatPayError('退款通知签名或JSON无效') from None
    if (not isinstance(envelope, dict) or envelope.get('resource_type') != 'encrypt-resource'
            or envelope.get('event_type') not in tuple('REFUND.' + state for state in STATES)):
        raise WeChatPayError('尚不支持此退款通知类型')
    resource = envelope.get('resource')
    if (not isinstance(resource, dict) or resource.get('algorithm') != 'AEAD_AES_256_GCM'
            or resource.get('original_type') != 'refund'
            or any(not isinstance(resource.get(k), str) for k in ('ciphertext', 'nonce'))
            or not 1 <= len(resource['nonce'].encode()) <= 32
            or not isinstance(resource.get('associated_data', ''), str)
            or len(resource.get('associated_data', '').encode()) > 16):
        raise WeChatPayError('退款通知加密资源格式无效')
    data = decrypt_resource(cfg.api_v3_key, ciphertext=resource['ciphertext'], nonce=resource['nonce'],
                            associated_data=resource.get('associated_data', ''))
    if (not isinstance(data, dict) or data.get('mchid') != cfg.mchid
            or data.get('refund_status') not in STATES or envelope['event_type'] != 'REFUND.' + data['refund_status']):
        raise WeChatPayError('退款通知商户或内外状态不一致')
    amount = data.get('amount')
    if (not isinstance(amount, dict)
            or any(type(amount.get(k)) is not int for k in ('total', 'refund', 'payer_total', 'payer_refund'))
            or not 0 < amount['refund'] <= amount['total'] <= 2147483647
            or not 0 <= amount['payer_total'] <= amount['total']
            or not 0 <= amount['payer_refund'] <= min(amount['refund'], amount['payer_total'])):
        raise WeChatPayError('退款通知金额无效')
    completed = _moment(data.get('success_time')) if data['refund_status'] == 'SUCCESS' else None
    return RefundNotice(
        _identifier(envelope.get('id'), r'[A-Za-z0-9_-]{1,36}'), _moment(envelope.get('create_time')),
        cfg.mchid, _identifier(data.get('out_trade_no'), r'[A-Za-z0-9_-]{1,32}'),
        _identifier(data.get('transaction_id'), r'[A-Za-z0-9_-]{1,32}'),
        _identifier(data.get('out_refund_no'), r'[A-Za-z0-9_\-|*@]{1,64}'),
        _identifier(data.get('refund_id'), r'[0-9]{1,32}'), data['refund_status'],
        amount['total'], amount['refund'], amount['payer_total'], amount['payer_refund'], completed)


def notice_evidence(notice: RefundNotice) -> str:
    """Bounded display data + canonical business fingerprint, not a retained RSA proof/WORM record."""
    canonical = json.dumps(asdict(notice), sort_keys=True, separators=(',', ':'))
    value = {'v': 1, 'notification_id': notice.notification_id, 'refund_no': notice.out_refund_no,
             'refund_id': notice.refund_id, 'state': notice.state, 'refund': notice.refund,
             'partial': notice.refund < notice.total, 'completed_at': notice.completed_at,
             'created_at': notice.created_at, 'fingerprint': hashlib.sha256(canonical.encode()).hexdigest()}
    result = json.dumps(value, sort_keys=True, separators=(',', ':'))
    if len(result) > 500:
        raise WeChatPayError('退款通知摘要超出记录上限')
    return result


async def save_notice(db: AsyncSession, cfg: PayConfig, notice: RefundNotice) -> bool:
    """Own commit/rollback. Exact provider-ID replay is a no-op; altered facts/ownership conflict.

    Hash merchant + provider notification ID into the existing attempt slot. One fixed kind
    across states prevents an ID from being reused to inject a different state under another kind.
    """
    try:
        order = await db.scalar(select(Order).where(Order.order_no == notice.order_no))
        if order is None:
            raise PaymentConflict('退款通知订单或原商户/应用不匹配')
        await lock_order(db, order)
        if (order.merchant_id, order.app_id, order.currency) != (cfg.mchid, cfg.appid, 'CNY'):
            raise PaymentConflict('退款通知原商户/应用或币种不匹配')
        receipt = await original_receipt(db, order, 'wechat')
        if (notice.merchant_id != receipt.merchant_id or notice.transaction_id != receipt.transaction_id
                or notice.total != receipt.amount
                or (notice.completed_at and receipt.paid_at
                    and datetime.fromisoformat(notice.completed_at) < as_utc(receipt.paid_at))):
            raise PaymentConflict('退款通知与原收款凭证不匹配')
        attempt = hashlib.sha256(('wechat-refund-notice\0' + notice.merchant_id + '\0' + notice.notification_id).encode()).hexdigest()[:32]
        evidence = notice_evidence(notice)
        existing = await db.scalar(select(PaymentEvent).where(PaymentEvent.attempt_id == attempt, PaymentEvent.kind == NOTICE_KIND))
        if existing:
            if (existing.order_id != order.id or existing.evidence != evidence
                    or existing.actor_id is not None or existing.actor_name is not None):
                raise PaymentConflict('相同通知ID与已记录事实冲突')
            await db.commit()
            return False
        db.add(PaymentEvent(order_id=order.id, attempt_id=attempt, kind=NOTICE_KIND, evidence=evidence))
        await db.commit()  # Durable inbox before ACK; no receipt/state/entitlement mutations.
        return True
    except IntegrityError as exc:
        await db.rollback()
        raise PaymentConflict('退款通知ID归属冲突，请按原通知重试核对') from exc
    except BaseException:
        await db.rollback()  # Also cancel/timeout: never acknowledge an uncommitted inbox entry.
        raise


def notice_view(event: PaymentEvent | None) -> dict | None:
    """Fail closed for malformed stored summaries; display/refill is never financial authorization."""
    if event is None or event.kind != NOTICE_KIND or event.actor_id is not None or event.actor_name is not None:
        return None
    try:
        if not isinstance(event.evidence, str) or len(event.evidence) > 500:
            return None
        data = json.loads(event.evidence)
        if (not isinstance(data, dict) or type(data.get('v')) is not int or data['v'] != 1
                or data.get('state') not in STATES or type(data.get('partial')) is not bool
                or type(data.get('refund')) is not int or not 0 < data['refund'] <= 2147483647):
            return None
        number = _identifier(data.get('refund_no'), r'[A-Za-z0-9_\-|*@]{1,64}')
        identity = _identifier(data.get('notification_id'), r'[A-Za-z0-9_-]{1,36}')
        refund_id = _identifier(data.get('refund_id'), r'[0-9]{1,32}')
        _identifier(data.get('fingerprint'), r'[0-9a-f]{64}')
        return {'notification_id': identity, 'refund_no': number, 'refund_id': refund_id,
                'state': data['state'], 'partial': data['partial'], 'refund': data['refund'],
                'received_at': event.create_time}
    except (WeChatPayError, ValueError, RecursionError):
        return None
