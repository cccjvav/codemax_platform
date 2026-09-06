from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..ratelimit import rate_limit
from ..schemas import PasswordChangeIn, RegisterIn, TokenOut, UserOut
from ..security import (
    AUTH_COOKIE,
    adummy_verify,
    ahash_password,
    averify_password,
    create_access_token,
)

router = APIRouter(prefix="/auth", tags=["认证中心"])


def _set_auth_cookie(response: Response, token: str) -> None:
    """把 token 写进 HttpOnly cookie，浏览器端因此不必再碰 localStorage（TD-44）。

    各属性的取舍：
    - HttpOnly：脚本读不到，XSS 拿不走 token —— 这正是本次改动的目的。
    - SameSite=Lax：跨站的 POST/PUT/DELETE 不带 cookie，CSRF 对写操作免疫。
      代价是**跨站顶层导航仍会带上** cookie，所以有副作用的接口一律不能是 GET；
      该不变式由 tests/test_auth_cookie.py 的路由清单钉住。
      不用 Strict 是因为它连「从微信/邮件点链接进来」的第一跳都不带 cookie，
      用户会看到一次莫名的未登录。
    - Secure 只在 production 开：本地是 http，加了浏览器根本不会存这个 cookie。
    - Max-Age 与 token 同寿命，免得 cookie 活得比 token 久、反复撞 401。
    """
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


@router.post("/register", response_model=UserOut, status_code=201,
             dependencies=[Depends(rate_limit("register", "RATE_LIMIT_AUTH"))])
async def register(data: RegisterIn, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "用户名已存在")
    user = User(username=data.username, password=await ahash_password(data.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut,
             dependencies=[Depends(rate_limit("login", "RATE_LIMIT_AUTH"))])
async def login(
    response: Response, form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)
):
    """登录。

    同时给两种客户端用：浏览器吃 Set-Cookie（HttpOnly，脚本读不到），
    API 客户端 / Swagger 吃响应体里的 access_token 走 Bearer 头。
    """
    user = await db.scalar(select(User).where(User.username == form.username))
    if user is None:
        # **不能短路**：直接返回会让「用户不存在」比「密码错」快 58 倍
        # （实测 4.6 ms vs 263 ms），响应时间就成了用户名枚举侧信道。
        # 跑一次假哈希把耗时拉平，对外仍是同一句错误、同一个状态码。
        await adummy_verify(form.password)
        raise HTTPException(401, "用户名或密码错误")
    if not await averify_password(form.password, user.password):
        raise HTTPException(401, "用户名或密码错误")
    if user.status != 1:
        raise HTTPException(403, "账号已禁用")
    token = create_access_token(user.username, user.password_changed_at)
    _set_auth_cookie(response, token)
    return TokenOut(access_token=token)


@router.post("/password", response_model=TokenOut,
             dependencies=[Depends(rate_limit("password", "RATE_LIMIT_AUTH"))])
async def change_password(
    data: PasswordChangeIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """改密码（TD-70）。

    改完会**吊销该用户此前签发的所有 token** —— 这正是改密码的主要用途之一
    （怀疑密码泄露时，光改密码而旧 token 还能用，等于没改）。

    返回一个**新** token：当前这次会话不该被自己踢下线，要踢的是**其他**会话。
    """
    if not await averify_password(data.old_password, user.password):
        # 与登录端点一样不透露具体原因，避免变成密码枚举接口
        raise HTTPException(400, "原密码不正确")
    if data.new_password == data.old_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    user.password = await ahash_password(data.new_password)
    # 用带时区的 UTC，列是 TIMESTAMPTZ（TD-146 的约定）
    user.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(user.username, user.password_changed_at)
    # cookie 也要换成新的：旧 cookie 里的 token 刚被自己吊销了，
    # 不换的话当前浏览器会话下一秒就 401。
    _set_auth_cookie(response, token)
    return TokenOut(access_token=token)


@router.post("/logout", status_code=204)
async def logout(response: Response):
    """清掉 cookie。

    **故意不要求登录态**：拿着一个已过期或已失效 cookie 的客户端也该能清掉它，
    否则用户会卡在「我明明退出了，浏览器却还带着一个死 cookie」的状态。
    token 本身不在服务端记录，所以退出只是让浏览器丢掉它（TD-70 的粒度说明）。
    """
    response.delete_cookie(AUTH_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
