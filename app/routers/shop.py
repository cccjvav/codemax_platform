import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictInt, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..database import get_db, lock_user
from ..delivery import snapshot_product, verify_snapshot
from ..deps import get_current_user, require_admin, require_finance_origin
from ..middleware import public_base_url
from ..models import Order, PaymentEvent, PaymentReceipt, User
from ..order_state import (
    CLOSED,
    DOWNLOADED,
    PAID,
    PENDING,
    IllegalTransition,
    is_expired,
    mark_closed,
    mark_downloaded,
)
from ..payment_ledger import PaymentConflict, lock_order, settle
from ..ratelimit import rate_limit
from ..site import page_context, templates
from ..storage import StorageError, build_storage, verify_download
from ..wechat_pay import (
    WeChatPayError,
    assert_notify_configuration,
    assert_notify_fresh,
    assert_notify_identity,
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

    - 业务 CSP 只允许 self 脚本；服务端画码不引入额外前端库/CDN，
      避免为二维码拓宽脚本来源（开发 API 文档的 CDN 例外不适用于业务页）。
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
    微信预支付前先 commit 本地订单；提交失败不调用提供方，失败重试复用持久订单号。
    尝试开始/结果另存事件；未知结果不是失败，自动查单对账仍是发布阻断项。

    `SHOP_PAY_MODE=mock` 时不调微信，code_url 指向本站的模拟收银台（TD-124）。
    """
    mode = settings.SHOP_PAY_MODE
    if type(settings.SHOP_PRODUCT_AMOUNT) is not int or not 0 < settings.SHOP_PRODUCT_AMOUNT <= 2147483647:
        raise HTTPException(503, "商品分金额必须为正整数且在数据库范围内")
    if mode == 'mock' and settings.ENV != 'development':
        raise HTTPException(503, '模拟支付只允许开发环境')
    if mode not in ("wechat", "mock", "manual"):
        raise HTTPException(500, f"SHOP_PAY_MODE 只能是 wechat / mock / manual，当前是 {mode!r}")
    cfg = pay_config()
    if mode == "wechat":
        try:
            if not cfg.configured:
                raise WeChatPayError("下单配置不完整")
            assert_notify_configuration(cfg)
        except WeChatPayError:
            raise HTTPException(503, "支付未配置：请核对 WX_* 商户下单及平台回调凭据、证书有效期") from None

    # Serialize same-user checkout before copying: concurrent retries reuse one snapshot/order,
    # rather than competing for the bounded global copy slots. Commit before provider I/O releases it.
    await lock_user(db, user.id)
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
    if pending is not None:
        _check_order_channel(pending, mode, cfg)
    if pending is not None and (pending.code_url or mode == "manual"):
        return _payload(pending, reused=True, pay_mode=mode)

    order = pending
    if order is None:
        snapshot = await _snapshot(request, settings.STORAGE_PRODUCT_KEY)
        order = Order(
            order_no=new_order_no(),
            user_id=user.id,
            product_name=settings.SHOP_PRODUCT_NAME,
            amount=settings.SHOP_PRODUCT_AMOUNT,
            payment_mode=mode, merchant_id=cfg.mchid if mode == 'wechat' else None,
            app_id=cfg.appid if mode == 'wechat' else None, currency='CNY',
            delivery_key=snapshot.key, delivery_digest=snapshot.digest, delivery_size=snapshot.size,
            status=PENDING,
        )
        db.add(order)
        # `user.id` 必须在 flush **之前**取成局部变量：下面的 rollback 会无条件过期
        # 所有 ORM 对象（`expire_on_commit=False` 管不到 rollback），回滚后再碰
        # `user.id` 会触发同步懒加载，在 async 上下文里就是 MissingGreenlet
        # （真 PostgreSQL 上实测炸过，SQLite 单连接下反而看不出来）。
        user_id = user.id
        try:
            await db.flush()  # 仅取 id；事务失败仍会回滚，不能当作已经持久化
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
            _check_order_channel(order, mode, cfg)
            if order.code_url or mode == "manual":
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
        # A flush is not durable. Persist before crossing the external payment boundary.
        attempt_id = uuid.uuid4().hex
        db.add(PaymentEvent(order_id=order.id, attempt_id=attempt_id, kind='prepay_started'))
        await db.commit()
        try:
            order.code_url = await native_prepay(
                cfg,
                out_trade_no=order.order_no,
                description=order.product_name,
                total=order.amount,
            )
        except WeChatPayError as e:
            db.add(PaymentEvent(order_id=order.id, attempt_id=attempt_id, kind='prepay_unknown'))
            await db.commit()  # unknown means reconcile; never assert that no money moved
            raise HTTPException(502, str(e)) from e
        db.add(PaymentEvent(order_id=order.id, attempt_id=attempt_id, kind='prepay_ready'))
    await db.commit()
    await db.refresh(order)  # A callback may have changed status while prepay was in flight.
    return _payload(order, reused=False, pay_mode=mode)


@router.get("/orders/{order_no}")
async def order_status(
    order_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """只读查询当前用户的订单状态，返回展示字段和 expired，并禁止缓存。

    过期关闭只发生在主动下单流程；轮询不下新单、不发链接、不改变订单状态。
    不是当前用户的订单与不存在均返回 404。downloaded 表示曾发过链接，不消灭购买权益。"""
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
    if settings.SHOP_PAY_MODE != "mock" or settings.ENV != "development":
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

    复用与真实回调相同的原子凭证结算（`settle`），但严格绑定不同渠道，
    这样演示走通的路径和上生产走的是同一条，不会因为"演示专用代码"而漏测。
    """
    if settings.SHOP_PAY_MODE != "mock" or settings.ENV != "development":
        raise HTTPException(404, "模拟支付通道未开启（SHOP_PAY_MODE != mock）")
    order = await db.scalar(select(Order).where(Order.order_no == payload.order_no))
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")  # 不是自己的单一律 404，不暴露是否存在
    try:
        await settle(db, order, source='mock', transaction_id=f"MOCK-{order.order_no}",
                     paid_at=datetime.now(timezone.utc))
    except PaymentConflict as e:
        raise HTTPException(409, str(e)) from e
    return {
        "order_no": order.order_no,
        "status": order.status,
        "transaction_id": order.transaction_id,
        "pay_mode": "mock",
    }


# Structured operational logs supplement the transactional receipt; they are not the ledger.
_audit_logger = logging.getLogger("codemax.audit")


# ---------------------------------------------------------------- 人工确认收款（S5-04）


class EvidenceIn(BaseModel):
    evidence: str = Field(min_length=3, max_length=500, pattern=r"^[^\x00-\x1f]+$")

    @field_validator('evidence')
    @classmethod
    def meaningful_evidence(cls, value):
        if len(value.strip()) < 3:
            raise ValueError('核账依据不能只是空白')
        return value.strip()


class ManualReceiptIn(EvidenceIn):
    amount: StrictInt = Field(gt=0)
    reference: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    evidence: str = Field(min_length=3, max_length=500, pattern=r"^[^\x00-\x1f]+$")


@router.post("/orders/{order_no}/confirm", dependencies=[Depends(require_finance_origin)])
async def confirm_paid_manually(
    order_no: str,
    proof: ManualReceiptIn,
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

    凭证结算复用`settle`，提交状态与凭证；`CLOSED`也能收 ——
    用户扫了旧码照样可能付钱，钱到账就必须发货（TD-156）。
    """
    if settings.SHOP_PAY_MODE != "manual":
        raise HTTPException(404, "人工确认收款未开启（SHOP_PAY_MODE != manual）")
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        # 这里**可以**返回 404 而不是像用户侧那样刻意模糊：调用方是管理员，
        # 「这个单号不存在」是他需要知道的运维信息，不是要对他保密的东西。
        raise HTTPException(404, "订单不存在")
    try:
        changed = await settle(db, order, source='manual', transaction_id=proof.reference,
                               actor=admin, evidence=proof.evidence, amount=proof.amount, paid_at=datetime.now(timezone.utc))
    except PaymentConflict as e:
        raise HTTPException(409, str(e)) from e
    receipt = await db.scalar(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id))
    if changed:
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
        "confirmed_by": receipt.actor_name,
        "changed": changed,
    }


# Paid entitlements survive issuance failures; short-lived bearer links are reissuable.
@router.get("/orders")
async def order_history(before: int | None = Query(None, gt=0), user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    stmt = select(Order).where(Order.user_id == user.id)
    if before is not None:
        stmt = stmt.where(Order.id < before)
    rows = list((await db.scalars(stmt.order_by(Order.id.desc()).limit(50))).all())
    return {"orders": [_payload(r, reused=True, pay_mode=settings.SHOP_PAY_MODE) for r in rows],
            "next_cursor": rows[-1].id if len(rows) == 50 else None}


@router.post("/download/{order_no}", dependencies=[Depends(rate_limit("download", "RATE_LIMIT_TOOLS"))])
async def download_url(
    request: Request, order_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """为已付款订单所有者领取或重领短时链接，返回 URL、有效秒数和状态。

    先验证所有权、paid/downloaded 状态和对象存在，再生成 URL、幂等记录发放。
    downloaded 不是已收完文件的证明，也不是禁止重领的标志。"""
    storage = _storage(request)
    order = await db.scalar(select(Order).where(Order.order_no == order_no, Order.user_id == user.id))
    if order is None:
        raise HTTPException(404, "订单不存在")
    if order.status in (PENDING, CLOSED):
        raise HTTPException(403, "订单未支付")
    if order.status not in (PAID, DOWNLOADED):
        raise HTTPException(409, "订单状态不支持下载，请联系站内客服")
    key = order.delivery_key
    if not key or not order.delivery_digest or order.delivery_size is None:
        raise HTTPException(409, "历史交付对象未核准，请联系站内客服；购买权益未删除")
    if not storage.exists(key):
        raise HTTPException(404, f"商品文件不存在（对象 key：{key}）")
    if not await run_in_threadpool(verify_snapshot, storage, key, order.delivery_digest, order.delivery_size):
        raise HTTPException(409, "交付快照损坏，需从备份恢复；购买权益未删除")
    url = storage.presigned_url(key, expires_in=settings.DOWNLOAD_URL_TTL)
    try:
        await mark_downloaded(db, order)  # idempotent; not winning CAS no longer destroys paid rights
    except IllegalTransition as e:
        raise HTTPException(409, str(e)) from e
    return {"order_no": order.order_no, "download_url": url,
            "expires_in": settings.DOWNLOAD_URL_TTL, "status": order.status}


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
    if key.startswith('.snapshots/'):
        parts = key.split('/')
        if len(parts) != 3 or not await run_in_threadpool(verify_snapshot, storage, key, parts[1], path.stat().st_size):
            raise HTTPException(409, "交付快照完整性校验失败")
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
    pay_mode = order.payment_mode or 'legacy'
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
    已验证且处理/忽略成功 → 200 SUCCESS JSON；失败 → 4XX/5XX + `{"code": "FAIL", "message": "..."}`。
    精确重复成功通知200；冲突和未接入事件不伪装成功，退款须另行接入与运维告警。
    """
    cfg = pay_config()
    if not cfg.notify_ready:
        return _fail(503, "回调商户、应用或平台验签配置不完整")

    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
            return _fail(413, "回调报文过大")
        raw.extend(chunk)
    try:
        body = raw.decode("utf-8")  # Preserve exact signed text; never decode with replacement.
    except UnicodeDecodeError:
        return _fail(400, "回调报文必须为 UTF-8")
    h = request.headers
    try:
        # P1-3：先查新鲜度再验签。抓到真实回调原样重放时签名一直是合法的，
        # 只有时间戳能暴露它 —— 放在验签之前还能省掉一次 RSA 运算。
        if len(h.get("Wechatpay-Nonce", "")) > 128 or len(h.get("Wechatpay-Signature", "")) > 1024:
            raise WeChatPayError("回调签名头过长")
        assert_notify_fresh(h.get("Wechatpay-Timestamp", ""))
        assert_notify_identity(cfg, h.get("Wechatpay-Serial", ""))
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
    except (ValueError, RecursionError):
        return _fail(400, "回调报文不是合法有界JSON")

    if not isinstance(notify, dict) or not isinstance(notify.get("resource"), dict):
        return _fail(400, "回调结构无效")
    resource = notify["resource"]
    if any(not isinstance(resource.get(k), str) for k in ("ciphertext", "nonce")) or not isinstance(resource.get("associated_data", ""), str):
        return _fail(400, "回调加密字段无效")
    try:
        data = decrypt_resource(
            cfg.api_v3_key,
            ciphertext=resource.get("ciphertext", ""),
            nonce=resource.get("nonce", ""),
            associated_data=resource.get("associated_data") or "",
        )
    except WeChatPayError as e:
        return _fail(400, str(e))

    if not isinstance(data, dict):
        return _fail(400, "回调业务数据必须为对象")
    if notify.get("event_type") != "TRANSACTION.SUCCESS" or data.get("trade_state") != "SUCCESS":
        return _fail(422, "此入口仅处理支付成功；退款及其他事件尚未接入，不能当作已处理")

    if (data.get("appid") != cfg.appid or data.get("mchid") != cfg.mchid
            or data.get("trade_type") != "NATIVE" or resource.get("algorithm") != "AEAD_AES_256_GCM"
            or resource.get("original_type") != "transaction" or notify.get("resource_type") != "encrypt-resource"):
        return _fail(400, "回调商户、应用或交易类型不匹配")
    order_no, transaction_id = data.get("out_trade_no"), data.get("transaction_id")
    if any(not isinstance(value, str) or not value or "\x00" in value or len(value) > size
           for value, size in ((order_no, 32), (transaction_id, 64))):
        return _fail(400, "回调订单标识无效")
    amount = data.get("amount")
    if not isinstance(amount, dict) or type(amount.get("total")) is not int or amount.get("currency") != "CNY":
        return _fail(400, "回调金额结构无效")
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        return _fail(404, f"订单不存在：{data.get('out_trade_no')}")
    total = amount["total"]
    if total != order.amount:
        return _fail(400, f"金额不符：回调 {total} 分，订单 {order.amount} 分")

    try:
        success_time = data.get('success_time')
        if not isinstance(success_time, str) or len(success_time) > 40:
            raise ValueError('missing time')
        provider_paid_at = datetime.fromisoformat(success_time)
        if provider_paid_at.tzinfo is None:
            raise ValueError('missing timezone')
    except ValueError:
        return _fail(400, '支付成功时间必须为带时区的有效时间')

    # 幂等（S3-01-3-3）：重复通知不会二次迁移、不报错，也不覆盖首次的支付信息
    try:
        await settle(db, order, source='wechat', transaction_id=transaction_id,
                     merchant_id=data['mchid'], app_id=data['appid'], amount=total, paid_at=provider_paid_at)
    except PaymentConflict as e:
        return _fail(409, str(e))
    return _ok()


async def _snapshot(request: Request, key: str):
    try:
        return await run_in_threadpool(snapshot_product, _storage(request), key)
    except StorageError as e:
        raise HTTPException(503, str(e)) from e


def _check_order_channel(order: Order, mode: str, cfg) -> None:
    expected = (mode, cfg.mchid if mode == 'wechat' else None, cfg.appid if mode == 'wechat' else None)
    if (order.payment_mode, order.merchant_id, order.app_id) != expected:
        raise HTTPException(409, "待支付订单的渠道/商户与当前配置不一致，请核账后处理，不能换渠道确认")


class LegacyBindingIn(EvidenceIn):
    payment_mode: str = Field(pattern=r"^(manual|wechat|mock)$")
    source_key: str = Field(min_length=1, max_length=400, pattern=r"^[^\x00-\x1f]+$")
    evidence: str = Field(min_length=3, max_length=500, pattern=r"^[^\x00-\x1f]+$")


@router.post('/orders/{order_no}/legacy-binding', dependencies=[Depends(require_finance_origin)])
async def bind_legacy_order(order_no: str, proof: LegacyBindingIn, request: Request,
                            db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """Operator-reviewed one-time binding, not a guessed backfill or a new receipt for past money."""
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(404, '订单不存在')
    if order.payment_mode is not None or order.delivery_key is not None:
        raise HTTPException(409, '已绑定的订单合同不能改写')
    if proof.payment_mode == 'mock' and settings.ENV != 'development':
        raise HTTPException(409, '生产不能把历史订单绑定为模拟支付')
    cfg = pay_config()
    if proof.payment_mode == 'wechat':
        try:
            assert_notify_configuration(cfg)
        except WeChatPayError as e:
            raise HTTPException(503, '平台凭据不可用') from e
    snapshot = await _snapshot(request, proof.source_key)
    await lock_order(db, order)
    if order.payment_mode is not None or order.delivery_key is not None:
        await db.rollback()
        raise HTTPException(409, '订单已被其他维护者绑定')
    order.payment_mode = proof.payment_mode
    order.merchant_id = cfg.mchid if proof.payment_mode == 'wechat' else None
    order.app_id = cfg.appid if proof.payment_mode == 'wechat' else None
    order.delivery_key, order.delivery_digest, order.delivery_size = snapshot.key, snapshot.digest, snapshot.size
    db.add(PaymentEvent(order_id=order.id, attempt_id=uuid.uuid4().hex, kind='legacy_bound',
                        actor_id=admin.id, actor_name=admin.username, evidence=proof.evidence))
    await db.commit()
    return {'order_no': order.order_no, 'bound': True}


@router.get('/admin/orders/{order_no}/ledger')
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
    return {'order_no': order.order_no,
            'order': {'user_id': order.user_id, 'product_name': order.product_name, 'amount': order.amount,
                      'currency': order.currency, 'status': order.status, 'payment_mode': order.payment_mode or 'legacy',
                      'merchant_id': order.merchant_id, 'app_id': order.app_id,
                      'delivery_key': order.delivery_key, 'delivery_digest': order.delivery_digest,
                      'delivery_size': order.delivery_size},
            'actions': {'manual': settings.SHOP_PAY_MODE == 'manual', 'mock_binding': settings.ENV == 'development'},
            'receipt': ({'source': receipt.source, 'reference': receipt.transaction_id,
                         'amount': receipt.amount, 'currency': receipt.currency,
                         'actor': receipt.actor_name, 'evidence': receipt.evidence,
                         'paid_at': receipt.paid_at, 'received_at': receipt.received_at} if receipt else None),
            'events': [{'id': e.id, 'attempt_id': e.attempt_id, 'kind': e.kind,
                        'actor': e.actor_name, 'evidence': e.evidence, 'create_time': e.create_time} for e in events],
            'next_cursor': events[-1].id if len(events) == 50 else None}
