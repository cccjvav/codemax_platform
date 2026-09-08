"""建表脚本与 ORM 模型必须同步（HANDOVER 点名的坑）。

用项目自己的 DDL 解析器读 `database init/full_init.sql`，与 SQLAlchemy 元数据
逐表逐列比对 —— 任何一边改了没同步，这里立刻报错，不用等线上炸。
"""
import re
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
    assert _sql_schema()["sys_diagram"] == [
        "content", "create_time", "deleted_at", "id", "name", "update_time", "user_id", "version"
    ]


def test_every_datetime_column_is_timezone_aware():
    """所有时间列必须带时区（TD-146）。

    test_schema_sync 只比「表名 + 列名」，**不比类型**，所以有人以后又写了个裸
    `mapped_column(DateTime)` 不会被上面那两条拦住 —— 这条专门守它。
    裸 TIMESTAMP 会让「应用时钟」与「数据库时钟」写进同一张表却无法正确比较。
    """
    from sqlalchemy import DateTime as _DateTime

    naive = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if isinstance(c.type, _DateTime) and not c.type.timezone
    ]
    assert not naive, f"这些时间列没有时区，应写成 DateTime(timezone=True)：{naive}"


def test_full_init_sql_uses_timestamptz():
    """建表脚本里的时间列必须是 TIMESTAMPTZ，且 DEFAULT 不能被误改。"""
    sql = FULL_INIT_SQL.read_text(encoding="utf-8")
    assert "TIMESTAMPTZ" in sql
    # 不应再出现裸 TIMESTAMP 类型（DEFAULT CURRENT_TIMESTAMP 里的字样要排除）
    bare = [
        line.strip()
        for line in sql.split("\n")
        if re.search(r"\bTIMESTAMP\b", line)
        and "TIMESTAMPTZ" not in line
        and "CURRENT_TIMESTAMP" not in line
    ]
    assert not bare, f"full_init.sql 里还有裸 TIMESTAMP 类型：{bare}"
    assert "DEFAULT CURRENT_TIMESTAMP" in sql, "DEFAULT 子句不应被改动"
