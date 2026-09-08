# tests/test_frontend_supply_chain.py
#
# 前端第三方依赖的供应链风险（2026-09-06 全仓体检的 A-3 / A-4；报告已归档，见 HANDOVER.md「历次 code review 报告的处置与归档」）。
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


def test_no_template_loads_third_party_js_from_a_cdn():
    """A-3 的最终形态：模板里的第三方 JS 必须逐个登记，不许悄悄多出来。

    原来这条位置上的断言是「d3 的 `<script src>` 必须带 integrity + crossorigin」。
    那个保证在 TD-222 之后被一个**更强**的取代了：d3 由 npm 打进
    `app/static/js/er-page.js`，运行时根本不访问 jsdelivr。

    为什么这比 SRI 更强：SRI 只能保证「拿到的内容没被改」，但代码仍然是
    **运行时**从别人的服务器取的 —— CDN 挂了、被墙了、或域名易主了，页面就白屏，
    而本站的任何一次部署都管不到那台服务器。打进产物后代码随部署走，
    版本与完整性由 package-lock.json 的 integrity 哈希在**安装时**校验。

    ⚠️ **必须同时匹配两种写法**，这是踩过才知道的：
      ① 经典 `<script src="https://...">`；
      ② `<script type="module">` 里的**裸 ESM import**（mermaid 就是这么写的）。
    第一版只写了 ①，于是 mermaid 那条 CDN 引用完全没被看见 —— 而它恰恰是
    当时唯一还在用 CDN 的依赖。只匹配一种写法的「反扫」等于没扫。

    ⚠️ 剩余允许项只有 mermaid（TD-222 记为后续项：压缩后近 2 MB，本轮不打包）。
    这个清单**必须保持只减不增**：任何新增 CDN 依赖都要在这里显式登记，
    从而逼出一次「为什么不能像 d3 一样打进产物」的讨论。
    """
    allowed = ("mermaid@",)  # TD-222 后续项：mermaid 仍走 CDN

    found = []
    for f in sorted(TEMPLATES.glob("*.html")):
        # ⚠️ 先剥掉 HTML 注释再扫：注释里提到某个 CDN 地址不构成加载。
        #    这不是假想 —— 我在 mermaid.html 写过一句解释性注释，里面引用了
        #    「import ... from "https://cdn.jsdelivr.net/..."」这样的示例文本，
        #    结果被本用例当成一条未登记的真实依赖（实测踩过）。
        body = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
        urls = re.findall(r'<script[^>]*\bsrc="(https?://[^"]+)"', body)
        urls += re.findall(r'\bimport\s[^;]*?from\s*"(https?://[^"]+)"', body)
        for u in urls:
            found.append((f.name, u))

    unregistered = [(n, u) for n, u in found if not any(a in u for a in allowed)]
    assert not unregistered, (
        "以下模板在从外部 CDN 加载脚本，且不在允许清单里。"
        "请改成 npm 依赖 + 构建产物（TD-222）；确有理由保留的话，"
        "必须写进上面的 allowed 并说明原因：\n  "
        + "\n  ".join(f"{n}: {u}" for n, u in unregistered)
    )

    # d3 必须**不在**任何 CDN 引用里 —— 它已经被打包了，再出现就是回退
    d3_cdn = [(n, u) for n, u in found if "d3" in u]
    assert not d3_cdn, f"d3 已经打进产物了，不该再从 CDN 取：{d3_cdn}"


def test_d3_is_actually_bundled_into_the_local_artifact():
    """d3 必须**真的**被打进本地产物 —— 不能只是把 CDN 标签删掉了事。

    删掉 d3 的 script 标签很容易，但如果忘了把 d3 加进构建入口，结果就是
    ER 图页面静默白屏，而上面那条测试照样全绿。所以从两个方向夹住：
      ① 源码层面：er-page.js 必须 import d3（否则打包器会把它整个 tree-shake 掉）；
      ② 产物层面：er-page.js 的体积必须明显大于页面自身代码。
         页面渲染代码只有约 86 行（约 2 kB），实测打进 d3 后是 49 kB —— 取 20 kB
         作阈值，既能抓住「d3 没打进去」，又不会因 d3 升级小幅波动而误报。
    """
    src = (ROOT / "app/frontend/er-page.js").read_text(encoding="utf-8")
    assert 'import * as d3 from "d3";' in src, (
        "app/frontend/er-page.js 缺少 d3 的 import 语句 —— "
        "d3 不会被打进产物，ER 图页面会白屏"
    )

    bundle = ROOT / "app/static/js/er-page.js"
    assert bundle.is_file(), "缺构建产物 app/static/js/er-page.js —— 忘了跑 npm run build？"
    size = bundle.stat().st_size
    assert size > 20_000, (
        f"app/static/js/er-page.js 只有 {size} 字节，远小于打进 d3 后应有的量级（实测约 49 kB）。"
        "很可能 d3 没有真的被打进来（比如 vite 入口漏配），页面会白屏。"
    )


def test_d3_version_is_pinned_by_the_lockfile():
    """lockfile 必须把 d3 钉到精确版本并带 integrity 哈希。

    这是原来那条「模板里的 SRI 必须与真实 npm 包对得上」的等价物，只是校验点从
    **运行时**挪到了**安装时**：npm 在 `npm ci` 时会用 lockfile 里的 integrity
    逐个核对 tarball，对不上直接拒绝安装。比 SRI 更早失败，也更容易定位。

    ⚠️ 顺带钉住 `package-lock.json` **必须入库**：它一度被 `.gitignore` 忽略，
    那样 CI 的 `npm ci` 会直接失败，而且每个人装到的依赖树都可能不同。
    """
    import json

    lock_path = ROOT / "package-lock.json"
    assert lock_path.is_file(), (
        "package-lock.json 不在仓库里 —— CI 的 npm ci 会失败，"
        "且失去了「精确版本 + integrity 哈希」这层供应链保证。检查 .gitignore。"
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    entry = lock.get("packages", {}).get("node_modules/d3")
    assert entry, "package-lock.json 里没有 node_modules/d3 条目"

    ver = entry.get("version", "")
    assert re.fullmatch(r"\d+\.\d+\.\d+", ver), f"d3 版本不是精确的 x.y.z：{ver!r}"
    assert entry.get("integrity", "").startswith(("sha512-", "sha256-")), (
        f"d3 条目缺 integrity 哈希：{entry}"
    )

    # 装了 node_modules 就顺手核对实装版本与 lockfile 一致（CI 上一定装）
    installed = ROOT / "node_modules/d3/package.json"
    if installed.is_file():
        actual = json.loads(installed.read_text(encoding="utf-8"))["version"]
        assert actual == ver, f"实装 d3 {actual} 与 lockfile 记录的 {ver} 不一致"


# ---------------------------------------------------------------- A-4 mermaid


def test_mermaid_does_not_use_loose_security_level():
    """mermaid 必须用 `strict`，不能是 `loose`。

    `loose` 允许图定义里带 HTML 标签和点击回调（`click nodeId href ...`）。
    而本站的 mermaid 文本是 **LLM 生成的**（`POST /tools/mermaid` 把用户的
    自然语言描述交给大模型产出图定义）—— 等于把一段不可信输入交给一个
    「允许内嵌 HTML 与跳转」的渲染器，在本站同源下执行。

    `strict` 会把标签转义、禁用点击交互，正是这种场景该有的档位。
    """
    # C2 之后 mermaid 的初始化在 app/frontend/mermaid-page.js（模板里只剩 <script src>）
    js = (ROOT / "app/frontend/mermaid-page.js").read_text(encoding="utf-8")
    m = re.search(r"securityLevel\s*:\s*[\"'](\w+)[\"']", js)
    assert m, "mermaid-page.js 里找不到 securityLevel 配置 —— 默认值同样不安全，必须显式写 strict"
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
    js = (ROOT / "app/frontend/mermaid-page.js").read_text(encoding="utf-8")
    m = re.search(r"/npm/mermaid@([\d.]+)", js)
    assert m, "mermaid-page.js 里找不到 mermaid 的 CDN 引用"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m.group(1)), (
        f"mermaid 版本是 {m.group(1)!r}，必须钉成精确的 x.y.z"
    )
