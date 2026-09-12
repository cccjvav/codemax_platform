"""智能客服三层编排（S4-02-3 / S4-02-4）。

    用户提问
      │
      ├─ 第二层：意图路由（app/tools/intent.py）
      │
      ├─ FAQ        → 直接返回 FAQ 答案（不调 LLM，秒回）
      ├─ 通用闲聊    → 轻量 LLM 直接应答
      └─ 专业问题    → RAG：从 sys_article 检索 → 把原文片段塞进 prompt → LLM 作答
      │
      └─ 兜底（S4-02-4）：置信度过低 / 命中转人工关键词 / LLM 不可用 → 转人工

**RAG 为什么必须带原文引用**：让 LLM「凭记忆」回答专业问题会杜撰。把检索到的
文章原文放进 prompt 并要求「只依据下面材料作答」，同时把文章标题回给前端展示，
用户能自己核对 —— 这和 S4-01 让 LLM 只指认选择器、正文由 BeautifulSoup 提取
是同一个原则：**不让模型生产事实，只让它组织已有的事实**。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..models import Article
from .faq import FaqHit, search, semantic_search, semantic_threshold, tokenize
from .faq import _Index as RetrievalIndex  # 复用 BM25+余弦，别再写一遍
from .intent import Intent, IntentResult, default_router, llm_classify
from .llm import LLMClient, LLMError, default_llm

# 级联标注样本的落地日志（S4-02-5）。单独一个 logger 名，方便运维按名字分流到
# 一个文件里 —— 那就是将来训 BERT 的训练集（见 _log_labeling_sample）。
_label_logger = logging.getLogger("codemax.intent.labels")

# 低于这个置信度就不作答，直接转人工（S4-02-4）
LOW_CONFIDENCE = 0.35

# 命中这些词直接转人工，不尝试自动回答
ESCALATE_KEYWORDS: tuple[str, ...] = ("人工", "转人工", "投诉", "举报", "客服在吗")

# RAG 索引缓存：(指纹, 索引, 对应的文章行)。
#
# 为什么要缓存：改前每个请求都要「全表捞出所有文章（含完整正文）→ 逐篇 jieba
# 分词 → 现建 BM25/余弦索引」。实测 200 篇时墙钟 242.6 ms，而语料没变时
# 算出来的索引**一模一样** —— 纯粹的重复劳动。
#
# 按读取快照的 id/title/content 哈希，不遗漏 UPDATE，也不依赖本进程清缓存。
# 代价：每次读取语料正文；大语料应迁移至数据库修订号/检索服务，而非退回 count/max。
_ARTICLE_CACHE: tuple[str, object, list] | None = None


def _build_index(rows: list) -> RetrievalIndex:
    """分词 + 建索引。**整体在线程池里跑**，见 `_retrieve_articles` 的注释。"""
    return RetrievalIndex([tokenize(f"{a.title} {a.content}") for a in rows])


def reset_article_index() -> None:
    """清空 RAG 索引缓存。测试用（进程内全局，用例之间必须隔离）。"""
    global _ARTICLE_CACHE
    _ARTICLE_CACHE = None


RAG_TOP_K = 3  # 塞进 prompt 的原文片段数
RAG_SNIPPET_CHARS = 600  # 每篇截多长，控制 prompt 体积与费用

CHITCHAT_SYSTEM = (
    "你是毕业设计服务平台的在线客服，语气友好简短。"
    "只回答与平台使用、服务流程相关的闲聊；涉及具体技术问题请引导用户描述清楚。"
    "不要编造平台没有的服务。"
)

RAG_SYSTEM = (
    "你是毕业设计服务平台的技术顾问。"
    "**只依据下面提供的材料作答**，材料里没有的内容要明确说「资料中没有提到」，"
    "不要凭记忆补充。回答用中文，简洁分点。"
)


@dataclass(frozen=True)
class SupportReply:
    answer: str
    intent: Intent
    confidence: float
    source: str  # faq / llm / rag / human
    escalated: bool = False  # 是否转人工
    reason: str = ""  # 判定/兜底依据
    references: tuple[str, ...] = field(default=())  # RAG 引用的文章标题


def _log_labeling_sample(question: str, rule: IntentResult, final: IntentResult) -> None:
    """把一次「规则没把握 → LLM 定夺」的判定记成一行 JSON。

    **这就是将来训 BERT 的训练数据来源。** 现在没有标注数据所以训不了模型；
    而级联跑一段时间后，这个日志里就是 (原文, 标签) 对，而且是**真实用户分布**的 ——
    比拿 BQ / LCQMC 那种「句子对相似度」语料硬套意图分类靠谱得多
    （那些语料的任务定义就不是三分类）。

    用日志而不是建表：写入零成本、不影响主链路，导出也只是 `grep` + `jq`。
    真要上规模了再改成表，接口不用动。
    """
    _label_logger.info(
        json.dumps(
            {"q": question, "rule": rule.intent.value, "label": final.intent.value,
             "rule_conf": round(rule.confidence, 3), "label_conf": round(final.confidence, 3)},
            ensure_ascii=False,
        )
    )


async def _second_opinion(
    text: str, rule: IntentResult, llm: LLMClient
) -> tuple[IntentResult, FaqHit | None]:
    """规则路由没把握时的级联第二级。返回 (新的判定, 语义命中的 FAQ 或 None)。

    顺序是**先便宜后贵**：
    1. 语义检索 —— 一次 embeddings 调用，而且命中就能直接作答，连 RAG 都省了。
       它专治词袋的死穴：「贵不贵」和「怎么收费」字面不重合，词袋永远匹配不上。
    2. LLM 分类 —— 语义也没命中时才问，纯分类不作答。
    两步都失败就返回低置信度，让上层转人工（和改动前的行为一致）。
    """
    hits = await semantic_search(text, k=1, client=llm)
    # 阈值每次现读：它是配置项，换 embedding 模型就要重新标定（TD-206）
    threshold = semantic_threshold()
    if hits and hits[0].confidence >= threshold:
        h = hits[0]
        return (
            IntentResult(
                Intent.FAQ,
                h.confidence,
                f"语义检索命中「{h.faq.q}」，余弦 {h.confidence:.2f} ≥ {threshold}"
                f"（词袋只给到 {rule.confidence:.2f}）",
            ),
            h,
        )

    verdict = await llm_classify(text, llm)
    reason = f"规则无把握（{rule.reason}）→ {verdict.reason}"
    return IntentResult(verdict.intent, verdict.confidence, reason), None


def _escalate(question: str, reason: str, intent: Intent, confidence: float) -> SupportReply:
    return SupportReply(
        answer="这个问题需要管理员协助。请登录站内客服页发送留言；提交成功后管理员可查看并回复。",
        intent=intent,
        confidence=confidence,
        source="human",
        escalated=True,
        reason=reason,
    )


async def _retrieve_articles(
    db: AsyncSession, question: str
) -> list[tuple[str, str]] | None:
    """从 sys_article 里检索最相关的几篇，返回 [(标题, 正文片段)]。

    返回 `None` 表示**知识库不可用**（表不存在 / 连不上），与「查得到但没有相关
    内容」的空列表区分开 —— 两者的兜底理由不同，排错时要能分辨。

    为什么必须捕获数据库异常：`sys_article` 是 S4-01 才加的表，用旧版
    `full_init.sql` 建的库、或没跑过迁移的库上它压根不存在，直接查会抛
    `UndefinedTableError` 变成 500。而 S4-02-4 的要求是绝不把异常抛给用户。
    注意两个测试套件都发现不了这个问题：fixture 里 `create_all` 总会把表建出来，
    只有真起服务连真库才会暴露。

    索引按实际语料 SHA-256 指纹缓存复用，且分词/建索引整体在线程池里跑
    —— 见 `_ARTICLE_CACHE` 与 `_build_index` 的注释（TD-214）。
    ⚠️ 旧版这里写的是「每次都现建索引……记在 TD-151」，两处都不对：
    行为已改，而 TD-151 讲的是 FAQ 阈值标定，与索引重建无关。
    """
    global _ARTICLE_CACHE

    try:
        rows = list((await db.scalars(select(Article).order_by(Article.id))).all())
    except SQLAlchemyError:
        return None
    if not rows:
        return []
    fp = hashlib.sha256(json.dumps(
        [(a.id, a.title, a.content) for a in rows], ensure_ascii=False
    ).encode()).hexdigest()
    if _ARTICLE_CACHE is not None and _ARTICLE_CACHE[0] == fp:
        index, rows = _ARTICLE_CACHE[1], _ARTICLE_CACHE[2]
    else:
        # ⚠️ 分词与建索引必须**整体**丢到线程池：jieba 是同步 CPU 活，
        # 200 篇实测要吃 200 ms 量级。留在事件循环里的话，那 0.2 秒内
        # **全站所有请求**都排不上队 —— 而 /support/ask 是不鉴权的，
        # 匿名用户反复提问就能让整站周期性卡顿（实测漂移 232.3 ms）。
        # 与 A-1（bcrypt 堵事件循环）、TD-159/183/186 是同一条原则。
        #
        # 注意必须包成一个函数再丢进去：写成
        # `run_in_threadpool(RetrievalIndex, [tokenize(...) for a in rows])`
        # 是**没用的** —— 那个列表推导式在传参时就已在事件循环里算完了，
        # 只有便宜的建索引进了线程。第一版就是这么写错的，漂移纹丝不动。
        index = await run_in_threadpool(_build_index, rows)
        _ARTICLE_CACHE = (fp, index, rows)

    # 单次问题的分词很轻（实测约 0.02 ms），不值得为它再起一次线程。
    q = tokenize(question)
    if not q:
        return []
    # 与 FAQ 一样两路融合；这里直接复用索引的两个分量
    bm = index.bm25(q)
    cos = index.cosine(q)
    peak_b = max(bm) if bm else 0.0
    peak_c = max(cos) if cos else 0.0
    fused = [
        0.6 * (b / peak_b if peak_b else 0.0) + 0.4 * (c / peak_c if peak_c else 0.0)
        for b, c in zip(bm, cos, strict=True)
    ]
    ranked = sorted(range(len(rows)), key=lambda i: fused[i], reverse=True)
    return [
        (rows[i].title, rows[i].content[:RAG_SNIPPET_CHARS])
        for i in ranked[:RAG_TOP_K]
        if fused[i] > 0
    ]


async def answer(
    question: str,
    db: AsyncSession,
    llm: LLMClient = default_llm,
    router=default_router,
) -> SupportReply:
    """三层客服的总入口。任何一层出问题都退到「转人工」，不抛异常给用户。"""
    text = (question or "").strip()
    if not text:
        return _escalate(question, "空提问", Intent.CHITCHAT, 0.0)

    # ---- 兜底 1：用户明确要人工 ----
    hit = [w for w in ESCALATE_KEYWORDS if w in text]
    if hit:
        return _escalate(question, f"用户明确要求人工（命中 {hit}）", Intent.CHITCHAT, 1.0)

    # ---- 第二层：意图路由（同步、零成本、确定性）----
    rule_result: IntentResult = router.classify(text)
    result = rule_result
    semantic_hit: FaqHit | None = None

    # ---- 第二层半：级联（S4-02-5）----
    # 改动前这一桶直接转人工；现在先做两次更贵的尝试（语义检索 → LLM 分类），
    # 都不行才转人工。级联判定同时记一条标注样本，那是将来训 BERT 的训练集。
    if result.confidence < LOW_CONFIDENCE:
        result, semantic_hit = await _second_opinion(text, rule_result, llm)
        _log_labeling_sample(question, rule_result, result)

    # ---- 兜底 2：级联之后仍然没把握 ----
    if result.confidence < LOW_CONFIDENCE:
        return _escalate(
            question,
            f"置信度 {result.confidence:.2f} < {LOW_CONFIDENCE}（{result.reason}）",
            result.intent,
            result.confidence,
        )

    # ---- 第三层：分支处理 ----
    if result.intent is Intent.FAQ:
        # 级联里语义命中的那条直接就是答案，不必再查一遍；
        # 否则走词袋（`search` 已是模块级导入 —— 本模块早就从 .faq 引了
        # `_Index` 和 `tokenize`，不存在循环，原先那句局部导入的理由已不成立）。
        hit = semantic_hit if semantic_hit is not None else next(iter(search(text, k=1)), None)
        if hit is not None:
            return SupportReply(
                answer=hit.faq.a,
                intent=Intent.FAQ,
                confidence=result.confidence,
                source="faq-semantic" if hit.semantic else "faq",
                reason=result.reason,
                references=(hit.faq.q,),
            )
        return _escalate(question, "路由判为 FAQ 但检索已无命中", Intent.FAQ, result.confidence)

    if result.intent is Intent.CHITCHAT:
        try:
            reply = await llm.chat(CHITCHAT_SYSTEM, text)
        except LLMError as e:
            # LLM 挂了不该让用户看到 502，退到人工
            return _escalate(question, f"闲聊应答失败：{e}", Intent.CHITCHAT, result.confidence)
        return SupportReply(
            answer=reply,
            intent=Intent.CHITCHAT,
            confidence=result.confidence,
            source="llm",
            reason=result.reason,
        )

    # 专业问题 → RAG
    refs = await _retrieve_articles(db, text)
    if refs is None:
        return _escalate(
            question,
            f"知识库不可用（sys_article 不存在或数据库异常），RAG 无法作答"
            f"（路由依据：{result.reason}）",
            Intent.PROFESSIONAL, result.confidence,
        )
    if not refs:
        return _escalate(
            question,
            f"知识库（sys_article）无相关内容，RAG 无法作答（路由依据：{result.reason}）",
            Intent.PROFESSIONAL, result.confidence,
        )
    context = "\n\n".join(f"【{title}】\n{body}" for title, body in refs)
    try:
        reply = await llm.chat(RAG_SYSTEM, f"材料：\n{context}\n\n用户问题：{text}")
    except LLMError as e:
        return _escalate(question, f"RAG 生成失败：{e}", Intent.PROFESSIONAL, result.confidence)
    return SupportReply(
        answer=reply,
        intent=Intent.PROFESSIONAL,
        confidence=result.confidence,
        source="rag",
        reason=result.reason,
        references=tuple(title for title, _ in refs),
    )
