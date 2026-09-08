"""S4-02-1 FAQ 融合召回的测试。

重点是三件事：**召回对不对**、**融合是不是真的两种信号都在起作用**、
以及 ROADMAP 要求的 **< 80ms 延迟**。
"""
import time

import pytest

from app.tools.faq import BM25_WEIGHT, FAQS, _Index, search, tokenize


def _top(query: str) -> str:
    hits = search(query, k=1)
    return hits[0].faq.q if hits else ""


# ---------- 召回质量 ----------

def test_keyword_hit():
    """直接打关键词就该命中讲发票那条。"""
    assert _top("发票") == "可以开发票吗"


def test_rephrased_query_still_hits():
    """换了说法（「开票」「报销」）也要命中——这是余弦分量的价值。"""
    assert _top("怎么开票") == "可以开发票吗"
    assert _top("能报销吗") == "可以开发票吗"


def test_database_question():
    assert _top("支持哪些数据库") == "支持哪些数据库"
    assert _top("mysql 能用吗") == "支持哪些数据库"


def test_download_question():
    assert _top("下载链接打不开") == "怎么下载已购买的文件"


def test_top_k_limit():
    assert len(search("价格", k=3)) <= 3
    assert len(search("价格", k=1)) == 1


def test_empty_query_returns_nothing():
    assert search("") == []
    assert search("   ") == []


# ---------- 分数性质 ----------

def test_scores_are_normalized_and_sorted():
    hits = search("毕业设计多少钱", k=5)
    assert hits, "应该能召回到内容"
    for h in hits:
        assert 0.0 < h.score <= 1.0, h
        assert 0.0 <= h.bm25 <= 1.0
        assert 0.0 <= h.cosine <= 1.0
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), "必须按分数降序"


def test_fusion_weight_is_applied():
    """融合分数必须真的等于两个分量的加权和，而不是只取其中一个。"""
    hits = search("怎么收费", k=3)
    for h in hits:
        expected = BM25_WEIGHT * h.bm25 + (1 - BM25_WEIGHT) * h.cosine
        assert h.score == pytest.approx(expected), h


def test_index_is_reusable():
    """索引构造一次、查询多次（这是能进 80ms 的前提）。"""
    idx = _Index([tokenize(f.q) for f in FAQS])
    a = idx.bm25(tokenize("价格"))
    b = idx.bm25(tokenize("价格"))
    assert a == b
    assert len(a) == len(FAQS)


# ---------- 融合确实两种信号都在起作用 ----------

def test_both_signals_contribute():
    """至少要有一条结果的两个分量都非零——否则融合退化成单路召回。"""
    hits = search("毕业设计服务怎么收费", k=3)
    assert any(h.bm25 > 0 and h.cosine > 0 for h in hits)


def test_bm25_and_cosine_are_independent_signals():
    """两路分量的**数值**必须不同，否则其中一路是多余的。

    注意这里刻意**不**断言两者排序不同：语料只有 12 条，两个相关信号在小结果集上
    排序一致是正常现象，不能当作「有一路没起作用」的证据（这条测试最初就是这么
    写错的）。真正的不变量是数值不同、且都不是恒等于融合分数。
    """
    hits = search("可以开发票吗", k=len(FAQS))
    assert len(hits) >= 3
    bm = [h.bm25 for h in hits]
    cos = [h.cosine for h in hits]
    assert bm != cos, "两路分量数值完全相同，说明其中一路是多余的"
    # 且都不能退化成「恒等于融合分数」
    assert bm != [h.score for h in hits]
    assert cos != [h.score for h in hits]


# ---------- 性能（ROADMAP 硬指标）----------

def test_latency_under_80ms():
    """ROADMAP S4-02-1 要求响应延迟 < 80ms。

    只测**热路径**：jieba 词典在模块导入时已预热，这里量的是稳态查询。
    取 200 次的平均值而不是单次，避免偶发抖动误判。
    """
    queries = ["怎么收费", "可以开发票吗", "支持哪些数据库", "下载链接打不开", "忘记密码"]
    for q in queries:  # 先跑一遍热身
        search(q)
    t0 = time.perf_counter()
    rounds = 200
    for _ in range(rounds):
        for q in queries:
            search(q)
    per_query_ms = (time.perf_counter() - t0) / (rounds * len(queries)) * 1000
    assert per_query_ms < 80, f"单次查询 {per_query_ms:.2f}ms，超过 80ms 指标"


def test_tokenize_is_warm():
    """预热必须真的生效：单次分词不该出现词典加载那种几百毫秒的开销。"""
    t0 = time.perf_counter()
    for _ in range(100):
        tokenize("毕业设计服务怎么收费")
    per_ms = (time.perf_counter() - t0) / 100 * 1000
    assert per_ms < 10, f"单次分词 {per_ms:.2f}ms，词典似乎没预热"
