"""文档站数据构建脚本 —— 从代码里**真实提取**站点所需的全部结构化数据。

为什么需要它：文档站里的模块依赖图、路由地图、符号索引必须是**扫代码扫出来的**，
不能手画。手画的图第一天就过期，而这份数据每次重跑都与代码一致。

用法：
    python scripts/build_docs_site.py              # 默认：出数据 + 渲染整站（**需要 mistune**）
    python scripts/build_docs_site.py --data-only  # 只出 docs/site/data/*.json，不需要 mistune

依赖：**默认那条要 mistune**（纯 Python、零依赖，`pip install mistune`）。
只有 `--data-only` 不需要任何额外依赖 —— 它只用标准库的 ast / json 扫代码。
本脚本不接入 pytest —— 它是文档工具，不是每次改动的必经检查（由 TD-201 记录代价）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
DATA = SITE / "data"

EXCLUDE_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".claude"}
CODE_EXT = {".py", ".js", ".mjs", ".sql", ".html", ".yml", ".yaml", ".toml", ".ini", ".json", ".css", ".xml"}


# ---------------------------------------------------------------- 模块与依赖


def module_name(py: Path) -> str:
    """把文件路径转成模块名：app/routers/tools.py -> app.routers.tools"""
    rel = py.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(mod: str) -> str:
    return mod.rpartition(".")[0]


def collect_python() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*.py")):
        if set(p.parts) & EXCLUDE_DIRS:
            continue
        out.append(p)
    return out


def build_import_graph() -> dict:
    """提取内部依赖边。

    关键：**必须解析相对导入**。本仓库大量使用 `from ..models import X`、`from .config import Y`，
    只看 `n.module.startswith("app")` 会漏掉绝大多数边（实测只抓到 5 条，真实远多于此）。
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    known: set[str] = set()

    for p in collect_python():
        mod = module_name(p)
        nodes[mod] = {
            "id": mod,
            "path": str(p.relative_to(ROOT)).replace("\\", "/"),
            "lines": len(p.read_text(encoding="utf-8").splitlines()),
            "layer": _layer_of(p),
        }
        known.add(mod)

    for p in collect_python():
        src_mod = module_name(p)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            target = None
            if isinstance(n, ast.ImportFrom):
                if n.level:  # 相对导入
                    base = src_mod
                    # level=1 表示当前包；模块本身不是包时要先退一层
                    if p.name != "__init__.py":
                        base = package_of(base)
                    for _ in range(n.level - 1):
                        base = package_of(base)
                    target = f"{base}.{n.module}" if n.module else base
                elif n.module and n.module.split(".")[0] in ("app", "main"):
                    target = n.module
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.split(".")[0] in ("app", "main"):
                        target = a.name
            if target and target in known and target != src_mod:
                edges.append({"from": src_mod, "to": target, "line": getattr(n, "lineno", 0)})

    # 去重（同一对模块可能 import 多次）
    seen, uniq = set(), []
    for e in edges:
        k = (e["from"], e["to"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return {"nodes": list(nodes.values()), "edges": uniq}


def _layer_of(p: Path) -> str:
    s = str(p.relative_to(ROOT)).replace("\\", "/")
    if s.startswith("app/routers/"):
        return "API 层"
    if s.startswith("app/tools/"):
        return "业务逻辑层"
    if s.startswith("app/templates/") or s.startswith("app/static/"):
        return "前端"
    if s.startswith("tests/"):
        return "测试"
    if s.startswith("app/"):
        return "基础设施"
    if s.startswith("database init/"):
        return "数据层"
    return "其它"


# ---------------------------------------------------------------- 路由表


def build_routes() -> list[dict]:
    """用 AST 提取路由，连同鉴权与限流信息。

    为什么用 AST 而不是正则：鉴权有**两种写法**，正则只能抓到第一种 ——
      ① 装饰器里：`@router.post("/x", dependencies=[Depends(require_admin)])`
      ② 函数签名里：`async def f(user: User = Depends(get_current_user), ...)`
    实测只用正则会得出「需鉴权 0 条」这种明显错误的结论。
    """
    out = []
    for p in sorted((ROOT / "app" / "routers").glob("*.py")):
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        m0 = re.search(r'APIRouter\(\s*prefix="([^"]*)"', src)
        prefix = m0.group(1) if m0 else ""
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue

        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in fn.decorator_list:
                # 形如 router.get("/path", ...)
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                method = dec.func.attr
                if method not in ("get", "post", "put", "delete", "patch"):
                    continue
                if not (dec.args and isinstance(dec.args[0], ast.Constant)):
                    continue
                path = dec.args[0].value

                # 鉴权 / 限流：装饰器 + 函数签名，两处都要看。
                # ⚠️ 不能用 ast.get_source_segment(src, fn.args) —— ast.arguments 节点
                #    实测返回 None。改成按行区间取「装饰器起 → 函数体第一句」，签名一定在内。
                start = min([d.lineno for d in fn.decorator_list] + [fn.lineno])
                end = fn.body[0].lineno if fn.body else fn.lineno + 1
                blob = "\n".join(lines[start - 1 : max(end - 1, start)])
                auth = "require_admin" in blob
                user = "get_current_user" in blob
                rl = "rate_limit" in blob

                line = dec.lineno
                out.append(
                    {
                        "method": method.upper(),
                        "path": prefix + path,
                        "file": f"app/routers/{p.name}",
                        "line": line,
                        "handler": fn.name,
                        "auth": auth or user,
                        "admin": auth,
                        "rate_limit": rl,
                        "doc": f"app/routers/README.md#{_file_anchor(p.name)}",
                        "snippet": lines[line - 1].strip() if line - 1 < len(lines) else "",
                    }
                )
    # ---- 页面路由（GET / 与 /tools/*）：注册方式不同，必须单独处理 ----
    #
    # app/routers/site.py:39-40 是这样注册的：
    #     for _tool in PAGES:
    #         router.add_api_route(_tool.path, _page_view(_tool), methods=["GET"], ...)
    #
    # 路径来自**变量**（`_tool.path`）而不是字面量，所以上面那套「扫 @router.get 装饰器
    # 取 dec.args[0]」的逻辑一条都抓不到 —— 实测漏了 4 条（GET / 与 3 个 /tools/*）。
    #
    # 这里改成去 app/site.py 里取 `Tool(... path="..." ...)` 的字面量。
    # 仍然只用标准库 ast：刻意**不** import app，否则会破坏本脚本
    # 「不需要数据库 / .env / 25 个依赖」的设计承诺（见 docs/site/README.md）。
    site_src = (ROOT / "app" / "site.py").read_text(encoding="utf-8")
    for call in ast.walk(ast.parse(site_src)):
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "Tool"):
            continue
        kws = {k.arg: k.value for k in call.keywords}
        pv, kv = kws.get("path"), kws.get("key")
        if not (isinstance(pv, ast.Constant) and isinstance(kv, ast.Constant)):
            continue
        # add_api_route 那行是所有页面共用的，行号取 Tool 定义处更有定位价值。
        out.append(
            {
                "method": "GET",
                "path": pv.value,
                "file": "app/site.py",
                "line": call.lineno,
                "handler": f"_page_view({kv.value})",
                "auth": False,  # 页面公开可访问（SEO 引流），见 TD-141
                "admin": False,
                "rate_limit": False,
                "doc": "app/README.md",
                "snippet": f'Tool(key="{kv.value}", path="{pv.value}", ...)  ← PAGES 清单',
            }
        )

    out.sort(key=lambda r: (r["path"], r["method"]))
    return out


def _file_anchor(filename: str) -> str:
    """复刻 GitHub 的标题→锚点算法（子 README 的小节标题形如 `### 📄 文件名：`x.py`（12 行）`）。"""
    import unicodedata

    t = re.sub(r"`", "", f"📄 文件名：{filename}").strip().lower()
    keep = []
    for ch in t:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N") or cat in ("Mn", "Mc", "Me", "Pc") or ch in "- ":
            keep.append(ch)
    return "".join(keep).replace(" ", "-")


# ---------------------------------------------------------------- 符号表


def build_symbols() -> list[dict]:
    """每个函数/类 → 源码位置 + 所属模块 + 对应文档锚点。"""
    out = []
    for p in collect_python():
        if str(p.relative_to(ROOT)).startswith("tests/"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        doc = _doc_for(rel)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            out.append(
                {
                    "name": n.name,
                    "kind": "class" if isinstance(n, ast.ClassDef) else "def",
                    "module": module_name(p),
                    "file": rel,
                    "line": n.lineno,
                    "end": getattr(n, "end_lineno", n.lineno),
                    "doc": doc,
                    "private": n.name.startswith("_"),
                }
            )
    out.sort(key=lambda s: (s["file"], s["line"]))
    return out


_DOC_MAP = [
    ("app/tools/", "app/tools/README.md"),
    ("app/routers/", "app/routers/README.md"),
    ("app/templates/", "app/templates/README.md"),
    ("app/static/", "app/static/README.md"),
    ("database init/", "database init/README.md"),
    ("app/", "app/README.md"),
    ("scripts/", "scripts/README.md"),
    (".github/", ".github/workflows/README.md"),
]


def _doc_for(rel: str) -> str | None:
    for pre, doc in _DOC_MAP:
        if rel.startswith(pre):
            return doc
    if "/" not in rel:  # 仓库根文件
        return "docs/ROOT_FILES.md"
    return None


# ---------------------------------------------------------------- 文档清单

# 分组顺序即侧边栏顺序
DOC_GROUPS = [
    ("入口", ["总览.md", "DOCUMENTATION_SUMMARY.md", "docs/site/README.md"]),
    ("项目", ["README.md", "AGENTS.md", "HANDOVER.md", "ROADMAP.md", "TECH_DECISIONS.md"]),
    ("架构讲解", ["docs/ARCHITECTURE_GUIDE.md", "docs/DEPLOY.md", "docs/WINDOWS_LOCAL_RUN.md", "docs/ROOT_FILES.md"]),
    (
        "代码级说明书",
        [
            "app/README.md",
            "app/routers/README.md",
            "app/tools/README.md",
            "app/templates/README.md",
            "app/static/README.md",
            "database init/README.md",
            "tests/README.md",
            "scripts/README.md",
            ".github/workflows/README.md",
        ],
    ),
]


def build_manifest() -> list[dict]:
    out = []
    for group, paths in DOC_GROUPS:
        for rel in paths:
            p = ROOT / rel
            if not p.exists():
                print(f"  ⚠️  清单里的文件不存在：{rel}", file=sys.stderr)
                continue
            text = p.read_text(encoding="utf-8")
            title = rel
            m = re.search(r"^#\s+(.+)$", text, flags=re.M)
            if m:
                title = m.group(1).strip()
            out.append(
                {
                    "path": rel,
                    "group": group,
                    "title": title,
                    "lines": len(text.splitlines()),
                    "chars": len(text),
                    "headings": len(re.findall(r"^#{2,4}\s", text, flags=re.M)),
                    "codeblocks": len(re.findall(r"^```\S", text, flags=re.M)),
                }
            )
    return out


# ---------------------------------------------------------------- 静态渲染


def slug(rel: str) -> str:
    """把仓库相对路径变成安全的文件名。

    ⚠️ 必须保留中文：本仓库有 `总览.md`，用 [^A-Za-z0-9._-] 会把汉字全删成空，
    产出 `__.md.html` 这种既难看又互相撞车的名字。
    """
    return re.sub(r"[^\w.-]", "_", rel, flags=re.UNICODE)


# ================================================================ 静态渲染
#
# 为什么是「服务端预渲染 + 零 CDN」而不是浏览器端渲染：
#   · 本沙箱实测 cdn.jsdelivr.net **不可达**（HTTP 000），浏览器端方案在这里根本跑不起来，
#     也就无从验证 —— 而「无法验证的东西不该交付」。
#   · mermaid 的 ESM 构建要带 206 个 chunk / 17 MB，不可能塞进仓库。
#   · highlight.js 的 npm 包里没有现成的浏览器包（只有 CJS/ESM 源）。
# 所以改成：markdown 用 mistune 服务端渲染，依赖图用 Python 直接生成 SVG，
# 交互（搜索/过滤/高亮）用几十行原生 JS。**双击 index.html 即可打开，完全离线。**


def _md():
    import mistune

    return mistune.create_markdown(escape=False, plugins=["table", "strikethrough", "url"])


def render_site(payload: dict) -> dict:
    """生成完整的静态站点到 docs/site/。返回统计信息。"""
    md = _md()
    manifest, graph, routes, symbols = (
        payload["manifest"], payload["graph"], payload["routes"], payload["symbols"]
    )

    # 文档路径 → 站点内相对文件名
    doc_href = {d["path"]: f"d/{slug(d['path'])}.html" for d in manifest}
    src_pages = _source_page_list()
    src_href = {p: f"s/{slug(p)}.html" for p in src_pages}

    nav = _render_nav(manifest, doc_href)
    stats = {"docs": 0, "sources": 0, "pages": 0}

    # ---- 每份文档一页
    for item in manifest:
        raw = (ROOT / item["path"]).read_text(encoding="utf-8")
        body = md(raw)
        body = _postprocess(body, item["path"], doc_href, src_href)
        page = _shell(
            title=item["title"],
            active=item["path"],
            nav=nav,
            main=f"""<div class="breadcrumb"><a href="../index.html">首页</a> · <code>{esc(item['path'])}</code></div>
<div class="doc-meta">
  <span>行数 <b>{item['lines']}</b></span><span>字符 <b>{item['chars']}</b></span>
  <span>小节 <b>{item['headings']}</b></span><span>代码块 <b>{item['codeblocks']}</b></span>
  <span>分组 <b>{esc(item['group'])}</b></span></div>
<article class="markdown-body">{body}</article>""",
            toc=_toc_of(raw),
            depth=1,
        )
        _write(SITE / doc_href[item["path"]], page)
        stats["docs"] += 1

    # ---- 每个源码文件一页（带行号）
    for rel in src_pages:
        text = (ROOT / rel).read_text(encoding="utf-8")
        lines = text.splitlines()
        rows = "\n".join(
            f'<span class="row" id="L{i}"><span class="ln">{i}</span>{esc(ln) or " "}</span>'
            for i, ln in enumerate(lines, 1)
        )
        sym = [s for s in symbols if s["file"] == rel]
        doc = _doc_for(rel)
        page = _shell(
            title=rel,
            active=None,
            nav=nav,
            main=f"""<div class="breadcrumb"><a href="../index.html">首页</a> · 源码</div>
<div class="src-head">
  <code>{esc(rel)}</code><span class="muted">{len(lines)} 行 · {len(sym)} 个函数/类</span>
  <span style="flex:1"></span>
  {f'<a class="mini" href="../{doc_href[doc]}">该模块的说明书</a>' if doc in doc_href else ''}
</div>
<div class="symbar">{' '.join(f'<a href="#L{s["line"]}"><code>{esc(s["name"])}</code></a>' for s in sym[:60])}</div>
<pre class="source"><code>{rows}</code></pre>""",
            toc="",
            depth=1,
        )
        _write(SITE / src_href[rel], page)
        stats["sources"] += 1

    # ---- 首页
    home = _shell(
        title="文档站首页",
        active=manifest[0]["path"] if manifest else None,
        nav=nav,
        main=_render_home(payload, doc_href),
        toc="",
    )
    _write(SITE / "index.html", home)
    stats["pages"] += 1

    # ---- 三个可视化页
    for name, title, body in [
        ("graph.html", "模块依赖图", _render_graph_page(graph, doc_href, src_href)),
        ("routes.html", "路由地图", _render_routes_page(routes)),
        ("symbols.html", "符号索引", _render_symbols_page(symbols, doc_href, src_href)),
    ]:
        _write(SITE / name, _shell(title=title, active=None, nav=nav, main=body, toc=""))
        stats["pages"] += 1

    # ---- 搜索索引（原生 JS 用，不依赖任何库）
    idx = []
    for item in manifest:
        raw = (ROOT / item["path"]).read_text(encoding="utf-8")
        # 只存标题与行首，控制体积；正文搜索靠浏览器 Ctrl+F
        heads = [
            {"t": m.group(2).strip(), "l": raw[: m.start()].count("\n") + 1, "i": _anchor(m.group(2))}
            for m in re.finditer(r"^(#{2,4})\s+(.+)$", raw, flags=re.M)
        ]
        idx.append({"p": item["path"], "h": doc_href[item["path"]], "t": item["title"], "s": heads})
    (SITE / "data" / "search.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")

    return stats


def _source_page_list() -> list[str]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or set(p.parts) & EXCLUDE_DIRS:
            continue
        if p.suffix.lower() not in (".py", ".js", ".mjs", ".sql", ".html", ".yml", ".toml", ".ini"):
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith("docs/site/"):
            continue
        out.append(rel)
    return out


def _anchor(text: str) -> str:
    import unicodedata

    t = re.sub(r"`", "", text).strip().lower()
    keep = [c for c in t if unicodedata.category(c)[0] in ("L", "N")
            or unicodedata.category(c) in ("Mn", "Mc", "Me", "Pc") or c in "- "]
    return "".join(keep).replace(" ", "-")


def _toc_of(raw: str) -> str:
    out = []
    for m in re.finditer(r"^(#{2,4})\s+(.+)$", raw, flags=re.M):
        lvl = len(m.group(1))
        out.append(f'<a class="lvl{lvl}" href="#{_anchor(m.group(2))}">{esc(m.group(2).strip())}</a>')
    return "\n".join(out) or '<span class="muted">本文无小节</span>'


def _mark_mermaid(html: str) -> str:
    """给 mermaid 代码块加一条说明。

    本站**刻意不加载 mermaid.js**：它的 ESM 构建要带 206 个 chunk / 共 17 MB，
    不可能进仓库，而沙箱实测 CDN 又不可达。所以图以源码形式展示 ——
    在 GitHub 上打开同名 .md 会看到渲染后的图，且这些图在文档里都另有纯文本缩进版。
    """
    note = ('<p class="mermaid-note">📊 下面是 Mermaid 图源码。本站离线运行、不加载 mermaid.js，'
            '故以源码展示；在 GitHub 上打开对应 <code>.md</code> 可看到渲染后的图，'
            '文档内通常另附纯文本缩进版。</p>')
    return re.sub(r'(<pre><code class="language-mermaid">)', note + r"\1", html)


def _inject_heading_ids(html: str) -> str:
    """给 h1~h4 注入 id。

    mistune 默认**不给标题加 id**（实测 `<h2 id=...>` 一个都没有），
    不注入的话右侧目录与跨文档锚点会全部失效。
    id 用与仓库里手写锚点相同的算法，保证 `app/README.md#3-执行逻辑流` 这类链接仍然可达。
    """
    seen: dict[str, int] = {}

    def repl(m):
        tag, attrs, inner = m.group(1), m.group(2), m.group(3)
        if "id=" in attrs:
            return m.group(0)
        base = _anchor(re.sub(r"<[^>]+>", "", inner))
        seen[base] = seen.get(base, 0) + 1
        hid = base if seen[base] == 1 else f"{base}-{seen[base] - 1}"
        return f'<{tag}{attrs} id="{hid}">{inner}</{tag}>'

    return re.sub(r"<(h[1-4])([^>]*)>(.*?)</\1>", repl, html, flags=re.S)


def _postprocess(html: str, here: str, doc_href: dict, src_href: dict) -> str:
    """把渲染结果里的链接改写成站内相对路径，并给标题注入 id。"""
    html = _inject_heading_ids(html)
    html = _mark_mermaid(html)
    # 所有文档页都输出到 docs/site/d/ 下，所以站点级链接一律要先退回一层。
    # （早先按 here 里的斜杠数算深度是错的：源文件在仓库根时算出 0，链接就少一层 ../）
    base = "../"

    # ① .md 链接 → 文档页
    def md_link(m):
        target, anchor = m.group(1), m.group(2) or ""
        # mistune 的 url 插件会把中文与空格百分号编码（总览.md → %E6%80%BB%E8%A7%88.md、
        # database init → database%20init），不解码就查不到 doc_href。
        from urllib.parse import unquote
        norm = _normalize(here, unquote(target))
        if norm in doc_href:
            return f'href="{base}{doc_href[norm]}{anchor}"'
        return f'href="{m.group(0)[6:]}"""'[:-1]

    html = re.sub(r'href="([^"#]+?\.md)(#[^"]*)?"', md_link, html)

    # ② 代码位置引用 file.py:12 → 源码页对应行
    known = set(src_href)

    def src_ref(m):
        name, line = m.group(1), m.group(2)
        rel = name if name in known else next((k for k in known if k.endswith("/" + name) or k == name), None)
        if not rel:
            return m.group(0)
        return f'<a class="srcref" href="{base}{src_href[rel]}#L{line}">{esc(m.group(0))}</a>'

    html = re.sub(
        r"(?<![\w/>])([A-Za-z0-9_.\-/]+\.(?:py|js|mjs|sql|html|yml|toml|ini)):(\d+)(?:-L?\d+)?",
        src_ref, html,
    )
    return html


def _normalize(here: str, to: str) -> str:
    """把文档里的相对链接解析成仓库相对路径。"""
    to = to[2:] if to.startswith("./") else to
    if to.startswith("/"):
        return to[1:]
    parts = here.split("/")[:-1]
    for seg in to.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _rebase_nav(nav: str, up: str) -> str:
    """把侧栏里的相对链接按页面深度重定位。"""
    if not up:
        return nav
    return re.sub(r'href="(?!(?:https?:|mailto:|#))', f'href="{up}', nav)


def _render_nav(manifest: list[dict], doc_href: dict) -> str:
    groups: dict[str, list] = {}
    for d in manifest:
        groups.setdefault(d["group"], []).append(d)
    out = []
    for g, items in groups.items():
        out.append(f'<div class="nav-group"><h2>{esc(g)}</h2>')
        for d in items:
            label = d["path"].replace("/README.md", "/").replace("docs/", "").replace("README.md", "README")
            out.append(f'<a href="{doc_href[d["path"]]}" data-doc="{esc(d["path"])}">'
                       f'<span>{esc(label)}</span><span class="lines">{d["lines"]}</span></a>')
        out.append("</div>")
    out.append(
        '<div class="nav-group nav-extra"><h2>可视化</h2>'
        '<a href="graph.html">🕸 模块依赖图</a>'
        '<a href="routes.html">🧭 路由地图</a>'
        '<a href="symbols.html">🔤 符号索引</a></div>'
    )
    return "\n".join(out)


def _shell(*, title: str, active: str | None, nav: str, main: str, toc: str, depth: int = 0) -> str:
    # 文档页与源码页在 d/ 与 s/ 子目录下，所有站点级资源都要加 ../ 前缀，
    # 否则从子目录打开会找不到 style.css / site.js / index.html。
    up = "../" * depth
    css_href = up + "style.css"
    js_href = up + "site.js"
    nav_html = _rebase_nav(nav, up)
    if active:
        nav_html = nav_html.replace(f'data-doc="{esc(active)}"', f'data-doc="{esc(active)}" class="active"', 1)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · codemax_platform 文档站</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="brand">
      <h1><a href="{up}index.html">codemax_platform</a></h1>
      <p>架构导读 + 代码级说明书</p>
    </div>
    <div class="search"><input type="text" id="q" placeholder="搜索标题与小节…" autocomplete="off">
      <div class="hits" id="hits"></div></div>
    <nav>{nav_html}</nav>
  </aside>
  <main class="main">{main}</main>
  <aside class="toc"><h2>本页目录</h2><div id="toc-list">{toc}</div></aside>
</div>
<script src="{js_href}"></script>
</body>
</html>
"""


def _render_home(payload: dict, doc_href: dict) -> str:
    m = payload["meta"]
    cards = []
    for g, items in _grouped(payload["manifest"]).items():
        cards.append(f"<h3>{esc(g)}</h3><div class='cards'>")
        for d in items:
            cards.append(
                f"<a class='card' href='{doc_href[d['path']]['' if False else ''] if False else doc_href[d['path']]}'>"
                f"<b>{esc(d['title'])}</b><span><code>{esc(d['path'])}</code> · {d['lines']} 行 · {d['headings']} 小节</span></a>"
            )
        cards.append("</div>")
    return f"""<h1>codemax_platform 文档站</h1>
<p class="lead">架构导读与代码级逐行说明书的可读 + 可视化版本。
本站<b>完全离线</b>：由 <code>scripts/build_docs_site.py</code> 从 Markdown 与源码预渲染，
不依赖任何 CDN 或运行时。</p>
<div class="stats">
  <div><b>{m['docs']}</b><span>份文档</span></div>
  <div><b>{m['doc_lines']}</b><span>文档行数</span></div>
  <div><b>{m['modules']}</b><span>个模块</span></div>
  <div><b>{m['edges']}</b><span>条依赖</span></div>
  <div><b>{m['routes']}</b><span>条路由</span></div>
  <div><b>{m['symbols']}</b><span>个符号</span></div>
</div>
<h2>可视化</h2>
<div class="cards">
  <a class="card viz" href="graph.html"><b>🕸 模块依赖图</b><span>{m['modules']} 个模块 / {m['edges']} 条依赖，从 import 真实提取</span></a>
  <a class="card viz" href="routes.html"><b>🧭 路由地图</b><span>{m['routes']} 条路由，含鉴权与限流标记，可过滤</span></a>
  <a class="card viz" href="symbols.html"><b>🔤 符号索引</b><span>{m['symbols']} 个函数与类，点击直达源码行</span></a>
</div>
<h2>文档</h2>
{''.join(cards)}
"""


def _grouped(manifest):
    g = {}
    for d in manifest:
        g.setdefault(d["group"], []).append(d)
    return g


# ---------------------------------------------------------------- 依赖图（生成 SVG）


def _render_graph_page(graph: dict, doc_href: dict, src_href: dict) -> str:
    nodes, edges = graph["nodes"], graph["edges"]
    # 只看非测试模块，图才看得清；测试模块单独统计
    core = [n for n in nodes if n["layer"] != "测试"]
    ids = {n["id"] for n in core}
    core_edges = [e for e in edges if e["from"] in ids and e["to"] in ids]

    # 分层：按拓扑深度（被依赖的在下层）
    depth: dict[str, int] = {}
    for _ in range(len(core) + 1):
        changed = False
        for n in core:
            d = 1 + max([depth[e["to"]] for e in core_edges if e["from"] == n["id"] and e["to"] in depth] or [0])
            if depth.get(n["id"]) != d:
                depth[n["id"]] = d
                changed = True
        if not changed:
            break

    layers: dict[int, list] = {}
    for n in core:
        layers.setdefault(depth.get(n["id"], 0), []).append(n)
    maxrow = max(len(v) for v in layers.values())

    X0, Y0, DX, DY, R = 90, 60, 250, 46, 7
    W = X0 * 2 + (len(layers) - 1) * DX + 200
    H = Y0 * 2 + (maxrow - 1) * DY + 40
    # 层内排序用**重心法**（barycenter）减少边交叉 —— 早先按 id 排，
    # 相邻节点在下一层的邻居散落各处，边大量交叉、视觉上糊成一团。
    adj: dict = {}
    for e in core_edges:
        adj.setdefault(e["from"], []).append(e["to"])
        adj.setdefault(e["to"], []).append(e["from"])
    order = {lv: sorted(layers[lv], key=lambda x: x["id"]) for lv in layers}
    for _ in range(4):
        rowindex = {n["id"]: i for c in order.values() for i, n in enumerate(c)}
        for lv in sorted(layers):
            # 用默认参数把本轮的 rowindex 绑进闭包（ruff B023）
            def bkey(n, ri=rowindex):
                ns = [ri[x] for x in adj.get(n["id"], []) if x in ri]
                return (sum(ns) / len(ns)) if ns else ri[n["id"]]
            order[lv] = sorted(order[lv], key=bkey)
    pos = {}
    for li, lv in enumerate(sorted(layers)):
        col = order[lv]
        for ri, n in enumerate(col):
            pos[n["id"]] = (X0 + li * DX, Y0 + ri * DY + (maxrow - len(col)) * DY / 2)

    LCOLOR = {"基础设施": "#3b82f6", "API 层": "#10b981", "业务逻辑层": "#f59e0b",
              "前端": "#8b5cf6", "数据层": "#ef4444", "其它": "#06b6d4", "测试": "#94a3b8"}

    parts = [f'<svg id="depgraph" viewBox="0 0 {W} {H}" width="100%" height="{H}">',
             '<defs><marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker></defs>']
    for e in core_edges:
        if e["from"] not in pos or e["to"] not in pos:
            continue
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        mx = (x1 + x2) / 2
        parts.append(f'<path class="edge" data-f="{esc(e["from"])}" data-t="{esc(e["to"])}" '
                     f'd="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" marker-end="url(#arw)"/>')
    for n in core:
        x, y = pos[n["id"]]
        c = LCOLOR.get(n["layer"], "#64748b")
        indeg = sum(1 for e in core_edges if e["to"] == n["id"])
        r = R + min(6, indeg)
        label = n["id"].replace("app.", "")
        doc = _doc_for(n["path"])
        # graph.html 在站点根目录，链接不需要 ../
        link = doc_href[doc] if doc in doc_href else src_href.get(n["path"], "#")
        parts.append(
            f'<g class="node" data-id="{esc(n["id"])}">'
            f'<a href="{link}"><circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>'
            f'<text x="{x + r + 5}" y="{y + 4}">{esc(label)}</text></a>'
            f'<title>{esc(n["id"])}&#10;{esc(n["path"])} · {n["lines"]} 行&#10;{esc(n["layer"])} · 被依赖 {indeg} 次</title></g>'
        )
    parts.append("</svg>")

    legend = "".join(
        f'<span><i style="background:{c}"></i>{esc(k)}（{sum(1 for n in core if n["layer"] == k)}）</span>'
        for k, c in LCOLOR.items() if any(n["layer"] == k for n in core)
    )
    tests = [n for n in nodes if n["layer"] == "测试"]
    return f"""<div class="breadcrumb"><a href="index.html">首页</a> · 可视化</div>
<h1>模块依赖图</h1>
<p>节点与边由 <code>scripts/build_docs_site.py</code> 从 <code>import</code> 语句<b>真实提取</b>
（含 <code>from ..models</code> 这类相对导入 —— 只认绝对导入会漏到只剩 5 条边，实测真实是
{len(edges)} 条）。横向按<b>拓扑深度</b>分层：越靠右越上层。点节点进入对应文档，
鼠标悬停某个节点可高亮它的依赖关系。</p>
<div class="viz-toolbar">
  <label>搜索 <input type="text" id="gq" size="16" placeholder="模块名"></label>
  <span class="spacer" style="flex:1"></span>
  <span class="muted">核心模块 {len(core)} 个 · 依赖 {len(core_edges)} 条 ·
    另有 {len(tests)} 个测试模块未画入（会让图糊成一团）</span>
  <button class="mini" id="greset">重置</button>
</div>
<div class="graph-wrap">{''.join(parts)}</div>
<div class="legend">{legend}</div>
<p class="muted">被依赖最多的模块：{esc('、'.join(f'{k}（{v} 次）' for k, v in _top_depended(core_edges)))}</p>"""


def _top_depended(edges, n=6):
    import collections
    return collections.Counter(e["to"] for e in edges).most_common(n)


# ---------------------------------------------------------------- 路由地图


def _render_routes_page(routes: list[dict]) -> str:
    rows = []
    for r in routes:
        auth = ('<span class="badge admin">管理员</span>' if r["admin"]
                else '<span class="badge auth">登录</span>' if r["auth"]
                else '<span class="badge off">公开</span>')
        rl = '<span class="badge rl">限流</span>' if r["rate_limit"] else '<span class="badge off">—</span>'
        rows.append(
            f'<tr data-m="{r["method"]}" data-auth="{int(r["auth"])}" data-rl="{int(r["rate_limit"])}" '
            f'data-p="{esc(r["path"])}">'
            f'<td class="m {r["method"]}">{r["method"]}</td>'
            f'<td class="p">{esc(r["path"])}</td>'
            f'<td><code>{esc(r["handler"])}</code></td>'
            f"<td>{auth}</td><td>{rl}</td>"
            f'<td><a class="srcref" href="s/{slug(r["file"])}.html#L{r["line"]}">{esc(r["file"])}:{r["line"]}</a></td></tr>'
        )
    nauth = sum(1 for r in routes if r["auth"])
    nadmin = sum(1 for r in routes if r["admin"])
    nrl = sum(1 for r in routes if r["rate_limit"])
    return f"""<div class="breadcrumb"><a href="index.html">首页</a> · 可视化</div>
<h1>路由地图</h1>
<p>{len(routes)} 条路由，由 AST 从 <code>@router.*</code> 装饰器与函数签名提取。
<b>鉴权有两种写法</b>：装饰器的 <code>dependencies=[...]</code> 与函数签名里的
<code>Depends(get_current_user)</code> —— 两处都要看，只看装饰器会得出「需鉴权 0 条」的错误结论。
实测：需鉴权 <b>{nauth}</b> 条、其中管理员 <b>{nadmin}</b> 条、有限流 <b>{nrl}</b> 条。</p>
<div class="viz-toolbar">
  <label>方法 <select id="fm"><option value="">全部</option>
    <option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option></select></label>
  <label><input type="checkbox" id="fa"> 只看需鉴权</label>
  <label><input type="checkbox" id="fr"> 只看有限流</label>
  <label>路径 <input type="text" id="fq" size="16"></label>
  <span class="spacer" style="flex:1"></span><span class="muted" id="fstat"></span>
</div>
<table class="route-table"><thead><tr>
<th>方法</th><th>路径</th><th>处理函数</th><th>鉴权</th><th>限流</th><th>位置</th>
</tr></thead><tbody id="rbody">{''.join(rows)}</tbody></table>"""


# ---------------------------------------------------------------- 符号索引


def _render_symbols_page(symbols: list[dict], doc_href: dict, src_href: dict) -> str:
    cards = []
    for s in symbols:
        sh = f's/{slug(s["file"])}.html#L{s["line"]}' if s["file"] in src_href else "#"
        dh = f'd/{slug(s["doc"])}.html' if s["doc"] in doc_href else ""
        # Python 3.11 的 f-string 表达式里不能再出现同类引号，先算好再拼
        span = f'-L{s["end"]}' if s["end"] > s["line"] else ""
        cards.append(
            f'<div class="sym-card" data-k="{s["kind"]}" data-pub="{int(not s["private"])}" '
            f'data-n="{esc(s["name"].lower())}" data-m="{esc(s["module"])}">'
            f'<div class="n {s["kind"]}">{"class " if s["kind"] == "class" else "def "}{esc(s["name"])}</div>'
            f'<div class="muted">{esc(s["module"])} · L{s["line"]}{span}</div>'
            f'<a href="{sh}">源码</a>' + (f' · <a href="{dh}">文档</a>' if dh else "") + "</div>"
        )
    ncls = sum(1 for s in symbols if s["kind"] == "class")
    return f"""<div class="breadcrumb"><a href="index.html">首页</a> · 可视化</div>
<h1>符号索引</h1>
<p>{len(symbols)} 个函数与类（其中 {ncls} 个类），由 AST 提取。点「源码」直达定义行，点「文档」跳到该模块说明书。</p>
<div class="viz-toolbar">
  <label>搜索 <input type="text" id="sq" size="18" placeholder="函数名 / 类名 / 模块"></label>
  <label>类型 <select id="sk"><option value="">全部</option><option value="def">函数</option><option value="class">类</option></select></label>
  <label><input type="checkbox" id="sp" checked> 只看公开（不以 _ 开头）</label>
  <span class="spacer" style="flex:1"></span><span class="muted" id="sstat"></span>
</div>
<div class="sym-list" id="slist">{''.join(cards)}</div>"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-only", action="store_true", help="只生成 data/*.json，不渲染 HTML")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)

    graph = build_import_graph()
    routes = build_routes()
    symbols = build_symbols()
    manifest = build_manifest()

    payload = {
        "graph": graph,
        "routes": routes,
        "symbols": symbols,
        "manifest": manifest,
        "meta": {
            "modules": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "routes": len(routes),
            "symbols": len(symbols),
            "docs": len(manifest),
            "doc_lines": sum(d["lines"] for d in manifest),
        },
    }
    for key in ("graph", "routes", "symbols", "manifest"):
        (DATA / f"{key}.json").write_text(
            json.dumps(payload[key], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    (DATA / "meta.json").write_text(json.dumps(payload["meta"], ensure_ascii=False, indent=1), encoding="utf-8")

    m = payload["meta"]
    print(f"✅ 数据已生成到 {DATA.relative_to(ROOT)}/")
    print(f"   模块 {m['modules']} 个 · 依赖边 {m['edges']} 条 · 路由 {m['routes']} 条 · "
          f"符号 {m['symbols']} 个 · 文档 {m['docs']} 份（{m['doc_lines']} 行）")

    if args.data_only:
        return 0

    try:
        import mistune  # noqa: F401
    except ImportError:
        print("渲染静态站需要 mistune：pip install mistune", file=sys.stderr)
        return 1

    stats = render_site(payload)
    print(f"✅ 静态站已生成：文档 {stats['docs']} 页 + 源码 {stats['sources']} 页 + "
          f"首页与 3 个可视化页 → {SITE.relative_to(ROOT)}/")
    print(f"   打开方式：直接在浏览器打开 {SITE.relative_to(ROOT)}/index.html（无需服务器、无需联网）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
