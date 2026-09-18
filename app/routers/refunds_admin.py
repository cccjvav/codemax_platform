"""Administrator full-refund preparation, authorization, gated sending and separate verification."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import refund_submissions as submissions
from ..config import settings
from ..database import get_db, lock_user
from ..deps import require_admin, require_finance_origin
from ..models import Order, PaymentEvent, User
from ..payment_ledger import PaymentConflict, lock_order
from ..ratelimit import rate_limit
from ..refund_requests import prepare_request, request_for, request_view
from ..refunds import original_receipt, record_refund, refund_for
from ..wechat_pay import WeChatPayError, assert_notify_configuration, pay_config, query_full_refund, submit_full_refund
from .shop import EvidenceIn

router = APIRouter(tags=['订单管理'])


class RefundIn(EvidenceIn):
    evidence: str = Field(min_length=3, max_length=160, pattern=r'^[^\x00-\x1f]+$')
    confirm_order_no: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9_-]+$')


class RefundQueryIn(RefundIn):
    out_refund_no: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_\-|*@]+$')


class ManualRefundIn(RefundIn):
    reference: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*$')
    amount: StrictInt = Field(gt=0, le=2147483647)
    completed_at: AwareDatetime


async def active_actor(db: AsyncSession, admin: User, revision: int) -> User:
    """User-before-order lock ordering, refresh role AND credential revision at final write boundary."""
    current = await lock_user(db, admin.id)
    if current is None or current.role != 1 or current.status != 1 or current.credential_version != revision:
        raise HTTPException(403, '权限已变更，请重新登录核对退款记录')
    return current


async def target(db: AsyncSession, number: str, proof: RefundIn) -> Order:
    if number != proof.confirm_order_no:
        raise HTTPException(409, '确认单号与目标不一致')
    order = await db.scalar(select(Order).where(Order.order_no == number))
    if order is None:
        raise HTTPException(404, '订单不存在')
    return order


@router.post('/shop/admin/orders/{order_no}/refunds/manual',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('refund-record', 'RATE_LIMIT_TOOLS'))])
async def manual_refund(order_no: str, proof: ManualRefundIn, response: Response,
                         db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Record actual full manual refund, not click-to-refund. Never applies to WeChat or mock money."""
    response.headers['Cache-Control'] = 'no-store'
    current = await active_actor(db, admin, admin.credential_version)
    order = await target(db, order_no, proof)
    event = PaymentEvent(order_id=order.id, attempt_id=uuid.uuid4().hex, kind='refund_manual_success',
                         actor_id=current.id, actor_name=current.username, evidence=proof.evidence)
    try:
        changed = await record_refund(db, order, source='manual', refund_id=proof.reference,
                                     out_refund_no=proof.reference, amount=proof.amount,
                                     completed_at=proof.completed_at, actor=current, evidence=proof.evidence, audit_event=event)
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {'order_no': order_no, 'refunded': True, 'changed': changed}


@router.post('/shop/admin/orders/{order_no}/refunds/query',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('refund-record', 'RATE_LIMIT_TOOLS'))])
async def query_refund(order_no: str, proof: RefundQueryIn, response: Response,
                        db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Durable started before read-only external I/O; signed SUCCESS receipt/audit committed together."""
    response.headers['Cache-Control'] = 'no-store'
    order = await target(db, order_no, proof)
    cfg = pay_config()
    if (order.merchant_id, order.app_id) != (cfg.mchid, cfg.appid):
        raise HTTPException(409, '退款核验凭据不属于原商户/应用')
    try:
        receipt = await original_receipt(db, order, 'wechat')
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        if not cfg.configured:
            raise WeChatPayError('missing configuration')
        assert_notify_configuration(cfg)
    except WeChatPayError:
        raise HTTPException(503, '退款查询/平台验签配置不完整') from None
    oid, amount, transaction_id = order.id, receipt.amount, receipt.transaction_id
    actor_id, actor_name, revision = admin.id, admin.username, admin.credential_version
    attempt = uuid.uuid4().hex
    def observation(kind, note):
        evidence = json.dumps({'v': 1, 'refund_no': proof.out_refund_no, 'note': note}, ensure_ascii=False, separators=(',', ':'))
        return PaymentEvent(order_id=oid, attempt_id=attempt, kind=kind, actor_id=actor_id,
                            actor_name=actor_name, evidence=evidence)
    db.add(observation('refund_query_started', proof.evidence))
    await db.commit()  # no actor/order lock spans provider I/O
    try:
        result = await query_full_refund(cfg, out_refund_no=proof.out_refund_no, out_trade_no=order_no,
                                         transaction_id=transaction_id, total=amount)
    except WeChatPayError:
        db.add(observation('refund_query_unknown', '未取得匹配的可信全额原路退款结果；未撤销权益'))
        await db.commit()
        raise HTTPException(502, '退款核验未取得可信结果；请查看记录，不能认定已退款或未退款') from None
    try:
        current = await active_actor(db, admin, revision)
    except HTTPException:
        db.add(observation('refund_query_aborted', '权限/凭据已变化，未记录退款成功'))
        await db.commit()
        raise
    changed = False
    if result.state == 'SUCCESS':
        try:
            changed = await record_refund(db, order, source='wechat', refund_id=result.refund_id,
                                         out_refund_no=proof.out_refund_no, amount=amount, completed_at=result.completed_at,
                                         actor=current, evidence=proof.evidence,
                                         audit_event=observation('refund_query_success', '已验签全额原路退款成功，与退款凭证同事务提交'))
        except PaymentConflict:
            db.add(observation('refund_query_conflict', '与既有凭证或时间不符；未覆盖退款记录'))
            await db.commit()
            raise HTTPException(409, '可信退款结果与本地凭证冲突，请核查；未覆盖原记录') from None
    else:
        await lock_order(db, order)
        db.add(observation('refund_query_' + result.state.lower(), '已验签观察；不是成功，不撤销或恢复权益'))
        await db.commit()
    return {'order_no': order_no, 'observed_state': result.state, 'changed': changed, 'attempt_id': attempt,
            'status': order.status, 'warning': '只有成功退款凭证才停止后续下载；原收款记录保留。未发起退款。'}


class RefundPrepareIn(RefundIn):
    model_config = ConfigDict(extra='forbid')
    evidence: str = Field(min_length=3, max_length=160, pattern=r'^[^\x00-\x1f\x7f-\x9f]+$')
    request_id: str = Field(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')
    amount: StrictInt = Field(gt=0, le=2147483647)


@router.post('/shop/admin/orders/{order_no}/refunds/requests',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('refund-prepare', 'RATE_LIMIT_TOOLS'))])
async def prepare_refund(order_no: str, proof: RefundPrepareIn, response: Response,
                          db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Persist a local preparation + stable reference, NOT authorization to send or proof of refund."""
    response.headers['Cache-Control'] = 'no-store'
    current = await active_actor(db, admin, admin.credential_version)
    order = await target(db, order_no, proof)
    try:
        row, changed = await prepare_request(db, order, actor=current, request_id=proof.request_id,
                                             amount=proof.amount, evidence=proof.evidence)
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, '退款准备保存结果未知，请刷新记录并使用原请求ID与内容重试') from None
    return {'order_no': order_no, 'changed': changed,
            'refund_request': request_view(row, await refund_for(db, order.id)),
            'warning': '仅保存本地准备，不发送或批准自动退款，不改变下载权。请保留原请求号。'}


class RefundAuthorizeIn(RefundPrepareIn):
    out_refund_no: str = Field(pattern=r'^CMR[0-9a-f]{32}$')
    reason: str = Field(min_length=1, max_length=80)

    @field_validator('reason')
    @classmethod
    def customer_reason(cls, value):
        if value != value.strip() or len(value.encode('utf-8')) > 80 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value):
            raise ValueError('客户可见退款原因须为1–80 UTF-8字节单行，不能透传内部依据')
        return value


class RefundSendIn(RefundPrepareIn):
    authorization_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    out_refund_no: str = Field(pattern=r'^CMR[0-9a-f]{32}$')


@router.post('/shop/admin/orders/{order_no}/refunds/authorize',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('refund-authorize', 'RATE_LIMIT_TOOLS'))])
async def authorize_refund(order_no: str, proof: RefundAuthorizeIn, response: Response,
                           db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Freeze separately confirmed full request; authorizing alone does not send."""
    response.headers['Cache-Control'] = 'no-store'
    current = await active_actor(db, admin, admin.credential_version)
    order = await target(db, order_no, proof)
    try:
        changed = await submissions.authorize(db, order, actor=current, key=proof.request_id,
                    refund_no=proof.out_refund_no, amount=proof.amount, reason=proof.reason,
                    evidence=proof.evidence, origin=settings.SITE_BASE_URL)
        return {'changed': changed, 'submission': await submissions.submission_view(db, await request_for(db, order.id))}
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, '授权保存结果未知；刷新并用原请求内容恢复，不另造退款号') from None


@router.post('/shop/admin/orders/{order_no}/refunds/send',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('refund-send', 'RATE_LIMIT_TOOLS'))])
async def send_refund(order_no: str, proof: RefundSendIn, response: Response,
                      db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Fresh explicit money-moving action, gated off by default; exact attempt replay never resends."""
    response.headers['Cache-Control'] = 'no-store'
    current = await active_actor(db, admin, admin.credential_version)
    order = await target(db, order_no, proof)
    cfg = pay_config()
    try:
        view, packet = await submissions.begin_send(db, order, actor=current, key=proof.request_id,
                        authorization_id=proof.authorization_id, expected_digest=proof.digest,
                        refund_no=proof.out_refund_no, amount=proof.amount, evidence=proof.evidence,
                        cfg=cfg, enabled=settings.WX_REFUND_SEND_ENABLED)
        if packet is not None:
            body, number, transaction, total = packet
            try:
                result = await submit_full_refund(cfg, body=body, out_trade_no=number,
                                                   transaction_id=transaction, total=total)
            except WeChatPayError:
                result = None
            view = await submissions.finish_send(db, order, key=proof.request_id, result=result)
        return {'attempt': view, 'warning': '申请观察不是成功凭证；下载权不变。请按原商户退款号独立查询，不换号重退。'}
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, '发送/保存结果未知；先刷新原记录并查询原号，不自动重发') from None


@router.post('/shop/admin/orders/{order_no}/refunds/stop',
             dependencies=[Depends(require_finance_origin), Depends(rate_limit('refund-stop', 'RATE_LIMIT_TOOLS'))])
async def stop_refund_sending(order_no: str, proof: RefundSendIn, response: Response,
                              db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Permanently stop new local sends; NOT a channel cancellation or financial correction."""
    response.headers['Cache-Control'] = 'no-store'
    current = await active_actor(db, admin, admin.credential_version)
    order = await target(db, order_no, proof)
    try:
        stop, changed = await submissions.stop_sending(db, order, actor=current, key=proof.request_id,
                        authorization_id=proof.authorization_id, expected_digest=proof.digest,
                        refund_no=proof.out_refund_no, amount=proof.amount, evidence=proof.evidence)
        return {'stop': stop, 'changed': changed,
                'warning': '仅停止新的本站发送；已经开始的请求仍可能退款，须查询原号。不是渠道取消，不改金额、正文或下载权益。'}
    except PaymentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, '停止保存结果未知；刷新原记录并用原请求ID/内容重试，勿据错误推断已停止') from None
