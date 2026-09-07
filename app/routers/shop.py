import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..middleware import public_base_url
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
from ..site import page_context, templates
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


def _qr_svg(text: str) -> str:
    """把微信 Native 支付返回的 `code_url` 画成**内联 SVG** 二维码。

    ## 为什么服务端画，而不是前端引 JS 库

    - 前端方案要引 CDN 脚本，而 `script-src` 白名单里唯一的 `cdn.jsdelivr.net`
      在沙箱与部分网络下实测 HTTP=000 不可达 —— 开发时二维码画不出来，很难查。
    - 服务端方案零外部依赖：实测 `segno` 1.6.6 是**纯 Python、零依赖、0.07 MB**。
      对比 `qrcode[pil]`：qrcode 本身 0.04 MB，但画 PNG 要拖 **Pillow 6.61 MB 二进制**，
      差约 95 倍，而 Pillow 在 Windows 上还多一层二进制轮子的麻烦。
    - 输出是**纯 `<path>` 的内联 SVG**（实测 1493 字节，不含 `<script>`、不含外链），
      直接塞进 HTML。内联 SVG **不受 CSP `img-src` 约束**，不用改安全策略。

    ## 为什么不做成 `GET /shop/qr?url=...` 这种通用接口

    那等于开了一个「任意内容二维码生成器」，会被拿去钓鱼（生成指向恶意站点的码）。
    现在只在 `_payload` 里对**本站自己订单的 code_url** 生成，不接收外部输入。
    """
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", xmldecl=False, svgns=False)
    return buf.getvalue().decode("utf-8")


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
    if mode not in ("wechat", "mock", "manual"):
        raise HTTPException(500, f"SHOP_PAY_MODE 只能是 wechat / mock / manual，当前是 {mode!r}")
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
    # manual 模式**没有** code_url（收款码是全站共用的一张静态图，不是每单一串），
    # 所以「这张单能不能直接用」不能只看 code_url，否则每次下单都会新建一张。
    if pending is not None and (pending.code_url or mode == "manual"):
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
        # `user.id` 必须在 flush **之前**取成局部变量：下面的 rollback 会无条件过期
        # 所有 ORM 对象（`expire_on_commit=False` 管不到 rollback），回滚后再碰
        # `user.id` 会触发同步懒加载，在 async 上下文里就是 MissingGreenlet
        # （真 PostgreSQL 上实测炸过，SQLite 单连接下反而看不出来）。
        user_id = user.id
        try:
            await db.flush()  # 先拿到 id，后面 commit 才不会因为下单失败而丢单
        except IntegrityError:
            # 撞上了 uq_sys_order_user_pending（TD-199）：并发下别人先插成功了。
            # 回滚本次插入，改用**已经存在的那张**单 —— 对客户端来说语义不变
            # （拿到同一张待支付单），但库里不会堆出一排 pending。
            # 这里必须由数据库兜底：应用层「先查后建」在 asyncio 交错下必然漏，
            # 而进程内锁在多实例部署时各算各的（与限流 TD-141 同一个道理）。
            await db.rollback()
            order = await db.scalar(
                select(Order)
                .where(Order.user_id == user_id, Order.status == PENDING)
                .order_by(Order.id.desc())
                .limit(1)
            )
            if order is None:  # 极端情况：对方在我们回滚期间把单关掉了
                raise HTTPException(409, "下单冲突，请重试") from None
            if order.code_url:
                return _payload(order, reused=True, pay_mode=mode)

    if mode == "manual":
        # 刻意**不写** code_url：收款码是 settings.SHOP_MANUAL_QR 那张静态图，
        # 与具体订单无关。往这一列塞图片路径会让「有 code_url 就代表能扫码付款」
        # 这个隐含前提失效，也让 _payload 去给一个路径字符串画二维码。
        pass
    elif mode == "mock":
        # 用请求推出来的站点根而不是 SITE_BASE_URL：本地演示时链接要能直接点开。
        # 必须走 public_base_url() 而不是 str(request.base_url) —— 后者不看转发头，
        # 在反向代理之后会退化成 http://，浏览器按混合内容拦掉（A-9）。
        base = public_base_url(request)
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


@router.get("/orders/{order_no}")
async def order_status(
    order_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """查订单状态（S2-02-2）：给下单页轮询用。

    ## 为什么必须有这个接口

    微信 Native 支付的流程是：用户扫码付钱 → 微信回调 `POST /shop/pay/notify` → 订单变 `paid`。
    **前端完全不知道这件事发生了** —— 回调是微信打到服务端的，浏览器那边没有任何推送。
    所以页面只能轮询。而在此接口之前，全站**没有任何可以轮询的接口**：
    `POST /shop/download/{order_no}` 虽然也能反映状态，但它**会把订单烧成 `downloaded`**
    （一次性下载，见 download_url 里的 CAS），拿它当状态查询等于把用户的货直接销毁。

    ## 三条设计约束

    1. **只读**。轮询每 3 秒一次，绝不能在里面写库（连"顺手关掉过期单"都不行）——
       过期关单只在 `create_order` 里做，那是用户主动重新下单时的一次性动作。
       这里只**报告**是否已过期，由前端提示"二维码已失效，请重新下单"。
    2. **非本人一律 404**，与 `download_url` 同一口径：不暴露"这个订单号存在"。
    3. **禁止缓存**。这是个轮询接口（前端 3 秒一次），而浏览器/中间代理完全可能
       缓存一个 200 GET。一旦命中缓存，页面会**永远看不到订单变 paid** ——
       用户付了钱页面却一直转圈。`no-store` 同时挡掉浏览器与中间代理。
    """
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")
    payload = _payload(order, reused=False, pay_mode=settings.SHOP_PAY_MODE)
    payload["expired"] = is_expired(order, settings.ORDER_EXPIRE_MINUTES)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


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
    # 必须走 page_context：base.html 还要 site_name / tools / shop_path / product_name
    # 与 SEO 三件套。早先这里只传了 title 与 order_no，页面渲染出空品牌、空导航、
    # CTA 的 href="" —— 返回 200、测试也只断言「页面能开」，所以一直没被发现。
    # Starlette 1.x：request 是第一个位置参数（见 site.py 的同款注释）
    return templates.TemplateResponse(
        request,
        "mock_pay.html",
        page_context(request, title="模拟收银台", order_no=order_no),
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
    if order.status in (PENDING, CLOSED):  # 迟到支付（TD-156/TD-198）同样要留下支付流水
        order.transaction_id = f"MOCK-{order.order_no}"
        order.paid_at = datetime.now(timezone.utc)  # 带时区，列为 TIMESTAMPTZ（TD-146 已修）
    await mark_paid(db, order)
    return {
        "order_no": order.order_no,
        "status": order.status,
        "transaction_id": order.transaction_id,
        "pay_mode": "mock",
    }


# 人工确认收款是**人**替机器做了「钱到账了」这个判断，必须留下可追溯的记录。
# 没有审计表（sys_order 也没有可放备注的列 —— `remark` 属于 SysConfig 不是 Order），
# 所以走结构化日志：这也是这类运维动作的行业标准做法，进日志管道、可集中检索，
# 且不会因为业务表结构调整而丢。
_audit_logger = logging.getLogger("codemax.audit")


# ---------------------------------------------------------------- 人工确认收款（S5-04）


@router.post("/orders/{order_no}/confirm")
async def confirm_paid_manually(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """把订单人工标记为已支付。**仅 `SHOP_PAY_MODE=manual` 时存在**，否则 404。

    为什么需要它：manual 模式展示的是个人收款码，**服务端收不到任何支付通知** ——
    没有商户号就没有回调（TD-113 / TD-205）。既然机器不知道钱到没到，
    就只能由人告诉系统。

    与 `mock_pay_confirm` 的关键区别是**谁能调**：mock 是登录用户自己点，
    等于免费发货按钮（所以只能开在开发环境）；这里是 `require_admin`，
    只有管理员能确认，可以留在生产上。

    状态机刻意复用真实回调那条（`mark_paid`）：幂等、且 `CLOSED` 也能收 ——
    用户扫了旧码照样可能付钱，钱到账就必须发货（TD-156）。
    """
    if settings.SHOP_PAY_MODE != "manual":
        raise HTTPException(404, "人工确认收款未开启（SHOP_PAY_MODE != manual）")
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        # 这里**可以**返回 404 而不是像用户侧那样刻意模糊：调用方是管理员，
        # 「这个单号不存在」是他需要知道的运维信息，不是要对他保密的东西。
        raise HTTPException(404, "订单不存在")
    if order.status in (PENDING, CLOSED):
        order.transaction_id = f"MANUAL-{order.order_no}"
        order.paid_at = datetime.now(timezone.utc)  # TIMESTAMPTZ，必须带时区（TD-146）
    await mark_paid(db, order)
    # 谁、什么时候、把哪一单标成已支付 —— 这三件事必须落盘。
    # 只在响应体里回一个 confirmed_by 等于没记录：调用方关掉页面就什么都没了，
    # 事后要查「这单是谁放的货」时无从查起（钱货争议时这是唯一证据）。
    _audit_logger.info(
        "人工确认收款 order_no=%s user_id=%s amount=%s status=%s confirmed_by=%s",
        order.order_no,
        order.user_id,
        order.amount,
        order.status,
        admin.username,
        extra={"audit_event": "manual_payment_confirmed", "order_no": order.order_no},
    )
    return {
        "order_no": order.order_no,
        "status": order.status,
        "transaction_id": order.transaction_id,
        "pay_mode": "manual",
        "confirmed_by": admin.username,
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
    # P1-6：不再 `storage.read(key)` 把整个文件读进内存 —— 一个 500MB 的软件包就是
    # 500MB 常驻，几个用户同时下载就 OOM。改成把**已校验过目录穿越**的路径交给
    # FileResponse，由 starlette 分块读盘、边读边发，Content-Length 也自动算对。
    # 由 test_download_streams_instead_of_reading_whole_file 盯住「不许再调 read」。
    try:
        path = storage.local_path(key)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "文件不存在") from exc
    # FileResponse 遇到文件不存在是在**响应阶段**才炸的，那时已经出了 HTTPException
    # 的管辖范围（会变成 500），所以必须在这里先判一次。
    # 由 test_signed_url_for_missing_file_returns_404 守着。
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(
        path, media_type="application/octet-stream", filename=Path(key).name
    )


def _storage(request: Request):
    try:
        # 同 A-9：预签名链接的 scheme/host 必须与 HSTS 用同一套转发头信任规则，
        # 否则会出现「HSTS 说本站只有 https，下载链接却给 http」的自相矛盾响应。
        return build_storage(public_base_url(request))
    except StorageError as e:
        raise HTTPException(503, str(e)) from e


def _ok() -> JSONResponse:
    return JSONResponse({"code": "SUCCESS", "message": "成功"})


def _fail(status: int, message: str) -> JSONResponse:
    """微信规定的失败应答体，与 FastAPI 默认的 {"detail": ...} 不同。"""
    return JSONResponse({"code": "FAIL", "message": message}, status_code=status)


def _payload(order: Order, *, reused: bool, pay_mode: str) -> dict:
    # 只有微信 Native 支付的 `weixin://` 串需要画二维码。
    # mock 模式（TD-124）的 code_url 是本站的 http 链接，前端直接给个按钮点开就行，
    # 画成二维码反而多一步扫码 —— 所以这里按前缀区分，不给 http 链接生成码。
    needs_qr = bool(order.code_url) and not order.code_url.startswith(("http://", "https://"))
    return {
        "order_no": order.order_no,
        "code_url": order.code_url,
        "qr_svg": _qr_svg(order.code_url) if needs_qr else None,
        # manual 模式的静态收款码地址。只在 manual 下非空，前端据此二选一渲染：
        # 有 qr_svg 就内联 SVG，否则用 <img> 引这张图。
        "qr_image": settings.SHOP_MANUAL_QR if pay_mode == "manual" else None,
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
    if order.status in (PENDING, CLOSED):  # 迟到支付（TD-156/TD-198）同样要留下支付流水
        order.transaction_id = data.get("transaction_id")
        order.paid_at = datetime.now(timezone.utc)  # 带时区，列为 TIMESTAMPTZ（TD-146 已修）
    try:
        await mark_paid(db, order)
    except IllegalTransition as e:
        return _fail(409, str(e))
    return _ok()
