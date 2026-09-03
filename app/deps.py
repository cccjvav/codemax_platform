from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User
from .security import decode_token
from .timeutil import as_utc

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
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
