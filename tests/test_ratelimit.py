"""TD-15 测试：接口限流。

限流在 conftest 里**默认关闭**（几十个用例共用同一个客户端 IP，开着会互相挤爆配额），
这里显式打开来测。时钟用注入的假时钟，不必真的等窗口过期。

除了"限得住"，还要测两件容易做错的事：
- **滑动窗口**不是"每 60 秒清零"，而是逐条过期
- **默认不信任 `X-Forwarded-For`**：那是客户端能随便填的头，信了等于没有配额
"""
import pytest

from app.config import settings
from app.ratelimit import Limiter, limiter

DDL = {"ddl": "CREATE TABLE t (id INT);"}


@pytest.fixture(autouse=True)
def _clean():
    limiter.reset()
    yield
    limiter.reset()


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
