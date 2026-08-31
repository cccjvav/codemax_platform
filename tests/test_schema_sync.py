"""建表脚本与 ORM 模型必须同步（HANDOVER 点名的坑）。

用项目自己的 DDL 解析器读 `database init/full_init.sql`，与 SQLAlchemy 元数据
逐表逐列比对 —— 任何一边改了没同步，这里立刻报错，不用等线上炸。
"""
from pathlib import Path

import app.models  # noqa: F401  确保所有模型都注册进 Base.metadata
from app.database import Base
from app.tools.sql_ddl import parse_ddl

FULL_INIT_SQL = Path(__file__).resolve().parents[1] / "database init" / "full_init.sql"


def _sql_schema() -> dict:
    graph = parse_ddl(FULL_INIT_SQL.read_text(encoding="utf-8"))
    return {t["name"]: sorted(c["name"] for c in t["columns"]) for t in graph["tables"]}


def _orm_schema() -> dict:
    return {t.name: sorted(c.name for c in t.columns) for t in Base.metadata.sorted_tables}


def test_table_sets_match():
    assert set(_sql_schema()) == set(_orm_schema())


def test_columns_match_for_every_table():
    sql, orm = _sql_schema(), _orm_schema()
    diff = {name: {"sql": sql[name], "orm": orm.get(name)} for name in sql if sql[name] != orm.get(name)}
    assert not diff, f"full_init.sql 与 app/models.py 不一致：{diff}"


def test_sys_diagram_shape():
    """S2-01-3 新增表：两边都得有，且列一致。"""
    assert _sql_schema()["sys_diagram"] == _orm_schema()["sys_diagram"]
    assert _sql_schema()["sys_diagram"] == ["content", "create_time", "id", "name", "update_time", "user_id"]
