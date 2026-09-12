"""底层爬虫模块（S4-01-1）：`httpx` 抓页面 + `BeautifulSoup` 解析。

两条设计约束：

1. **绝不把整页 HTML 丢给 LLM**。一篇博客的 HTML 动辄上百 KB，直接喂会撑爆 token 预算，
   而且绝大部分是脚本、样式、导航噪声。所以先把页面压成「DOM 骨架」—— 只留标签名、
   id/class 和截断后的文本，用缩进表示层级。LLM 只需在骨架上指认 CSS 选择器（S4-01-3），
   真正的提取仍然由 BeautifulSoup 按选择器完成，**不让 LLM 直接吐正文**（它会改写原文）。

2. **抓取目标必须校验**。这个模块会让服务器去访问别人给的 URL，不校验就是一个 SSRF 洞：
   `http://169.254.169.254/`（云厂商元数据）、`http://127.0.0.1:8000/`（本机服务）
   都能被读走。所以只允许 http/https，且解析出来的每个地址都必须是公网地址。

礼貌性约束见 `app/tools/politeness.py`（TD-133 已解决）：抓前读并遵守 robots.txt
（含 401/403 视为全站禁止、5xx 视为规则不可知则不抓）、按域遵守 `Crawl-delay`
（无则用默认间隔）、全局并发上限。

**robots.txt 自己的抓取必须过 SSRF 校验，但绝不能再过 `check_allowed`** ——
否则会变成「为了判断能不能抓 robots.txt 而去读 robots.txt」的无限递归。
所以这里拆成 `_request()`（只做 SSRF + 发请求）与 `fetch()`（加礼貌性检查）两层。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString

from . import politeness

TIMEOUT = 15.0
MAX_BYTES = 2_000_000  # 2MB：比这还大的基本不是文章页，别把自己拖死
MAX_NODES = 400  # 骨架最多多少个节点，防止 LLM 输入失控
MAX_TEXT = 80  # 每个文本节点截断到多少字符
# HTTP 头只能 latin-1 编码，UA 里写中文会在发请求时抛 UnicodeEncodeError（已踩过）
USER_AGENT = "codemax-platform/1.0 (+https://codemax.top; content-bootstrap-crawler)"
# 自己跟重定向（见 `_request` 的说明），所以要自己定次数上限，防重定向环
MAX_REDIRECTS = 10
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

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


async def assert_public_url(url: str) -> list[str]:
    """挡住 SSRF：只允许 http/https，且解析出来的**每一个**地址都必须是公网地址。

    用 `is_global` 一次覆盖私网 / 环回 / 链路本地 / 组播 / 保留段（含云厂商元数据
    地址 169.254.169.254）。域名可能解析出多个地址，所以逐个检查，不能只看第一个。
    """
    try:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as e:
        raise CrawlError("URL 主机或端口无效") from e
    if parsed.scheme not in ("http", "https"):
        raise CrawlError(f"只支持 http/https，收到 {parsed.scheme or '(无协议)'!r}")
    host = parsed.hostname
    if not host:
        raise CrawlError("URL 里没有主机名")
    if parsed.username is not None or parsed.password is not None:
        raise CrawlError("URL 不能包含认证信息")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
    except socket.gaierror as e:
        raise CrawlError(f"域名解析失败：{host}（{e}）") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise CrawlError(f"目标不是公网地址：{host} -> {ip}")
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise CrawlError("域名没有可用的公网地址")
    return addresses


class PublicTransport(httpx.AsyncHTTPTransport):
    """Pin each TCP connection to an approved address; retain the original Host and TLS SNI.

    Disable keepalive: pooling by numeric IP must not reuse one hostname's TLS connection
    for another hostname sharing that IP. Environment proxies cannot bypass this transport.
    """
    def __init__(self):
        super().__init__(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = await assert_public_url(str(request.url))
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=addresses[0]),
            headers=request.headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        return await super().handle_async_request(pinned)


async def _request(
    url: str, *, transport: httpx.BaseTransport | None, max_bytes: int
) -> httpx.Response:
    """只做 SSRF 校验 + 发请求，**不做礼貌性检查**。

    这一层的存在理由就是给 robots.txt 的抓取用：robots 请求自己也要防 SSRF，
    但绝不能再触发一次 `check_allowed`。

    ## 为什么自己跟重定向，而不用 `follow_redirects=True`

    交给 httpx 自动跟随的话，SSRF 校验只作用于**最初那个 URL**，而重定向目标不再
    校验 —— 攻击者拿一个自己控制的公网页面 302 到 `http://169.254.169.254/` 就能读走
    云厂商临时凭证（已用 `test_redirect_to_internal_address_is_blocked` 钉住）。
    所以这里刻意关掉自动跟随，改成**每跳一次就重新校验一次**，校验不过就不发那一跳。

    注意断言的是「请求根本没发出去」而不是「没把内容返回给调用方」：请求一旦发出，
    内网服务就已经被打到了，事后丢弃响应体毫无意义。
    """
    await assert_public_url(url)
    async with httpx.AsyncClient(
        transport=transport if transport is not None else PublicTransport(),
        trust_env=False,
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        current = url
        r: httpx.Response | None = None
        for _ in range(MAX_REDIRECTS):
            # 每一跳都 stream=True：响应体先不落内存，等确认了大小再说。
            r = await client.send(client.build_request("GET", current), stream=True)
            location = r.headers.get("location") if r.status_code in _REDIRECT_CODES else None
            if location is None:
                break  # 不是重定向；或 3xx 但没给 Location，按普通响应处理
            await r.aclose()  # 重定向的响应体不要
            current = str(r.url.join(location))  # 相对 Location 要按当前 URL 解析
            await assert_public_url(current)
        else:
            raise CrawlError(f"重定向次数超过上限 {MAX_REDIRECTS}（可能存在重定向环）")

        assert r is not None

        # 服务器自己就声明了超限时，一个字节都不必下。
        declared = r.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            await r.aclose()
            raise CrawlError(f"页面过大：Content-Length 声明 {declared} 字节，超过上限 {max_bytes}")

        # P1-4：**边下边判**。旧写法 `if len(r.content) > max_bytes` 里，`r.content`
        # 这个属性访问本身就已经把整个响应体读进内存了 —— 判断发生在读完之后，
        # 等于没有内存预算：喂一个 10GB 的 URL 进来，会先全下完才说「太大了」。
        # 实测（test_oversized_response_is_aborted_before_fully_downloading）：
        # 1MB 上限 + 4MB 响应体，旧代码拉走 20/20 块，改后只拉走 6/20 块。
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise CrawlError(f"页面过大：已收 {total} 字节仍未结束，超过上限 {max_bytes}")
                chunks.append(chunk)
        finally:
            await r.aclose()

        # 用公开 API 重建一个普通 Response 返回，理由有二：
        # ① `.text` 的字符集解码（含 gbk 等非 UTF-8 页面）继续由 httpx 按
        #    Content-Type 处理，不必自己重写一套编码探测逻辑（已实测 gbk 保留）；
        # ② 不必往 `r._content` 这类私有属性里塞字节。
        # aiter_bytes 已完成内容解压；不可把压缩态的编码与长度带给新 Response，
        # 否则会再次解压。新长度由 httpx 根据解码后的 bytes 生成。
        headers = r.headers.copy()
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(name, None)
        return httpx.Response(
            r.status_code, headers=headers, content=b"".join(chunks), request=r.request
        )


async def fetch(url: str, *, transport: httpx.BaseTransport | None = None, max_bytes: int = MAX_BYTES) -> Page:
    """抓一个页面，抓之前先守礼貌性约束（TD-133）。

    `transport` 只为测试注入 `httpx.MockTransport` 而存在；测试里用字面量公网 IP 当主机名，
    这样 SSRF 校验依然真跑（字面量 IP 的 DNS 解析不需要联网），又不真的出网。

    顺序是有讲究的：**先 robots 再限速**。反过来会为了一个根本不让抓的 URL
    白等一个抓取间隔。
    """

    async def fetch_text(robots_url: str) -> tuple[int, str]:
        r = await _request(
            robots_url, transport=transport, max_bytes=politeness.ROBOTS_MAX_BYTES
        )
        return r.status_code, r.text

    await politeness.check_allowed(url, USER_AGENT, fetch_text)
    state = politeness.state_for(url)
    async with politeness._get_semaphore():  # 全局并发闸
        await politeness.throttle(url, state)
        r = await _request(url, transport=transport, max_bytes=max_bytes)
    if r.status_code != 200:
        raise CrawlError(f"抓取失败：HTTP {r.status_code} {url}")
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
    「标题在哪、正文在哪」。

    **真正的保证是输出有界，不是压缩比。** 本机实测：2.66 MB / 10000 节点的页面
    压出 13 198 字符、恰好 400 行（`MAX_NODES` 封顶），最长一行 86 字符
    （缩进 + 引号 + `MAX_TEXT=80` 截断）。而压缩比本身随页面形态在
    **1/1.5 ~ 1/201** 之间摆动 —— 文本密集的页面几乎压不动，节点密集的页面压得很狠。
    所以别拿「几十分之一」当设计依据；**不管输入多大，喂给 LLM 的体积是有上界的**，
    这才是 token 预算可控的原因。
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()

    lines: list[str] = []
    size = 0
    stack = [(iter((soup.body or soup).children), 0)]
    while stack and len(lines) < min(max_nodes, 1000):
        children, depth = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            continue
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = " ".join(str(child).split())
            if not text:
                continue
            line = "  " * depth + f'"{text[:min(max_text, 200)]}"'
        else:
            ident = child.name[:64]
            if child.get("id") and len(child["id"]) <= 80:
                ident += f"#{child['id']}"
            classes = [c for c in child.get("class", []) if len(c) <= 80][:3]
            if classes:
                ident += "." + ".".join(classes)
            line = "  " * depth + ident
            if depth < 32:
                stack.append((iter(child.children), depth + 1))
        if size + len(line) + 1 > 32000:
            break
        size += len(line) + 1
        lines.append(line)
    return "\n".join(lines)
