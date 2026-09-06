# tests/test_docs_site.py
#
# `scripts/build_docs_site.py`（935 行 / 32 个函数）**曾经零测试、CI 也不构建文档站** ——
# 于是它静默烂掉也没人知道：`build_routes()` 漏掉 4 条路由（`GET /` 与 3 个 `/tools/*`），
# 生成的 `routes.json` 是 32 条而运行时真实是 36 条，7 处文档跟着写成 32 并互相「印证」。
#
# 这里补的是**回归测试**而不是覆盖率凑数：
#   - 路由数必须等于运行时 `app.routes` 的真值（就是这条能抓住上面那次漂移）
#   - 锚点/slug/排除清单这些「一旦错就静默产出坏页面」的纯函数
#   - `DOC_GROUPS` 里每个文件必须真实存在（漏加新 README 会在这里炸，而不是等人发现）
#
# 设计约束：本文件**只测纯函数与数据提取**，不执行整站渲染。
# 整站渲染（21 份文档 → 116 页）放在 CI 里跑一次（`.github/workflows/ci.yml` 的
# `docs` job），因为它慢且需要 mistune；单元测试不该为此付费。
"""文档站构建脚本的回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_docs_site as bds  # noqa: E402

mistune = pytest.importorskip("mistune", reason="文档站渲染需要 mistune")  # noqa: F841


# ---------------------------------------------------------------- 路由口径
def test_route_count_matches_runtime_app(client):  # noqa: ARG001
    """文档站提取的路由数必须等于运行时真实注册数。

    **这就是能抓住「32 vs 36」那次漂移的测试。** 之前 `build_routes()` 只扫
    `@router.*` 装饰器，而 `app/routers/site.py:39-40` 用
    `router.add_api_route(_tool.path, ...)` 循环 `PAGES` 注册页面路由 —— 路径是变量
    不是字面量，装饰器扫描一条都抓不到，静默少 4 条。

    ⚠️ 排除规则要与 `main.py` 一致：`/healthz` 与 `/health` 在 OpenAPI 里都
    `include_in_schema=False`，但仍算业务路由。
    """
    from main import app

    runtime = {
        (m, r.path)
        for r in app.routes
        for m in getattr(r, "methods", ())
        if m in {"GET", "POST", "PUT", "DELETE", "PATCH"}
        and not r.path.startswith("/openapi")
        and r.path not in {"/docs", "/docs/oauth2-redirect", "/redoc"}
    }
    extracted = {(r["method"], r["path"]) for r in bds.build_routes()}

    assert extracted == runtime, (
        f"文档站提取与运行时不一致：多 {sorted(extracted - runtime)}，"
        f"少 {sorted(runtime - extracted)}"
    )
    # 36 → 38：S2-02-2 加了 GET /shop（落地页）与 GET /shop/orders/{order_no}（状态轮询）
    # 38 → 39：S5-04 加了 POST /shop/orders/{order_no}/confirm（人工确认收款，TD-205）
    assert len(extracted) == 39, f"业务路由应为 39 条，实际 {len(extracted)}"


def test_page_routes_are_extracted(client):  # noqa: ARG001
    """`GET /`、3 个 `/tools/*` 与 `/shop` 页面路由必须在提取结果里（回归）。

    这些路由是 `router.add_api_route(_tool.path, ...)` 循环 PAGES 注册的，
    路径来自变量不是字面量，装饰器扫描抓不到 —— 曾因此静默少 4 条（32 vs 36）。
    S2-02-2 又把 `/shop` 加进了 PAGES，所以这条清单要跟着长。
    """
    paths = {(r["method"], r["path"]) for r in bds.build_routes()}
    expected_pages = [
        ("GET", "/"),
        ("GET", "/tools/er"),
        ("GET", "/tools/mermaid"),
        ("GET", "/tools/drawio"),
        ("GET", "/shop"),
    ]
    for expected in expected_pages:
        assert expected in paths, f"{expected} 未被提取 —— build_routes() 漏了 app/site.py 的 Tool 清单"


def test_auth_and_ratelimit_counts(client):  # noqa: ARG001
    """鉴权/限流计数（docs/site/README.md 把它当断言写进了文档，必须对得上）。"""
    routes = bds.build_routes()
    # 17 → 18：人工确认收款端点走 require_admin（S5-04）
    assert sum(1 for r in routes if r["auth"]) == 18
    # 8 → 9：/oauth/token 补挂 rate_limit("token", "RATE_LIMIT_AUTH")。
    # 它是密码交换端点，此前是全站唯一没有速率约束的敏感端点。
    assert sum(1 for r in routes if r["rate_limit"]) == 9


# ---------------------------------------------------------------- 纯函数
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("main.py", "-文件名mainpy"),
        ("shop.py", "-文件名shoppy"),
        ("database.py", "-文件名databasepy"),
    ],
)
def test_file_anchor_matches_github(filename, expected):
    """`_file_anchor` 必须复刻 GitHub 的标题→锚点算法。

    写错不会报错，只会让 `routes.html` 里每个「查看文档」链接跳到 404 锚点。

    期望值不是拍脑袋写的，是按实现规则（小写 → 去 `` ` `` → 只保留字母/数字/
    组合记号/连接标点/空格/连字符 → 空格转 `-`）逐字推出来并实跑核对过的：
    emoji `📄` 的 Unicode 类别是 `So`，**不在**保留集里，所以会被删掉，
    只剩下前导连字符 —— 这正是容易写错的地方。
    """
    assert bds._file_anchor(filename) == expected


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("README.md", "README.md"),
        ("docs/site/README.md", "docs_site_README.md"),
        ("database init/README.md", "database_init_README.md"),  # 路径里有空格
        ("总览.md", "总览.md"),                                   # ⚠️ 中文必须保留
    ],
)
def test_slug_keeps_chinese_and_spaces(rel, expected):
    """`slug()` 把文档路径转成安全的文件名。

    它服务的是 `docs/site/d/*.html` 与 `s/*.html` 的**文件名**。实现是
    `re.sub(r"[^\w.-]", "_", rel, flags=re.UNICODE)` —— 保留字母/数字/下划线/
    点/连字符，其余（`/`、空格）换成 `_`。

    ⚠️ `flags=re.UNICODE` 是关键：不带它 `\w` 在 Python 3 虽然默认也含中文，
    但早期版本用的是 `[^A-Za-z0-9._-]`，会把汉字全删成空，
    于是 `总览.md` 与任何纯中文路径都撞成同一个空串，页面互相覆盖。
    """
    assert bds.slug(rel) == expected


def test_mistune_needs_heading_id_injection():
    """mistune 默认**不给标题加 `id`**，必须靠 `_inject_heading_ids` 后处理。

    少了它，文档站所有目录锚点都跳不动 —— 页面看起来完全正常，
    只有点链接才发现，所以必须钉死在这里。
    """
    md = bds._md()
    raw = md("## 中文标题\n")
    assert "id=" not in raw, "mistune 竟然自己加了 id —— 后处理逻辑的前提变了，请重新评估"
    assert 'id="中文标题"' in bds._inject_heading_ids(raw)


def test_duplicate_headings_get_numbered_ids():
    """同名标题必须自动加 `-1`/`-2` 后缀，否则重复 id 会让锚点跳到第一个。"""
    out = bds._inject_heading_ids("<h2>安装</h2><h2>安装</h2><h2>安装</h2>")
    assert out.count('id="安装"') == 1
    assert 'id="安装-1"' in out and 'id="安装-2"' in out


# ---------------------------------------------------------------- 清单与排除项
def test_doc_groups_all_exist():
    """`DOC_GROUPS` 里每个文件都必须真实存在。

    这条就是「新增文档必须同步进构建清单」的机器闸门：
    加了文件没登记 → 文档站没有那一页；登记了却没加文件 → 这里直接炸。
    """
    assert isinstance(bds.DOC_GROUPS, list), "DOC_GROUPS 是 [(组名, [路径…]), …]"
    for name, files in bds.DOC_GROUPS:
        assert files, f"分组「{name}」是空的"
        for rel in files:
            assert (ROOT / rel).exists(), f"DOC_GROUPS 登记的 {rel} 不存在"


def test_docs_site_readme_is_in_doc_groups():
    """`docs/site/README.md` 必须在清单里（曾漏登记，导致文档站没有自己的说明页）。"""
    flat = [rel for _, files in bds.DOC_GROUPS for rel in files]
    assert "docs/site/README.md" in flat


def test_generated_paths_are_gitignored():
    """所有生成物都必须在 .gitignore 里 —— 漏一条就会把 100+ 个 HTML 提交进仓库。

    实测漏过 6 条（`docs/site/{index,graph,routes,symbols}.html` + `d/` + `s/`）。
    """
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (
        "docs/site/data/",
        "docs/site/index.html",
        "docs/site/graph.html",
        "docs/site/routes.html",
        "docs/site/symbols.html",
        "docs/site/d/",
        "docs/site/s/",
    ):
        assert entry in ignore, f".gitignore 缺 {entry}"


def test_data_extraction_is_dependency_free():
    """数据提取阶段只依赖标准库 —— 这是文档站的设计承诺。

    `--data-only` 必须能在没装 mistune 的环境跑（`docs/site/README.md` 明确写了
    只有渲染那一步才需要 mistune）。这里断言 `mistune` 不在模块顶层导入。
    """
    src = (ROOT / "scripts" / "build_docs_site.py").read_text(encoding="utf-8")
    toplevel = [
        line
        for line in src.splitlines()
        if line.startswith("import mistune") or line.startswith("from mistune")
    ]
    assert not toplevel, f"mistune 被顶层导入，会破坏 --data-only 的零依赖承诺：{toplevel}"
