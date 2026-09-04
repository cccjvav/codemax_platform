"""S4-01-1 测试：底层爬虫模块（httpx + BeautifulSoup）。

两块重点：
- **SSRF 防护**：这个模块会让服务器去访问别人给的 URL，必须挡住内网与元数据地址
- **DOM 骨架**：喂给 LLM 的必须是压缩后的结构，不是整页 HTML（体积与信息量都要断言）

抓取一律用 `httpx.MockTransport`，绝不真出网；假主机用**字面量公网 IP**，
这样 `assert_public_url` 依然真跑（字面量 IP 的 DNS 解析不需要联网）。
"""
import socket

import httpx
import pytest

from app.tools.crawler import (
    MAX_BYTES,
    MAX_TEXT,
    USER_AGENT,
    CrawlError,
    assert_public_url,
    fetch,
    to_skeleton,
)
from app.tools.politeness import MAX_CONCURRENCY

FAKE_IP = "93.184.216.34"  # 公网字面量 IP：is_global=True，请求被 MockTransport 截下
BASE = f"http://{FAKE_IP}"

BLOG_HTML = """
<html>
  <head>
    <title>站点标题</title>
    <style>body{color:red}</style>
    <script>var tracking = "噪声";</script>
  </head>
  <body>
    <nav class="top"><a href="/">首页</a><a href="/about">关于</a></nav>
    <!-- 这是注释，不该出现 -->
    <article class="post entry" id="main">
      <h1 class="title">毕业设计的开题报告怎么写</h1>
      <div class="meta">
        <span class="author">张三</span>
        <time datetime="2026-03-01">2026-03-01</time>
      </div>
      <div class="content">
        <p>第一段正文。</p>
        <p>第二段正文。</p>
      </div>
    </article>
    <footer>版权所有</footer>
    <script>console.log("页脚脚本")</script>
  </body>
</html>
"""


def handler(html: str = BLOG_HTML, status: int = 200, captured: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(status, text=html)

    return handle


# ---------------------------------------------------------------- SSRF 防护


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://93.184.216.34/x",
        "gopher://93.184.216.34/x",
        "93.184.216.34/article",  # 没写协议
        "http://127.0.0.1:8000/health",
        "http://localhost:8000/",
        "http://10.0.0.5/admin",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # 云厂商元数据
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://",  # 没有主机名
    ],
)
async def test_fetch_rejects_non_public_targets(url):
    with pytest.raises(CrawlError):
        await fetch(url)


async def test_fetch_allows_public_literal_ip():
    await assert_public_url(f"{BASE}/article")  # 不抛异常即通过


async def test_dns_failure_is_reported(monkeypatch):
    def boom(host, port):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(CrawlError) as e:
        await assert_public_url("http://no-such-host.example/article")
    assert "域名解析失败" in str(e.value)


async def test_every_resolved_address_is_checked(monkeypatch):
    """域名解析出多个地址时逐个检查：一个公网 + 一个内网也必须拒（DNS 指向内网的绕过手法）。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.9", port)),
        ],
    )
    with pytest.raises(CrawlError) as e:
        await assert_public_url("http://dual.example/article")
    assert "10.0.0.9" in str(e.value)


# ---------------------------------------------------------------- 抓取


async def test_fetch_returns_page_and_sets_user_agent():
    seen: list = []
    page = await fetch(f"{BASE}/article", transport=httpx.MockTransport(handler(captured=seen)))
    assert page.status == 200
    assert "毕业设计" in page.html
    assert page.url == f"{BASE}/article"
    assert seen[0].headers["user-agent"] == USER_AGENT


async def test_fetch_raises_on_http_error():
    with pytest.raises(CrawlError) as e:
        await fetch(f"{BASE}/gone", transport=httpx.MockTransport(handler(status=404)))
    assert "404" in str(e.value)


async def test_fetch_enforces_size_limit():
    big = "x" * 5000
    with pytest.raises(CrawlError) as e:
        await fetch(f"{BASE}/big", transport=httpx.MockTransport(handler(html=big)), max_bytes=1000)
    assert "页面过大" in str(e.value)


async def test_max_bytes_budget_is_pinned_at_2mb():
    """`MAX_BYTES` 的**具体数值**必须被钉住 —— 现有两条测试都抓不到它被改。

    本条由一个**存活的变异体**换来（写第 7 课时实测）：把 `MAX_BYTES` 从
    2_000_000 改成 20_000_000，全量测试一条不红（当时套件是 417 条，加本条后 418）。原因是——

    - `test_fetch_enforces_size_limit` 传 `max_bytes=1000` **注入小值**，测的是
      「闸门存在且生效」，与常量取值无关（这也是对的：真造 2MB 数据太慢）；
    - `test_dynamic_crawl` 造 `MAX_BYTES + 1` 的页面，是**相对断言** ——
      常量取任何值它都通过。

    于是「2MB」这个数就成了没人看守的策略值。它不是 arbitrary 的：这是
    **单页内存预算**，配合 `MAX_CONCURRENCY=4` 决定最坏情况下抓取占用多少内存
    （4 × 2MB ≈ 8MB）。放大 10 倍就是 80MB，而测试一句话都不会说。

    **相对断言抓不住常量本身的改动** —— 需要钉住的具体数值，就得写具体数值。
    """
    assert MAX_BYTES == 2_000_000, (
        f"MAX_BYTES 被改成 {MAX_BYTES} 了。这是单页内存预算，配合 "
        f"MAX_CONCURRENCY={MAX_CONCURRENCY} 决定抓取的最坏内存占用；"
        "要改请连同这条断言一起改，并在 TECH_DECISIONS.md 记一条 TD。"
    )


async def test_fetch_follows_redirect():
    def redirect_then_ok(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": f"{BASE}/new"})
        return httpx.Response(200, text=BLOG_HTML)

    page = await fetch(f"{BASE}/old", transport=httpx.MockTransport(redirect_then_ok))
    assert page.url == f"{BASE}/new"
    assert "毕业设计" in page.html


# ---------------------------------------------------------------- DOM 骨架


def test_skeleton_keeps_structure_and_drops_noise():
    s = to_skeleton(BLOG_HTML)
    # 结构与标识保留
    assert "article#main.post.entry" in s
    assert "h1.title" in s
    assert "span.author" in s
    assert '"毕业设计的开题报告怎么写"' in s
    assert '"张三"' in s
    # 噪声剥掉
    for noise in ("script", "style", "nav", "footer", "tracking", "页脚脚本", "版权所有", "这是注释"):
        assert noise not in s, f"{noise} 不该出现在骨架里"


def test_skeleton_uses_indentation_for_hierarchy():
    s = to_skeleton(BLOG_HTML)
    lines = s.splitlines()
    article = next(i for i, ln in enumerate(lines) if ln.startswith("article"))
    h1 = next(i for i, ln in enumerate(lines) if "h1.title" in ln)
    title = next(i for i, ln in enumerate(lines) if "毕业设计的开题报告" in ln)
    assert lines[article] == "article#main.post.entry"
    assert lines[h1].startswith("  h1.title") and not lines[h1].startswith("    ")
    assert lines[title].startswith("    "), "标题文本应比 h1 深一层"


def test_skeleton_truncates_long_text():
    html = "<body><p>" + "很长的正文" * 100 + "</p></body>"
    s = to_skeleton(html)
    text_line = next(ln for ln in s.splitlines() if ln.strip().startswith('"'))
    assert len(text_line.strip().strip('"')) <= MAX_TEXT


def test_skeleton_respects_node_cap():
    html = "<body>" + "<div><span>x</span></div>" * 200 + "</body>"
    assert len(to_skeleton(html, max_nodes=10).splitlines()) == 10
    assert len(to_skeleton(html).splitlines()) > 10


def test_skeleton_is_much_smaller_than_raw_html():
    """骨架的意义就是压缩：塞满噪声的真实页面，骨架应当远小于原文。"""
    noisy = BLOG_HTML.replace(
        "<script>var tracking = \"噪声\";</script>",
        "<script>" + "var a=1;" * 2000 + "</script>",
    )
    s = to_skeleton(noisy)
    assert len(s) < len(noisy) // 10
    assert '"毕业设计的开题报告怎么写"' in s, "压缩不能把正文结构也压掉"


def test_skeleton_handles_html_without_body():
    assert "p" in to_skeleton("<p>片段</p>")


def test_skeleton_limits_classes_to_three():
    s = to_skeleton('<body><div class="a b c d e">x</div></body>')
    assert "div.a.b.c" in s
    assert "div.a.b.c.d" not in s
