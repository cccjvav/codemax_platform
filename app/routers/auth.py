from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import RegisterIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["认证中心"])


@router.post("/register", response_model=UserOut, status_code=201)
async def register(data: RegisterIn, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "用户名已存在")
    user = User(username=data.username, password=hash_password(data.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.password):
        raise HTTPException(401, "用户名或密码错误")
    if user.status != 1:
        raise HTTPException(403, "账号已禁用")
    return TokenOut(access_token=create_access_token(user.username))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
