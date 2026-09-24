"""TD-15 测试：接口限流。

限流在 conftest 里**默认关闭**（几十个用例共用同一个客户端 IP，开着会互相挤爆配额），
这里显式打开来测。时钟用注入的假时钟，不必真的等窗口过期。

除了"限得住"，还要测两件容易做错的事：
- **滑动窗口**不是"每 60 秒清零"，而是逐条过期
- **默认不信任 `X-Forwarded-For`**：那是客户端能随便填的头，信了等于没有配额

TD-261 再补键容量这一层（复核报告 F-09）：满桶时先回收已过期桶再判容量，满且全活跃仍拒绝
新键（不驱逐活跃桶），IPv6 按 /64 归并成一个身份。全部用合成时钟与进程内 ASGI，不是压测。
"""
import logging

import httpx
import pytest

from app.config import settings
from app.ratelimit import IPV6_PREFIX, Limiter, client_key, limiter, register_daily_limiter
from main import app

DDL = {"ddl": "CREATE TABLE t (id INT);"}


@pytest.fixture(autouse=True)
def _clean():
    limiter.reset()
    register_daily_limiter.reset()   # 每日桶表独立，测试之间也要清干净
    yield
    limiter.reset()
    register_daily_limiter.reset()


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOOLS", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH", 5)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)


# ---------------------------------------------------------------- 计数器本身


def test_blocks_after_limit_and_recovers_after_window():
    clock = FakeClock()
    lim = Limiter(clock=clock)
    for _ in range(3):
        assert lim.allow("k", limit=3, window=60)[0] is True
    ok, retry_after = lim.allow("k", limit=3, window=60)
    assert ok is False
    assert 1 <= retry_after <= 61

    clock.advance(59)
    assert lim.allow("k", limit=3, window=60)[0] is False, "窗口没到就还不行"
    clock.advance(2)
    assert lim.allow("k", limit=3, window=60)[0] is True


def test_window_slides_instead_of_resetting():
    """滑动窗口：逐条过期，不是每 60 秒统一清零。"""
    clock = FakeClock()
    lim = Limiter(clock=clock)
    assert lim.allow("k", limit=2, window=60)[0] is True
    clock.advance(40)
    assert lim.allow("k", limit=2, window=60)[0] is True
    clock.advance(30)  # 第一条已 70 秒（过期），第二条才 30 秒（仍有效）
    assert lim.allow("k", limit=2, window=60)[0] is True
    assert lim.allow("k", limit=2, window=60)[0] is False


def test_keys_are_independent():
    lim = Limiter(clock=FakeClock())
    assert lim.allow("a", limit=1, window=60)[0] is True
    assert lim.allow("a", limit=1, window=60)[0] is False
    assert lim.allow("b", limit=1, window=60)[0] is True


def test_prune_drops_spent_keys():
    """白盒：key 过期后要能清掉，否则字典会随不同 IP 无限增长。"""
    clock = FakeClock()
    lim = Limiter(clock=clock)
    lim.allow("spent", limit=5, window=60)
    lim.allow("fresh", limit=5, window=60)
    assert set(lim._hits) == {"spent", "fresh"}

    clock.advance(61)  # spent 最后一次命中已在窗口外
    lim.allow("fresh", limit=5, window=60)  # fresh 又有新命中
    lim.prune(60)
    assert "spent" not in lim._hits
    assert "fresh" in lim._hits


# ---------------------------------------------------------------- 键容量（TD-261 / F-09）


def _fill(lim: Limiter, count: int, *, window: int = 60) -> None:
    for i in range(count):
        assert lim.allow(f"scope:{i}", limit=1, window=window)[0] is True


def test_full_table_admits_new_key_once_any_bucket_expired():
    """满桶但有过期桶：新键必须放行，且只回收过期桶，活跃桶原样保留。"""
    clock = FakeClock()
    lim = Limiter(clock=clock, max_keys=4)
    _fill(lim, 2)  # scope:0 / scope:1 在 t=1000 放行
    clock.advance(30)
    for i in (2, 3):  # scope:2 / scope:3 在 t=1030 放行
        assert lim.allow(f"scope:{i}", limit=1, window=60)[0] is True
    assert len(lim._hits) == 4
    assert lim.admit("newcomer", limit=1, window=60).reason == "capacity"

    clock.advance(31)  # scope:0/scope:1 的窗口已过（61 秒），scope:2/scope:3 才 31 秒
    verdict = lim.admit("newcomer", limit=1, window=60)
    assert verdict.allowed is True and verdict.reason == "ok"
    assert "newcomer" in lim._hits
    assert "scope:2" in lim._hits and "scope:3" in lim._hits, "活跃桶不能被驱逐"
    assert "scope:0" not in lim._hits and "scope:1" not in lim._hits
    assert lim.allow("scope:2", limit=1, window=60)[0] is False, "活跃桶的配额记录必须还在"


def test_full_table_with_only_live_buckets_still_refuses_and_reports_capacity():
    """满且全活跃：仍拒绝新键（不驱逐活跃桶），原因是 capacity 而非 quota，Retry-After 指向最早到期桶。"""
    clock = FakeClock()
    lim = Limiter(clock=clock, max_keys=3)
    _fill(lim, 3)
    clock.advance(10)
    verdict = lim.admit("newcomer", limit=5, window=60)
    assert verdict.allowed is False and verdict.reason == "capacity"
    assert verdict.retry_after == 51, "最早的桶在 60 秒到期，已过 10 秒，再加 1 秒余量"
    assert "newcomer" not in lim._hits and len(lim._hits) == 3
    assert lim.admit("scope:0", limit=1, window=60).reason == "quota", "老身份自己的超额仍是 quota"


def test_expired_recovery_is_bounded_per_call_but_amortised():
    """一次调用最多回收固定批量，不做全表扫描；多次调用后过期桶全部释放。"""
    clock = FakeClock()
    lim = Limiter(clock=clock, max_keys=200)
    _fill(lim, 200)
    clock.advance(61)
    assert lim.allow("n0", limit=1, window=60)[0] is True
    assert 200 - 32 <= len(lim._hits) <= 200, "单次调用只回收有限批量"
    for i in range(1, 20):
        assert lim.allow(f"n{i}", limit=1, window=60)[0] is True
    assert set(lim._hits) == {f"n{i}" for i in range(20)}, "过期桶最终全部释放，新桶全部保留"


def test_capacity_refusal_logs_a_rate_limited_warning(caplog):
    clock = FakeClock()
    lim = Limiter(clock=clock, max_keys=2)
    _fill(lim, 2)
    with caplog.at_level(logging.WARNING, logger="codemax.ratelimit"):
        for _ in range(5):
            assert lim.admit("x", limit=1, window=60).reason == "capacity"
        clock.advance(59)
        assert lim.admit("y", limit=1, window=60).reason == "capacity"
    warnings = [r for r in caplog.records if r.name == "codemax.ratelimit" and r.levelno == logging.WARNING]
    assert len(warnings) == 1, "同一分钟内重复满桶只记一条，攻击期间不能反过来刷爆日志"
    assert "2/2" in warnings[0].getMessage() and "429" in warnings[0].getMessage()


def test_reset_clears_every_container():
    lim = Limiter(clock=FakeClock(), max_keys=2)
    _fill(lim, 2)
    assert lim.admit("x", limit=1, window=60).reason == "capacity"
    lim.reset()
    assert not lim._hits and not lim._expires
    assert lim.allow("x", limit=1, window=60)[0] is True


# ---------------------------------------------------------------- 客户端身份（IPv6 /64）


def _key_for(peer: str, headers: dict | None = None) -> str:
    scope = {
        "type": "http", "method": "GET", "path": "/", "query_string": b"", "scheme": "http",
        "server": ("test", 80), "client": (peer, 1234),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    from fastapi import Request

    return client_key(Request(scope))


def test_ipv6_peers_in_one_64_share_an_identity_and_ipv4_stays_exact():
    assert IPV6_PREFIX == 64
    assert _key_for("2001:db8:1:2::1") == _key_for("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert _key_for("2001:db8:1:3::1") != _key_for("2001:db8:1:2::1"), "相邻 /64 是不同身份"
    assert _key_for("203.0.113.9") == "203.0.113.9"
    assert _key_for("::ffff:203.0.113.9") == "203.0.113.9", "IPv4 映射地址与直接 IPv4 是同一客户端"
    assert _key_for("testclient") == "testclient", "解析不出的对端仍占一个固定键，不是放行"


def test_forwarded_ipv6_client_is_also_aggregated(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128")
    a = _key_for("127.0.0.1", {"X-Forwarded-For": "2001:db8:9::1, 127.0.0.1"})
    b = _key_for("127.0.0.1", {"X-Forwarded-For": "2001:db8:9::2, 127.0.0.1"})
    assert a == b == "2001:db8:9::/64"


async def test_two_addresses_in_one_64_share_the_quota_end_to_end(enabled):
    """ASGI 全链路：同一 /64 内换地址不能刷新配额；不同 /64 各有各的配额。"""

    async def post(peer: str) -> int:
        transport = httpx.ASGITransport(app=app, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return (await c.post("/tools/er-diagram", json=DDL)).status_code

    assert [await post(f"2001:db8:aa::{i}") for i in range(1, 5)] == [200, 200, 200, 429]
    assert await post("2001:db8:ab::1") == 200


# ---------------------------------------------------------------- 端点


async def test_tool_endpoint_returns_429_with_retry_after(client, enabled):
    codes = [(await client.post("/tools/er-diagram", json=DDL)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = await client.post("/tools/er-diagram", json=DDL)
    assert r.status_code == 429
    assert r.headers["retry-after"].isdigit()
    assert "过于频繁" in r.json()["detail"]


async def test_llm_endpoint_has_its_own_tighter_quota(client, enabled):
    """LLM 单独一档更严的配额（每次调用都花钱），且不与 DDL 端点互相占用。"""
    codes = [(await client.post("/tools/mermaid", json={"text": "画个类图"})).status_code for _ in range(4)]
    # 502 是因为没配 LLM_API_KEY —— 但配额照样被扣掉了，这正是我们要的
    assert codes == [502, 502, 429, 429]
    assert (await client.post("/tools/er-diagram", json=DDL)).status_code == 200


async def test_auth_endpoints_are_rate_limited(client, enabled):
    """登录/注册不限流就能被在线爆破。"""
    payload = {"username": "someone", "password": "secret123"}
    codes = [(await client.post("/auth/register", json=payload)).status_code for _ in range(7)]
    assert codes[:5] != [429] * 5
    assert codes.count(429) >= 2, f"注册应被限流，实际：{codes}"


async def test_disabled_means_no_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOOLS", 1)
    codes = [(await client.post("/tools/er-diagram", json=DDL)).status_code for _ in range(5)]
    assert codes == [200] * 5


async def test_forwarded_for_is_ignored_by_default(client, enabled):
    """默认不信任 XFF：一直换 XFF 不能刷新配额，否则限流形同虚设。"""
    for i in range(3):
        r = await client.post("/tools/er-diagram", json=DDL, headers={"X-Forwarded-For": f"1.2.3.{i}"})
        assert r.status_code == 200
    r = await client.post("/tools/er-diagram", json=DDL, headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 429, "换个伪造的 XFF 就绕过限流，等于没限"


async def test_forwarded_for_honoured_only_when_trusted(client, enabled, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    for _ in range(3):
        r = await client.post("/tools/er-diagram", json=DDL, headers={"X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 200
    assert (
        await client.post("/tools/er-diagram", json=DDL, headers={"X-Forwarded-For": "1.2.3.4"})
    ).status_code == 429
    assert (
        await client.post("/tools/er-diagram", json=DDL, headers={"X-Forwarded-For": "9.9.9.9"})
    ).status_code == 200, "不同真实客户端应当各有各的配额"


# ---------------------------------------------------------------- 流程图写入与注册每日上限（复核 N-02，TD-275）

DIAGRAM = {"name": "验收图", "content": "<mxfile><diagram/></mxfile>"}


async def _login(client, username: str = "rate_buyer") -> dict:
    await client.post("/auth/register", json={"username": username, "password": "secret123"})
    r = await client.post("/auth/login", data={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_diagram_writes_are_rate_limited(client, enabled, monkeypatch):
    """N-02：`POST/PUT /diagrams` 此前**没有任何限流依赖**。

    一次性 SQLite 实测：单 IP 15 秒写入 490 张图、约 196 MB（单次请求体上限 2 MiB
    只限单次）。这里把写入配额调到 2，验证 POST 与 PUT 共用同一个写入桶、
    第三次写入 429，而读取（列表/打开）完全不受影响。
    """
    monkeypatch.setattr(settings, "RATE_LIMIT_DIAGRAM_WRITES", 2)
    h = await _login(client)

    first = await client.post("/diagrams", json=DIAGRAM, headers=h)          # 第 1 次写入
    assert first.status_code == 201, first.text
    diagram_id, etag = first.json()["id"], first.headers["etag"]

    put = await client.put(f"/diagrams/{diagram_id}", json=DIAGRAM,
                           headers={**h, "If-Match": etag})                   # 第 2 次写入
    assert put.status_code == 200, put.text

    blocked = await client.post("/diagrams", json=DIAGRAM, headers=h)         # 第 3 次写入
    assert blocked.status_code == 429, (
        f"流程图写入没有被限流（{blocked.status_code}）—— 旧版单 IP 15 秒能写约 196 MB"
    )
    assert blocked.headers["retry-after"].isdigit()
    assert "频繁" in blocked.json()["detail"]
    # PUT 与 POST 共用一个桶：刚被 429 的不只是 POST 这一条路径
    assert (await client.put(f"/diagrams/{diagram_id}", json=DIAGRAM,
                             headers={**h, "If-Match": etag})).status_code == 429
    # 读取不吃写入桶
    assert (await client.get("/diagrams", headers=h)).status_code == 200
    assert (await client.get(f"/diagrams/{diagram_id}", headers=h)).status_code == 200


async def test_register_has_a_daily_per_ip_cap(client, enabled, monkeypatch):
    """N-02/O-16：分钟级限流挡不住"批量开号"——账号是 20 MB 字节配额的单位。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER_DAILY", 2)
    codes = [(await client.post("/auth/register",
                                json={"username": f"daily{i}", "password": "secret123"})).status_code
             for i in range(4)]
    assert codes == [201, 201, 429, 429], f"每日注册上限没有生效：{codes}"

    r = await client.post("/auth/register", json={"username": "daily_x", "password": "secret123"})
    assert r.status_code == 429
    assert "注册" in r.json()["detail"] and "上限" in r.json()["detail"], r.json()
    assert int(r.headers["retry-after"]) > settings.RATE_LIMIT_WINDOW, (
        "每日窗口的重试提示不该只说 60 秒（那会把用户引向分钟级配额）"
    )


async def test_daily_register_bucket_does_not_consume_the_short_window_table(client, enabled, monkeypatch):
    """每日桶表必须独立：否则一条 24 小时的桶会长期占住主桶表的队头，
    F-09 的容量保护（过期桶回收）会退化，工具/登录也跟着 429。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER_DAILY", 1)
    assert (await client.post("/auth/register",
                              json={"username": "solo1", "password": "secret123"})).status_code == 201
    assert (await client.post("/auth/register",
                              json={"username": "solo2", "password": "secret123"})).status_code == 429
    # 工具接口用主桶表，不因为注册的每日桶被占而受影响
    assert (await client.post("/tools/er-diagram", json=DDL)).status_code == 200


async def test_daily_register_cap_is_per_ip(client, enabled, monkeypatch):
    """每日上限按客户端身份：同一 IP 用尽后，另一个 IP 仍能注册。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER_DAILY", 1)

    async def post(peer: str, username: str) -> int:
        # `client` fixture 已经把测试库接到 app.dependency_overrides 上，这里只是换一个
        # socket 对端地址 —— 限流身份来自对端，数据库仍然是同一个测试库。
        transport = httpx.ASGITransport(app=app, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return (await c.post("/auth/register",
                                 json={"username": username, "password": "secret123"})).status_code

    assert await post("203.0.113.7", "ip_a1") == 201
    assert await post("203.0.113.7", "ip_a2") == 429
    assert await post("203.0.113.8", "ip_b1") == 201


async def test_diagram_write_quota_is_per_ip(client, enabled, monkeypatch):
    """写入配额也按客户端身份（并且沿用 /64 归并）：换 IP 不能刷新，同一 /64 共享。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_DIAGRAM_WRITES", 1)

    async def write(peer: str, username: str) -> int:
        transport = httpx.ASGITransport(app=app, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            headers = await _login(c, username)
            return (await c.post("/diagrams", json=DIAGRAM, headers=headers)).status_code

    assert await write("198.51.100.5", "w_a") == 201
    assert await write("198.51.100.5", "w_b") == 429          # 同一 IP：写入桶不放行
    assert await write("198.51.100.6", "w_c") == 201          # 另一个 IP 有自己的配额
    assert await write("2001:db8:77::1", "w_d") == 201
    assert await write("2001:db8:77::9", "w_e") == 429        # 同一 /64 归并成一个身份
