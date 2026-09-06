"""FAQ 检索（S4-02-1）：BM25 + 余弦相似度**融合召回**。

为什么要融合而不是只用一种：
- **BM25** 看的是「查询词在这条 FAQ 里出现得多不多、这个词在全库稀不稀有」，
  对**关键词命中**敏感（用户打「发票」就该命中讲发票那条）。
- **余弦相似度**（TF-IDF 向量）看的是「整体用词分布像不像」，对**换了说法**
  更宽容（「怎么开票」和「能否开发票」词面不同但分布接近）。
两者互补，所以各归一化到 [0,1] 后按权重相加（`BM25_WEIGHT`）。

性能：ROADMAP 要求响应延迟 < 80ms。jieba **首次**分词要加载词典（实测 ~560ms），
所以在模块导入时就 `jieba.initialize()` 预热；热身后单次分词实测 ~0.02ms。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import jieba

from ..config import settings
from .llm import LLMClient, LLMError, default_llm

jieba.initialize()  # 预热词典，别把 ~560ms 算进第一次请求

BM25_WEIGHT = 0.6  # 融合权重：BM25 占 0.6，余弦占 0.4
_K1 = 1.5  # BM25 词频饱和参数
_B = 0.75  # BM25 文档长度归一化强度


@dataclass(frozen=True)
class Faq:
    q: str  # 标准问法
    a: str  # 答案
    keywords: tuple[str, ...] = ()  # 额外召回词（用户可能的其它说法）


# FAQ 语料。放在代码里而不是数据库：条目少、随代码评审、不需要运营后台。
# 要改成数据库表时，把 FAQS 换成从表里读即可，检索逻辑不用动。
FAQS: tuple[Faq, ...] = (
    Faq("毕业设计服务怎么收费", "按题目难度与工作量报价，本科毕设 199 元起，"
        "下单前可先免费咨询评估。价格在下单页明确标注，不会中途加价。",
        ("价格", "多少钱", "报价", "费用", "贵吗")),
    Faq("可以开发票吗", "可以。支持电子普通发票，付款完成后在订单页提交开票信息，"
        "3 个工作日内开出。", ("发票", "开票", "报销", "增值税")),
    Faq("支持哪些数据库", "MySQL、PostgreSQL、SQL Server、Oracle、SQLite 都支持。"
        "在线工具目前可直接解析 MySQL 与 PostgreSQL 的建表语句生成 ER 图。",
        ("数据库", "mysql", "postgresql", "sqlserver", "oracle")),
    Faq("交付周期是多久", "常规题目 7-15 天，加急可缩短到 3 天（需加价）。"
        "具体周期在需求评估后给出，并写进订单备注。", ("工期", "多久", "加急", "时间", "几天")),
    Faq("会给源码吗", "会。交付内容包含完整源码、数据库脚本、部署说明，"
        "以及可用于答辩的论文初稿与答辩 PPT 提纲。", ("源代码", "代码", "源码", "论文")),
    Faq("怎么保证不是抄袭", "所有项目从零开发，交付时提供代码查重报告。"
        "同一题目不会重复卖给两个人。", ("查重", "抄袭", "重复", "原创")),
    Faq("可以先看演示再付款吗", "可以。免费工具（SQL 转 ER 图、Word 数据字典导出、"
        "流程图）无需注册即可直接使用；定制类服务可先看同类作品演示。",
        ("演示", "试用", "免费", "体验", "例子")),
    Faq("付款后多久开始做", "付款成功后 1 个工作日内安排开发者对接，"
        "并给出排期与里程碑。", ("开始", "排期", "对接", "什么时候开始")),
    Faq("中途可以修改需求吗", "可以。小范围调整不额外收费；"
        "若需求变化导致工作量明显增加，会先给出补充报价再动手。",
        ("改需求", "变更", "返工", "修改")),
    Faq("验收不通过怎么办", "按订单里约定的验收标准逐条核对，"
        "未达标的部分免费修改到通过为止。", ("验收", "不合格", "退款", "不满意")),
    Faq("怎么下载已购买的文件", "付款后在「我的订单」里点下载，"
        "链接带签名且**只能用一次**，过期需重新生成。",
        ("下载", "下载不了", "链接", "打不开")),
    Faq("账号忘记密码怎么办", "目前暂不支持自助找回，请联系人工客服核验身份后重置。",
        ("密码", "忘记", "登不上", "找回密码")),
)


def tokenize(text: str) -> list[str]:
    """中文分词。用 jieba 精确模式，过滤空白。

    不做停用词过滤：FAQ 语料只有十几条，停用词带来的噪音很小，
    而滤掉「怎么」「可以」这类词反而会让「怎么收费」和「怎么退款」变得无法区分。
    """
    return [t for t in jieba.cut(text.lower()) if t.strip()]


class _Index:
    """语料的 BM25 与 TF-IDF 统计量，构造一次、查询多次。"""

    def __init__(self, docs: list[list[str]]):
        self.docs = docs
        self.n = len(docs)
        self.doc_len = [len(d) for d in docs]
        self.avgdl = sum(self.doc_len) / self.n if self.n else 0.0
        # 词 -> 每篇文档里的出现次数
        self.tf: list[dict[str, int]] = []
        for d in docs:
            counts: dict[str, int] = {}
            for t in d:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
        # 词 -> 出现过该词的文档数
        self.df: dict[str, int] = {}
        for counts in self.tf:
            for t in counts:
                self.df[t] = self.df.get(t, 0) + 1
        # TF-IDF 向量（tf × idf）与它的模长。
        # 余弦的分子分母必须同量纲：早先分子是 tf·idf 点积、分母却用裸 tf 的模长，
        # 算出来的「余弦」能超过 1（实测 1.050），压根不是余弦，阈值也就无从标定。见 TD-152。
        self.vec: list[dict[str, float]] = [
            {t: c * self._idf(t) for t, c in counts.items()} for counts in self.tf
        ]
        self.norm = [math.sqrt(sum(v * v for v in vec.values())) or 1.0 for vec in self.vec]

    def _idf(self, term: str) -> float:
        """BM25 的 IDF（Robertson 形式，恒为正，避免高频词出现负权重）。"""
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def bm25(self, query: list[str]) -> list[float]:
        out = []
        for i, counts in enumerate(self.tf):
            s = 0.0
            for t in query:
                f = counts.get(t, 0)
                if not f:
                    continue
                denom = f + _K1 * (1 - _B + _B * self.doc_len[i] / (self.avgdl or 1.0))
                s += self._idf(t) * f * (_K1 + 1) / denom
            out.append(s)
        return out

    def cosine(self, query: list[str]) -> list[float]:
        q_counts: dict[str, int] = {}
        for t in query:
            q_counts[t] = q_counts.get(t, 0) + 1
        # 查询向量同样带 idf，与文档向量同量纲；语料里没有的词丢掉
        # （它们对点积无贡献，留在分母里只会把余弦整体压低）。结果严格落在 [0,1]。
        q_vec = {t: qf * self._idf(t) for t, qf in q_counts.items() if self.df.get(t)}
        q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
        out = []
        for i, vec in enumerate(self.vec):
            dot = sum(w * vec[t] for t, w in q_vec.items() if t in vec)
            out.append(dot / (self.norm[i] * q_norm))
        return out


def _corpus_tokens() -> list[list[str]]:
    """每条 FAQ 的可检索文本 = 标准问法 + 额外召回词 + 答案。"""
    return [tokenize(" ".join((f.q, *f.keywords, f.a))) for f in FAQS]


_INDEX = _Index(_corpus_tokens())


@dataclass(frozen=True)
class FaqHit:
    faq: Faq
    score: float  # 融合分数（**同一结果集内**归一化）：只用于排序，不能跨查询比较
    bm25: float  # 归一化后的 BM25 分量
    cosine: float  # 归一化后的余弦分量
    confidence: float  # 绝对置信度 [0,1]：由未归一化的原始分算出，**可以**跨查询比较
    semantic: bool = False  # True = 本条来自语义检索（S4-02-5）。此时 bm25 无意义、恒为 0


# BM25 无上界，不能直接和 [0,1] 的余弦相加。用饱和函数 x/(x+S) 压到 [0,1)，
# S 取 3.0 是按现有语料的实测分布定的（见 TD-151 的标定记录）。
_BM25_SATURATION = 3.0


def _saturate(x: float) -> float:
    """把无上界的 BM25 压进 [0,1)，单调递增，x=0 时为 0。"""
    return x / (x + _BM25_SATURATION) if x > 0 else 0.0


def _normalize(scores: list[float]) -> list[float]:
    """按最大值归一化到 [0,1]，让两种量纲不同的分数可以相加。"""
    peak = max(scores) if scores else 0.0
    if peak <= 0:
        return [0.0] * len(scores)
    return [s / peak for s in scores]


def search(query: str, k: int = 3) -> list[FaqHit]:
    """融合召回 top-k，按分数降序。查不到相关内容时返回空列表（由上层决定兜底）。"""
    q = tokenize(query)
    if not q:
        return []
    raw_bm = _INDEX.bm25(q)
    raw_cos = _INDEX.cosine(q)
    # 注意：score 用「按最大值归一化」，它只表示**本结果集内**的相对强弱。
    # 单看 score 会以为 top1 永远是 1.0（确实如此），拿它当绝对置信度是错的 ——
    # 那会让「python 部署 nginx 报错」也判成 FAQ 命中。绝对判断请用 confidence。
    bm = _normalize(raw_bm)
    cos = _normalize(raw_cos)
    fused = [BM25_WEIGHT * b + (1 - BM25_WEIGHT) * c for b, c in zip(bm, cos, strict=True)]
    ranked = sorted(range(len(FAQS)), key=lambda i: fused[i], reverse=True)
    return [
        FaqHit(
            faq=FAQS[i],
            score=fused[i],
            bm25=bm[i],
            cosine=cos[i],
            confidence=BM25_WEIGHT * _saturate(raw_bm[i]) + (1 - BM25_WEIGHT) * raw_cos[i],
        )
        for i in ranked[:k]
        if fused[i] > 0
    ]


# ------------------------------------------------- 语义检索（S4-02-5）
#
# 词袋只认字面：「多少钱」和「怎么收费」对不上，除非有人在 keywords 里手写召回词。
# 语义向量让「贵不贵」「怎么算钱」也能命中，而且**不需要任何标注数据** ——
# FAQ 表本身就是语料，12 条在启动时向量化一次即可。
#
# 为什么 `search()` 仍然是同步的：向量化查询要发网络请求，而
# `RuleIntentRouter.classify()` 是同步接口（将来接 BERT 同样是同步的）。
# 所以分成两条路，各归其位：
#   search()            同步、纯本地、零成本 —— 路由的快车道
#   semantic_search()   异步、要调 /embeddings —— 只在快车道没把握时用
#
# 索引是**进程内缓存**，不是数据库：12 条向量几 KB，重启时重新预热一次比
# 引入一张表划算。预热失败就退回词袋，功能不缺失（见 warm_semantic_index）。

_SEMANTIC: list[list[float]] | None = None  # 与 FAQS 等长且**同序**
_SEMANTIC_NORM: list[float] = []
_semantic_logger = logging.getLogger("codemax.faq")

def semantic_threshold() -> float:
    """语义命中判定阈值。**每次调用都读 settings**，两个原因：

    1. 标定结果只需改 `.env`，不用改代码、不用重新发版；
    2. 测试能 monkeypatch —— 常量在 import 时就被 support.py 绑走，改不动。

    ⚠️ 默认 0.55 **尚未实测标定**（沙箱里没有 embedding API key），是按
    text-embedding-3-small 的经验值：同义问句通常 0.6+，不相关问句 0.3 左右。
    上线前必须跑 `calibrate_semantic_threshold` 重新量，见 TD-206。
    """
    return settings.LLM_SEMANTIC_THRESHOLD


def calibrate_threshold(
    positive_scores: Sequence[float],
    negative_scores: Sequence[float],
    *,
    margin: float = 0.02,
) -> float:
    """从两组实测余弦里算出该用的阈值。

    `positive_scores` = 「问法不同但确实在问某条 FAQ」的最高相似度；
    `negative_scores` = 「与本站无关」的问句的最高相似度。

    阈值必须**同时**满足「不误杀真命中」与「不误收无关问句」，所以取两个分布
    之间的间隙中点。两侧各留 `margin` 的安全边距：embedding 是有噪声的，
    贴着边界取值，同一条问句今天命中明天不命中，比稳定地偏保守糟糕得多。

    **两侧分数重叠时抛 ValueError 而不是硬算一个数** —— 重叠意味着这两组语料
    在这个 embedding 模型下根本分不开，此时任何阈值都是错的，正确的动作是
    换模型或补 FAQ 语料，而不是挑一个看起来合理的数字糊过去。

    这个函数刻意是纯的、不含 I/O：它可以在沙箱里用合成数据充分测试
    （见 tests/test_faq_semantic.py），标定逻辑本身因此是被验证过的 ——
    真正需要 API key 的只是「喂给它真实分数」这一步。
    """
    if not positive_scores or not negative_scores:
        raise ValueError("两组分数都不能为空，否则算出来的阈值没有依据")
    # 阈值必须同时满足两条，所以取的是「真命中的最低分」与「无关问句的最高分」：
    #   floor = 最低的那条真命中 —— 阈值高于它就会漏答真问题
    #   ceil_ = 最高的那条无关问句 —— 阈值低于它就会把无关问句当 FAQ 作答
    floor = min(positive_scores)
    ceil_ = max(negative_scores)
    if floor < ceil_:
        raise ValueError(
            f"两组分数重叠（真命中最低 {floor:.3f} < 无关问句最高 {ceil_:.3f}）："
            f"该 embedding 模型下这两类分不开，任何阈值都不对。"
            f"请换 embedding 模型或补充 FAQ 语料，而不是挑一个数糊过去。"
        )
    threshold = (ceil_ + floor) / 2
    if floor - margin < ceil_ + margin:
        raise ValueError(
            f"间隙只有 {floor - ceil_:.3f}，不足以留出 ±{margin} 的安全边距。"
            f"阈值 {threshold:.3f} 会贴边抖动，请补语料把两个分布拉开。"
        )
    return round(threshold, 3)


# 预热专用超时。`LLMClient.timeout` 默认 60 s 是为对话调用留的，但预热跑在
# **启动路径**上：实测网关不可达时 `await warm_semantic_index()` 会整整挂住
# 60.1 秒才开始监听业务流量 —— 编排器看到的是「启动探针一直不过」，可能直接判
# 失败反复重启，滚动发布时每个副本还要各挨一次。预热失败本来就只意味着退回词袋，
# 没有任何理由为它等一分钟。
WARM_UP_TIMEOUT = 5.0


async def warm_semantic_index(client: LLMClient = default_llm) -> bool:
    """把 FAQS 向量化并缓存。返回是否成功。

    **失败只记日志、绝不抛**：语义检索是增强项，不是依赖项。
    没配 `LLM_API_KEY`、网络不通、模型不支持中文，任何一条都只意味着
    「退回词袋」，而不是「客服功能挂掉」。启动流程不能因为一个可选增强而拒绝起服务。
    """
    global _SEMANTIC, _SEMANTIC_NORM
    # 只给预热这一步换短超时，不动全局 client：对话调用该等就得等。
    # 用 replace 造副本而不是改属性 —— 改全局实例会让测试之间互相污染。
    if client.timeout > WARM_UP_TIMEOUT:
        client = replace(client, timeout=WARM_UP_TIMEOUT)
    if _SEMANTIC is not None:
        return True
    try:
        vectors = await client.embeddings([f.q for f in FAQS])
    except LLMError as exc:
        _semantic_logger.info("语义 FAQ 索引未启用，退回词袋检索：%s", exc)
        return False
    if len(vectors) != len(FAQS):
        _semantic_logger.warning("向量条数 %d ≠ FAQ 条数 %d，丢弃", len(vectors), len(FAQS))
        return False
    _SEMANTIC = vectors
    _SEMANTIC_NORM = [math.sqrt(sum(x * x for x in v)) or 1.0 for v in vectors]
    _semantic_logger.info("语义 FAQ 索引已预热：%d 条，维度 %d", len(vectors), len(vectors[0]))
    return True


def semantic_ready() -> bool:
    """语义索引是否可用（供上层决定走哪条路、供测试断言）。"""
    return _SEMANTIC is not None


def reset_semantic_index() -> None:
    """清空缓存。测试专用：不同用例注入不同的假客户端，必须能隔离。"""
    global _SEMANTIC, _SEMANTIC_NORM
    _SEMANTIC = None
    _SEMANTIC_NORM = []


async def semantic_search(
    query: str, k: int = 3, client: LLMClient = default_llm
) -> list[FaqHit] | None:
    """语义 top-k。**索引未预热或调用失败时返回 None**，由调用方回落词袋。

    返回 None 而不是空列表，是为了区分两种完全不同的情况：
    「语义检索不可用」（该回落）和「语义检索跑了但没相关内容」（该转人工）。
    用空列表会把这两件事混成一件，上层就没法做正确的兜底。
    """
    if _SEMANTIC is None or not query.strip():
        return None
    try:
        qv = await client.embeddings([query])
    except LLMError as exc:
        _semantic_logger.info("查询向量化失败，本次退回词袋：%s", exc)
        return None
    if not qv or not qv[0]:
        return None
    q = qv[0]
    qn = math.sqrt(sum(x * x for x in q)) or 1.0
    hits: list[FaqHit] = []
    for i, vec in enumerate(_SEMANTIC):
        dot = sum(a * b for a, b in zip(q, vec, strict=True))
        cos = max(0.0, dot / (qn * _SEMANTIC_NORM[i]))  # 截断负值：方向相反不是「负相关」
        hits.append(
            FaqHit(
                faq=FAQS[i],
                score=cos,  # 语义分数天然同量纲，不需要归一化
                bm25=0.0,
                cosine=cos,
                confidence=cos,
                semantic=True,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
