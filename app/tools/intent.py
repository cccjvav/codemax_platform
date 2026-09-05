"""意图路由（S4-02-2）：把用户输入分成 高频FAQ / 通用闲聊 / 专业问题 三类。

## 关于 ROADMAP 里写的「训练或微调一个 BERT 多分类模型」

**这部分没有做，也不该假装做了。** 训练 BERT 需要：标注数据（三类各上千条，
本项目一条都没有）、GPU 或至少数小时 CPU、以及几百 MB 的预训练权重下载。
沙箱里三样都不具备，写出来的「模型」无法验证，只会是个不能跑的摆设。

所以这里给出的是**可替换的路由接口 + 一个确定性实现**：
- `IntentRouter` 是接口，业务代码只依赖它；
- `RuleIntentRouter` 是当前实现（FAQ 检索分数 + 技术词表 + 兜底），
  完全确定、可测试、可解释（每个判定都带 `reason`）；
- 将来训练出 BERT 后，写一个 `BertIntentRouter` 实现同一个接口即可，
  `app/tools/support.py` 与路由层一行都不用改。

这样做的代价记在 TD-150：路由准确率没有模型背书，只能靠规则与阈值。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .faq import search
from .llm import LLMClient, LLMError, default_llm


class Intent(str, Enum):
    FAQ = "faq"  # 高频问题，直接用 FAQ 答案秒回
    CHITCHAT = "chitchat"  # 通用闲聊，交给轻量 LLM
    PROFESSIONAL = "professional"  # 专业问题，走 RAG


# FAQ 的**绝对**置信度达到这个值才算命中。低于它说明检索没把握，
# 宁可不秒回、也不要把不相干的 FAQ 甩给用户。
#
# 0.40 是在现有 12 条语料上实测标定的，不是拍脑袋：
#   「毕业设计服务怎么收费」0.537 / 「可以开发票吗」0.522 / 「服务多少钱」0.502  → 命中
#   「怎么退款」0.322（无对应 FAQ）/ 「python 部署 nginx 报错」0.306 → 不命中
# 语料变大后必须重新标定，否则这个数没有意义（TD-151）。
FAQ_CONFIDENCE_THRESHOLD = 0.40

# 技术/专业词表。命中即倾向判为专业问题。
PROFESSIONAL_KEYWORDS: tuple[str, ...] = (
    "部署", "nginx", "报错", "异常", "堆栈", "接口", "并发", "索引", "事务",
    "死锁", "外键", "缓存", "性能", "优化", "迁移", "docker", "linux",
    "python", "java", "sql", "数据库设计", "算法", "架构", "api",
    "空指针", "超时", "连接池", "日志", "调试", "环境", "依赖",
)

# 闲聊特征词。只在没命中 FAQ、也没有技术词时才用得上。
CHITCHAT_KEYWORDS: tuple[str, ...] = (
    "你好", "您好", "在吗", "谢谢", "感谢", "哈哈", "再见", "辛苦了", "早",
)


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float  # [0,1]
    reason: str  # 判定依据，答辩与排错都要用


class IntentRouter(Protocol):
    """意图路由接口。换成 BERT 实现时，业务代码不用改。"""

    def classify(self, text: str) -> IntentResult: ...


class RuleIntentRouter:
    """确定性实现：FAQ 检索分数 → 技术词表 → 兜底闲聊。

    判定顺序是有讲究的：**先看 FAQ 检索有没有把握**。因为 FAQ 命中是三种结果里
    唯一「答案已经在库里、可以秒回」的，优先判它能省掉一次 LLM 调用（花钱且慢）。
    """

    def classify(self, text: str) -> IntentResult:
        query = text.strip()
        if not query:
            return IntentResult(Intent.CHITCHAT, 0.0, "空输入，按闲聊处理")

        hits = search(query, k=1)
        # 必须比 confidence 而不是 score：score 是「本结果集内」归一化的，
        # 取 k=1 时那条永远是 max、恒等于 1.0，任何查询都会被判成命中。
        if hits and hits[0].confidence >= FAQ_CONFIDENCE_THRESHOLD:
            h = hits[0]
            return IntentResult(
                Intent.FAQ,
                h.confidence,
                f"FAQ 检索命中「{h.faq.q}」，绝对置信度 {h.confidence:.2f} ≥ {FAQ_CONFIDENCE_THRESHOLD}"
                f"（bm25={h.bm25:.2f} cos={h.cosine:.2f}）",
            )

        lowered = query.lower()
        matched = [w for w in PROFESSIONAL_KEYWORDS if w in lowered]
        if matched:
            # 命中越多越有把握，但封顶 0.9 —— 词表判定不该给出「绝对确定」
            confidence = min(0.5 + 0.15 * len(matched), 0.9)
            return IntentResult(
                Intent.PROFESSIONAL,
                confidence,
                f"命中技术词 {matched[:5]}{' 等' if len(matched) > 5 else ''}",
            )

        chit = [w for w in CHITCHAT_KEYWORDS if w in query]
        if chit:
            return IntentResult(Intent.CHITCHAT, 0.7, f"命中寒暄词 {chit[:3]}")

        # 三种都不像：按闲聊兜底，但给低置信度，让上层的兜底机制有机会转人工
        return IntentResult(Intent.CHITCHAT, 0.3, "未命中 FAQ、无技术词、无寒暄词，按闲聊兜底")


default_router: IntentRouter = RuleIntentRouter()


# ------------------------------------------------- LLM 二意见（S4-02-5）
#
# 为什么 `classify()` 保持同步、这个却是异步的：
#   `classify()` 走纯本地判据（词袋 + 词表），将来换成 `BertIntentRouter` 同样是
#   同步的 —— 权重在本地推理，不发网络。而 LLM 二意见必须发 HTTP 请求，天生异步。
#   硬把两者塞进同一个 Protocol，会逼着所有同步调用点变成 async，还会丢掉
#   「确定性、可离线测试、每条判定都有 reason」这三个优点。
#   所以刻意分开：快车道同步、二意见异步，级联由 support.answer() 编排。
#
# 定位是**级联的第二级**，不是替换：只有规则路由置信度 < LOW_CONFIDENCE
# （也就是原本直接转人工的那一桶）才会走到这里。绝大多数流量根本到不了这一步，
# 所以既不加延迟也不花钱。

LLM_ROUTER_SYSTEM = (
    "你是意图分类器，把用户的客服提问分成三类之一，**只输出一个词**，不要解释、不要标点：\n"
    "- faq：问平台自身的规则与流程（收费、发票、交付周期、下载、账号、退款等）\n"
    "- chitchat：寒暄、感谢、与平台无关的闲聊\n"
    "- professional：具体技术问题（代码、报错、部署、数据库、算法等）\n"
    "- unknown：信息太少无法判断，或三类都不像\n"
    "拿不准时输出 unknown，不要猜。"
)

# 答对时给的置信度。刻意**不给 1.0**：大模型不会输出校准过的概率，
# 它「自信」和它「正确」没有必然关系。0.8 足以压过 LOW_CONFIDENCE(0.35)
# 让它的答案生效，但不至于在日志里冒充确定结论。
LLM_ROUTER_CONFIDENCE = 0.8

# 模型爱写的各种变体都收进来，但**不做模糊匹配** —— 认不出就走 unknown，
# 宁可转人工，也不要把「不太像技术问题」硬猜成专业问题去调 RAG 烧钱。
_LLM_LABEL_MAP: dict[str, Intent] = {
    "faq": Intent.FAQ,
    "chitchat": Intent.CHITCHAT,
    "闲聊": Intent.CHITCHAT,
    "professional": Intent.PROFESSIONAL,
    "专业": Intent.PROFESSIONAL,
    "专业问题": Intent.PROFESSIONAL,
}


async def llm_classify(text: str, llm: LLMClient = default_llm) -> IntentResult:
    """问一次大模型。失败/超时/答非所问一律返回低置信度，由上层转人工。

    这个函数**不抛异常**：它在兜底链路上，兜底自己再抛就没意义了。
    """
    try:
        reply = await llm.chat(LLM_ROUTER_SYSTEM, text)
    except LLMError as exc:
        return IntentResult(Intent.CHITCHAT, 0.0, f"LLM 路由调用失败：{exc}")

    label = reply.strip().lower().strip("`。．.！!，, ").strip()
    intent = _LLM_LABEL_MAP.get(label)
    if intent is None and label:
        # 模型偶尔会回「意图：faq」这种带前缀的形式。按空白与常见分隔符切开取最后一段
        # 再试一次；**不做子串模糊匹配** —— 「这不是faq，是professional」这种
        # 话里两个标签都出现，模糊匹配只会猜错，不如老实认不出、走转人工。
        for sep in ("：", ":", "，", ",", " "):
            label = label.replace(sep, " ")
        intent = _LLM_LABEL_MAP.get(label.split()[-1]) if label.split() else None
    if intent is None:
        return IntentResult(
            Intent.CHITCHAT, 0.0, f"LLM 路由答非所问（原文 {reply[:40]!r}），按未知处理"
        )
    return IntentResult(
        intent,
        LLM_ROUTER_CONFIDENCE,
        f"LLM 路由判定为 {intent.value}",
    )
