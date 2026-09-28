"""Administrator payment workbench: read-only inventory and explicit signed reconciliation.

No charge or refund request is sent here. Channel close is separately gated and explicitly confirmed. Reconciliation is operator initiated,
not a scheduled accounting system. Existing receipt/snapshot contracts remain authoritative.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ConfigDict, Field, StrictInt
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import order_closures
from ..config import settings
from ..database import get_db
from ..deps import require_admin, require_finance_origin
from ..models import Order, PaymentEvent, PaymentReceipt, User
from ..payment_ledger import PaymentConflict, lock_order, settle
from ..payment_review import ISSUES, REVIEW_KIND, review_candidates, review_payload, review_states
from ..ratelimit import rate_limit
from ..refund_notifications import NOTICE_KIND, notice_view
from ..refund_requests import prior_refund_activity, request_for, request_view
from ..refund_submissions import submission_view
from ..refund_verification import jobs_view
from ..refunds import refund_for
from ..site import page_context, templates
from ..wechat_pay import WeChatPayError, assert_notify_configuration, close_order, pay_config, query_order
from .admin_common import confirmed_order, locked_active_admin
from .shop import NO_CONTROL_CHARS, EvidenceIn

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
                            PaymentEvent.kind.in_(ISSUES))))
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
    order = await confirmed_order(db, order_no, proof.confirm_order_no)
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
    current = await locked_active_admin(db, actor_id, revision)
    if current is None:
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
    evidence: str = Field(min_length=3, max_length=160, pattern=NO_CONTROL_CHARS)
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
    if order_no != proof.confirm_order_no:   # 单号不一致先于权限复核返回 409（原有顺序）
        raise HTTPException(409, '确认单号与目标不一致')
    admin = await locked_active_admin(db, admin.id, admin.credential_version)
    if admin is None:
        raise HTTPException(403, '权限已变更，请重新登录')
    order = await confirmed_order(db, order_no, proof.confirm_order_no)
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


class CloseChannelIn(ReconcileIn):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    query_attempt_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    amount: StrictInt = Field(gt=0, le=2147483647)
    evidence: str = Field(min_length=3, max_length=160, pattern=NO_CONTROL_CHARS)


@router.post('/shop/admin/orders/{order_no}/close-channel',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('payment-close', 'RATE_LIMIT_TOOLS'))])
async def close_channel(order_no: str, proof: CloseChannelIn, response: Response,
                        db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Explicit close, not refund or receipt mutation. Same key only reads; no automatic retries."""
    response.headers['Cache-Control'] = 'no-store'
    current = await locked_active_admin(db, admin.id, admin.credential_version)
    if current is None:
        raise HTTPException(403, '权限已变更，请重新登录')
    order = await confirmed_order(db, order_no, proof.confirm_order_no)
    cfg = pay_config()
    try:
        attempt, packet = await order_closures.begin(db, order, actor=current, key=proof.request_id,
            query_key=proof.query_attempt_id, amount=proof.amount, evidence=proof.evidence,
            cfg=cfg, enabled=settings.WX_ORDER_CLOSE_ENABLED)
        if packet is not None:
            acknowledged = False
            try:
                await close_order(cfg, out_trade_no=packet)
                acknowledged = True
            except WeChatPayError:
                pass  # Includes signed business errors, not an unpaid/closed conclusion.
            attempt = await order_closures.finish(db, order, proof.request_id, acknowledged)
        return {'attempt': attempt, 'warning': '仅记录渠道关单观察，不改变本地收款/下载。请单独核查原单；迟到可信SUCCESS仍须入账。'}
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, '关单或保存结果未知；保留原请求ID和完整内容恢复，不能自动重发') from None

# ---------------------------------------------------------------- 单个订单的证据视图（TD-310）
# 原在 routers/shop.py：它是本工作台选单后的详情接口，与上面的订单清单同属管理端只读视图；
# 放在商城路由里时，商城为它一个接口多 import 了 8 个退款/复核/关单模块。路径与函数名不变。


def _refund_view(refund) -> dict | None:
    if refund is None:
        return None
    return {'source': refund.source, 'refund_id': refund.refund_id, 'out_refund_no': refund.out_refund_no,
            'amount': refund.amount, 'currency': refund.currency, 'completed_at': refund.completed_at,
            'received_at': refund.received_at, 'actor': refund.actor_name,
            'recorded_by': 'system' if refund.verification_event_id is not None else 'administrator',
            'verification_event_id': refund.verification_event_id, 'evidence': refund.evidence}


def _receipt_view(receipt: PaymentReceipt | None) -> dict | None:
    if receipt is None:
        return None
    return {'source': receipt.source, 'reference': receipt.transaction_id,
            'amount': receipt.amount, 'currency': receipt.currency,
            'actor': receipt.actor_name, 'evidence': receipt.evidence,
            'paid_at': receipt.paid_at, 'received_at': receipt.received_at}


def _order_contract_view(order: Order) -> dict:
    """订单冻结的合同字段：价格、渠道/商户与交付快照；NULL 渠道显示 legacy。"""
    return {'user_id': order.user_id, 'product_name': order.product_name, 'amount': order.amount,
            'currency': order.currency, 'status': order.status, 'payment_mode': order.payment_mode or 'legacy',
            'merchant_id': order.merchant_id, 'app_id': order.app_id,
            'delivery_key': order.delivery_key, 'delivery_digest': order.delivery_digest,
            'delivery_size': order.delivery_size}


async def _refund_prepare_allowed(db: AsyncSession, order: Order, receipt, refund, prepared) -> bool:
    """只有带商户/应用凭证的微信收款、已付状态、且从未有过任何退款活动的单才能准备退款请求。"""
    return (receipt is not None and receipt.source == 'wechat' and order.status in ('paid', 'downloaded')
            and bool(receipt.merchant_id and receipt.app_id) and not refund and not prepared
            and not await prior_refund_activity(db, order.id))


@router.get('/shop/admin/orders/{order_no}/ledger')  # 本路由没有 prefix，路径写全（原在 prefix=/shop 的商城路由里）
async def payment_ledger(order_no: str, response: Response, before: int | None = Query(None, gt=0),
                         db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Bounded admin-only evidence view. Unknown attempts require real provider reconciliation."""
    response.headers["Cache-Control"] = "no-store"
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(404, '订单不存在')
    receipt = await db.scalar(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id))
    query = select(PaymentEvent).where(PaymentEvent.order_id == order.id)
    if before is not None:
        query = query.where(PaymentEvent.id < before)
    events = list((await db.scalars(query.order_by(PaymentEvent.id.desc()).limit(50))).all())
    review = (await review_states(db, [order]))[order.id]
    refund = await refund_for(db, order.id)
    notice = await db.scalar(select(PaymentEvent).where(PaymentEvent.order_id == order.id, PaymentEvent.kind == NOTICE_KIND)
                             .order_by(PaymentEvent.id.desc()).limit(1))
    prepared = await request_for(db, order.id)
    can_prepare = await _refund_prepare_allowed(db, order, receipt, refund, prepared)
    return {'order_no': order.order_no, 'review': review, 'refund_notice': notice_view(notice),
            'refund_auto_record_enabled': settings.WX_REFUND_AUTO_RECORD_ENABLED,
            'refund_verification': await jobs_view(db, order.id), 'refund_verify_enabled': settings.WX_REFUND_VERIFY_ENABLED,
            'channel_close': await order_closures.view(db, order, cfg=pay_config(), enabled=settings.WX_ORDER_CLOSE_ENABLED),
            'refund_submission': await submission_view(db, prepared), 'refund_send_enabled': settings.WX_REFUND_SEND_ENABLED,
            'refund_request': request_view(prepared, refund), 'refund_prepare_allowed': can_prepare,
            'refund': _refund_view(refund),
            'order': _order_contract_view(order),
            'actions': {'manual': settings.SHOP_PAY_MODE == 'manual', 'mock_binding': settings.ENV == 'development'},
            'receipt': _receipt_view(receipt),
            'events': [{'id': e.id, 'attempt_id': e.attempt_id, 'kind': e.kind,
                        'actor': e.actor_name, 'evidence': e.evidence, 'create_time': e.create_time} for e in events],
            'next_cursor': events[-1].id if len(events) == 50 else None}
