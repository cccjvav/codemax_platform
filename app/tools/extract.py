"""LLM 智能解析方案（S4-01-2 / 3 / 4）：模型指认选择器，BeautifulSoup 负责提取。

分工是刻意设计的（TD-135）：

- **LLM 只输出 CSS 选择器，不输出正文。** 让模型直接"提取正文"会改写、删节甚至杜撰原文，
  而内容冷启动要的恰恰是原文；而且每次结果都不一样，没法写回归测试。
- **提取仍由 BeautifulSoup 按选择器做**，所以入库的是页面上的真实字节，可复现、可测。
- 于是"自适应目标网站前端结构变更"（S4-01-4）就成立了：页面改版 → 骨架变了 →
  LLM 重新指认选择器 → 代码一行不用改。

喂给模型的是 `crawler.to_skeleton()` 压缩后的 DOM 骨架，不是整页 HTML（TD-134 附近）。
LLM 客户端可注入，测试用假客户端，绝不真打网络。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Article
from .crawler import fetch, to_skeleton
from .llm import LLMClient, LLMError, default_llm

# 要提取的字段；title 与 content 必需，另外两个允许缺
FIELDS = ("title", "author", "published_at", "content")
REQUIRED = ("title", "content")

SYSTEM_PROMPT = """你是一个网页正文定位器。用户会给你一个网页的 DOM 骨架（缩进表示层级，
形如 `div#id.class`，引号内是该节点的文本摘要），你要判断文章各部分对应的 CSS 选择器。

输出要求（务必严格遵守）：
1. **只输出一个 JSON 对象**，不要任何解释，不要 Markdown 代码围栏
2. 键固定为这四个：title、author、published_at、content
3. 值是 CSS 选择器字符串，例如 "article.post h1"、".meta .author"、".entry-content"
4. 选择器必须**只使用骨架里出现过的**标签名、id、class，不要凭空造
5. content 要指向**正文容器**（能一次选中全部段落的那个节点），不要指向单个 p
6. 判断不了的字段给空字符串 ""，不要猜、不要编
7. 忽略导航、侧边栏、评论区、版权声明、相关推荐 —— 它们不是正文"""

# 模型经常加 ```json 围栏（含忘记收尾的情况），与 llm.py 同一个套路
_FENCE = re.compile(r"```(?:json)?[ \t]*\n(.*?)(?:```|\Z)", re.DOTALL | re.IGNORECASE)


class ExtractError(RuntimeError):
    """解析失败：模型输出不合法、选择器匹配不到、或必需字段为空。"""


@dataclass(frozen=True)
class ParsedArticle:
    """解析结果。`published_at` 保留源站原文（TD-137）。"""

    url: str
    source_site: str
    title: str
    author: str | None
    published_at: str | None
    content: str


async def identify_selectors(skeleton: str, llm: LLMClient = default_llm) -> dict[str, str]:
    """S4-01-3：让 LLM 在 DOM 骨架上指认各字段的选择器。"""
    try:
        reply = await llm.chat(SYSTEM_PROMPT, skeleton)
    except LLMError as e:
        raise ExtractError(str(e)) from e
    return _parse_selectors(reply)


def _parse_selectors(reply: str) -> dict[str, str]:
    m = _FENCE.search(reply)
    raw = (m.group(1) if m else reply).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ExtractError(f"大模型没有返回合法 JSON：{e}；原始输出：{raw[:200]}") from e
    if not isinstance(data, dict):
        raise ExtractError(f"大模型返回的不是 JSON 对象：{raw[:200]}")
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise ExtractError(f"大模型返回了未知字段：{sorted(unknown)}")
    return {k: str(data.get(k) or "").strip() for k in FIELDS}


def extract_fields(html: str, selectors: dict[str, str]) -> dict[str, str]:
    """按选择器提取字段。匹配不到就报错 —— 这是"该重试/该报警"的信号，不能静默留空。"""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for name in FIELDS:
        sel = selectors.get(name, "")
        if not sel:
            out[name] = ""
            continue
        try:
            node = soup.select_one(sel)
        except Exception as e:  # soupsieve 对非法选择器抛异常，转成业务错误
            raise ExtractError(f"选择器 {sel!r}（字段 {name}）不合法：{e}") from e
        if node is None:
            raise ExtractError(f"选择器 {sel!r}（字段 {name}）在页面上匹配不到任何节点")
        if name == "content":
            # 正文按块级换行，保留段落结构；其余字段压成单行
            out[name] = "\n".join(
                line for line in (" ".join(p.get_text().split()) for p in node.find_all(["p", "li", "h2", "h3", "pre"]) or [node]) if line
            ) or " ".join(node.get_text().split())
        else:
            out[name] = " ".join(node.get_text().split())
    return out


async def parse_article(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    llm: LLMClient = default_llm,
) -> ParsedArticle:
    """抓取 → 骨架 → LLM 指认 → 提取（S4-01-2/3/4 串起来）。"""
    page = await fetch(url, transport=transport)
    selectors = await identify_selectors(to_skeleton(page.html), llm=llm)
    fields = extract_fields(page.html, selectors)
    for name in REQUIRED:
        if not fields[name]:
            raise ExtractError(f"必需字段 {name} 为空（选择器：{selectors.get(name)!r}）")
    return ParsedArticle(
        url=page.url,
        source_site=urlparse(page.url).netloc,
        title=fields["title"][:300],
        author=fields["author"][:100] or None,
        published_at=fields["published_at"][:50] or None,
        content=fields["content"],
    )


async def save_article(db: AsyncSession, article: ParsedArticle) -> Article:
    """入库；同一 URL 已存在就更新（重复抓取不产生重复行）。"""
    existing = await db.scalar(select(Article).where(Article.url == article.url))
    row = existing or Article(url=article.url)
    row.title = article.title
    row.author = article.author
    row.published_at = article.published_at
    row.content = article.content
    row.source_site = article.source_site
    if existing is None:
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
