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
    # TD-70：改过密码后，此前签发的 token 一律失效。
    #
    # 比较用 `<`：只有「token 的时间戳早于库里当前值」才拒。
    #
    # 注：`<` 与 `!=` 在所有**可达**路径上行为一致 —— 改密码后新签发的 token
    # 其 pwd 与库里当前值相等，两种写法都放行（`a != a` 本就是 False）。
    # 实测把这里改成 `!=` 是**杀不掉的等价变异**，15 条测试全绿。
    # 两者只在 `pwd > current`（机器时钟回拨、或有人手改了库里的值）时才有区别：
    # `<` 放行、`!=` 拒绝。选 `<` 是因为那种情况下拒绝会让用户莫名其妙登不进去，
    # 而放行并没有放宽真正的威胁模型（旧 token 该拒的都拒了）。
    #
    # `password_changed_at` 为 NULL（从未改过密码）时跳过检查，
    # 这样上线本次改动不会让全站已登录用户瞬间掉线。
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
