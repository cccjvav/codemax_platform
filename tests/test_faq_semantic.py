"""语义 FAQ 检索（S4-02-5）的测试。

重点不在「能算余弦」—— 那是十行数学。重点是三件容易出错又不会报错的事：

1. **向量与语料的对应关系**。`/embeddings` 只保证每条带 `index`，不保证数组有序。
   错一位的后果是「检索永远返回错的那条」，而它不会抛异常，只会安静地答错。
2. **失败要退化成「没有语义」而不是「服务挂掉」**。没配 key、网络不通、
   模型不支持中文，任何一条都只能让客服退回词袋检索。
3. **`None` 与空列表是两件事**：前者是「语义不可用，该回落」，
   后者是「跑了但没相关内容，该转人工」。混为一谈上层就做不出正确兜底。

所有用例都通过 `httpx.MockTransport` 打**真实的** `LLMClient.embeddings()`，
不写替身 —— 否则测的是替身，不是要上线的那段代码。
"""
import json

import httpx
import pytest

from app.tools.faq import (
    FAQS,
    reset_semantic_index,
    semantic_ready,
    semantic_search,
    warm_semantic_index,
)
from app.tools.llm import LLMClient, LLMError


@pytest.fixture(autouse=True)
def _isolate_index():
    """每个用例前后都清空进程内缓存 —— 它是全局变量，不隔离会互相污染。"""
    reset_semantic_index()
    yield
    reset_semantic_index()


def _client(handler) -> LLMClient:
    return LLMClient(
        api_key="test-key",
        base_url="http://llm.test/v1",
        transport=httpx.MockTransport(handler),
    )


def _embed_response(vectors: list[list[float]], *, shuffle: bool = False) -> httpx.Response:
    """构造一个 OpenAI 兼容的 /embeddings 响应体。

    `shuffle=True` 时刻意**逆序**返回 —— 真实实现就是按 index 排序取，
    这条用例守的正是「不能假设数组有序」。
    """
    data = [{"index": i, "embedding": v, "object": "embedding"} for i, v in enumerate(vectors)]
    if shuffle:
        data.reverse()
    return httpx.Response(200, json={"data": data, "model": "fake", "usage": {}})


# --------------------------------------------------------------- embeddings


async def test_embeddings_preserves_input_order_even_when_response_is_shuffled():
    """乱序返回也要还原成输入顺序。这是最坏的一类 bug：不报错，只是全答错。"""
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["input"])
        vectors = [[float(len(t)), 0.0] for t in body["input"]]
        return _embed_response(vectors, shuffle=True)

    out = await _client(handler).embeddings(["aaa", "bb", "c"])
    assert seen == [["aaa", "bb", "c"]], "必须把全部语料一次请求发出去"
    assert [v[0] for v in out] == [3.0, 2.0, 1.0], "顺序必须与输入一致"


async def test_embeddings_empty_input_does_not_hit_network():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("空输入不该发请求")

    assert await _client(handler).embeddings([]) == []


async def test_embeddings_without_api_key_raises_llm_error():
    """没配 key 要给出能看懂的原因，而不是 401 或超时。"""
    c = LLMClient(api_key="", transport=httpx.MockTransport(lambda r: _embed_response([[1.0]])))
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        await c.embeddings(["x"])


async def test_embeddings_non_200_raises_with_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="model_not_supported")

    with pytest.raises(LLMError, match="400"):
        await _client(handler).embeddings(["x"])


async def test_embeddings_rejects_count_mismatch():
    """返回条数对不上就不能用 —— 少一条会让后面每条都错位。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return _embed_response([[1.0]])

    with pytest.raises(LLMError, match="1 条.*2 条"):
        await _client(handler).embeddings(["a", "b"])


async def test_embeddings_rejects_empty_vector():
    """维度为 0 的向量算模长会除零，必须在这里就挡掉。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return _embed_response([[]])

    with pytest.raises(LLMError, match="空向量"):
        await _client(handler).embeddings(["a"])


async def test_embeddings_network_error_wrapped_as_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连不上")

    with pytest.raises(LLMError, match="调用向量化接口失败"):
        await _client(handler).embeddings(["a"])


# ----------------------------------------------------------- 索引预热与回落


async def test_warm_semantic_index_caches_every_faq():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _embed_response([[1.0, 0.0] for _ in body["input"]])

    assert await warm_semantic_index(_client(handler)) is True
    assert semantic_ready() is True


async def test_warm_semantic_index_swallows_llm_error():
    """没配 key / 网络不通时**只记日志**。启动流程不能因为一个可选增强而拒绝起服务。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连不上")

    assert await warm_semantic_index(_client(handler)) is False
    assert semantic_ready() is False


async def test_warm_semantic_index_rejects_count_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return _embed_response([[1.0]])

    assert await warm_semantic_index(_client(handler)) is False
    assert semantic_ready() is False, "条数对不上就必须整体丢弃，宁可不启用"


async def test_warm_semantic_index_is_idempotent():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(len(body["input"]))
        return _embed_response([[1.0] for _ in body["input"]])

    c = _client(handler)
    await warm_semantic_index(c)
    await warm_semantic_index(c)
    assert calls == [len(FAQS)], "第二次不该再打网络"


async def test_semantic_search_returns_none_before_warm_up():
    """未预热 → None（不是空列表）。上层据此回落词袋，而不是误判成「没相关内容」。"""
    assert await semantic_search("怎么收费") is None
    assert semantic_ready() is False


# ------------------------------------------------------------- 语义命中质量


def _space(text: str) -> list[float]:
    """一个手写的假向量空间：让「换个说法」在几何上真的更近。

    这是本文件最重要的一条用例。词袋的死穴是「贵不贵」和「怎么收费」
    一个字都不重合 —— 而语义空间里它们该挨着。

    三个轴分别代表：收费 / 其它话题 / 技术话题。预热时**全部 12 条** FAQ 都会
    被求向量，所以除收费那条以外的都必须给一个向量；给同一个向量会让排序打平、
    断言随机失败，于是按序号在第三轴上加一点点扰动。
    """
    table = {
        FAQS[0].q: [1.0, 0.0, 0.0],  # 毕业设计服务怎么收费 → 收费轴
        "贵不贵": [0.96, 0.12, 0.0],  # 换了说法，但意思一样
        "怎么算钱": [0.94, 0.10, 0.05],
        "python 部署报错": [0.0, 0.05, 1.0],  # 技术话题轴，离收费轴正交
    }
    if text in table:
        return table[text]
    i = next((n for n, f in enumerate(FAQS) if f.q == text), 0)
    return [0.0, 1.0, 0.01 * i]  # 其余 FAQ 一律落在「其它话题」轴上


def _semantic_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return _embed_response([_space(t) for t in body["input"]])


async def test_semantic_search_rescues_a_paraphrase_the_lexical_index_misses():
    """词袋搜不到的换说法，语义要能捞回来 —— 否则这一层就白加了。"""
    from app.tools.faq import search

    lexical = search("贵不贵", k=1)
    assert not lexical or lexical[0].faq is not FAQS[0], (
        "前提校验：词袋本就该搜不到「贵不贵」，否则这条用例证明不了什么"
    )

    # 预热与查询**必须用同一个客户端**：semantic_search 默认走 default_llm，
    # 而测试环境里它没有 api_key，查询向量化会直接失败返回 None。
    c = _client(_semantic_handler)
    await warm_semantic_index(c)
    hits = await semantic_search("贵不贵", k=1, client=c)
    assert hits is not None
    assert hits[0].faq is FAQS[0], "「贵不贵」应当命中收费那条"
    assert hits[0].semantic is True
    assert hits[0].bm25 == 0.0, "语义结果没有 BM25 分量，不该伪装成一个数"
    assert 0.9 < hits[0].confidence <= 1.0


async def test_semantic_search_ranks_by_similarity_not_by_order():
    c = _client(_semantic_handler)
    await warm_semantic_index(c)
    hits = await semantic_search("python 部署报错", k=len(FAQS), client=c)
    assert hits is not None
    assert hits[0].faq is not FAQS[0], "无关问题不该把收费那条排到第一"


async def test_semantic_search_query_embedding_failure_falls_back():
    """语料已预热、但**这一次**查询向量化失败 → 返回 None，让上层回落。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:  # 第一次是预热，放行
            return _semantic_handler(request)
        raise httpx.ConnectError("查询时断网")

    await warm_semantic_index(_client(handler))
    assert await semantic_search("贵不贵", client=_client(handler)) is None


async def test_semantic_search_empty_query_short_circuits():
    c = _client(_semantic_handler)
    await warm_semantic_index(c)
    assert await semantic_search("   ", client=c) is None


async def test_reset_semantic_index_isolates_cases():
    c = _client(_semantic_handler)
    await warm_semantic_index(c)
    assert semantic_ready() is True
    reset_semantic_index()
    assert semantic_ready() is False
    assert await semantic_search("贵不贵", client=c) is None, "清空后必须回落，不能再命中"


# ------------------------------------------------------------------ 标定


@pytest.mark.skipif(
    not __import__("os").environ.get("LLM_API_KEY"),
    reason="标定需要真实 embedding API：设 LLM_API_KEY 后运行，见 SEMANTIC_CONFIDENCE_THRESHOLD 注释",
)
async def test_calibrate_semantic_threshold():
    """**上线前必须跑一次**：在真实语料上量同义问句与无关问句的余弦分布，
    用两个分布的间隙重新标定 SEMANTIC_CONFIDENCE_THRESHOLD（做法见 TD-151）。

    沙箱里没有 key，所以默认跳过 —— 那个 0.55 目前是经验值，不是实测值。
    """
    from app.tools.faq import SEMANTIC_CONFIDENCE_THRESHOLD

    assert await warm_semantic_index()
    for paraphrase in ("贵不贵", "怎么算钱", "开发票要多久", "密码忘了"):
        hits = await semantic_search(paraphrase, k=1)
        assert hits is not None
        print(f"{paraphrase} → {hits[0].faq.q} {hits[0].confidence:.3f}")
        assert hits[0].confidence >= SEMANTIC_CONFIDENCE_THRESHOLD
