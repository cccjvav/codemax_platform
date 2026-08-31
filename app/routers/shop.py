from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Order, User
from ..order_state import PENDING
from ..wechat_pay import WeChatPayError, native_prepay, new_order_no, pay_config

router = APIRouter(prefix="/shop", tags=["商业平台"])


@router.get("/ping")
async def ping(_: User = Depends(get_current_user)):
    """商业平台受保护端点：与工具平台共享同一登录态（SSO）。"""
    return {"platform": "shop", "message": "pong"}


@router.post("/orders")
async def create_order(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """下单（S3-01-1）：建订单 → 调微信 NATIVE 下单 → 返回 code_url 供前端画二维码。

    已有未支付订单时复用同一单（S3-01-1-4），避免连点几下刷出一堆待支付单。
    订单先落库再去下单：微信那边付了、我们这边没单，比反过来难收拾得多。
    """
    cfg = pay_config()
    if not cfg.configured:
        raise HTTPException(503, "支付未配置：请在 .env 填齐 WX_APPID/WX_MCHID/WX_SERIAL_NO/"
                                 "WX_PRIVATE_KEY/WX_API_V3_KEY/WX_NOTIFY_URL 六项")

    pending = await db.scalar(
        select(Order)
        .where(Order.user_id == user.id, Order.status == PENDING)
        .order_by(Order.id.desc())
        .limit(1)
    )
    if pending is not None and pending.code_url:
        return _payload(pending, reused=True)

    order = pending
    if order is None:
        order = Order(
            order_no=new_order_no(),
            user_id=user.id,
            product_name=settings.SHOP_PRODUCT_NAME,
            amount=settings.SHOP_PRODUCT_AMOUNT,
            status=PENDING,
        )
        db.add(order)
        await db.flush()  # 先拿到 id，后面 commit 才不会因为下单失败而丢单

    try:
        order.code_url = await native_prepay(
            cfg,
            out_trade_no=order.order_no,
            description=settings.SHOP_PRODUCT_NAME,
            total=settings.SHOP_PRODUCT_AMOUNT,
        )
    except WeChatPayError as e:
        raise HTTPException(502, str(e)) from e
    await db.commit()
    return _payload(order, reused=False)


def _payload(order: Order, *, reused: bool) -> dict:
    return {
        "order_no": order.order_no,
        "code_url": order.code_url,
        "product_name": order.product_name,
        "amount": order.amount,
        "status": order.status,
        "reused": reused,
    }
