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
from dataclasses import dataclass

import httpx
import pytest

from app.config import settings
from app.tools.faq import (
    FAQS,
    calibrate_threshold,
    reset_semantic_index,
    semantic_ready,
    semantic_search,
    semantic_threshold,
    warm_semantic_index,
)
from app.tools.intent import Intent, IntentResult
from app.tools.llm import LLMClient, LLMError, default_llm


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
#
# 阈值标定的核心不是「同义问句分数够高」，而是**两个分布之间有没有间隙**：
#   · 同义问句（问法不同但在问某条 FAQ）的最高相似度 = 下限，低于它就漏答
#   · 无关问句（与本站无关）的最高相似度           = 上限，高于它就乱答
# 只看前者是**方向性错误**：那样阈值定得再低也能通过，而阈值偏低恰恰是
# 危险的一侧（会把无关问句当 FAQ 直接作答，用户拿到一个自信的错误答案）。
# 所以下面既测纯函数本身（沙箱可跑），也留了要真实 key 才跑的实测标定。

# 同义问法 → 期望命中的那条 FAQ。**必须断言命中身份**：
# 只断言分数够高，会让「高相似度命中错误 FAQ」蒙混过关 —— 那种情况
# 用户拿到的是另一条 FAQ 的答案，比不回答更糟。
_CALIBRATION_POSITIVES = [
    ("贵不贵", "毕业设计服务怎么收费"),
    ("怎么算钱", "毕业设计服务怎么收费"),
    ("能开发票吗", "可以开发票吗"),
    ("发票怎么开", "可以开发票吗"),
    ("做完要多久", "交付周期是多久"),
    ("源代码给不给", "会给源码吗"),
    ("是不是抄的", "怎么保证不是抄袭"),
    ("密码忘了", "账号忘记密码怎么办"),
    ("买完的文件在哪下", "怎么下载已购买的文件"),
]

# 与本站无关的问句。它们不该命中任何 FAQ —— 分数必须落在阈值之下。
_CALIBRATION_NEGATIVES = [
    "今天天气怎么样",
    "推荐一部好看的电影",
    "帮我写一首关于秋天的诗",
    "股票现在能不能买",
    "怎么把 Excel 转成 PDF",
]


def test_calibrate_threshold_picks_the_gap_midpoint():
    """间隙够宽时取中点。"""
    assert calibrate_threshold([0.82, 0.79, 0.75], [0.41, 0.35, 0.28]) == 0.58


def test_calibrate_threshold_rejects_overlapping_distributions():
    """两类分数重叠时必须报错，而不是硬算一个数糊过去。

    重叠意味着该 embedding 模型下这两类根本分不开，此时任何阈值都是错的。
    静默返回一个「看起来合理」的数字，是最坏的一种失败 —— 它把一个
    模型选型问题伪装成了一个已解决的配置问题。
    """
    with pytest.raises(ValueError, match="重叠"):
        calibrate_threshold([0.60, 0.58], [0.59, 0.30])


def test_calibrate_threshold_rejects_a_gap_too_narrow_to_be_stable():
    """间隙太窄时报错：贴边取值会让同一条问句今天命中明天不命中。"""
    with pytest.raises(ValueError, match="间隙"):
        calibrate_threshold([0.605], [0.600], margin=0.02)


def test_calibrate_threshold_needs_both_sides():
    with pytest.raises(ValueError, match="不能为空"):
        calibrate_threshold([0.8], [])
    with pytest.raises(ValueError, match="不能为空"):
        calibrate_threshold([], [0.3])


def test_semantic_threshold_reads_settings_so_recalibration_needs_no_code_change(monkeypatch):
    """阈值必须是现读的，不是 import 时绑死的。

    绑死的后果是「标定完必须改代码 + 重新发版」，而它本该只是改 .env。
    另一个后果更隐蔽：测试里 monkeypatch 改不动，于是标定用例只能去测
    一个写死的常量，测不出线上真实行为。
    """
    monkeypatch.setattr(settings, "LLM_SEMANTIC_THRESHOLD", 0.71)
    assert semantic_threshold() == 0.71


async def test_support_cascade_honours_the_configured_threshold(monkeypatch):
    """级联第二级必须用配置里的阈值，而不是另一个写死的数。

    两处各写一个数是很典型的失守方式：标定只改了一处，另一处还在按旧值判，
    而两边都「有测试通过」。这里把阈值调到 0.99 —— 语义必然不命中，
    级联必须继续往下走到 LLM 分类，reason 里不能再出现「语义检索命中」。
    """
    from app.tools import support

    client = _client(_calibration_handler)
    await warm_semantic_index(client)

    # 0.999 而不是 0.99：假空间里「贵不贵」的余弦实测是 0.99228，
    # 0.99 根本拦不住 —— 阈值取得贴近被测值，用例就变成在赌浮点。
    monkeypatch.setattr(settings, "LLM_SEMANTIC_THRESHOLD", 0.999)
    verdict, hit = await support._second_opinion("贵不贵", _low_rule(), client)
    assert hit is None, "阈值 0.999 时语义不该命中"
    assert "语义检索命中" not in verdict.reason

    monkeypatch.setattr(settings, "LLM_SEMANTIC_THRESHOLD", 0.50)
    verdict, hit = await support._second_opinion("贵不贵", _low_rule(), client)
    assert hit is not None, "阈值 0.50 时同一条问句必须命中"
    assert verdict.intent is Intent.FAQ


# ---- 以上是沙箱里就能跑的；以下这条要真实 embedding API ----


def _calibration_handler(request: httpx.Request) -> httpx.Response:
    """复用本文件已有的假向量空间 `_space()`，另给 chat 一个明确的失败响应。

    不共用 `_semantic_handler` 是因为这条用例要走到 LLM 分类那一步
    （阈值调到 0.99 时语义必然不命中，级联会继续往下）——
    给它一个 embeddings 形状的响应会让 `chat()` 的解析路径变成隐式行为。
    """
    if request.url.path.endswith("/chat/completions"):
        return httpx.Response(500, json={"error": "本用例只测语义那一层"})
    body = json.loads(request.content)
    return _embed_response([_space(x) for x in body["input"]])


def _low_rule() -> IntentResult:
    """规则层「没把握」的判定 —— 级联第二级的前提。"""
    return IntentResult(Intent.CHITCHAT, 0.20, "规则层没把握（测试构造）")


def _report_and_check(scores: dict[str, float], label: str) -> None:
    for text, score in scores.items():
        print(f"  [{label}] {text} → {score:.3f}")


async def _run_calibration(client: LLMClient) -> float:
    """标定流程本体。**刻意抽成可注入 client 的函数**：

    它默认要真实 embedding API，在沙箱与 CI 里永远被跳过。一条永远不执行的
    测试等于没有测试 —— 所以这里让沙箱可以用合成向量空间跑一遍同样的流程
    （见 `test_calibration_harness_has_teeth`），断言写错了当场就能发现。
    """
    assert await warm_semantic_index(client)

    positives: dict[str, float] = {}
    for paraphrase, expected_q in _CALIBRATION_POSITIVES:
        # client 必须一路传到底：semantic_search 默认走 default_llm，
        # 而沙箱/CI 里它没有 api_key，查询向量化会直接失败返回 None。
        hits = await semantic_search(paraphrase, k=1, client=client)
        assert hits is not None, "语义索引没预热成功"
        got = hits[0].faq.q
        assert got == expected_q, (
            f"「{paraphrase}」命中了「{got}」，期望「{expected_q}」。"
            f"高相似度命中错误 FAQ 比不命中更糟 —— 用户会拿到另一条的答案。"
        )
        positives[paraphrase] = hits[0].confidence

    negatives: dict[str, float] = {}
    for text in _CALIBRATION_NEGATIVES:
        hits = await semantic_search(text, k=1, client=client)
        assert hits is not None
        negatives[text] = hits[0].confidence

    print("\n实测余弦分布：")
    _report_and_check(positives, "同义")
    _report_and_check(negatives, "无关")

    suggested = calibrate_threshold(list(positives.values()), list(negatives.values()))
    print(f"\n  当前配置 LLM_SEMANTIC_THRESHOLD = {semantic_threshold()}")
    print(f"  实测建议值                     = {suggested}")
    print("  → 把建议值写进 .env 后，更新 TD-206 划掉「尚未标定」")

    floor, ceil_ = min(positives.values()), max(negatives.values())
    assert ceil_ < semantic_threshold() <= floor, (
        f"当前阈值 {semantic_threshold()} 不在实测间隙 [{ceil_:.3f}, {floor:.3f}] 内，"
        f"请改用建议值 {suggested}"
    )
    return suggested


# ---- 合成向量空间：让上面那套断言在没有 API key 的环境里也能被验证 ----

_SYNTH_DIM = 20


def _synth_vec(kw: dict[int, float]) -> list[float]:
    """每个向量都带 0.25 的公共分量，模拟真实 embedding 的「文本通用质量」——
    否则无关问句会正好 0 分、同义问句正好 1 分，那太假，测不出边界行为。"""
    x = [0.25] * _SYNTH_DIM
    for i, w in kw.items():
        x[i] += w
    return x


def _synth_space(text: str, *, break_identity: str | None = None) -> list[float]:
    idx = {f.q: i for i, f in enumerate(FAQS)}
    if text == break_identity:
        # 故意把这条同义问句指到**另一条** FAQ 上，用来验证「命中身份」断言有牙
        wrong = (idx[_CALIBRATION_POSITIVES[0][1]] + 5) % len(FAQS)
        return _synth_vec({wrong: 0.72, (wrong + 1) % 12: 0.18})
    if text in idx:
        return _synth_vec({idx[text]: 1.0})
    for paraphrase, expected in _CALIBRATION_POSITIVES:
        if text == paraphrase:
            j = idx[expected]
            return _synth_vec({j: 0.72, (j + 1) % 12: 0.18})
    if text in _CALIBRATION_NEGATIVES:
        # 落在 FAQ 用不到的维度上，但因为有公共分量，余弦不会正好是 0
        return _synth_vec({14 + _CALIBRATION_NEGATIVES.index(text): 1.0})
    return _synth_vec({})


def _synth_handler(break_identity: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _embed_response([_synth_space(x, break_identity=break_identity) for x in body["input"]])

    return handler


async def test_calibration_harness_has_teeth(monkeypatch):
    """**这条用例存在的唯一理由**：证明上面那个默认被跳过的标定流程本身是对的。

    标定用例没有 API key 就永远不跑，写在里面的断言也就永远不会被执行 ——
    写错了没人知道，等到上线前真跑那天才炸，那时最没时间修。
    所以用合成向量空间把它整条跑一遍。

    合成空间量出来的间隙是 [0.636, 0.983]，而仓库里的默认阈值是 0.55（带外），
    所以这里必须先把阈值挪进带内 —— 这本身就说明「带内检查」那条断言是活的。
    """
    monkeypatch.setattr(settings, "LLM_SEMANTIC_THRESHOLD", 0.75)
    suggested = await _run_calibration(_client(_synth_handler()))
    # 合成空间的实测分布：同义 0.983 / 无关 0.636 → 中点 0.810
    assert suggested == 0.81


async def test_calibration_harness_rejects_an_out_of_band_threshold():
    """当前阈值不在实测间隙内时必须报错 —— 这正是「0.55 尚未标定」的真实处境。

    合成空间的间隙是 [0.636, 0.983]，默认的 0.55 落在带外。
    这条用例把那种情况固定下来：标定跑完发现配置值不对，就必须吵出来，
    而不是打印一句「建议 0.81」然后让用例绿着过去。
    """
    assert semantic_threshold() == 0.55, "前提：仓库默认阈值就是 0.55"
    with pytest.raises(AssertionError, match="不在实测间隙"):
        await _run_calibration(_client(_synth_handler()))


async def test_calibration_harness_catches_a_paraphrase_matching_the_wrong_faq():
    """高相似度命中**错误**的 FAQ 必须被抓住。

    这是原标定用例漏掉的一类：它只比分数够不够高，于是「很像，但像错了那条」
    能顺利通过 —— 而那种情况用户拿到的是另一条 FAQ 的答案，比不回答更糟。
    """
    victim = _CALIBRATION_POSITIVES[0][0]
    with pytest.raises(AssertionError, match="命中了"):
        await _run_calibration(_client(_synth_handler(break_identity=victim)))


@pytest.mark.skipif(
    not __import__("os").environ.get("LLM_API_KEY"),
    reason="标定需要真实 embedding API：设 LLM_API_KEY 后运行（做法见 TD-206）",
)
async def test_calibrate_semantic_threshold():
    """**上线前必须跑一次**，并且要带 `-s` 才看得到打印出来的分布。

    它做三件事，缺一不可（流程本体在 `_run_calibration`，沙箱里由
    `test_calibration_harness_has_teeth` 用合成向量空间验证过）：

    1. 同义问句必须命中**正确的那条** FAQ —— 只比分数不够，命中错条更糟；
    2. 无关问句的最高分必须落在阈值之下；
    3. 用两个分布的间隙算出该用的阈值，并检查当前配置是否落在间隙内。

    跑完把打印出来的建议值写进 `.env` 的 `LLM_SEMANTIC_THRESHOLD`，
    再更新 TD-206 把「尚未标定」那条划掉。
    """
    await _run_calibration(default_llm)


# ---------------------------------------------------------- 预热超时（N-1）
#
# ⚠️ 这里**不能**用「让 handler 睡很久、看预热多久放弃」来测：
# `httpx.MockTransport` 没有真实 I/O，**不执行 timeout** —— 实测 `timeout=1.0`
# 的 client 配一个 `sleep(30)` 的 handler，照样等满 30 秒。那样写出来的用例
# 会永远挂着（我第一版就是这么写的，直接把测试跑超时了）。
# 所以改成直接断言「预热时真正生效的 timeout 是多少」。


@dataclass
class _TimeoutSpyClient(LLMClient):
    """记录 `embeddings` 被调用时自己身上挂的 timeout。

    必须是 `LLMClient` 的子类而不是替身：`warm_semantic_index` 用
    `dataclasses.replace(client, timeout=...)` 造副本，替身没有这些字段就炸；
    而 `replace` 会保留子类，所以记录到的正是预热那一次真正生效的值。
    """

    async def embeddings(self, texts: list[str]) -> list[list[float]]:
        _SEEN_TIMEOUTS.append(self.timeout)
        return [[1.0, 0.0] for _ in texts]


_SEEN_TIMEOUTS: list[float] = []


@pytest.fixture(autouse=True)
def _clear_seen_timeouts():
    _SEEN_TIMEOUTS.clear()
    yield
    _SEEN_TIMEOUTS.clear()


async def test_warm_up_uses_a_short_timeout_not_the_60s_default():
    """**启动路径不能被一次外部调用挂住 60 秒。**

    实测：LLM 网关不可达时 `await warm_semantic_index()` 会整整等满
    `LLMClient.timeout`（默认 60.0 s）才返回 —— 那 60 秒里应用还没开始监听
    业务流量，编排器看到的是「启动探针一直不过」，可能直接判失败反复重启，
    滚动发布时每个副本还要各挨一次。预热失败本来就只意味着退回词袋。
    """
    from app.tools.faq import WARM_UP_TIMEOUT

    client = _TimeoutSpyClient(api_key="k", base_url="http://llm.test/v1", timeout=60.0)
    assert await warm_semantic_index(client) is True

    assert _SEEN_TIMEOUTS == [WARM_UP_TIMEOUT], (
        f"预热实际用的 timeout 是 {_SEEN_TIMEOUTS}，期望 [{WARM_UP_TIMEOUT}] —— "
        "短超时没生效，启动又会被外部 HTTP 挂住。"
    )
    assert WARM_UP_TIMEOUT <= 5.0, "预热超时不该超过 5 秒"


async def test_warm_up_does_not_shrink_the_callers_client():
    """短超时只能作用于预热那一次调用，不能顺带改掉调用方的 client。

    改传进来的实例是这里最容易犯的错：测试之间互相污染，而且线上对话调用
    会因为一个 5 秒上限而频繁超时（对话本来就该允许等久一点）。
    """
    client = _TimeoutSpyClient(api_key="k", base_url="http://llm.test/v1", timeout=60.0)
    await warm_semantic_index(client)
    assert client.timeout == 60.0, "预热不许修改传进来的 client"


async def test_warm_up_keeps_an_already_short_timeout():
    """调用方给的超时本来就比上限短时，不该被放大。"""
    from app.tools.faq import WARM_UP_TIMEOUT

    client = _TimeoutSpyClient(api_key="k", base_url="http://llm.test/v1", timeout=1.0)
    await warm_semantic_index(client)
    assert _SEEN_TIMEOUTS == [1.0], f"不该把 1.0 放大成 {WARM_UP_TIMEOUT}"
