from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from starlette.concurrency import run_in_threadpool

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
    return await run_in_threadpool(pwd_context.verify, plain, hashed)


_DUMMY_HASH: str | None = None


async def adummy_verify(plain: str) -> None:
    """对固定假哈希跑一次 verify，只为把耗时拉平。结果恒为 False，直接丢弃。

    用户不存在时若不跑 bcrypt，登录失败耗时差就是**用户名枚举侧信道**：
    实测用户不存在 4.6 ms vs 密码错 263 ms，差 58 倍 —— 攻击者不用撞密码，
    光看响应时间就能把有效用户名列出来。
    """
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
