"""S4-02-2/3/4：意图路由、三层编排、兜底转人工。"""
import pytest
from sqlalchemy import select

from app.models import Article
from app.tools import faq as faq_mod
from app.tools.intent import FAQ_CONFIDENCE_THRESHOLD, Intent, RuleIntentRouter
from app.tools.llm import LLMError
from app.tools.support import ESCALATE_KEYWORDS, LOW_CONFIDENCE, answer


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
    # TD-292：reason 会返回给匿名用户，只写阶段，上游异常原文（这里的「模拟上游 502」）只进日志
    assert "闲聊应答失败" in r.reason
    assert "502" not in r.reason


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
    assert set(body) == {"answer", "intent", "confidence", "source", "escalated", "reason", "references", "human_support_url"}


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


@pytest.mark.asyncio
async def test_missing_article_table_degrades_to_human_not_500(db):
    """回归：`sys_article` 是 S4-01 才加的表。用旧版 full_init.sql 建的库上它不存在，
    查询会抛 UndefinedTableError 变成 500。两个测试套件都发现不了这点，因为 fixture 里
    create_all 总会把表建出来 —— 只有真起服务连真库才暴露。"""
    from sqlalchemy import text

    await db.execute(text("DROP TABLE IF EXISTS sys_article"))
    await db.commit()
    r = await answer("python 部署 nginx 报错怎么排查", db, llm=FakeLLM())
    assert r.escalated and r.source == "human"
    assert "不可用" in r.reason


@pytest.mark.parametrize(("weight", "first"), [(1.0, "T2"), (0.0, "T1")])
def test_rag_ranking_follows_the_faq_fusion_weight(monkeypatch, weight, first):
    """TD-289：客服 RAG 原来手抄了一份融合公式（权重写死 0.6/0.4），改 `faq.BM25_WEIGHT` 只影响 FAQ。
    现在两边共用 `faq.fused_scores`。这组三篇语料在纯 BM25 与纯余弦下的第一名不同（实测 T2 / T1），
    所以权重改了，RAG 的排序必须跟着变；写死权重的旧实现两种设置下排序相同，本用例失败。"""
    from types import SimpleNamespace

    from app.tools import support as support_module

    rows = [
        SimpleNamespace(id=0, title="T0", content="压缩 缓存 超时 监控 续期"),
        SimpleNamespace(id=1, title="T1", content="日志 监控 续期 续期 重试"),
        SimpleNamespace(id=2, title="T2", content="负载 负载 监控 负载 缓存 日志 证书 负载 监控"),
    ]
    index = support_module._build_index(rows)
    monkeypatch.setattr(faq_mod, "BM25_WEIGHT", weight)
    ranked = support_module._rank_articles(index, rows, "证书 续期")
    assert [title for title, _ in ranked][0] == first


class _LeakyLLM:
    """上游错误文本里带着内部信息：地址、状态码与配置状态。"""

    DETAIL = "上游 http://10.0.0.5:8080 返回 503；未配置 LLM_API_KEY"

    async def chat(self, system: str, text: str) -> str:
        raise LLMError(self.DETAIL, category="http", status_code=503)


@pytest.mark.parametrize("question,stage,seed", [
    ("你好", "闲聊应答失败", False),
    ("python 部署 nginx 报错怎么排查", "RAG 生成失败", True),
    ("asdfghjkl 嗯嗯", "LLM 路由调用失败", False),
])
async def test_llm_failure_detail_goes_to_logs_not_to_the_public_reason(
    db, client, caplog, monkeypatch, question, stage, seed
):
    """TD-292：闲聊、RAG、意图路由三处 LLM 失败，reason 只写阶段；异常原文、类别与状态码记入服务日志。"""
    import logging

    from app.routers import support as support_router

    if seed:
        await _seed_articles(db)
    caplog.set_level(logging.WARNING)
    r = await answer(question, db, llm=_LeakyLLM())
    assert r.escalated and stage in r.reason
    assert "10.0.0.5" not in r.reason and "LLM_API_KEY" not in r.reason and "503" not in r.reason
    assert any(_LeakyLLM.DETAIL in rec.getMessage() and "category=http" in rec.getMessage() for rec in caplog.records)
    # 结束 db 会话的事务再走 HTTP：本用例同时用了 db 与 client 两个夹具，PostgreSQL 上 answer() 的查询会让
    # 事务一直持有表锁；client 先拆除时的 drop_all 要排他锁，会一直等这个会话（db 夹具更晚拆除），整轮测试挂死。
    await db.rollback()
    # HTTP 出口同样不带原文：/support/ask 把 reason 原样返回
    real_answer = support_router.answer
    monkeypatch.setattr(support_router, "answer", lambda text, session: real_answer(text, session, llm=_LeakyLLM()))
    body = (await client.post("/support/ask", json={"text": question})).json()
    assert body["escalated"] and "10.0.0.5" not in body["reason"] and "LLM_API_KEY" not in body["reason"]


async def test_busy_llm_keeps_a_retry_hint_in_the_public_reason(db):
    """TD-292：本进程模型闸门已满（category=busy）不含内部细节，对用户有用：reason 保留「繁忙，请稍后再试」。"""

    class BusyLLM:
        async def chat(self, system: str, text: str) -> str:
            raise LLMError("客服繁忙：本站同时处理的模型请求已达 4 个，请 5 秒后再试", category="busy")

    r = await answer("你好", db, llm=BusyLLM())
    assert r.escalated and "闲聊应答失败（模型繁忙，请稍后再试）" in r.reason
    assert "4 个" not in r.reason
