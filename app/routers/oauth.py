import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import OAuthClient, OAuthCode, User
from ..ratelimit import rate_limit
from ..security import adummy_verify, averify_password, create_access_token
from ..site import page_context, templates
from ..timeutil import as_utc

router = APIRouter(prefix="/oauth", tags=["OAuth2 授权码 SSO"])

AUTH_CODE_EXPIRE_MINUTES = 10  # 授权码有效期


def _utcnow() -> datetime:
    """带时区的 UTC 现在时刻。时间列已是 TIMESTAMPTZ（TD-146），
    所以**不要**再 .replace(tzinfo=None) —— 抹掉时区就退化成裸值，
    与数据库写入的绝对时刻无法正确比较。"""
    return datetime.now(timezone.utc)


def _oauth_error(error: str, description: str = "") -> HTTPException:
    return HTTPException(400, {"error": error, "error_description": description})


async def _active_client(db: AsyncSession, client_id: str, redirect_uri: str) -> OAuthClient:
    """取一个启用中、且回调地址与登记值完全一致的客户端。同意页与签发码共用这套校验，
    免得两边校验强度不一致 —— 校验弱的那一边就是漏洞。"""
    client = await db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if not client or client.status != 1:
        raise _oauth_error("invalid_client")
    if client.redirect_uri != redirect_uri:
        raise _oauth_error("invalid_redirect_uri")
    return client


def _sign(client_id: str, redirect_uri: str, state: str | None) -> str:
    """给授权请求的三个参数签名，塞进同意页的隐藏表单里。

    作用是**把 POST 绑定到「本站渲染过的那张同意页」**：攻击者没有 SECRET_KEY，
    签不出合法签名，就无法跳过用户点同意直接构造一个 POST。顺带也防住了
    同意页渲染之后有人篡改 redirect_uri / state。

    这不是本项目 CSRF 的**主要**防线 —— 主要防线是登录 cookie 的 SameSite=Lax
    （跨站 POST 根本不带 cookie）。这条是第二层，好处是零会话状态。
    """
    payload = f"{client_id}\n{redirect_uri}\n{state or ''}".encode()
    digest = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@router.get("/authorize")
async def authorize(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    state: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """**只渲染授权同意页，不签发授权码**（TD-78）。签发在下面的 POST。

    原来这里是直接签发 code 并 302 的，有两个问题：

    1. 用户从头到尾没见过「某某应用请求访问你的账号」，不符合 OAuth 的用户同意语义。
    2. TD-44 把登录态改成 cookie 之后，它成了一个**新引入**的 CSRF 面（TD-175）：
       SameSite=Lax 挡不住跨站顶层导航的 GET，攻击者一个跳转就能替用户签发授权码。
       现在 GET 只渲染页面、**签不出任何东西**，那个面就关掉了。

    刻意保留「未登录直接 401」而不是跳转到登录页：本站没有独立登录页
    （登录表单内嵌在工具页里），为一个跳转新造一页不值得。
    """
    if response_type != "code":
        raise _oauth_error("unsupported_response_type")
    client = await _active_client(db, client_id, redirect_uri)
    return templates.TemplateResponse(
        "oauth_consent.html",
        page_context(
            request,
            title=f"授权 {client.name}",
            # 同意页**必须零脚本**（发放授权码的安全关键页，见 TD-163 与
            # tests/test_oauth_consent.py）。它也不需要登录 UI —— 能走到这页
            # 说明用户已经登录，所以整套浮层与 auth.js 都不渲染。
            auth_ui=False,
            client_name=client.name,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state or "",
            sig=_sign(client_id, redirect_uri, state),
            username=user.username,
        ),
    )


@router.post("/authorize")
async def authorize_submit(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    sig: str = Form(...),
    state: str | None = Form(None),
    approve: str = Form("0"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户点了「同意」之后才签发授权码并重定向。

    `approve` 默认 `"0"`（拒绝）—— 表单里少传字段时必须落到**更安全**的那一侧。
    """
    # compare_digest：签名比对不能短路返回，否则响应时间会泄露信息
    if not hmac.compare_digest(sig, _sign(client_id, redirect_uri, state)):
        raise _oauth_error("invalid_request", "同意页签名无效，请重新发起授权")
    client = await _active_client(db, client_id, redirect_uri)

    if approve != "1":
        params = {"error": "access_denied", "error_description": "用户拒绝授权"}
        if state:
            params["state"] = state
        return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)

    code = secrets.token_urlsafe(24)
    db.add(OAuthCode(
        code=code,
        user_id=user.id,
        client_id=client.id,
        redirect_uri=redirect_uri,
        expires_at=_utcnow() + timedelta(minutes=AUTH_CODE_EXPIRE_MINUTES),
    ))
    await db.commit()

    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)


@router.post("/token",
             dependencies=[Depends(rate_limit("token", "RATE_LIMIT_AUTH"))])
async def token(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """令牌端点：客户端用授权码 + 客户端凭证换取 access_token（授权码一次性、短时有效）。"""
    if grant_type != "authorization_code":
        raise _oauth_error("unsupported_grant_type")
    client = await db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if client is None or client.status != 1:
        # 与登录端点同理：不跑 bcrypt 就返回，「未知 client_id」会比「密钥错」快几十倍，
        # 响应时间直接变成 client_id 枚举侧信道。跑一次假哈希把耗时拉平。
        await adummy_verify(client_secret)
        raise _oauth_error("invalid_client", "客户端凭证无效")
    if not await averify_password(client_secret, client.client_secret_hash):
        raise _oauth_error("invalid_client", "客户端凭证无效")

    oauth_code = await db.scalar(select(OAuthCode).where(OAuthCode.code == code))
    if (
        not oauth_code
        or oauth_code.client_id != client.id
        or oauth_code.redirect_uri != redirect_uri
        or oauth_code.used
    ):
        raise _oauth_error("invalid_grant", "授权码无效或已使用")
    if as_utc(oauth_code.expires_at) < _utcnow():
        raise _oauth_error("invalid_grant", "授权码已过期")

    # 原子消费授权码：把"检查未使用 + 标记已使用"合成一条 UPDATE，
    # 并发重放同一个 code 时只有一个请求能拿到 rowcount=1（原先先查后改存在重放窗口）
    consumed = await db.execute(
        update(OAuthCode).where(OAuthCode.code == code, OAuthCode.used.is_(False)).values(used=True)
    )
    if consumed.rowcount != 1:
        raise _oauth_error("invalid_grant", "授权码无效或已使用")

    user = await db.get(User, oauth_code.user_id)
    if user is None:  # 授权码签发后用户被删：不能让它变成 500
        raise _oauth_error("invalid_grant", "用户不存在")
    # 必须在 commit 前取值：`password_changed_at` 要原样进 token，
    # 漏传的话 `get_current_user` 会把这枚**刚签发的** token 判成「密码已修改」（TD-197）。
    subject, pwd_changed_at = user.username, user.password_changed_at
    await db.commit()

    return {
        "access_token": create_access_token(subject, pwd_changed_at),
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
