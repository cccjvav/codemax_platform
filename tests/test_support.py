"""S4-02-2/3/4：意图路由、三层编排、兜底转人工。"""
import pytest
from sqlalchemy import select

from app.models import Article
from app.tools import faq as faq_mod
from app.tools.intent import FAQ_CONFIDENCE_THRESHOLD, Intent, RuleIntentRouter
from app.tools.llm import LLMError
from app.tools.support import ESCALATE_KEYWORDS, LOW_CONFIDENCE, answer

from .conftest import Base, TestSession, engine


class FakeLLM:
    """假客户端：绝不真打网络，同时记录被调用了几次、拿到什么 system prompt。"""

    def __init__(self, reply="（这是 LLM 的回答）", fail=False):
        self.reply = reply
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, text: str) -> str:
        self.calls.append((system, text))
        if self.fail:
            raise LLMError("模拟上游 502")
        return self.reply


@pytest.fixture
def router() -> RuleIntentRouter:
    return RuleIntentRouter()


@pytest.fixture
async def db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestSession() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _seed_articles(db, rows=(("Nginx 反向代理配置", "用 proxy_pass 把 80 转发到 8000。"),)):
    db.add_all([Article(title=t, content=c, url=f"http://x/{i}") for i, (t, c) in enumerate(rows)])
    await db.commit()


# ---------------- S4-02-2 意图路由 ----------------

def test_router_verbatim_faq_question_routes_to_faq(router):
    r = router.classify("毕业设计服务怎么收费")
    assert r.intent is Intent.FAQ
    assert r.confidence >= FAQ_CONFIDENCE_THRESHOLD


def test_router_technical_question_routes_to_professional(router):
    r = router.classify("python 部署 nginx 报错怎么排查")
    assert r.intent is Intent.PROFESSIONAL
    assert "nginx" in r.reason


def test_router_greeting_routes_to_chitchat(router):
    r = router.classify("你好")
    assert r.intent is Intent.CHITCHAT
    assert r.confidence >= LOW_CONFIDENCE  # 寒暄是明确的，不该被兜底


def test_router_ambiguous_gets_low_confidence_so_upstream_can_escalate(router):
    """三种都不像时必须给低分 —— 让 support 层有机会转人工，而不是硬答。"""
    r = router.classify("asdfghjkl 嗯嗯")
    assert r.confidence < LOW_CONFIDENCE


def test_router_empty_input_does_not_crash(router):
    assert router.classify("   ").intent is Intent.CHITCHAT


def test_router_is_deterministic(router):
    """规则路由没有随机性：同样的输入必须给同样的判定，否则没法写测试也没法复现 bug。"""
    a, b = router.classify("服务多少钱"), router.classify("服务多少钱")
    assert (a.intent, a.confidence) == (b.intent, b.confidence)


def test_faq_confidence_is_comparable_across_queries():
    """回归：早先用 max-normalized 的 score 当阈值，k=1 时恒为 1.00，
    连「python 部署 nginx 报错」都判成 FAQ 命中。这里钉死 score 与 confidence 的区别。"""
    on_topic = faq_mod.search("毕业设计服务怎么收费", k=1)[0]
    off_topic = faq_mod.search("python 部署 nginx 报错怎么排查", k=1)[0]
    assert on_topic.score == off_topic.score == 1.0  # score 就是没用的，恒为 1
    assert on_topic.confidence > off_topic.confidence  # confidence 才有区分力


def test_cosine_is_a_real_cosine():
    """回归：余弦的分子曾用 tf·idf 点积、分母用裸 tf 模长，量纲不一致，实测 1.050 > 1。"""
    index = faq_mod._Index(faq_mod._corpus_tokens())
    # 「会给源码吗」是关键用例：原版实现在这条上算出 1.011 > 1，是量纲错误的铁证
    for q in ("毕业设计服务怎么收费", "会给源码吗", "开发票", "nginx", "随便打一串没有的词"):
        assert all(0.0 <= c <= 1.0 for c in index.cosine(faq_mod.tokenize(q)))


# ---------------- S4-02-3 三层分支 ----------------

@pytest.mark.asyncio
async def test_faq_branch_answers_without_calling_llm(db):
    llm = FakeLLM()
    r = await answer("毕业设计服务怎么收费", db, llm=llm)
    assert r.source == "faq"
    assert not r.escalated
    assert llm.calls == []  # 秒回的关键：一次 LLM 都不调
    assert r.references == ("毕业设计服务怎么收费",)


@pytest.mark.asyncio
async def test_chitchat_branch_uses_llm(db):
    llm = FakeLLM(reply="你好呀，有什么可以帮你？")
    r = await answer("你好", db, llm=llm)
    assert r.source == "llm"
    assert r.answer == "你好呀，有什么可以帮你？"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_professional_branch_does_rag_and_cites_articles(db):
    await _seed_articles(db)
    llm = FakeLLM(reply="根据资料：用 proxy_pass 转发。")
    r = await answer("python 部署 nginx 报错怎么排查", db, llm=llm)
    assert r.source == "rag"
    assert r.references == ("Nginx 反向代理配置",)  # 必须给出可核对的出处
    assert "Nginx 反向代理配置" in llm.calls[0][1]  # 原文确实进了 prompt
    assert "只依据下面提供的材料作答" in llm.calls[0][0]


@pytest.mark.asyncio
async def test_rag_without_knowledge_base_escalates(db):
    """库里没文章时不能硬编，只能转人工。"""
    r = await answer("python 部署 nginx 报错怎么排查", db, llm=FakeLLM())
    assert r.escalated and r.source == "human"
    assert "sys_article" in r.reason


# ---------------- S4-02-4 兜底 ----------------

@pytest.mark.asyncio
@pytest.mark.parametrize("word", ESCALATE_KEYWORDS)
async def test_explicit_human_request_short_circuits(db, word):
    """用户要人工就别再让模型演了。"""
    llm = FakeLLM()
    r = await answer(f"我要{word}", db, llm=llm)
    assert r.escalated and llm.calls == []


@pytest.mark.asyncio
async def test_llm_outage_degrades_to_human_not_500(db):
    """上游挂了是常态，用户不该看到 500/502。"""
    r = await answer("你好", db, llm=FakeLLM(fail=True))
    assert r.escalated and r.source == "human"
    assert "502" in r.reason


@pytest.mark.asyncio
async def test_rag_generation_failure_degrades_to_human(db):
    await _seed_articles(db)
    r = await answer("python 部署 nginx 报错怎么排查", db, llm=FakeLLM(fail=True))
    assert r.escalated and r.source == "human"


@pytest.mark.asyncio
async def test_low_confidence_question_escalates(db):
    r = await answer("asdfghjkl 嗯嗯", db, llm=FakeLLM())
    assert r.escalated
    assert str(LOW_CONFIDENCE) in r.reason


@pytest.mark.asyncio
async def test_empty_question_escalates(db):
    assert (await answer("   ", db, llm=FakeLLM())).escalated


# ---------------- HTTP ----------------

@pytest.mark.asyncio
async def test_ask_endpoint_returns_full_payload(client):
    resp = await client.post("/support/ask", json={"text": "毕业设计服务怎么收费"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "faq"
    assert body["source"] == "faq"
    assert body["escalated"] is False
    assert set(body) == {"answer", "intent", "confidence", "source", "escalated", "reason", "references"}


@pytest.mark.asyncio
async def test_ask_endpoint_rejects_empty_text(client):
    resp = await client.post("/support/ask", json={"text": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ask_endpoint_needs_no_auth(client):
    """售前咨询不要求注册 —— 没有 Authorization 头也要能用。"""
    resp = await client.post("/support/ask", json={"text": "你好"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ask_endpoint_is_rate_limited(client, monkeypatch):
    # 限流默认关闭，且配额读的是已加载的 settings 对象，setenv 不起作用
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM", 2)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    codes = [(await client.post("/support/ask", json={"text": "你好"})).status_code for _ in range(3)]
    assert codes == [200, 200, 429]  # 每次问答都可能花钱，必须挡


@pytest.mark.asyncio
async def test_ask_endpoint_rate_limited_response_has_retry_after(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_LLM", 1)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    await client.post("/support/ask", json={"text": "你好"})
    r = await client.post("/support/ask", json={"text": "你好"})
    assert r.status_code == 429
    assert r.headers["Retry-After"]
    assert r.json()["detail"].startswith("请求过于频繁")  # 实际文案带秒数：「请求过于频繁，请 60 秒后再试」


@pytest.mark.asyncio
async def test_articles_are_readable(db):
    """RAG 的检索依赖 sys_article 能被 ORM 查出来 —— 先确认这条通路本身没断。"""
    await _seed_articles(db)
    assert len((await db.execute(select(Article))).scalars().all()) == 1
