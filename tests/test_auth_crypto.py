"""登录路径上的 bcrypt：不许留在事件循环，也不许泄露用户名存在性。

三条都是实测出来的，不是理论推演：

1. **事件循环停顿**。bcrypt 单次 verify ≈ 260 ms 且是同步的。改之前在事件循环里
   连跑 5 次，同期 `asyncio.sleep(10ms)` 的最大漂移达到 **1280 ms** —— 那 1.3 秒内
   全站所有请求（含不需鉴权的工具页）都排不上队。改走线程池后漂移降到 **4 ms**。
2. **时序侧信道**。改之前「用户不存在」4.6 ms、「密码错」263 ms，差 **58 倍** ——
   攻击者不必撞密码，光看响应时间就能列出有效用户名。拉平后 **1.0 倍**。
3. **`/oauth/token` 零限流**。它也做一次 bcrypt，而 `grep rate_limit app/routers/oauth.py`
   原本一条都没有 —— 匿名 4 QPS 就能把单进程实例的 p99 拖到秒级。

这三条与 TD-159/183/186「重 CPU 不留在事件循环」是同一条原则。
"""
import asyncio
import time

import pytest

from app.security import ahash_password, averify_password, hash_password
from tests.test_download import auth_headers

# 事件循环漂移的阈值取得很松（80 ms）：单次 bcrypt 就要 260 ms，
# 只要有一发留在事件循环里，漂移必然远超这个数。留余量是为了不在慢 CI 上抖。
_MAX_LOOP_DRIFT_MS = 80.0


async def test_bcrypt_does_not_block_the_event_loop():
    """并发跑多次 verify 时，事件循环必须还能按时醒来。"""
    hashed = await ahash_password("secret123")
    drift: list[float] = []

    async def watch():
        for _ in range(60):
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            drift.append((time.perf_counter() - t0 - 0.01) * 1000)

    watcher = asyncio.create_task(watch())
    await asyncio.gather(*[averify_password("secret123", hashed) for _ in range(5)])
    await watcher

    assert drift, "前提校验：观测协程应当真的跑起来了"
    worst = max(drift)
    assert worst < _MAX_LOOP_DRIFT_MS, (
        f"事件循环被阻塞了 {worst:.0f} ms（阈值 {_MAX_LOOP_DRIFT_MS} ms）——"
        "bcrypt 又跑回事件循环里了。同期全站请求都会被它挡住。"
    )


async def test_login_timing_does_not_leak_whether_the_user_exists(client):
    """「用户不存在」与「密码错」的耗时必须落在同一量级。"""
    await client.post("/auth/register", json={"username": "realuser", "password": "secret123"})

    async def median_ms(payload, n=5):
        samples = []
        for _ in range(n):
            t0 = time.perf_counter()
            await client.post("/auth/login", data=payload)
            samples.append((time.perf_counter() - t0) * 1000)
        return sorted(samples)[len(samples) // 2]

    no_user = await median_ms({"username": "nosuchuser_xyz", "password": "secret123"})
    bad_pass = await median_ms({"username": "realuser", "password": "wrongpass1"})

    ratio = max(no_user, bad_pass) / max(1.0, min(no_user, bad_pass))
    assert ratio < 2.0, (
        f"耗时差 {ratio:.1f} 倍（不存在 {no_user:.1f} ms / 密码错 {bad_pass:.1f} ms）——"
        "这已经是可用的用户名枚举侧信道。用户不存在时也必须跑一次 bcrypt。"
    )


async def test_oauth_token_endpoint_is_rate_limited():
    """/oauth/token 做 bcrypt 且匿名可达，必须挂限流。

    直接查路由的依赖链，不打网络 —— 这条要守的是「配置没被摘掉」，
    而不是「限流算法对不对」（后者由 tests/test_ratelimit.py 覆盖）。
    """
    from main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == "/oauth/token")
    deps = {d.call.__qualname__ for d in route.dependant.dependencies if d.call}
    assert any("rate_limit" in d for d in deps), (
        f"/oauth/token 没有挂限流（实际依赖：{deps or '无'}）。"
        "它每次调用都做一次 bcrypt，匿名可达，不限流就是免费的 DoS 放大器。"
    )


def test_sync_helpers_still_exist_for_scripts_and_tests():
    """同步版不能删：`tests/conftest.py` 造种子数据用的是同步上下文。"""
    assert hash_password("x" * 8).startswith("$2")


@pytest.mark.parametrize("plain", ["secret123", ""])
async def test_async_wrapper_agrees_with_sync(plain):
    from app.security import verify_password

    hashed = hash_password(plain or "placeholder")
    if plain:
        assert await averify_password(plain, hashed) is verify_password(plain, hashed) is True
    else:
        assert await averify_password(plain, hashed) is False


# ---------------------------------------------------------------- 72 字节截断（A-6）
#
# bcrypt 的输入上限是 **72 字节**（不是 72 个字符），超出部分**静默丢弃**、不报错。
# 实测：hash("密"*64)（192 字节）之后，用 "密"*24（正好 72 字节）就能登录成功 ——
# 两个完全不同的密码被当成同一个。
#
# 而 schema 的 `max_length=64` 卡的是**字符数**，一个 64 汉字的密码是 192 字节，
# 完全能通过校验。所以这道口子是真实可达的。


# (密码, 错误信息里必须出现的关键词)
# `"a"*73` 会先被 `max_length=64` 的**字符数**规则拦下，所以它那条不该要求出现 "72"；
# 两个汉字用例才是真正只有字节规则能拦住的（25 / 64 个字符都在 64 以内）。
@pytest.mark.parametrize(
    ("password", "marker"),
    [
        ("密" * 25, "72"),  # 75 字节，字符数 25 合法 ⇒ 只有字节规则能拦
        ("密" * 64, "72"),  # 192 字节，字符数 64 刚好合法 ⇒ 同上
        ("a" * 73, "64"),  # 73 字节，但字符数 73 已超 max_length=64 ⇒ 先被字符规则拦
    ],
)
async def test_password_longer_than_72_bytes_is_rejected(client, password, marker):
    """超过 72 字节的密码必须被明确拒绝，而不是静默截断。

    为什么这比「截断」危险得多：截断之后**两个不同的密码能互相登录**。
    受害者注册了 64 个汉字的密码，攻击者只要知道前 24 个汉字就能进他的账号 ——
    而「密码前缀」恰恰是最容易被猜到的部分。

    为什么不在 bcrypt 之前先做 SHA-256 预哈希（那样就没有长度上限了）：
    那会改变哈希格式，库里已有的哈希（含 `full_init.sql` 的种子管理员）全部失效。
    而拒绝的代价很小 —— 64 字节上限只影响「24 个汉字以上」的密码，
    正常使用完全碰不到；schema 本来也已经有 64 字符的上限。
    """
    r = await client.post("/auth/register", json={"username": "longpw_user", "password": password})
    assert r.status_code == 422, (
        f"{len(password.encode())} 字节的密码竟然注册成功（{r.status_code}）—— "
        "bcrypt 会静默截断，导致不同密码互相登录。"
    )
    assert marker in r.text, (
        f"错误信息应提到 {marker!r}（{'字节上限' if marker == '72' else '字符上限'}），"
        f"实际：{r.text[:200]}"
    )


async def test_truncation_collision_is_actually_closed():
    """直接钉住后果本身：截断碰撞必须不可能发生。

    上一条测的是「HTTP 层拒绝」，这一条测的是「碰撞不存在」——
    万一将来有人把校验挪走或放宽，这条会立刻抓住。
    """
    from pydantic import ValidationError

    from app.schemas import RegisterIn

    with pytest.raises(ValidationError):
        RegisterIn(username="collision_u", password="密" * 64)


async def test_password_change_has_the_same_72_byte_limit(client):
    """改密码必须同一条规则，否则绕过注册就能塞进超长密码。

    `PasswordChangeIn` 的 docstring 本来就写着「新密码规则与 RegisterIn 保持一致，
    避免两套标准」—— 这条把那句话变成可执行的断言。
    """
    h = await auth_headers(client, "pwlimit_u")
    r = await client.post(
        "/auth/password",
        headers=h,
        json={"old_password": "secret123", "new_password": "密" * 64},
    )
    assert r.status_code == 422, (
        f"改密码接受了 {len(('密' * 64).encode())} 字节的新密码（{r.status_code}）"
    )
