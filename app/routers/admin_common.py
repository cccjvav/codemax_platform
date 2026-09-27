"""管理员资金操作共用的两道检查（payments_admin / refunds_admin，TD-297）。

两处原先各写一遍，payments_admin 里同一段「锁管理员行并复核身份」重复了三次，写法还不一样。
这里只放检查本身，不决定失败时写什么事件、回什么文案 —— 那是各端点自己的契约。
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import lock_user
from ..models import Order, User


async def locked_active_admin(db: AsyncSession, admin_id: int, revision: int) -> User | None:
    """锁住管理员行（先用户后订单的加锁顺序）并从数据库重读，确认仍是启用中的管理员、凭据版本未变。

    通过返回重读后的 User；否则返回 None，由调用方决定记什么事件、回什么 403 文案。
    `revision` 是请求开始时鉴权拿到的凭据版本：期间改过密码、被降权或禁用都会不一致。
    """
    current = await lock_user(db, admin_id)
    if current is None or current.role != 1 or current.status != 1 or current.credential_version != revision:
        return None
    return current


async def confirmed_order(db: AsyncSession, order_no: str, confirm_order_no: str) -> Order:
    """管理员手输的确认单号必须与路径单号一致（409），订单必须存在（404）。不加锁。"""
    if confirm_order_no != order_no:
        raise HTTPException(409, '确认单号与目标不一致')
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(404, '订单不存在')
    return order
