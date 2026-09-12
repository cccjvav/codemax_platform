"""TD-138：管理员抓取入库端点。

这个端点会让服务器去访问调用方给的 URL 并调用一次 LLM，所以测试要同时覆盖
「谁能调」和「调了之后各种失败怎么映射」。

**边界划在哪**：只伪造两个外部依赖 ——
1. **网络**：把 `crawler` 模块里创建的 `httpx.AsyncClient` 换成挂 `MockTransport`
   的子类。注意这**不是**把 `parse_article` 打桩掉：SSRF 校验、robots 判定、
   体积上限、BeautifulSoup 提取全都真跑，只是不出网。
2. **LLM**：用 `app.dependency_overrides[get_llm]` 换假客户端（与 test_mermaid 同套路）。

于是 `save_article` 是真的写进测试库的，断言查的是库里的行，不是接口回显。
"""
import json

import httpx
import pytest
from sqlalchemy import func, select

from app.models import Article, User
from app.tools import crawler, politeness
from app.tools.llm import LLMError, get_llm
from main import app
from tests.conftest import TestSession

FAKE_IP = "93.184.216.34"  # 公网字面量 IP：is_global=True，且 DNS 解析不需要联网
BASE = f"http://{FAKE_IP}"
URL = f"{BASE}/article"

SELECTORS = {
    "title": "h1.title",
    "author": ".meta .author",
    "published_at": ".meta time",
    "content": "div.content",
}

PAGE_HTML = """
<html><body>
  <article class="post">
    <h1 class="title">毕业设计的开题报告怎么写</h1>
    <div class="meta"><span class="author">张三</span><time>2026-03-01</time></div>
    <div class="content"><p>第一段正文。</p><p>第二段正文。</p></div>
  </article>
</body></html>
"""

ROBOTS_DENY = "User-agent: *\nDisallow: /\n"

# 见 net fixture 里的说明：基类要固定，不能跟着 monkeypatch 漂。
_REAL_ASYNC_CLIENT = httpx.AsyncClient


class FakeLLM:
    """假 LLM：返回预设选择器 JSON，或抛预设异常。"""

    def __init__(self, reply: str | None = None, exc: Exception | None = None):
        self.reply = reply if reply is not None else json.dumps(SELECTORS, ensure_ascii=False)
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.exc:
            raise self.exc
        return self.reply


@pytest.fixture
def net(monkeypatch):
    """把 crawler 里创建的 AsyncClient 接到 MockTransport 上。

    默认：robots.txt 返回 404（按 TD-133 的约定 404/410 = 允许抓），
    其余路径返回 PAGE_HTML。`robots` 参数可换成别的内容。
    """

    def install(
        robots: tuple[int, str] = (404, ""),
        html: str = PAGE_HTML,
        status: int = 200,
        exc: Exception | None = None,
    ):
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(robots[0], text=robots[1])
            if exc is not None:
                raise exc
            return httpx.Response(status, text=html)

        transport = httpx.MockTransport(handle)

        # 基类必须固定成**真正的** AsyncClient。若写成 `class X(httpx.AsyncClient)`，
        # 第二次 install 时该名字已被上一次 monkeypatch 换成旧 MockedClient，新类就
        # 继承了旧类，旧类的 __init__ 又把 transport 改回第一次的 —— 后续 install
        # 全部静默失效（第一版 robots/404 两条测试就是这么假绿的）。
        class MockedClient(_REAL_ASYNC_CLIENT):
            def __init__(self, **kw):
                kw["transport"] = transport
                super().__init__(**kw)

        monkeypatch.setattr(crawler.httpx, "AsyncClient", MockedClient)

    # politeness 的 robots 判定按 origin 缓存 1 小时（TD-133），是模块级的，
    # 不清的话上一条测试对同一 IP 的「允许抓」会被下一条继承 —— robots 拒绝那条
    # 就是这么假绿的。`reset_cache()` 同时清并发闸，正是为测试准备的。
    politeness.reset_cache()
    install()
    yield install
    politeness.reset_cache()


@pytest.fixture(autouse=True)
def fake_llm():
    """默认装一个能正常返回选择器的假 LLM。

    不装的话走的是 `default_llm`，而沙箱没有 `LLM_API_KEY`，于是每条测试都在
    测「没配密钥」而不是测端点 —— 这正是第一版 8 条红的原因。
    """
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    yield
    app.dependency_overrides.pop(get_llm, None)


def _use_llm(llm: FakeLLM) -> None:
    app.dependency_overrides[get_llm] = lambda: llm


async def _register_and_login(client, username: str, password: str = "pw123456"):
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _set_role(username: str, role: int, *, status: int = 1):
    """直接改库提权 —— 生产上也只能这么提权（迁移脚本刻意不做，见 0005）。"""
    async with TestSession() as s:
        u = await s.scalar(select(User).where(User.username == username))
        u.role = role
        u.status = status
        await s.commit()


async def _admin_client(client, username: str = "boss"):
    await _register_and_login(client, username)
    await _set_role(username, 1)
    return client


# ------------------------------------------------------------------ 鉴权


@pytest.mark.asyncio
async def test_anonymous_cannot_ingest(client, net):
    """没登录连 403 都不该看到，直接 401。"""
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_regular_user_gets_403(client, net):
    """登录了但 role=0 → 403，不是 404：端点存在与否不是秘密。"""
    await _register_and_login(client, "alice")
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 403
    assert "管理员" in r.json()["detail"]


@pytest.mark.asyncio
async def test_disabled_admin_gets_401(client, net):
    """role=1 但 status=0（封号）→ 401。封号必须压过角色。"""
    await _register_and_login(client, "fired")
    await _set_role("fired", 1, status=0)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_demoted_admin_loses_access_immediately(client, net):
    """降权**立刻生效**，不必等 token 过期 —— 因为角色从库里读、不从 JWT 读。"""
    await _admin_client(client, "temp")
    assert (await client.post("/admin/articles/ingest", json={"url": URL})).status_code == 200
    await _set_role("temp", 0)  # 同一个 token，不重新登录
    assert (await client.post("/admin/articles/ingest", json={"url": URL})).status_code == 403


# ------------------------------------------------------------------ 正常路径


@pytest.mark.asyncio
async def test_admin_ingests_article(client, net):
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "毕业设计的开题报告怎么写"
    assert body["author"] == "张三"
    assert body["source_site"] == FAKE_IP
    assert body["ingested_by"] == "boss"

    # 断言查的是**库里的行**，不是接口回显
    async with TestSession() as s:
        row = await s.scalar(select(Article).where(Article.url == URL))
    assert row is not None
    assert row.title == "毕业设计的开题报告怎么写"
    assert row.content == "第一段正文。\n第二段正文。"  # 段落结构保留，见 extract_fields
    assert row.id == body["id"]


@pytest.mark.asyncio
async def test_same_url_twice_updates_instead_of_duplicating(client, net):
    """重复抓同一篇是更新而不是新增（`sys_article.url` 唯一）。"""
    await _admin_client(client)
    for _ in range(2):
        assert (await client.post("/admin/articles/ingest", json={"url": URL})).status_code == 200
    async with TestSession() as s:
        n = await s.scalar(select(func.count()).select_from(Article).where(Article.url == URL))
    assert n == 1


# ------------------------------------------------------------------ 失败映射


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.9/article",  # 私网
        "http://127.0.0.1/article",  # 环回
        "http://169.254.169.254/latest/meta-data/",  # 云厂商元数据
        "file:///etc/passwd",  # 非 http/https
    ],
)
async def test_ssrf_and_scheme_are_rejected_with_400(client, net, url):
    """SSRF 判定真跑（`assert_public_url`），且必须是 400 不是 500。

    这几条是本端点最要紧的断言：服务器会去访问调用方给的地址，
    内网 / 环回 / 元数据地址一律不能放过。
    """
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": url})
    assert r.status_code == 400, r.text
    assert "抓取失败" in r.json()["detail"]


@pytest.mark.asyncio
async def test_robots_disallow_maps_to_400(client, net):
    """robots.txt 说不让抓 → 400，并且**不去抓正文**（TD-133 的顺序：先 robots 再限速）。"""
    net(robots=(200, ROBOTS_DENY))
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_target_404_maps_to_400(client, net):
    net(status=404, html="not found")
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_target_unreachable_maps_to_400_not_500(client, net):
    """目标站连不上/超时 → 400。

    这条守的是一个**很容易漏成 500** 的分支：`httpx.HTTPError` 既不是
    `CrawlError` 也不是 `ExtractError`，路由里不显式接就会冒到顶层变成
    「服务器内部错误」—— 而明明是对方站点挂了，与本站无关。
    """
    net(exc=httpx.ConnectError("connection refused"))
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 400, r.text
    assert "抓取失败" in r.json()["detail"]


@pytest.mark.asyncio
async def test_unmatched_selector_maps_to_422(client, net):
    """抓到了但提不出正文 → 422（页面结构问题，换 URL 或改提示词）。"""
    _use_llm(FakeLLM(json.dumps({**SELECTORS, "title": "h1.does-not-exist"}, ensure_ascii=False)))
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 422, r.text
    assert "内容提取失败" in r.json()["detail"]


@pytest.mark.asyncio
async def test_llm_failure_maps_to_502(client, net):
    """上游大模型挂了 → 502，不是 500：不是本站的错，也不该让调用方重试打本站。"""
    _use_llm(FakeLLM(exc=LLMError("大模型返回 429：限流")))
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": URL})
    assert r.status_code == 502, r.text
    assert "大模型调用失败" in r.json()["detail"]


@pytest.mark.asyncio
async def test_failed_ingest_writes_nothing(client, net):
    """失败路径不能留下半条记录。"""
    _use_llm(FakeLLM(exc=LLMError("boom")))
    await _admin_client(client)
    assert (await client.post("/admin/articles/ingest", json={"url": URL})).status_code == 502
    async with TestSession() as s:
        n = await s.scalar(select(func.count()).select_from(Article))
    assert n == 0


# ------------------------------------------------------------------ 入参与限流


@pytest.mark.asyncio
async def test_url_over_500_chars_rejected(client, net):
    """500 与 `sys_article.url VARCHAR(500)` 对齐，超长在入口就挡掉。"""
    await _admin_client(client)
    r = await client.post("/admin/articles/ingest", json={"url": "http://x.example/" + "a" * 600})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ingest_is_rate_limited(client, net, monkeypatch):
    """挂的是 LLM 档限流：每次调用都花钱，管理员也不能无限刷。

    `conftest.py` 全局把 `RATE_LIMIT_ENABLED` 关了（否则每条测试都在攒配额），
    所以这条要自己打开，与 `tests/test_ratelimit.py` 的 `enabled` fixture 同套路。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM", 2)
    await _admin_client(client)
    codes = [(await client.post("/admin/articles/ingest", json={"url": URL})).status_code for _ in range(4)]
    assert codes[:2] == [200, 200], codes
    assert 429 in codes[2:], codes


@pytest.mark.asyncio
@pytest.mark.parametrize('title', ['x' * 301, 'bad\x00title'])
async def test_extracted_storage_validation_is_http_422(client, net, title):
    """Real parsing and save validation must not escape the endpoint as an unhandled error."""
    await _admin_client(client)
    net(html=PAGE_HTML.replace('毕业设计的开题报告怎么写', title))
    response = await client.post('/admin/articles/ingest', json={'url': URL})
    assert response.status_code == 422, response.text
    async with TestSession() as db:
        assert await db.scalar(select(func.count()).select_from(Article)) == 0
