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
