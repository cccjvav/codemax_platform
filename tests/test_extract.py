"""S4-01-2/3/4 测试：LLM 指认选择器 + BeautifulSoup 提取 + 入库。

LLM 一律用假客户端（不打网络），抓取一律用 `httpx.MockTransport`。
两个重点：

1. **入库的是页面上的真实字节，不是模型编的** —— 所以断言直接比对 fixture HTML 里的原文。
   这正是"LLM 只指认选择器、不让它吐正文"这个设计的意义（TD-135）。
2. **模型不听话时要报清楚的错**：返回非 JSON、造不存在的选择器、漏必需字段，
   都不能静默产出半截数据。
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Article
from app.tools.crawler import CrawlError
from app.tools.extract import (
    SYSTEM_PROMPT,
    ExtractError,
    ParsedArticle,
    extract_fields,
    identify_selectors,
    parse_article,
    save_article,
)
from app.tools.llm import LLMError
from tests.conftest import TestSession

FAKE_IP = "93.184.216.34"
URL = f"http://{FAKE_IP}/blog/1"

BLOG_HTML = f"""
<html><head><script>var noise=1;</script></head>
<body>
  <nav><a href="/">首页</a></nav>
  <article class="post entry" id="main">
    <h1 class="title">毕业设计的开题报告怎么写</h1>
    <div class="meta"><span class="author">张三</span><time>2026-03-01</time></div>
    <div class="content">
      <p>第一段正文，讲选题背景。</p>
      <p>第二段正文，讲研究方法。</p>
    </div>
    <div class="related"><p>相关推荐：不该被当成正文</p></div>
  </article>
</body></html>
"""

SELECTORS = {
    "title": "h1.title",
    "author": "span.author",
    "published_at": "time",
    "content": "div.content",
}


class FakeLLM:
    """只记录被喂了什么，然后回一句固定的话。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest_asyncio.fixture(autouse=True)
async def _schema(client):
    """借 conftest 的 client fixture 建表（本文件只有入库那几条需要）。"""


# ---------------------------------------------------------------- S4-01-3 选择器识别


async def test_identify_parses_plain_json():
    import json

    llm = FakeLLM(json.dumps(SELECTORS, ensure_ascii=False))
    assert await identify_selectors("article\n  h1.title", llm=llm) == SELECTORS
    assert llm.calls[0][0] == SYSTEM_PROMPT


async def test_identify_strips_code_fence():
    import json

    body = json.dumps(SELECTORS, ensure_ascii=False)
    for wrapped in (f"```json\n{body}\n```", f"```\n{body}\n```", f"```json\n{body}"):
        assert await identify_selectors("x", llm=FakeLLM(wrapped)) == SELECTORS


async def test_identify_tolerates_padding_text():
    import json

    reply = "好的，结果如下：\n" + json.dumps(SELECTORS, ensure_ascii=False)
    # 没有围栏时整体不是合法 JSON，必须报清楚的错而不是猜
    with pytest.raises(ExtractError) as e:
        await identify_selectors("x", llm=FakeLLM(reply))
    assert "合法 JSON" in str(e.value)


async def test_identify_rejects_bad_replies():
    for reply in ("我不知道", "[1, 2, 3]", '{"title": "h1", "bogus": "x"}', "null"):
        with pytest.raises(ExtractError):
            await identify_selectors("x", llm=FakeLLM(reply))


async def test_identify_wraps_llm_error():
    with pytest.raises(ExtractError) as e:
        await identify_selectors("x", llm=FakeLLM(LLMError("未配置 LLM_API_KEY")))
    assert "LLM_API_KEY" in str(e.value)


async def test_missing_optional_field_becomes_empty_string():
    import json

    partial = {**SELECTORS, "author": "", "published_at": None}
    got = await identify_selectors("x", llm=FakeLLM(json.dumps(partial)))
    assert got["author"] == "" and got["published_at"] == ""
    assert got["title"] == SELECTORS["title"]


# ---------------------------------------------------------------- 提取（真实字节）


def test_extract_returns_the_real_text():
    got = extract_fields(BLOG_HTML, SELECTORS)
    assert got["title"] == "毕业设计的开题报告怎么写"
    assert got["author"] == "张三"
    assert got["published_at"] == "2026-03-01"
    assert got["content"] == "第一段正文，讲选题背景。\n第二段正文，讲研究方法。"
    assert "相关推荐" not in got["content"], "容器选对了就不该混进相关推荐"


def test_extract_rejects_selector_that_matches_nothing():
    with pytest.raises(ExtractError) as e:
        extract_fields(BLOG_HTML, {**SELECTORS, "title": "h2.not-exist"})
    assert "匹配不到" in str(e.value)


def test_extract_rejects_illegal_selector():
    with pytest.raises(ExtractError) as e:
        extract_fields(BLOG_HTML, {**SELECTORS, "content": "div[["})
    assert "不合法" in str(e.value)


def test_extract_without_optional_selector_gives_empty():
    got = extract_fields(BLOG_HTML, {**SELECTORS, "author": "", "published_at": ""})
    assert got["author"] == "" and got["published_at"] == ""
    assert got["title"] == "毕业设计的开题报告怎么写"


def test_extract_content_falls_back_to_container_text():
    html = "<body><div class='c'>没有 p 标签的正文</div></body>"
    assert extract_fields(html, {"title": ".c", "content": ".c"})["content"] == "没有 p 标签的正文"


# ---------------------------------------------------------------- 端到端


async def test_parse_article_end_to_end(monkeypatch):
    import httpx

    llm = FakeLLM(__import__("json").dumps(SELECTORS, ensure_ascii=False))
    article = await parse_article(
        URL, transport=httpx.MockTransport(lambda req: httpx.Response(200, text=BLOG_HTML)), llm=llm
    )
    assert isinstance(article, ParsedArticle)
    assert article.title == "毕业设计的开题报告怎么写"
    assert article.author == "张三"
    assert article.source_site == FAKE_IP
    assert article.content.startswith("第一段正文")

    # 关键：喂给模型的是 DOM 骨架，不是整页 HTML
    skeleton = llm.calls[0][1]
    assert "article#main.post.entry" in skeleton
    assert "<script>" not in skeleton and "var noise" not in skeleton
    assert len(skeleton) < len(BLOG_HTML)


async def test_parse_article_requires_title_and_content():
    import httpx
    import json

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=BLOG_HTML))
    for bad in ({**SELECTORS, "title": ""}, {**SELECTORS, "content": ""}):
        with pytest.raises(ExtractError) as e:
            await parse_article(URL, transport=transport, llm=FakeLLM(json.dumps(bad)))
        assert "必需字段" in str(e.value)


async def test_parse_article_propagates_crawl_error():
    import httpx

    with pytest.raises(CrawlError):
        await parse_article(
            URL,
            transport=httpx.MockTransport(lambda req: httpx.Response(404, text="nope")),
            llm=FakeLLM("{}"),
        )


# ---------------------------------------------------------------- 入库


async def _save(article: ParsedArticle) -> Article:
    async with TestSession() as s:
        return await save_article(s, article)


def _mk(**kw) -> ParsedArticle:
    base = dict(
        url=URL,
        source_site=FAKE_IP,
        title="标题",
        author="张三",
        published_at="2026-03-01",
        content="正文",
    )
    return ParsedArticle(**{**base, **kw})


async def test_save_article_inserts():
    row = await _save(_mk())
    assert row.id > 0
    async with TestSession() as s:
        assert len((await s.execute(select(Article))).scalars().all()) == 1


async def test_save_article_updates_same_url_instead_of_duplicating():
    """重复抓同一篇不该产生重复行（url 唯一）。"""
    await _save(_mk(title="旧标题"))
    await _save(_mk(title="新标题"))
    async with TestSession() as s:
        rows = (await s.execute(select(Article))).scalars().all()
    assert len(rows) == 1
    assert rows[0].title == "新标题"
