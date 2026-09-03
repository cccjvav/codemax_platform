import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Order, User
from ..order_state import (
    CLOSED,
    DOWNLOADED,
    PENDING,
    IllegalTransition,
    is_expired,
    mark_closed,
    mark_downloaded,
    mark_paid,
)
from ..site import templates
from ..storage import StorageError, build_storage, verify_download
from ..wechat_pay import (
    WeChatPayError,
    decrypt_resource,
    native_prepay,
    new_order_no,
    pay_config,
    verify_notify_signature,
)

router = APIRouter(prefix="/shop", tags=["商业平台"])

MOCK_PAY_PATH = "/shop/mock-pay"  # 模拟收银台（TD-124），仅 SHOP_PAY_MODE=mock 时存在


@router.get("/ping")
async def ping(_: User = Depends(get_current_user)):
    """商业平台受保护端点：与工具平台共享同一登录态（SSO）。"""
    return {"platform": "shop", "message": "pong"}


@router.post("/orders")
async def create_order(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """下单（S3-01-1）：建订单 → 取 code_url → 前端画二维码。

    已有未支付订单时复用同一单（S3-01-1-4），避免连点几下刷出一堆待支付单。
    订单先落库再去下单：微信那边付了、我们这边没单，比反过来难收拾得多。

    `SHOP_PAY_MODE=mock` 时不调微信，code_url 指向本站的模拟收银台（TD-124）。
    """
    mode = settings.SHOP_PAY_MODE
    if mode not in ("wechat", "mock"):
        raise HTTPException(500, f"SHOP_PAY_MODE 只能是 wechat 或 mock，当前是 {mode!r}")
    cfg = pay_config()
    if mode == "wechat" and not cfg.configured:
        raise HTTPException(503, "支付未配置：请在 .env 填齐 WX_APPID/WX_MCHID/WX_SERIAL_NO/"
                                 "WX_PRIVATE_KEY/WX_API_V3_KEY/WX_NOTIFY_URL 六项")

    pending = await db.scalar(
        select(Order)
        .where(Order.user_id == user.id, Order.status == PENDING)
        .order_by(Order.id.desc())
        .limit(1)
    )
    # 超时未支付（S5-01-1）：旧单关掉、另起一单。不这么做的话，二维码过期后
    # 这个单会被无限复用，用户扫了必然失败，且没有任何出路（TD-109）。
    if pending is not None and is_expired(pending, settings.ORDER_EXPIRE_MINUTES):
        await mark_closed(db, pending)
        pending = None
    if pending is not None and pending.code_url:
        return _payload(pending, reused=True, pay_mode=mode)

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

    if mode == "mock":
        # 用请求的 base_url 而不是 SITE_BASE_URL：本地演示时链接要能直接点开
        base = str(request.base_url).rstrip("/")
        order.code_url = f"{base}{MOCK_PAY_PATH}?order_no={order.order_no}"
    else:
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
    return _payload(order, reused=False, pay_mode=mode)


# ---------------------------------------------------------------- 模拟支付通道（TD-124）
# 只在 SHOP_PAY_MODE=mock 时存在；生产（wechat）下这两个端点一律 404，
# 免得演示用的后门被带上生产环境。


class MockPayIn(BaseModel):
    order_no: str


@router.get("/mock-pay", include_in_schema=False)
async def mock_pay_page(request: Request, order_no: str = ""):
    """模拟收银台页面（答辩演示用）。"""
    if settings.SHOP_PAY_MODE != "mock":
        raise HTTPException(404, "模拟支付通道未开启（SHOP_PAY_MODE != mock）")
    return templates.TemplateResponse(
        "mock_pay.html", {"request": request, "title": "模拟收银台", "order_no": order_no}
    )


@router.post("/mock-pay/confirm")
async def mock_pay_confirm(
    payload: MockPayIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """把订单标记为已支付（模拟）。

    刻意复用与真实回调**完全相同**的状态机与幂等逻辑（`mark_paid`），
    这样演示走通的路径和上生产走的是同一条，不会因为"演示专用代码"而漏测。
    """
    if settings.SHOP_PAY_MODE != "mock":
        raise HTTPException(404, "模拟支付通道未开启（SHOP_PAY_MODE != mock）")
    order = await db.scalar(select(Order).where(Order.order_no == payload.order_no))
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")  # 不是自己的单一律 404，不暴露是否存在
    if order.status == PENDING:
        order.transaction_id = f"MOCK-{order.order_no}"
        order.paid_at = datetime.now(timezone.utc)  # 带时区，列为 TIMESTAMPTZ（TD-146 已修）
    await mark_paid(db, order)
    return {
        "order_no": order.order_no,
        "status": order.status,
        "transaction_id": order.transaction_id,
        "pay_mode": "mock",
    }


# ---------------------------------------------------------------- 一次性下载（S3-02-4）


# 必须是 POST 而不是 GET：这个端点**会改状态**（把 paid 烧成 downloaded，
# 一次性下载就没了）。GET 带副作用本来就是错的，而在 TD-44 之后它还是个
# CSRF 靶子 —— 登录态改成 cookie 后，SameSite=Lax 只挡跨站的写方法，
# 跨站顶层导航的 GET 照样带上 cookie，攻击者一个跳转就能替用户把下载额度烧掉。
@router.post("/download/{order_no}")
async def download_url(
    request: Request, order_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """发一个一次性下载链接。**第一重校验：数据库下载状态**。

    状态机保证 `paid → downloaded` 只能走一次，所以同一订单第二次来要链接会被拒 ——
    这是"防止资源被无限倒卖"的主力；预签名 URL 的过期时间只是第二重（缩小转发窗口）。

    注意语义：标记为已下载发生在**发出链接时**，不是文件真的被下载时。
    云存储是客户端直连对象存储，应用根本看不到那次下载，只能在发链接时记账（TD-129）。
    """
    storage = _storage(request)
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")  # 不是自己的单一律 404，不暴露是否存在
    if order.status in (PENDING, CLOSED):
        # CLOSED 是超时关闭：没付过钱，与未支付同等对待。
        # 不显式写这一行的话，它会一路落到状态机、由 CLOSED→DOWNLOADED 不在
        # ALLOWED 里而抛 409。虽然也拦住了，但语义是错的（409 是状态冲突，
        # 这里是没权限），而且整个安全性都押在 ALLOWED 表不新增那条边上，太脆。
        raise HTTPException(403, "订单未支付")
    if order.status == DOWNLOADED:
        raise HTTPException(403, "该订单已下载过：一次性下载，防止资源被转卖")

    key = settings.STORAGE_PRODUCT_KEY
    if not storage.exists(key):
        raise HTTPException(404, f"商品文件不存在（对象 key：{key}）")

    url = storage.presigned_url(key, expires_in=settings.DOWNLOAD_URL_TTL)
    try:
        won = await mark_downloaded(db, order)
    except IllegalTransition as e:
        raise HTTPException(409, str(e)) from e
    # **必须看返回值**：上面的 `order.status == DOWNLOADED` 检查读的是请求开始时
    # 的快照，并发下多个请求会同时读到 paid。真正决定谁能拿到链接的是这次 CAS ——
    # 忽略返回值的话，并发 4 个请求会全部拿到有效链接，"一次性下载"直接失效。
    # 由 test_concurrent_download_only_one_wins 守着。
    if not won:
        raise HTTPException(403, "该订单已下载过：一次性下载，防止资源被转卖")
    return {
        "order_no": order.order_no,
        "download_url": url,
        "expires_in": settings.DOWNLOAD_URL_TTL,
        "status": order.status,
    }


@router.get("/dl", include_in_schema=False)
async def serve_download(request: Request, key: str, expires: int, signature: str):
    """本地后端的下载出口。**第二重校验：预签名 URL 的签名与过期时间**。

    云存储后端不需要这个端点（客户端直连对象存储），所以非 local 后端一律 404。
    """
    storage = _storage(request)
    if storage.backend != "local":
        raise HTTPException(404, "当前存储后端由客户端直连下载，不经过本站")
    if not verify_download(settings.SECRET_KEY, key, expires, signature):
        raise HTTPException(403, "下载链接无效或已过期")
    try:
        data = storage.read(key)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise HTTPException(404, "文件不存在") from exc
    filename = Path(key).name
    return Response(
        data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _storage(request: Request):
    try:
        return build_storage(str(request.base_url))
    except StorageError as e:
        raise HTTPException(503, str(e)) from e


def _ok() -> JSONResponse:
    return JSONResponse({"code": "SUCCESS", "message": "成功"})


def _fail(status: int, message: str) -> JSONResponse:
    """微信规定的失败应答体，与 FastAPI 默认的 {"detail": ...} 不同。"""
    return JSONResponse({"code": "FAIL", "message": message}, status_code=status)


def _payload(order: Order, *, reused: bool, pay_mode: str) -> dict:
    return {
        "order_no": order.order_no,
        "code_url": order.code_url,
        "product_name": order.product_name,
        "amount": order.amount,
        "status": order.status,
        "reused": reused,
        "pay_mode": pay_mode,
    }


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
        order.paid_at = datetime.now(timezone.utc)  # 带时区，列为 TIMESTAMPTZ（TD-146 已修）
    try:
        await mark_paid(db, order)
    except IllegalTransition as e:
        return _fail(409, str(e))
    return _ok()
