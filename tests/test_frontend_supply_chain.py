# tests/test_frontend_supply_chain.py
#
# 前端第三方依赖的供应链风险（CODE_REVIEW_99662ca 的 A-3 / A-4）。
#
# 两条问题：
#
# **A-3 浮动大版本 + 无 SRI。** 模板里写的是 `d3@7` 与 `mermaid@11` —— 这是
# 「同一个大版本里的任何发布都收」。CDN 被投毒、或上游发一个行为变化的 patch，
# 站点会在**没有任何一次部署**的情况下拿到新代码。而这两个库都是直接操作 DOM 的，
# 拿到的就是与本站同源的执行权。
#
# **A-4 `securityLevel: "loose"` 配 LLM 输出。** mermaid 的 loose 模式允许图里
# 带 HTML 标签与点击回调。而本站的 mermaid 文本是**LLM 生成的**（`/tools/mermaid`），
# 也就是把一段不可信输入交给一个「允许内嵌 HTML」的渲染器。
"""A-3 / A-4：CDN 依赖必须钉死版本，mermaid 不许用 loose。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app/templates"


def _read(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- A-3 钉版本


def test_cdn_urls_pin_an_exact_version():
    """CDN 链接必须钉到 `x.y.z`，不许写浮动大版本。

    `d3@7` 的语义是「7.x 里当前最新的那个」—— 站点拿到什么代码取决于
    **访问那一刻**上游发布了什么，与本站的任何一次部署都无关。
    这意味着：① CDN 或上游被投毒时，攻击面是自动打开的；
    ② 一个改了行为的 patch 会让页面在无人改动的情况下坏掉，
       而排查时「代码没变」恰恰是最误导人的线索。

    钉死版本后升级变成一次**有意识的、可回滚的**改动。
    """
    offenders: list[str] = []
    for f in sorted(TEMPLATES.glob("*.html")):
        for m in re.finditer(r"https://[^\s\"']*?/npm/([^/\"']+)", f.read_text(encoding="utf-8")):
            spec = m.group(1)  # 形如 d3@7 或 d3@7.9.0
            if "@" not in spec:
                offenders.append(f"{f.name}: {spec}（完全没写版本）")
                continue
            name, _, ver = spec.partition("@")
            if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
                offenders.append(f"{f.name}: {name}@{ver}（不是精确的 x.y.z）")

    assert not offenders, "以下 CDN 引用没有钉死版本：\n  " + "\n  ".join(offenders)


def test_d3_script_tag_has_subresource_integrity():
    """经典 `<script src>` 必须带 `integrity` + `crossorigin`。

    SRI 的作用是：即使 CDN 下发的内容与预期不同，浏览器也会**拒绝执行**。
    这是钉版本之外唯一能挡住「CDN 被投毒」的手段 —— 版本号一样、内容不一样，
    光看 URL 是发现不了的。

    `crossorigin="anonymous"` 不是可选的：没有它，跨域脚本的 integrity 校验
    会被浏览器直接跳过（请求不带 CORS 模式，响应也就不参与校验）。
    """
    html = _read("er.html")
    m = re.search(r'<script[^>]*src="https://[^"]*d3[^"]*"[^>]*>', html)
    assert m, "er.html 里找不到 d3 的 <script src> 标签"
    tag = m.group(0)

    assert "integrity=" in tag, f"d3 的 script 标签缺 integrity：{tag}"
    assert re.search(r'integrity="sha(256|384|512)-[A-Za-z0-9+/=]{40,}"', tag), (
        f"integrity 不是合法的 SRI 格式：{tag}"
    )
    assert 'crossorigin="anonymous"' in tag, (
        f"缺 crossorigin=\"anonymous\" —— 没有它浏览器会跳过 integrity 校验：{tag}"
    )


def test_sri_hash_matches_the_real_npm_artifact():
    """模板里的 SRI 哈希必须与**真实 npm 包**里的文件对得上。

    这条是防止「SRI 写了但是个编出来的值」—— 那种情况下页面会因为校验失败
    而**完全加载不出 d3**，比不写 SRI 更糟，而且现象是页面白屏、极难联想到这里。

    ⚠️ 沙箱访问不到 cdn.jsdelivr.net（实测 HTTP=000），所以这里校验的是
    npm registry 的官方 tarball（jsdelivr 的 /npm/ 路径就是原样转发 npm 包）。
    上线前请在能联网的机器上打开一次 ER 图页面确认脚本确实加载成功。
    """
    import re as _re

    html = _read("er.html")
    m = _re.search(r'integrity="(sha(?:256|384|512)-[A-Za-z0-9+/=]+)"', html)
    assert m, "er.html 里没有 integrity 属性"
    declared = m.group(1)
    algo, _, b64 = declared.partition("-")

    ver = _re.search(r"/npm/d3@(\d+\.\d+\.\d+)", html)
    assert ver, "er.html 里的 d3 没有精确版本号，无从校验"

    import base64
    import hashlib
    import io
    import json
    import tarfile
    import urllib.request

    try:
        meta = json.load(
            urllib.request.urlopen(f"https://registry.npmjs.org/d3/{ver.group(1)}", timeout=25)
        )
        raw = urllib.request.urlopen(meta["dist"]["tarball"], timeout=60).read()
    except Exception as exc:  # 离线环境不该让整组测试红掉
        import pytest

        pytest.skip(f"取不到 npm registry，无法核对 SRI：{exc}")

    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        names = [n for n in tf.getnames() if n.endswith("dist/d3.min.js")]
        assert names, f"d3@{ver.group(1)} 的 tarball 里没有 dist/d3.min.js"
        data = tf.extractfile(names[0]).read()

    expect = f"{algo}-" + base64.b64encode(hashlib.new(algo, data).digest()).decode()
    assert declared == expect, (
        f"模板里的 SRI 与 d3@{ver.group(1)} 的真实文件不符。\n"
        f"  模板写的：{declared}\n  实际应为：{expect}\n"
        "照这样上线，浏览器会拒绝执行 d3，ER 图页面直接白屏。"
    )


# ---------------------------------------------------------------- A-4 mermaid


def test_mermaid_does_not_use_loose_security_level():
    """mermaid 必须用 `strict`，不能是 `loose`。

    `loose` 允许图定义里带 HTML 标签和点击回调（`click nodeId href ...`）。
    而本站的 mermaid 文本是 **LLM 生成的**（`POST /tools/mermaid` 把用户的
    自然语言描述交给大模型产出图定义）—— 等于把一段不可信输入交给一个
    「允许内嵌 HTML 与跳转」的渲染器，在本站同源下执行。

    `strict` 会把标签转义、禁用点击交互，正是这种场景该有的档位。
    """
    html = _read("mermaid.html")
    m = re.search(r"securityLevel\s*:\s*[\"'](\w+)[\"']", html)
    assert m, "mermaid.html 里找不到 securityLevel 配置 —— 默认值同样不安全，必须显式写 strict"
    assert m.group(1) == "strict", (
        f"securityLevel 是 {m.group(1)!r}，必须是 'strict' —— "
        "loose 会让 LLM 产出的图定义里内嵌的 HTML/点击回调在本站同源下执行。"
    )


def test_mermaid_cdn_url_is_pinned_too():
    """mermaid 走的是裸 ESM `import`，挂不上 `integrity` —— 那就更得钉死版本。

    SRI 只对 `<script src>` / `<link>` 这类标签生效，ES module 的 `import`
    语句没有 integrity 属性可写。所以这里唯一能做的就是把版本钉死，
    让「上游发了什么」不再自动影响本站。
    """
    html = _read("mermaid.html")
    m = re.search(r"/npm/mermaid@([\d.]+)", html)
    assert m, "mermaid.html 里找不到 mermaid 的 CDN 引用"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m.group(1)), (
        f"mermaid 版本是 {m.group(1)!r}，必须钉成精确的 x.y.z"
    )
