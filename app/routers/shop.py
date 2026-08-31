import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Order, User
from ..order_state import PENDING, IllegalTransition, mark_paid
from ..wechat_pay import (
    WeChatPayError,
    decrypt_resource,
    native_prepay,
    new_order_no,
    pay_config,
    verify_notify_signature,
)

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


@router.post("/pay/notify")
async def pay_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """微信支付结果回调（S3-01-3）。

    这个端点**不走登录鉴权**：调用方是微信支付，身份靠平台证书验签确认（TD-115）。

    应答格式是微信规定的，不能用 `HTTPException`（那会返回 `{"detail": ...}`）：
    验签通过 → 200/204 无包体；失败 → 4XX/5XX + `{"code": "FAIL", "message": "..."}`。
    **4XX/5XX 会被微信重推**，所以"重试也没用"的情况（非支付成功通知、已处理过）
    必须回 200，否则会被无限重推。
    """
    cfg = pay_config()
    if not cfg.notify_ready:
        return _fail(503, "未配置 WX_API_V3_KEY / WX_PLATFORM_CERT，无法处理回调")

    body = (await request.body()).decode("utf-8")  # 原始报文主体，验签必须用它
    h = request.headers
    try:
        verify_notify_signature(
            cfg.platform_cert,
            timestamp=h.get("Wechatpay-Timestamp", ""),
            nonce=h.get("Wechatpay-Nonce", ""),
            body=body,
            signature=h.get("Wechatpay-Signature", ""),
        )
    except WeChatPayError as e:
        return _fail(401, str(e))

    try:
        notify = json.loads(body)
    except json.JSONDecodeError as e:
        return _fail(400, f"回调报文不是合法 JSON：{e}")

    resource = notify.get("resource") or {}
    try:
        data = decrypt_resource(
            cfg.api_v3_key,
            ciphertext=resource.get("ciphertext", ""),
            nonce=resource.get("nonce", ""),
            associated_data=resource.get("associated_data") or "",
        )
    except WeChatPayError as e:
        return _fail(400, str(e))

    if notify.get("event_type") != "TRANSACTION.SUCCESS" or data.get("trade_state") != "SUCCESS":
        return _ok()  # 退款 / 未支付等通知本阶段不处理，但要确认收到，否则会被重推

    order = await db.scalar(select(Order).where(Order.order_no == data.get("out_trade_no")))
    if order is None:
        return _fail(404, f"订单不存在：{data.get('out_trade_no')}")
    total = (data.get("amount") or {}).get("total")
    if total != order.amount:
        return _fail(400, f"金额不符：回调 {total} 分，订单 {order.amount} 分")

    # 幂等（S3-01-3-3）：重复通知不会二次迁移、不报错，也不覆盖首次的支付信息
    if order.status == PENDING:
        order.transaction_id = data.get("transaction_id")
        order.paid_at = datetime.now()
    try:
        await mark_paid(db, order)
    except IllegalTransition as e:
        return _fail(409, str(e))
    return _ok()


def _ok() -> JSONResponse:
    return JSONResponse({"code": "SUCCESS", "message": "成功"})


def _fail(status: int, message: str) -> JSONResponse:
    """微信规定的失败应答体，与 FastAPI 默认的 {"detail": ...} 不同。"""
    return JSONResponse({"code": "FAIL", "message": message}, status_code=status)


def _payload(order: Order, *, reused: bool) -> dict:
    return {
        "order_no": order.order_no,
        "code_url": order.code_url,
        "product_name": order.product_name,
        "amount": order.amount,
        "status": order.status,
        "reused": reused,
    }
