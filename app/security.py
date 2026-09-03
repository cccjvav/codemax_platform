from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings
from .timeutil import as_utc

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 浏览器侧存放 access token 的 cookie 名（TD-44）。
# 必须是 HttpOnly —— 这是整件事的重点：脚本读不到它，XSS 就拿不走 token；
# 存在 localStorage 里的话任何一段注入脚本都能直接读走。
AUTH_COOKIE = "access_token"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


@dataclass(frozen=True)
class TokenClaims:
    """JWT 里我们关心的声明。

    `pwd` 是签发时该用户 `password_changed_at` 的 UNIX 秒；None 表示签发时
    用户从未改过密码。校验逻辑在 `app/deps.py`（要查库，所以不放这里）。
    """

    sub: str
    pwd: int | None


def _pwd_stamp(moment: datetime | None) -> int | None:
    """把改密码时刻压成 UNIX 秒。JWT 声明里放数字比放 ISO 串小得多，
    而且不受时区/格式歧义影响。"""
    if moment is None:
        return None
    return int(as_utc(moment).timestamp())


def create_access_token(subject: str, password_changed_at: datetime | None = None) -> str:
    """签发 access token。

    `password_changed_at` 必须传**当前库里的值**，否则改过密码的用户拿到的
    token 会立刻被判为失效。
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims: dict = {"sub": subject, "exp": expire, "pwd": _pwd_stamp(password_changed_at)}
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> TokenClaims | None:
    """解析并校验 JWT 签名与过期时间，返回声明；无效返回 None。

    注意这里**只**验签与 exp，不判断 pwd 是否过期 —— 那需要查库拿用户当前值，
    属于 `get_current_user` 的职责。
    """
    try:
        raw = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    sub = raw.get("sub")
    if not sub:
        return None
    pwd = raw.get("pwd")
    return TokenClaims(sub=sub, pwd=int(pwd) if pwd is not None else None)
