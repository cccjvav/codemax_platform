"""Authenticated, durable customer/admin messaging; public page contains no private data."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, lock_user
from ..deps import get_current_user, require_admin
from ..models import SupportMessage, User
from ..ratelimit import rate_limit
from ..site import page_context, templates

router = APIRouter(prefix="/support", tags=["站内人工客服"])


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    client_nonce: UUID

    @field_validator('body')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or '\x00' in value:
            raise ValueError('请输入非空留言，不能包含空字符')
        return value.strip()


def payload(row: SupportMessage) -> dict:
    return {'id': row.id, 'customer_id': row.customer_id, 'sender_role': row.sender_role,
            'body': row.body, 'create_time': row.create_time}


async def history(db: AsyncSession, customer_id: int, after: int | None, before: int | None) -> list[dict]:
    if after is not None and before is not None:
        raise HTTPException(422, 'after 与 before 不能同时提供')
    stmt = select(SupportMessage).where(SupportMessage.customer_id == customer_id)
    if after is not None:
        stmt = stmt.where(SupportMessage.id > after).order_by(SupportMessage.id)
    else:
        if before is not None:
            stmt = stmt.where(SupportMessage.id < before)
        stmt = stmt.order_by(SupportMessage.id.desc())
    rows = list((await db.scalars(stmt.limit(50))).all())
    if after is None:
        rows.reverse()
    return [payload(r) for r in rows]


async def send_message(db: AsyncSession, user: User, customer_id: int, data: MessageIn, role: int) -> dict:
    sender_id, nonce = user.id, str(data.client_nonce)
    stmt = select(SupportMessage).where(
        SupportMessage.sender_id == sender_id, SupportMessage.client_nonce == nonce)
    existing = await db.scalar(stmt)
    if existing is None:
        await lock_user(db, customer_id)
        row = SupportMessage(customer_id=customer_id, sender_id=sender_id, sender_role=role,
                             body=data.body, client_nonce=nonce)
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
            return payload(row)
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(stmt)
            if existing is None:
                raise
    if existing.customer_id != customer_id or existing.body != data.body:
        raise HTTPException(409, '消息标识已用于其他内容，请重新发送')
    return payload(existing)


@router.get('/center', include_in_schema=False)
async def center(request: Request):
    return templates.TemplateResponse(request, 'support-center.html', page_context(
        request, title='站内客服 - CodeMax', description='与平台管理员沟通数字产品、定制需求和售后问题。'))


@router.get('/messages')
async def my_messages(after: int | None = Query(None, ge=0), before: int | None = Query(None, gt=0),
                      user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await history(db, user.id, after, before)


@router.post('/messages', dependencies=[Depends(rate_limit('support-message', 'RATE_LIMIT_SUPPORT_MESSAGES'))])
async def write_message(data: MessageIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await send_message(db, user, user.id, data, 0)


@router.get('/conversations')
async def inbox(before: int | None = Query(None, gt=0), _: User = Depends(require_admin),
                db: AsyncSession = Depends(get_db)):
    latest = select(SupportMessage.customer_id, func.max(SupportMessage.id).label('last_id')).group_by(
        SupportMessage.customer_id).subquery()
    stmt = select(User.username, SupportMessage).join(latest, User.id == latest.c.customer_id).join(
        SupportMessage, SupportMessage.id == latest.c.last_id)
    if before is not None:
        stmt = stmt.where(SupportMessage.id < before)
    rows = (await db.execute(stmt.order_by(SupportMessage.id.desc()).limit(50))).all()
    return [{'username': name, **payload(row), 'awaiting_admin': row.sender_role == 0} for name, row in rows]


@router.get('/conversations/{customer_id}/messages')
async def admin_messages(customer_id: int, after: int | None = Query(None, ge=0),
                         before: int | None = Query(None, gt=0), _: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    if await db.get(User, customer_id) is None:
        raise HTTPException(404, '客户不存在')
    return await history(db, customer_id, after, before)


@router.post('/conversations/{customer_id}/messages',
             dependencies=[Depends(rate_limit('support-message', 'RATE_LIMIT_SUPPORT_MESSAGES'))])
async def admin_reply(customer_id: int, data: MessageIn, user: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    if await db.get(User, customer_id) is None:
        raise HTTPException(404, '客户不存在')
    return await send_message(db, user, customer_id, data, 1)
