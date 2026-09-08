"""级联路由（S4-02-5）的测试：规则没把握时，先语义、再 LLM、最后才转人工。

改动前，规则路由置信度 < 0.35 的提问**直接转人工**。改动后这一桶会先做两次
更贵的尝试。所以这里的重点是三件事：

1. **不越权**：规则有把握时绝不能去调 LLM —— 那是白花一次钱、白等一次延迟。
2. **不掩盖失败**：语义和 LLM 都拿不准时，必须还是转人工（与改动前一致），
   而不是硬答一个。
3. **留下训练数据**：每次级联判定都要记一条 (原文, 标签) 样本 ——
   这是将来能训 BERT 的前提，也是这次改动最主要的长期收益。
"""
import json
import logging

import pytest

from app.models import Article
from app.tools.faq import FAQS, reset_semantic_index, warm_semantic_index
from app.tools.intent import (
    LLM_ROUTER_CONFIDENCE,
    LLM_ROUTER_SYSTEM,
    Intent,
    RuleIntentRouter,
    llm_classify,
)
from app.tools.llm import LLMError
from app.tools.support import LOW_CONFIDENCE, answer


class FakeLLM:
    """同时提供 chat 与 embeddings，并记录各自的调用次数。

    级联会**先后**用到这两个方法，所以必须能分别断言谁被调了、调了几次 ——
    「规则有把握时不该调 LLM」这条不变式只能靠计数来守。
    """

    def __init__(self, reply="unknown", vectors=None, fail=False):
        self.reply = reply
        self.vectors = vectors or {}
        self.fail = fail
        self.chat_calls: list[tuple[str, str]] = []
        self.embed_calls: list[list[str]] = []

    async def chat(self, system: str, user: str) -> str:
        self.chat_calls.append((system, user))
        if self.fail:
            raise LLMError("模拟上游 502")
        return self.reply

    async def embeddings(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        if self.fail:
            raise LLMError("模拟上游 502")
        return [self.vectors.get(t, [0.0, 1.0, 0.0]) for t in texts]


# 「贵不贵」在假向量空间里贴近收费那条，离其它话题很远
_VECTORS = {FAQS[0].q: [1.0, 0.0, 0.0], "贵不贵": [0.96, 0.12, 0.0]}


@pytest.fixture(autouse=True)
def _isolate_index():
    reset_semantic_index()
    yield
    reset_semantic_index()


async def _seed_article(db):
    db.add(Article(title="Nginx 反向代理配置", content="location / 里写 proxy_pass 指向后端。", url="http://x/1"))
    await db.commit()


# ------------------------------------------------------------- llm_classify


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("faq", Intent.FAQ),
        ("FAQ", Intent.FAQ),
        ("chitchat", Intent.CHITCHAT),
        ("闲聊", Intent.CHITCHAT),
        ("professional", Intent.PROFESSIONAL),
        ("专业问题", Intent.PROFESSIONAL),
        ("`faq`", Intent.FAQ),  # 模型爱加的代码围栏
        ("意图：faq", Intent.FAQ),  # 带前缀的变体
        ("faq。", Intent.FAQ),  # 带中文句号
    ],
)
async def test_llm_classify_maps_known_labels(reply, expected):
    r = await llm_classify("服务怎么收费", llm=FakeLLM(reply=reply))
    assert r.intent is expected
    assert r.confidence == LLM_ROUTER_CONFIDENCE


@pytest.mark.parametrize("reply", ["unknown", "我不知道", "", "这个问题很好", "faq-ish"])
async def test_llm_classify_treats_unrecognized_reply_as_no_verdict(reply):
    """认不出就交给转人工，绝不猜 —— 猜错的代价是白花一次 RAG 调用。"""
    r = await llm_classify("服务怎么收费", llm=FakeLLM(reply=reply))
    assert r.confidence == 0.0
    assert r.confidence < LOW_CONFIDENCE


async def test_llm_classify_never_raises_on_llm_error():
    """它在兜底链路上，自己再抛就没意义了。"""
    r = await llm_classify("服务怎么收费", llm=FakeLLM(fail=True))
    assert r.confidence == 0.0
    assert "调用失败" in r.reason


async def test_llm_router_prompt_covers_all_four_labels():
    for label in ("faq", "chitchat", "professional", "unknown"):
        assert label in LLM_ROUTER_SYSTEM


# ------------------------------------------------------------------- 级联


async def test_semantic_hit_rescues_a_paraphrase_that_rule_router_missed(db):
    """核心用例：「贵不贵」词袋搜不到、规则判成闲聊 0.30，语义把它捞回来。"""
    rule = RuleIntentRouter().classify("贵不贵")
    assert rule.confidence < LOW_CONFIDENCE, "前提：这条必须落进级联那一桶"

    llm = FakeLLM(vectors=_VECTORS)
    assert await warm_semantic_index(llm) is True

    r = await answer("贵不贵", db, llm=llm)
    assert not r.escalated
    assert r.intent is Intent.FAQ
    assert r.source == "faq-semantic", "要能看出这条答案是语义检索救回来的"
    assert r.answer == FAQS[0].a
    assert r.references == (FAQS[0].q,)
    assert llm.chat_calls == [], "语义已经命中，就不该再问 LLM（省钱）"


async def test_llm_verdict_is_used_when_semantic_finds_nothing(db):
    """语义没捞到 → 问 LLM。这里让它判专业问题，于是要走 RAG。

    查询向量刻意指向第三轴，与所有 FAQ（第二轴）正交，余弦为 0 ——
    否则语义会"命中"，就走不到 LLM 这一步，这条用例也就测不到想测的东西。
    """
    await _seed_article(db)
    llm = FakeLLM(reply="professional", vectors={"嗯嗯那个": [0.0, 0.0, 1.0]})
    assert await warm_semantic_index(llm) is True

    r = await answer("嗯嗯那个", db, llm=llm)
    assert r.intent is Intent.PROFESSIONAL
    assert "LLM 路由判定为 professional" in r.reason, "要能看出是级联改判的，不是规则判的"
    # 确实走进了 RAG 分支：「嗯嗯那个」与文章零词面重合，检索不到内容而正当拒答。
    # 这正是想要的行为 —— LLM 说是专业问题不等于库里有答案，没材料就该转人工，
    # 而不是让模型凭记忆编（见 support.py 顶部的 RAG 原则）。
    assert r.escalated and "sys_article" in r.reason


async def test_rule_router_high_confidence_never_consults_llm(db):
    """规则有把握时不许调 LLM —— 级联的意义就是绝大多数流量走免费快车道。"""
    llm = FakeLLM(reply="professional")
    r = await answer("毕业设计服务怎么收费", db, llm=llm)
    assert r.source == "faq"
    assert llm.chat_calls == [], "规则已经命中 FAQ，不该再花钱问 LLM"
    assert llm.embed_calls == [], "同理也不该去查语义"


async def test_cascade_still_escalates_when_both_attempts_fail(db):
    """语义没预热、LLM 也答非所问 → 必须转人工，与改动前行为一致。"""
    llm = FakeLLM(reply="unknown")
    r = await answer("asdfghjkl 嗯嗯", db, llm=llm)
    assert r.escalated and r.source == "human"
    assert "LLM 路由答非所问" in r.reason


async def test_cascade_escalates_when_llm_is_down(db):
    """上游挂了不能让级联本身抛异常。"""
    llm = FakeLLM(fail=True)
    r = await answer("asdfghjkl 嗯嗯", db, llm=llm)
    assert r.escalated and r.source == "human"
    assert "调用失败" in r.reason


async def test_explicit_human_request_skips_the_cascade(db):
    """用户明确要人工时，级联也不该浪费两次调用。"""
    llm = FakeLLM(reply="professional")
    r = await answer("我要转人工", db, llm=llm)
    assert r.escalated
    assert llm.chat_calls == [] and llm.embed_calls == []


# -------------------------------------------------------------- 标注样本


async def test_cascade_logs_a_labeling_sample(db, caplog):
    """每次级联判定落一条 JSON —— 那就是将来训 BERT 的训练集。

    没有这条日志，这次的 LLM 判定就白花了：钱花了、答案给了，
    却没沉淀下任何可以拿去训模型的数据。
    """
    llm = FakeLLM(reply="professional")
    with caplog.at_level(logging.INFO, logger="codemax.intent.labels"):
        await answer("嗯嗯那个", db, llm=llm)

    rows = [json.loads(r.message) for r in caplog.records if r.name == "codemax.intent.labels"]
    assert len(rows) == 1
    assert rows[0]["q"] == "嗯嗯那个"
    assert rows[0]["rule"] == "chitchat"  # 规则的判定
    assert rows[0]["label"] == "professional"  # 级联最终采信的标签
    assert rows[0]["rule_conf"] < LOW_CONFIDENCE


async def test_labeling_sample_not_logged_when_rule_router_is_confident(db, caplog):
    """只有走了级联才记样本 —— 规则能搞定的不需要花钱也不需要标注。"""
    with caplog.at_level(logging.INFO, logger="codemax.intent.labels"):
        await answer("毕业设计服务怎么收费", db, llm=FakeLLM())
    assert not [r for r in caplog.records if r.name == "codemax.intent.labels"]
