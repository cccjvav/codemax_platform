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

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Article
from .faq import _Index as RetrievalIndex  # 复用 BM25+余弦，别再写一遍
from .faq import tokenize
from .intent import Intent, IntentResult, default_router
from .llm import LLMClient, LLMError, default_llm

# 低于这个置信度就不作答，直接转人工（S4-02-4）
LOW_CONFIDENCE = 0.35

# 命中这些词直接转人工，不尝试自动回答
ESCALATE_KEYWORDS: tuple[str, ...] = ("人工", "转人工", "投诉", "举报", "客服在吗")

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


def _escalate(question: str, reason: str, intent: Intent, confidence: float) -> SupportReply:
    return SupportReply(
        answer="这个问题我暂时答不好，已为你转接人工客服，请稍等。",
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

    每次都现建索引：语料量小时开销可接受，代价与改法记在 TD-151。
    """
    try:
        rows = (await db.execute(select(Article))).scalars().all()
    except SQLAlchemyError:
        return None
    if not rows:
        return []
    docs = [tokenize(f"{a.title} {a.content}") for a in rows]
    index = RetrievalIndex(docs)
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

    # ---- 第二层：意图路由 ----
    result: IntentResult = router.classify(text)

    # ---- 兜底 2：路由自己也没把握 ----
    if result.confidence < LOW_CONFIDENCE:
        return _escalate(
            question,
            f"置信度 {result.confidence:.2f} < {LOW_CONFIDENCE}（{result.reason}）",
            result.intent,
            result.confidence,
        )

    # ---- 第三层：分支处理 ----
    if result.intent is Intent.FAQ:
        from .faq import search  # 局部导入避免模块级循环

        hits = search(text, k=1)
        if hits:
            return SupportReply(
                answer=hits[0].faq.a,
                intent=Intent.FAQ,
                confidence=result.confidence,
                source="faq",
                reason=result.reason,
                references=(hits[0].faq.q,),
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
            question, "知识库不可用（sys_article 不存在或数据库异常），RAG 无法作答",
            Intent.PROFESSIONAL, result.confidence,
        )
    if not refs:
        return _escalate(
            question, "知识库（sys_article）无相关内容，RAG 无法作答",
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
