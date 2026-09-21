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

    返回 **403 而不是 404**：端点存在与否不是本站的秘密（开发 API 文档可列出它；生产文档关闭），
    假装不存在只会让管理员自己调试时对着 404 猜半天。真正的防线是下面那条 ——
    端点只接受管理员，而不是「别人找不到」。

    角色从库里读、不从 JWT 读，所以**降权立刻生效**，不用等 token 过期。
    """
    if user.role != 1:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


def check_browser_origin(request: Request, *, metadata_error: str, origin_error: str) -> None:
    """Reject requests a browser marks as cross-site, or whose Origin is not this site's canonical origin.

    Headerless non-browser clients (no Origin, no Sec-Fetch-Site) pass: this is origin/fetch-metadata
    defense, not a synchronizer-token system. Proxy canonical origin follows the same trusted
    configuration as generated public links (`public_base_url`).
    """
    from urllib.parse import urlsplit

    from .middleware import public_base_url

    origins = request.headers.getlist('origin')
    site = request.headers.get('sec-fetch-site')
    if site is not None and site != 'same-origin':
        raise HTTPException(403, metadata_error)
    if not origins:
        return
    def key(value):
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('origin')
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        valid = len(origins) == 1 and key(origins[0]) == key(public_base_url(request))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(403, origin_error)


def require_finance_origin(request: Request, token: str | None = Depends(oauth2_scheme)) -> None:
    """Reject foreign browser-origin financial writes, in addition to Lax cookies and JSON bodies.

    Explicit Bearer clients retain their API channel (get_current_user still validates that exact
    token before the operation). Cookie clients go through `check_browser_origin`.
    """
    if token:
        return
    check_browser_origin(request, metadata_error='财务操作必须从本站页面发起',
                         origin_error='财务操作来源不匹配，请从本站管理页面重试')


def require_login_origin(request: Request) -> None:
    """TD-262：表单登录只接受本站页面或无来源头的非浏览器客户端发起。

    登录没有 Bearer 可豁免（它就是签发凭证的入口）。浏览器带 `Sec-Fetch-Site: cross-site`
    或外站 `Origin` 的表单提交是登录 CSRF（把受害者登进攻击者账号，再借 Lax Cookie 观察其
    后续操作），拒绝之；Swagger / 脚本 / curl 不带这两个头，行为不变。
    """
    check_browser_origin(request, metadata_error='登录必须从本站页面发起',
                         origin_error='登录来源不匹配，请从本站页面重试')
