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

import pytest

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
    """保留全套项目表DDL，排除维护过程语句以遵守公开接口预算。"""
    from app.tools.sql_ddl import parse_ddl
    raw = FULL_INIT_SQL.read_text(encoding="utf-8")
    from tests.conftest import project_ddl_for_api
    ddl = project_ddl_for_api()
    r = await client.post("/tools/er-diagram", json={"ddl": ddl})
    assert r.status_code == 200
    assert r.json() == parse_ddl(raw)  # Removing prose must not remove any table/column/FK.
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
    _assert_geometry(layout)
    # 分列：父表（被引用）在子表左边 —— 项目 schema 没有环，每条非自引用的线都应从右往左指
    by_name = {n["name"]: n for n in layout["nodes"]}
    for link in layout["links"]:
        if link["from"] != link["to"]:
            assert by_name[link["to"]]["x"] < by_name[link["from"]]["x"], link["label"]


def _row_y(node: dict, column: str, layout: dict) -> float:
    names = [c["name"] for c in node["columns"]]
    if column not in names:
        return node["y"] + layout["headH"] / 2
    return node["y"] + layout["headH"] + (names.index(column) + 0.5) * layout["rowH"]


def _assert_geometry(layout: dict) -> None:
    """TD-279 布局契约（替换原来钉死「子表右中点 → 父表左中点」的四条坐标断言）。

    原断言描述的正是被修掉的缺陷：固定从右中点连到左中点，线常横穿中间的表，也看不出是哪一列。
    现在钉住更强、与具体坐标算法无关的性质：
    ① 起点在子表朝向父表的那条边上、y 等于**外键列那一行**的中线；终点同理落在父表被引用列那一行；
    ② 折线每段水平或竖直，且**没有任何一段进入任何表格内部**（含两端的表）；
    ③ 最后一段水平进入父表（箭头方向可读）；path 字符串与 points 一致；
    ④ 表格两两不重叠，宽度在 [160, 340]，被截断的文字以省略号结尾并保留完整原文；
    ⑤ 画布能装下所有节点和线。
    """
    nodes = layout["nodes"]
    by_name = {n["name"]: n for n in nodes}
    for link in layout["links"]:
        src, dst = by_name[link["from"]], by_name[link["to"]]
        assert link["x1"] in (src["x"], src["x"] + src["w"])
        assert link["y1"] == _row_y(src, link["fromColumn"], layout)
        assert link["x2"] in (dst["x"], dst["x"] + dst["w"])
        assert link["y2"] == _row_y(dst, link["toColumn"], layout)
        pts = link["points"]
        assert pts[0] == [link["x1"], link["y1"]] and pts[-1] == [link["x2"], link["y2"]]
        assert pts[-2][1] == link["y2"], "最后一段必须水平进入父表"
        assert link["path"] == "M" + " L".join(f"{x:g},{y:g}" for x, y in pts)
        assert "→" in link["label"]
        for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
            assert ax == bx or ay == by, f"{link['label']} 有斜线段"
            for n in nodes:
                inside_x = max(ax, bx) > n["x"] and min(ax, bx) < n["x"] + n["w"]
                inside_y = max(ay, by) > n["y"] and min(ay, by) < n["y"] + n["h"]
                if ax == bx:
                    inside_x = n["x"] < ax < n["x"] + n["w"]
                if ay == by:
                    inside_y = n["y"] < ay < n["y"] + n["h"]
                assert not (inside_x and inside_y), f"{link['from']}→{link['to']} 的线段穿过表 {n['name']}"
            assert min(ax, bx) >= 0 and max(ax, bx) <= layout["width"]
            assert min(ay, by) >= 0 and max(ay, by) <= layout["height"]
    for i, a in enumerate(nodes):
        assert 160 <= a["w"] <= 340
        texts = [(a["header"], a["headerFull"])] + [(r["text"], r["full"]) for r in a["rows"]]
        for shown, full in texts:
            assert shown == full or (shown.endswith("…") and full.startswith(shown[:-1]))
        for b in nodes[i + 1:]:
            apart = (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                     or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
            assert apart, f"{a['name']} 与 {b['name']} 重叠"
    if nodes:
        assert layout["width"] >= max(n["x"] + n["w"] for n in nodes)
        assert layout["height"] >= max(n["y"] + n["h"] for n in nodes)


def _layout(graph: dict, tmp_path) -> dict:
    payload = tmp_path / "graph.json"
    payload.write_text(json.dumps(graph), encoding="utf-8")
    out = subprocess.run(
        [shutil.which("node"), *_NODE_ARGS, _NODE_HARNESS, str(payload), str(ER_JS)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _table(name, *cols, comment=None):
    return {"name": name, "comment": comment,
            "columns": [{"name": c, "type": "INT", "primary_key": c == "id"} for c in cols]}


def _edge(a, col, b, to="id"):
    return {"from_table": a, "from_column": col, "to_table": b, "to_column": to}


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实布局代码")
def test_layout_edge_cases_keep_the_geometry_contract(tmp_path):
    """长名截断、自引用、环、星型大表（拆子列、走顶部车道）都不许有线穿表或表重叠（TD-279）。"""
    long_name = "t_" + "very_long_table_name_" * 4
    star = [_table("hub", "id")] + [_table(f"leaf{i}", "id", "hub_id", "a", "b", "c", "d", "e", "f")
                                    for i in range(14)]
    graphs = {
        "truncate": {"tables": [_table(long_name, "id", "x" * 90, comment="一个非常非常长的中文注释" * 3)],
                     "edges": []},
        "self_ref": {"tables": [_table("node", "id", "parent_id")], "edges": [_edge("node", "parent_id", "node")]},
        "cycle": {"tables": [_table("a", "id", "b_id"), _table("b", "id", "a_id")],
                  "edges": [_edge("a", "b_id", "b"), _edge("b", "a_id", "a")]},
        "star": {"tables": star + [_table("grand", "id", "leaf13_id"), _table("alone", "id")],
                 "edges": [_edge(f"leaf{i}", "hub_id", "hub") for i in range(14)]
                          + [_edge("grand", "leaf13_id", "leaf13")]},
    }
    for name, graph in graphs.items():
        layout = _layout(graph, tmp_path)
        assert [n["name"] for n in layout["nodes"]] == [t["name"] for t in graph["tables"]], name
        assert len(layout["links"]) == len(graph["edges"]), name
        _assert_geometry(layout)
        if name == "truncate":
            node = layout["nodes"][0]
            assert node["w"] == 340 and node["header"].endswith("…") and node["rows"][1]["text"].endswith("…")
        if name == "star":
            xs = {n["x"] for n in layout["nodes"] if n["name"].startswith("leaf")}
            assert len(xs) >= 2, "14 张叶子表应拆成多列，而不是一根 5000px 的长柱"
            lanes = [lk for lk in layout["links"] if len(lk["points"]) == 6]
            assert lanes, "不相邻的列之间应走顶部车道"
            by = {n["name"]: n for n in layout["nodes"]}
            assert by["alone"]["x"] == max(n["x"] for n in layout["nodes"]), "无关系的表放最右"
            fk = [r for r in by["leaf0"]["rows"] if r["fk"]]
            assert [r["full"] for r in fk] == ["FK hub_id: INT"]


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


_VIEW_HARNESS = """
import { pathToFileURL } from "node:url";
const { initialView, READABLE } = await import(pathToFileURL(process.argv[1]).href);
const small = { width: 500, height: 300 }, big = { width: 2400, height: 850 };
console.log(JSON.stringify({ READABLE,
  small: initialView(small, 900, 620), big: initialView(big, 900, 620),
  bigFit: initialView(big, 900, 620, "fit"), smallReadable: initialView(small, 900, 620, "readable") }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实布局代码")
def test_initial_view_is_readable_instead_of_squeezing_the_whole_diagram():
    """TD-279：原来 viewBox 永远等于整图，本项目 16 张表被压到约 40%，12px 列名缩成 5px。

    整图按比例放进画布后仍 ≥ 可读比例才显示全图（居中、不放大超过 1）；否则以可读比例从左上角开始，
    「查看全图」再切到全图比例。
    """
    out = subprocess.run([shutil.which("node"), *_NODE_ARGS, _VIEW_HARNESS, str(ER_JS)],
                         capture_output=True, text=True, check=True)
    v = json.loads(out.stdout)
    small, big, big_fit = v["small"], v["big"], v["bigFit"]
    assert small["fits"] and small["k"] == 1, "小图按原尺寸显示，不放大"
    assert small["x"] == (900 - 500) / 2 and small["y"] == (620 - 300) / 2
    assert not big["fits"] and big["k"] == v["READABLE"] == 0.75 and (big["x"], big["y"]) == (12, 12)
    assert big_fit["k"] == big["fitK"] < 0.75 and big_fit["k"] * 2400 <= 900 - 24
    assert v["smallReadable"]["k"] == 0.75
