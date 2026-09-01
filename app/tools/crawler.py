"""底层爬虫模块（S4-01-1）：`httpx` 抓页面 + `BeautifulSoup` 解析。

两条设计约束：

1. **绝不把整页 HTML 丢给 LLM**。一篇博客的 HTML 动辄上百 KB，直接喂会撑爆 token 预算，
   而且绝大部分是脚本、样式、导航噪声。所以先把页面压成「DOM 骨架」—— 只留标签名、
   id/class 和截断后的文本，用缩进表示层级。LLM 只需在骨架上指认 CSS 选择器（S4-01-3），
   真正的提取仍然由 BeautifulSoup 按选择器完成，**不让 LLM 直接吐正文**（它会改写原文）。

2. **抓取目标必须校验**。这个模块会让服务器去访问别人给的 URL，不校验就是一个 SSRF 洞：
   `http://169.254.169.254/`（云厂商元数据）、`http://127.0.0.1:8000/`（本机服务）
   都能被读走。所以只允许 http/https，且解析出来的每个地址都必须是公网地址。

礼貌性约束（robots.txt、抓取间隔、并发限速）目前**没有做**，见 TECH_DECISIONS.md TD-133。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString

TIMEOUT = 15.0
MAX_BYTES = 2_000_000  # 2MB：比这还大的基本不是文章页，别把自己拖死
MAX_NODES = 400  # 骨架最多多少个节点，防止 LLM 输入失控
MAX_TEXT = 80  # 每个文本节点截断到多少字符
# HTTP 头只能 latin-1 编码，UA 里写中文会在发请求时抛 UnicodeEncodeError（已踩过）
USER_AGENT = "codemax-platform/1.0 (+https://codemax.top; content-bootstrap-crawler)"

# 这些标签对"识别文章结构"没有帮助，先丢掉：既减体积也减噪声
DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "nav",
    "footer",
    "header",
    "form",
    "aside",
)


class CrawlError(Exception):
    """目标不合法或抓取失败。"""


@dataclass(frozen=True)
class Page:
    """抓回来的页面。`url` 是跟随重定向之后的最终地址。"""

    url: str
    status: int
    html: str


async def assert_public_url(url: str) -> None:
    """挡住 SSRF：只允许 http/https，且解析出来的**每一个**地址都必须是公网地址。

    用 `is_global` 一次覆盖私网 / 环回 / 链路本地 / 组播 / 保留段（含云厂商元数据
    地址 169.254.169.254）。域名可能解析出多个地址，所以逐个检查，不能只看第一个。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise CrawlError(f"只支持 http/https，收到 {parsed.scheme or '(无协议)'!r}")
    host = parsed.hostname
    if not host:
        raise CrawlError("URL 里没有主机名")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
    except socket.gaierror as e:
        raise CrawlError(f"域名解析失败：{host}（{e}）") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise CrawlError(f"目标不是公网地址：{host} -> {ip}")


async def fetch(url: str, *, transport: httpx.BaseTransport | None = None, max_bytes: int = MAX_BYTES) -> Page:
    """抓一个页面。

    `transport` 只为测试注入 `httpx.MockTransport` 而存在；测试里用字面量公网 IP 当主机名，
    这样 SSRF 校验依然真跑（字面量 IP 的 DNS 解析不需要联网），又不真的出网。
    """
    await assert_public_url(url)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        r = await client.get(url)
    if r.status_code != 200:
        raise CrawlError(f"抓取失败：HTTP {r.status_code} {url}")
    if len(r.content) > max_bytes:
        raise CrawlError(f"页面过大：{len(r.content)} 字节，超过上限 {max_bytes}")
    return Page(url=str(r.url), status=r.status_code, html=r.text)


def to_skeleton(html: str, *, max_nodes: int = MAX_NODES, max_text: int = MAX_TEXT) -> str:
    """把 HTML 压成给 LLM 看的 DOM 骨架（S4-01-2/3 的输入）。

    输出形如::

        article.post
          h1
            "文章标题"
          div.meta
            span.author
              "作者名"

    只保留标签名、id、前 3 个 class 和截断后的文本 —— 这些信息足够让 LLM 指认
    「标题在哪、正文在哪」，而体积通常只有原 HTML 的几十分之一。
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()

    lines: list[str] = []

    def walk(node, depth: int) -> None:
        for child in node.children:
            if len(lines) >= max_nodes:
                return
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                text = " ".join(str(child).split())  # 折叠空白，去掉排版缩进
                if text:
                    lines.append("  " * depth + f'"{text[:max_text]}"')
                continue
            ident = child.name
            if child.get("id"):
                ident += f"#{child['id']}"
            classes = child.get("class") or []
            if classes:
                ident += "." + ".".join(classes[:3])
            lines.append("  " * depth + ident)
            walk(child, depth + 1)

    walk(soup.body or soup, 0)
    return "\n".join(lines)
