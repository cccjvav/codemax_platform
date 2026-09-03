from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..ratelimit import rate_limit
from ..schemas import PasswordChangeIn, RegisterIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["认证中心"])


@router.post("/register", response_model=UserOut, status_code=201,
             dependencies=[Depends(rate_limit("register", "RATE_LIMIT_AUTH"))])
async def register(data: RegisterIn, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "用户名已存在")
    user = User(username=data.username, password=hash_password(data.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut,
             dependencies=[Depends(rate_limit("login", "RATE_LIMIT_AUTH"))])
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.password):
        raise HTTPException(401, "用户名或密码错误")
    if user.status != 1:
        raise HTTPException(403, "账号已禁用")
    return TokenOut(
        access_token=create_access_token(user.username, user.password_changed_at)
    )


@router.post("/password", response_model=TokenOut,
             dependencies=[Depends(rate_limit("password", "RATE_LIMIT_AUTH"))])
async def change_password(
    data: PasswordChangeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """改密码（TD-70）。

    改完会**吊销该用户此前签发的所有 token** —— 这正是改密码的主要用途之一
    （怀疑密码泄露时，光改密码而旧 token 还能用，等于没改）。

    返回一个**新** token：当前这次会话不该被自己踢下线，要踢的是**其他**会话。
    """
    if not verify_password(data.old_password, user.password):
        # 与登录端点一样不透露具体原因，避免变成密码枚举接口
        raise HTTPException(400, "原密码不正确")
    if data.new_password == data.old_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    user.password = hash_password(data.new_password)
    # 用带时区的 UTC，列是 TIMESTAMPTZ（TD-146 的约定）
    user.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return TokenOut(
        access_token=create_access_token(user.username, user.password_changed_at)
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
