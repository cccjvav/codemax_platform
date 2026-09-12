from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from starlette.concurrency import run_in_threadpool

from .config import settings
from .timeutil import as_utc

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 浏览器侧存放 access token 的 cookie 名（TD-44）。
# HttpOnly 限制脚本直接读取 Cookie，但注入脚本仍可能借会话发请求；
# 存在 localStorage 里的话任何一段注入脚本都能直接读走。
AUTH_COOKIE = "access_token"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if "\x00" in plain or len(plain.encode("utf-8")) > 72:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


# ⚠️ **HTTP 处理路径上必须用下面这两个 async 版本。**
#
# bcrypt 是刻意的慢函数（本机实测单次 verify ≈ 260 ms），而它是**同步**的。
# 直接在 async 端点里调用会把整个事件循环冻住那么久 —— 实测在事件循环里连跑
# 5 次 verify，同期 `asyncio.sleep(10ms)` 的最大漂移达到 1280 ms，也就是
# 这 1.3 秒内**全站所有请求**（含不需要鉴权的工具页）都排不上队。
# 这与 TD-159/183/186「重 CPU 不留在事件循环」是同一条原则。
#
# 用线程池而不是 `cpu_pool` 的进程池：bcrypt 在哈希期间会释放 GIL，线程就够；
# 进程池还要 pickle 参数、在 Windows 上 spawn 重新导入模块，不划算。
# 同步版保留给测试与脚本用（`tests/conftest.py` 造种子数据是同步上下文）。
async def ahash_password(password: str) -> str:
    return await run_in_threadpool(pwd_context.hash, password)


async def averify_password(plain: str, hashed: str) -> bool:
    return await run_in_threadpool(verify_password, plain, hashed)


_DUMMY_HASH: str | None = None


async def adummy_verify(plain: str) -> None:
    """对固定假哈希执行校验并丢弃结果，返回 None。

    用于减小不存在用户与错误密码的明显耗时差；不是对所有输入保证恒定时间的实现。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = await ahash_password("dummy-password-for-timing-equalization")
    await averify_password(plain, _DUMMY_HASH)


@dataclass(frozen=True)
class TokenClaims:
    """JWT 里我们关心的声明。

    `pwd` 是签发时该用户 `password_changed_at` 的 UNIX 秒；None 表示签发时
    用户从未改过密码。校验逻辑在 `app/deps.py`（要查库，所以不放这里）。
    """

    sub: str
    pwd: int | None
    version: int


def _pwd_stamp(moment: datetime | None) -> int | None:
    """把改密码时刻压成 UNIX 秒。JWT 声明里放数字比放 ISO 串小得多，
    而且不受时区/格式歧义影响。"""
    if moment is None:
        return None
    return int(as_utc(moment).timestamp())


def create_access_token(subject: str, password_changed_at: datetime | None = None, credential_version: int = 0) -> str:
    """由用户名及数据库当前改密时间、凭据版本签发 JWT 字符串。

    本函数不查库。调用者必须传当前 credential_version；默认 0 仅适用于尚处于版本 0 的用户。"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims: dict = {"sub": subject, "exp": expire, "pwd": _pwd_stamp(password_changed_at), "ver": credential_version}
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> TokenClaims | None:
    """校验签名、过期和声明结构，返回 TokenClaims；无效返回 None。

    不查用户状态/当前凭据版本，也不单独授权请求；get_current_user 负责数据库校验。"""
    try:
        raw = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    sub = raw.get("sub")
    if not sub:
        return None
    pwd = raw.get("pwd")
    version = raw.get("ver")
    if type(version) is not int or version < 0 or (pwd is not None and type(pwd) is not int):
        return None
    return TokenClaims(sub=sub, pwd=pwd, version=version)
