import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import OAuthClient, OAuthCode, User
from ..security import create_access_token, verify_password

router = APIRouter(prefix="/oauth", tags=["OAuth2 授权码 SSO"])

AUTH_CODE_EXPIRE_MINUTES = 10  # 授权码有效期


def _utcnow() -> datetime:
    """统一使用无时区的 UTC 时间（与数据库 TIMESTAMP 列保持一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _oauth_error(error: str, description: str = "") -> HTTPException:
    return HTTPException(400, {"error": error, "error_description": description})


@router.get("/authorize")
async def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    state: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """授权码端点：用户已登录（Bearer JWT）后，向客户端签发一次性授权码并重定向。

    浏览器流程：认证中心登录（拿 JWT）→ 携带 JWT 访问本端点 → 302 跳回客户端回调地址。
    """
    if response_type != "code":
        raise _oauth_error("unsupported_response_type")
    client = await db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if not client or client.status != 1:
        raise _oauth_error("invalid_client")
    if client.redirect_uri != redirect_uri:
        raise _oauth_error("invalid_redirect_uri")

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


@router.post("/token")
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
    if not client or client.status != 1 or not verify_password(client_secret, client.client_secret_hash):
        raise _oauth_error("invalid_client", "客户端凭证无效")

    oauth_code = await db.scalar(select(OAuthCode).where(OAuthCode.code == code))
    if (
        not oauth_code
        or oauth_code.client_id != client.id
        or oauth_code.redirect_uri != redirect_uri
        or oauth_code.used
    ):
        raise _oauth_error("invalid_grant", "授权码无效或已使用")
    if oauth_code.expires_at < _utcnow():
        raise _oauth_error("invalid_grant", "授权码已过期")

    oauth_code.used = True
    user = await db.get(User, oauth_code.user_id)
    await db.commit()

    return {
        "access_token": create_access_token(user.username),
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
