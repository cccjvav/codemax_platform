"""S2-01-1 前端（ER 图页面）测试。

重点不只是"页面能返回 200"，而是**后端接口与前端代码的字段契约**：
拿 /tools/er-diagram 的真实返回喂给 app/frontend/er-layout.js 的 layoutEr（用 node 真实执行），
一旦后端字段改名而前端没跟上，这里立刻报错。
期望的表名/连线一律从接口返回推导，项目加表不会误报。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from main import app
from tests.conftest import iter_app_routes

ROOT = Path(__file__).resolve().parents[1]
# 契约测试喂的是**纯布局**那一半（app/frontend/er-layout.js）。
# ⚠️ 不能指向 er-page.js：那个文件 import d3，而 CI 上没有 node_modules，
#    node 会加载失败。布局与渲染拆开正是为了让这条契约测试在 CI 上跑得动。
ER_JS = ROOT / "app" / "frontend" / "er-layout.js"
FULL_INIT_SQL = ROOT / "database init" / "full_init.sql"

# er.js 里 layoutEr/renderEr 真正读取的字段名（后端必须全部提供）
FRONTEND_READS = [
    "name",
    "columns",
    "comment",
    "type",
    "primary_key",
    "from_table",
    "from_column",
    "to_table",
    "to_column",
]

# node 侧执行器：argv[1] = 接口返回的 JSON，argv[2] = er-layout.js
#
# ⚠️ 必须是 **ESM**（顶层 await + import），不能再用 require：er-layout.js 是
# ES 模块（`export { layoutEr }`）。而 package.json 里**刻意不写** "type": "module"
# （那会让全仓 .js 都变 ESM，见 TD-221），所以这里靠 **`.mjs` 后缀**声明 ESM。
_NODE_HARNESS = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const { layoutEr } = await import(pathToFileURL(process.argv[2]).href);
const graph = JSON.parse(readFileSync(process.argv[1], "utf8"));
console.log(JSON.stringify(layoutEr(graph)));
"""

# ⚠️ `--input-type=module` 与 `pathToFileURL` 都不是可选的：
# ① `-e` 默认按 CommonJS 试解析，Node 22 虽然能靠「检测到模块语法后重新解析」兜住，
#    但那是隐式行为且每次都要解析两遍；显式声明更稳。
# ② Windows 上 `await import("C:\\...\\er-layout.js")` 会被当成非法 URL 直接失败，
#    而本项目的主要使用者就在 Windows 上 —— 必须转成 file:// URL。
_NODE_ARGS = ["--input-type=module", "-e"]


async def _graph_from_project_sql(client) -> dict:
    """以项目自身的 full_init.sql 为输入，取接口真实返回。"""
    r = await client.post("/tools/er-diagram", json={"ddl": FULL_INIT_SQL.read_text(encoding="utf-8")})
    assert r.status_code == 200
    return r.json()


async def test_er_page_served(client):
    r = await client.get("/tools/er")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="ddl-input"' in r.text
    assert "/static/js/er-page.js" in r.text
    assert "d3" in r.text


async def test_er_page_calls_a_registered_route(client):
    """页面里调用的接口地址必须是真实注册过的路由，防止前后端路径写歪。"""
    html = (await client.get("/tools/er")).text
    assert 'id="er-word"' in html, "页面缺导出 Word 入口"
    urls = re.findall(r'"(/tools/[\w-]+)"', html)
    assert urls, "页面里没有调用任何 /tools 接口"
    registered = {getattr(route, "path", None) for route in iter_app_routes(app.routes)}
    assert set(urls) <= registered, f"{sorted(set(urls) - registered)} 不是已注册路由"


async def test_layout_consumes_backend_payload(client, tmp_path):
    graph = await _graph_from_project_sql(client)
    node = shutil.which("node")
    if node is None:
        # 环境没有 node：退化为静态契约检查（前端要读的字段，后端都返回了）
        blob = json.dumps(graph, ensure_ascii=False)
        missing = [k for k in FRONTEND_READS if k not in blob]
        assert not missing, f"接口返回缺少前端要读的字段：{missing}"
        return

    payload = tmp_path / "graph.json"
    payload.write_text(json.dumps(graph), encoding="utf-8")
    out = subprocess.run(
        [node, *_NODE_ARGS, _NODE_HARNESS, str(payload), str(ER_JS)],
        capture_output=True,
        text=True,
        check=True,
    )
    layout = json.loads(out.stdout)

    # 表节点：与后端返回的表一一对应，且带列信息
    assert [n["name"] for n in layout["nodes"]] == [t["name"] for t in graph["tables"]]
    assert "sys_diagram" in [n["name"] for n in layout["nodes"]]  # S2-01-3 新增表也要画得出来
    assert all(n["columns"] for n in layout["nodes"])
    assert any(c["primary_key"] for n in layout["nodes"] for c in n["columns"])

    # 外键连线：与后端 edges 一一对应，两端坐标都落在对应表节点上
    assert {(link["from"], link["label"], link["to"]) for link in layout["links"]} == {
        (e["from_table"], f"{e['from_column']} → {e['to_column']}", e["to_table"]) for e in graph["edges"]
    }
    assert {"sys_order", "oauth_code", "sys_diagram"} <= {link["from"] for link in layout["links"]}
    by_name = {n["name"]: n for n in layout["nodes"]}
    for link in layout["links"]:
        src, dst = by_name[link["from"]], by_name[link["to"]]
        assert link["x1"] == src["x"] + src["w"]
        assert link["y1"] == src["y"] + src["h"] / 2
        assert link["x2"] == dst["x"]
        assert link["y2"] == dst["y"] + dst["h"] / 2
        assert "→" in link["label"]

    # 网格排布：同一行的表不重叠，画布尺寸能装下所有节点
    rows: dict[float, list[dict]] = {}
    for n in layout["nodes"]:
        rows.setdefault(n["y"], []).append(n)
    for row in rows.values():
        xs = sorted(n["x"] for n in row)
        assert all(b - a >= by_name[row[0]["name"]]["w"] for a, b in zip(xs, xs[1:], strict=False))
    assert layout["width"] >= max(n["x"] + n["w"] for n in layout["nodes"])
    assert layout["height"] >= max(n["y"] + n["h"] for n in layout["nodes"])


async def test_layout_handles_empty_graph(tmp_path):
    """空输入不能算出负宽度（曾经返回 width=-90）。"""
    node = shutil.which("node")
    if node is None:
        assert "widest ?" in ER_JS.read_text(encoding="utf-8")
        return

    payload = tmp_path / "graph.json"
    payload.write_text(json.dumps({"tables": [], "edges": []}), encoding="utf-8")
    out = subprocess.run(
        [node, *_NODE_ARGS, _NODE_HARNESS, str(payload), str(ER_JS)],
        capture_output=True,
        text=True,
        check=True,
    )
    layout = json.loads(out.stdout)
    assert layout["nodes"] == [] and layout["links"] == []
    assert layout["width"] == 0 and layout["height"] == 0


async def test_layout_skips_dangling_fk(tmp_path):
    """外键指向 DDL 之外的表时，只丢这条线，不让整张图崩掉。"""
    node = shutil.which("node")
    if node is None:
        assert "return null" in ER_JS.read_text(encoding="utf-8")
        return

    graph = {
        "tables": [{"name": "a", "comment": None, "columns": [{"name": "id", "type": "INT", "primary_key": True}]}],
        "edges": [{"from_table": "a", "from_column": "b_id", "to_table": "b", "to_column": "id"}],
    }
    payload = tmp_path / "graph.json"
    payload.write_text(json.dumps(graph), encoding="utf-8")
    out = subprocess.run(
        [node, *_NODE_ARGS, _NODE_HARNESS, str(payload), str(ER_JS)],
        capture_output=True,
        text=True,
        check=True,
    )
    layout = json.loads(out.stdout)
    assert len(layout["nodes"]) == 1
    assert layout["links"] == []
