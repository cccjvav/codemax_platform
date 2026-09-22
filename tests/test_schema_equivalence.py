"""TD-265 / ROADMAP O-03：fresh-init 与迁移链在真实 PostgreSQL 目录上的完整等价。

`test_schema_sync.py` 只比表名/列名（用项目自己的 DDL 解析器读 SQL 文本）。这里用一次性
pgserver 建三个库并读系统目录：

- **fresh**：`db_admin.initialize` 直接执行 full_init.sql；
- **adopted**：fresh 之后用测试里的 `legacy_0008` 拆回 0008 形状，再 `adopt_legacy` 走 0009–0017；
- **replayed**：在 adopted 基础上再拆回 0002 之前的形状，把历史 0002–0008 的 SQL 原样执行，
  然后 `adopt_legacy`——覆盖仓库里能重放的最长一段迁移链（0001 只改类型，前置形状与现行文件
  无法重建，见模块末尾说明）。

比较的目录切面：列（类型/udt/可空/默认/长度/精度/identity）、约束（PK/FK/UNIQUE/CHECK 的
`pg_get_constraintdef`）、索引定义、触发器定义、函数定义、序列、表清单。任何一处不同都会
列出，不再只比"表/列/时区"。仍然不是：数据迁移正确性、生产库实际状态或非 public schema。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from app import db_admin
from tests.test_db_admin import isolated_pg as isolated_pg  # noqa: F401  pytest fixture re-export
from tests.test_db_admin import legacy_0008

SQL_ROOT = Path(__file__).resolve().parents[1] / "database init"

# 0002–0008 各自新增的结构；剥掉它们就是"0001 之后、0002 之前"的形状。逆序执行，与文件顺序相反。
_STRIP_TO_PRE_0002 = [
    "ALTER TABLE sys_user DROP COLUMN credential_version",           # 0008
    "ALTER TABLE oauth_code DROP COLUMN credential_version",         # 0008
    "DROP TABLE support_message",                                     # 0007
    "DROP INDEX uq_sys_order_user_pending",                           # 0006
    "ALTER TABLE sys_user DROP COLUMN role",                          # 0005
    "ALTER TABLE sys_diagram DROP COLUMN version",                    # 0004
    "DROP INDEX idx_sys_diagram_user",                                # 0003
    "ALTER TABLE sys_diagram DROP COLUMN deleted_at",                 # 0003
    "ALTER TABLE sys_user DROP COLUMN password_changed_at",           # 0002
]
_HISTORICAL_CHAIN = [
    "migrate_0002_password_changed_at.sql",
    "migrate_0003_diagram_deleted_at.sql",
    "migrate_0004_diagram_version.sql",
    "migrate_0005_user_role.sql",
    "migrate_0006_order_single_pending.sql",
    "migrate_0007_support_messages.sql",
    "migrate_0008_credential_revision.sql",
]


def catalog(conn) -> dict:
    """public schema 的结构快照；全部来自系统目录，不解析 SQL 文本。"""
    out: dict = {}
    with conn, conn.cursor() as cur:
        cur.execute(
            """SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default,
                      character_maximum_length, numeric_precision, numeric_scale, datetime_precision,
                      is_identity, identity_generation
               FROM information_schema.columns WHERE table_schema='public' ORDER BY 1, 2"""
        )
        cols: dict = {}
        for row in cur.fetchall():
            cols.setdefault(row[0], {})[row[1]] = tuple(row[2:])
        out["columns"] = cols
        cur.execute(
            """SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid)
               FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"""
        )
        cons: dict = {}
        for table, name, kind, definition in cur.fetchall():
            cons.setdefault(table, {})[name] = (kind, definition)
        out["constraints"] = cons
        cur.execute("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1, 2")
        idx: dict = {}
        for table, name, definition in cur.fetchall():
            idx.setdefault(table, {})[name] = definition
        out["indexes"] = idx
        cur.execute(
            """SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) FROM pg_trigger
               WHERE NOT tgisinternal
                 AND tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'public'::regnamespace)
               ORDER BY 1, 2"""
        )
        trg: dict = {}
        for table, name, definition in cur.fetchall():
            trg.setdefault(table, {})[name] = definition
        out["triggers"] = trg
        cur.execute("SELECT proname, pg_get_functiondef(oid) FROM pg_proc WHERE pronamespace = 'public'::regnamespace ORDER BY 1")
        out["functions"] = dict(cur.fetchall())
        cur.execute("SELECT sequencename, data_type, start_value, increment_by FROM pg_sequences WHERE schemaname='public' ORDER BY 1")
        out["sequences"] = {row[0]: tuple(row[1:]) for row in cur.fetchall()}
        cur.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")
        out["tables"] = dict(cur.fetchall())
    return out


def differences(a: dict, b: dict, path: str = "") -> list[str]:
    """递归列出两份快照的全部差异；空列表才算等价。"""
    if isinstance(a, dict) and isinstance(b, dict):
        found: list[str] = []
        for key in sorted(set(a) | set(b)):
            if key not in a:
                found.append(f"only in B: {path}/{key} = {str(b[key])[:160]}")
            elif key not in b:
                found.append(f"only in A: {path}/{key} = {str(a[key])[:160]}")
            else:
                found.extend(differences(a[key], b[key], f"{path}/{key}"))
        return found
    return [] if a == b else [f"{path}: A={str(a)[:160]} | B={str(b)[:160]}"]


@pytest.fixture(scope="module")
def snapshots(isolated_pg):  # noqa: F811  module fixture from test_db_admin
    """三种建库路径各建一个一次性库，取目录快照后删库；一次建好供本模块所有用例比较。"""

    def new_connection():
        name = "eq_" + uuid.uuid4().hex
        isolated_pg.psql(f"CREATE DATABASE {name} OWNER maintenance_test")
        uri = make_url(isolated_pg.get_uri()).set(username="maintenance_test", database=name).render_as_string(hide_password=False)
        return name, psycopg2.connect(uri)

    made: list[tuple[str, object]] = []
    result: dict[str, dict] = {}
    try:
        name, conn = new_connection()
        made.append((name, conn))
        db_admin.initialize(conn)
        result["fresh"] = catalog(conn)

        name, conn = new_connection()
        made.append((name, conn))
        db_admin.initialize(conn)
        legacy_0008(conn)
        db_admin.adopt_legacy(conn)
        result["adopted"] = catalog(conn)

        name, conn = new_connection()
        made.append((name, conn))
        db_admin.initialize(conn)
        legacy_0008(conn)
        conn.autocommit = True  # 历史脚本 0007/0008 自带 BEGIN/COMMIT，不能套在外层事务里
        with conn.cursor() as cur:
            for statement in _STRIP_TO_PRE_0002:
                cur.execute(statement)
            for filename in _HISTORICAL_CHAIN:
                cur.execute((SQL_ROOT / filename).read_text(encoding="utf-8"))
        conn.autocommit = False
        db_admin.adopt_legacy(conn)
        result["replayed"] = catalog(conn)
        yield result
    finally:
        for name, conn in made:
            conn.close()
            isolated_pg.psql(f"DROP DATABASE {name}")


def test_snapshot_covers_every_contract_kind(snapshots):
    """前提校验：快照真的读到了触发器/函数/CHECK/部分索引，否则下面的"等价"是空对空。"""
    fresh = snapshots["fresh"]
    assert len(fresh["tables"]) >= 16
    assert len(fresh["functions"]) >= 8 and sum(len(v) for v in fresh["triggers"].values()) >= 13
    checks = [d for t in fresh["constraints"].values() for kind, d in t.values() if kind == "c"]
    assert len(checks) >= 10, "CHECK 约束没读到"
    assert "WHERE ((status)::text = 'pending'::text)" in fresh["indexes"]["sys_order"]["uq_sys_order_user_pending"]
    assert fresh["columns"]["sys_user"]["role"][2] == "NO", "sys_user.role 必须 NOT NULL（与 migrate_0005 一致）"


def test_legacy_adoption_reaches_the_fresh_catalog(snapshots):
    """0008 旧库经 adopt-legacy-0008 + 0009–0017 后，与 full_init.sql 的目录逐项相同。"""
    assert differences(snapshots["fresh"], snapshots["adopted"]) == []


def test_replaying_historical_migrations_reaches_the_fresh_catalog(snapshots):
    """从 0002 之前的形状原样执行 0002–0008 历史 SQL，再走 adopt，仍与 fresh 逐项相同。"""
    assert differences(snapshots["fresh"], snapshots["replayed"]) == []


def test_differences_reports_every_kind_of_drift():
    """比较器自检：多列、少索引、默认值变化都要被点名，不能因为某一类漏比而假绿。"""
    a = {"columns": {"t": {"c": ("integer", "int4", "NO", "0")}}, "indexes": {"t": {"i": "CREATE INDEX i ON t (c)"}}}
    b = {"columns": {"t": {"c": ("integer", "int4", "NO", "1"), "extra": ("text",)}}, "indexes": {"t": {}}}
    report = differences(a, b)
    assert any("/columns/t/c:" in line for line in report)
    assert any("only in B: /columns/t/extra" in line for line in report)
    assert any("only in A: /indexes/t/i" in line for line in report)
    assert differences(a, a) == []


def test_ledger_ddl_is_the_full_init_statement():
    """adopt 用的账本建表语句必须与 full_init.sql 逐字相同（此前由 ORM 编译，默认值拼法不同）。"""
    statement = db_admin.ledger_ddl()
    assert statement in (SQL_ROOT / "full_init.sql").read_text(encoding="utf-8")
    assert "DEFAULT CURRENT_TIMESTAMP" in statement and statement.startswith("CREATE TABLE schema_migration (")


# 为什么不重放 0001：它把无时区 TIMESTAMP 改成 TIMESTAMPTZ，前置形状（裸 TIMESTAMP 列）在现行
# full_init.sql 里已不存在，重建它等于自己再写一份旧 DDL 去验证自己；0001 的幂等分支
# （已是 timestamptz 就跳过）由 replayed 库上"再跑一次不报错"的事实覆盖——见下面这条。


def test_migration_0001_is_idempotent_on_current_schema(isolated_pg):  # noqa: F811
    name = "eq_" + uuid.uuid4().hex
    isolated_pg.psql(f"CREATE DATABASE {name} OWNER maintenance_test")
    uri = make_url(isolated_pg.get_uri()).set(username="maintenance_test", database=name).render_as_string(hide_password=False)
    conn = psycopg2.connect(uri)
    try:
        db_admin.initialize(conn)
        before = catalog(conn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((SQL_ROOT / "migrate_0001_timestamptz.sql").read_text(encoding="utf-8"))
        conn.autocommit = False
        assert differences(before, catalog(conn)) == [], "0001 在已是 TIMESTAMPTZ 的库上必须是空操作"
    finally:
        conn.close()
        isolated_pg.psql(f"DROP DATABASE {name}")
