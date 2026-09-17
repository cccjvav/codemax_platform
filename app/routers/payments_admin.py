"""Administrator payment workbench: read-only inventory and explicit signed reconciliation.

No charge, close-order or refund request is sent here. Reconciliation is operator initiated,
not a scheduled accounting system. Existing receipt/snapshot contracts remain authoritative.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, StrictInt
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import require_admin, require_finance_origin
from ..models import Order, PaymentEvent, PaymentReceipt, User
from ..payment_ledger import PaymentConflict, lock_order, settle
from ..payment_review import REVIEW_KIND, review_candidates, review_payload, review_states
from ..ratelimit import rate_limit
from ..site import page_context, templates
from ..wechat_pay import WeChatPayError, assert_notify_configuration, pay_config, query_order
from .shop import EvidenceIn

router = APIRouter(tags=['订单管理'])


@router.get('/admin/payments', include_in_schema=False)
async def payments_page(request: Request):
    """Public login shell only; every data/operation endpoint independently requires an active admin."""
    return templates.TemplateResponse(request, 'payments-admin.html',
                                      page_context(request, title='订单与收款管理'),
                                      headers={'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow'})


def order_summary(order: Order) -> dict:
    """Admin-visible frozen contract, never a price/product lookup from today's settings."""
    return {'id': order.id, 'order_no': order.order_no, 'user_id': order.user_id,
            'product_name': order.product_name, 'amount': order.amount, 'currency': order.currency,
            'status': order.status, 'payment_mode': order.payment_mode or 'legacy',
            'merchant_id': order.merchant_id, 'app_id': order.app_id,
            'delivery_bound': bool(order.delivery_key), 'create_time': order.create_time}


@router.get('/shop/admin/orders')
async def orders(response: Response, bucket: Literal['all', 'manual', 'wechat', 'legacy', 'issues', 'needs_review', 'reviewed'] = 'all',
                 order_no: str | None = Query(None, min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_-]+$'),
                 before: int | None = Query(None, gt=0),
                 db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """50-row keyset pages; 'issues' is historical evidence, not a magically resolved incident queue."""
    response.headers['Cache-Control'] = 'no-store'
    query = select(Order, User.username).join(User, Order.user_id == User.id)
    has_receipt = exists(select(PaymentReceipt.id).where(PaymentReceipt.order_id == Order.id))
    if bucket in ('manual', 'wechat'):
        query = query.where(Order.payment_mode == bucket, ~has_receipt, Order.status.in_(('pending', 'closed')))
    elif bucket == 'legacy':
        query = query.where(Order.payment_mode.is_(None))
    elif bucket == 'issues':
        query = query.where(exists(select(PaymentEvent.id).where(PaymentEvent.order_id == Order.id,
                            PaymentEvent.kind.in_(('prepay_unknown', 'query_unknown', 'query_conflict', 'query_refund')))))
    if order_no is not None:
        query = query.where(Order.order_no == order_no)
    if bucket not in ('needs_review', 'reviewed'):
        if before is not None:
            query = query.where(Order.id < before)
        rows = (await db.execute(query.order_by(Order.id.desc()).limit(50))).all()
        return {'orders': [{**order_summary(o), 'username': username} for o, username in rows],
                'next_cursor': rows[-1][0].id if len(rows) == 50 else None}
    # A projection filter may skip rows: bound work to 200 candidates, not an unbounded fill loop.
    now = datetime.now(timezone.utc)
    query = query.where(review_candidates(now))
    output, scanned, cursor = [], 0, before
    while scanned < 200:
        page = query.where(Order.id < cursor) if cursor is not None else query
        rows = (await db.execute(page.order_by(Order.id.desc()).limit(50))).all()
        states = await review_states(db, [o for o, _ in rows], now=now)
        for index, (order, username) in enumerate(rows):
            scanned += 1
            cursor = order.id
            state = states[order.id]
            matches = state['state'] == 'reviewed' if bucket == 'reviewed' else state['state'] in ('open', 'followup')
            if matches:
                output.append({**order_summary(order), 'username': username, 'review': state})
            if len(output) == 50 or scanned == 200:
                more = len(rows) == 50 or index < len(rows) - 1
                return {'orders': output, 'next_cursor': cursor if more else None}
        if len(rows) < 50:
            return {'orders': output, 'next_cursor': None}
    raise AssertionError('bounded review pagination must return')



class ReconcileIn(EvidenceIn):
    confirm_order_no: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_-]+$')


@router.post('/shop/admin/orders/{order_no}/reconcile',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('payment-reconcile', 'RATE_LIMIT_TOOLS'))])
async def reconcile(order_no: str, proof: ReconcileIn, response: Response,
                    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Commit started before network; only authenticated matching SUCCESS may atomically settle.

    Recheck administrator revision/role after network. Query failures cannot mark an order unpaid;
    non-success observations never close/refund/revoke local rights. Orphan started = unknown.
    """
    response.headers['Cache-Control'] = 'no-store'
    if proof.confirm_order_no != order_no:
        raise HTTPException(409, '确认单号与目标不一致')
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(404, '订单不存在')
    cfg = pay_config()
    if order.payment_mode != 'wechat' or (order.merchant_id, order.app_id) != (cfg.mchid, cfg.appid):
        raise HTTPException(409, '只能核查与当前商户凭据匹配的微信订单，不能猜测历史合同')
    try:
        if not cfg.configured:
            raise WeChatPayError('missing configuration')
        assert_notify_configuration(cfg)
    except WeChatPayError:
        raise HTTPException(503, '微信下单/验签凭据尚未配齐') from None
    oid, amount = order.id, order.amount
    actor_id, actor_name, revision = admin.id, admin.username, admin.credential_version
    attempt = uuid.uuid4().hex
    def observation(kind: str, evidence: str | None = None):
        return PaymentEvent(order_id=oid, attempt_id=attempt, kind=kind, actor_id=actor_id,
                            actor_name=actor_name, evidence=evidence)
    db.add(observation('query_started', proof.evidence))
    await db.commit()  # neither user nor order locks are held while contacting the merchant API
    try:
        result = await query_order(cfg, out_trade_no=order_no, total=amount)
    except WeChatPayError:
        db.add(observation('query_unknown', '请求或应答验证失败；未改收款状态'))
        await db.commit()
        raise HTTPException(502, '查单未取得可信结果；已保留核查记录，请勿据此认定未付款') from None
    await db.execute(update(User).where(User.id == actor_id)
                     .values(credential_version=User.credential_version, update_time=User.update_time)
                     .execution_options(synchronize_session=False))
    current = await db.get(User, actor_id, populate_existing=True)
    if current is None or current.role != 1 or current.status != 1 or current.credential_version != revision:
        db.add(observation('query_aborted', '发起者权限/凭据在核查期间变更，未补记收款'))
        await db.commit()
        raise HTTPException(403, '权限已变更，结果未补记，请重新登录核对记录')
    if result.state == 'SUCCESS':
        try:
            changed = await settle(db, order, source='wechat', transaction_id=result.transaction_id,
                                   merchant_id=cfg.mchid, app_id=cfg.appid, amount=amount,
                                   paid_at=result.paid_at, actor=current,
                                   audit_event=observation('query_success', '已验签查单成功，与收款凭证同事务提交'))
        except PaymentConflict:
            db.add(observation('query_conflict', '可信查单与既有订单/流水不一致，需要人工处理'))
            await db.commit()
            raise HTTPException(409, '查单与已有凭证冲突；未覆盖原收款记录') from None
    else:
        await lock_order(db, order)
        kind = 'query_' + result.state.lower()
        if result.state != 'REFUND' and order.status in ('paid', 'downloaded'):
            kind = 'query_conflict'
        db.add(observation(kind, '已验签观察状态；不自动关单、退款或撤销交付'))
        await db.commit()
        changed = False
    return {'order_no': order_no, 'observed_state': result.state, 'status': order.status,
            'changed': changed, 'attempt_id': attempt,
            'warning': '查单不是退款；非成功结果不会撤销已有权益。退款、冲突和历史异常仍需人工跟进。'}


class ReviewIn(EvidenceIn):
    evidence: str = Field(min_length=3, max_length=160, pattern=r"^[^\x00-\x1f]+$")
    action: Literal['followup', 'close', 'reopen']
    snapshot: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_version: StrictInt = Field(ge=0, le=9223372036854775807)
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    confirm_order_no: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_-]+$')


@router.post('/shop/admin/orders/{order_no}/review',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('payment-review', 'RATE_LIMIT_TOOLS'))])
async def review_order(order_no: str, proof: ReviewIn, db: AsyncSession = Depends(get_db),
                       admin: User = Depends(require_admin)):
    """Append an operator assessment with optimistic facts/version and exact-request replay checks.

    This records review only; no receipt, provider request or entitlement change. A later visible
    event (even with a lower ID), aged orphan or receipt makes a prior close marker stale.
    """
    if order_no != proof.confirm_order_no:
        raise HTTPException(409, '确认单号与目标不一致')
    oid, revision = admin.id, admin.credential_version
    await db.execute(update(User).where(User.id == oid)
                     .values(credential_version=User.credential_version, update_time=User.update_time)
                     .execution_options(synchronize_session=False))
    await db.refresh(admin)
    if admin.role != 1 or admin.status != 1 or admin.credential_version != revision:
        raise HTTPException(403, '权限已变更，请重新登录')
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(404, '订单不存在')
    await lock_order(db, order)
    try:
        payload = review_payload(proof.action, proof.snapshot, proof.expected_version, proof.evidence)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    previous = await db.scalar(select(PaymentEvent).where(PaymentEvent.attempt_id == proof.request_id,
                                                         PaymentEvent.kind == REVIEW_KIND))
    if previous is not None:
        if previous.order_id != order.id or previous.actor_id != admin.id or previous.evidence != payload:
            raise HTTPException(409, '复核请求标识已用于另一项操作，不可改写')
        await db.commit()
        return {'saved': True, 'review_id': previous.id, 'replayed': True}
    state = (await review_states(db, [order]))[order.id]
    if state['snapshot'] != proof.snapshot or state['version'] != proof.expected_version:
        raise HTTPException(409, '订单进展或复核记录已变化，请刷新后重新核对')
    if proof.action == 'close' and state['state'] == 'none':
        raise HTTPException(409, '没有待复核事项；如需人工跟进，请先登记说明')
    entry = PaymentEvent(order_id=order.id, attempt_id=proof.request_id, kind=REVIEW_KIND,
                         actor_id=admin.id, actor_name=admin.username, evidence=payload)
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, '复核请求标识发生竞争，请刷新核对记录') from None
    return {'saved': True, 'review_id': entry.id, 'replayed': False}
