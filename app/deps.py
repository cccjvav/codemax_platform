from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User
from .security import AUTH_COOKIE, decode_token
from .timeutil import as_utc

# auto_error=False：缺 Authorization 头时返回 None 而不是直接 401 ——
# 还要给 cookie 一次机会（TD-44：浏览器走 cookie，API 客户端/Swagger 走 Bearer）。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # 两者都给时以请求头为准：那是调用方显式表达的意图。
    token = token or request.cookies.get(AUTH_COOKIE)
    if not token:
        # auto_error=False 之后 WWW-Authenticate 要自己补，否则 Swagger 的
        # 「Authorize」按钮不再弹出登录框。
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录",
                            headers={"WWW-Authenticate": "Bearer"})
    claims = decode_token(token)
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的登录凭证")
    user = await db.scalar(select(User).where(User.username == claims.sub))
    if not user or user.status != 1:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已禁用")
    # Durable revision prevents same-second changes and clock rollback from reviving sessions.
    if claims.version != user.credential_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码已修改，请重新登录")
    if user.password_changed_at is not None:
        current = int(as_utc(user.password_changed_at).timestamp())
        if claims.pwd is None or claims.pwd < current:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码已修改，请重新登录")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """TD-138：只有管理员（`role == 1`）能通过。

    返回 **403 而不是 404**：端点存在与否不是本站的秘密（`/docs` 里本来就列着），
    假装不存在只会让管理员自己调试时对着 404 猜半天。真正的防线是下面那条 ——
    端点只接受管理员，而不是「别人找不到」。

    角色从库里读、不从 JWT 读，所以**降权立刻生效**，不用等 token 过期。
    """
    if user.role != 1:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user
