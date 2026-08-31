from pathlib import Path

from app.tools.sql_ddl import parse_ddl

DDL = Path(__file__).resolve().parents[1] / "database init" / "full_init.sql"

PG_DDL = """
-- 用户表
CREATE TABLE sys_user (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50)  UNIQUE NOT NULL,
    status      SMALLINT DEFAULT 1, -- 状态：1正常，0禁用
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sys_order (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES sys_user(id),
    amount  NUMERIC(10,2) NOT NULL,
    status  VARCHAR(20) DEFAULT 'pending'
);
"""


def col(table, name):
    return next(c for c in table["columns"] if c["name"] == name)


def test_parses_tables_and_columns():
    graph = parse_ddl(PG_DDL)
    assert [t["name"] for t in graph["tables"]] == ["sys_user", "sys_order"]
    username = col(graph["tables"][0], "username")
    assert username["type"] == "VARCHAR(50)"
    assert username["nullable"] is False
    assert username["primary_key"] is False


def test_primary_key_and_nullable():
    graph = parse_ddl(PG_DDL)
    pk = col(graph["tables"][0], "id")
    assert pk["primary_key"] is True
    assert pk["nullable"] is False
    assert col(graph["tables"][0], "status")["nullable"] is True


def test_default_values_including_quoted():
    graph = parse_ddl(PG_DDL)
    assert col(graph["tables"][0], "status")["default"] == "1"
    assert col(graph["tables"][1], "status")["default"] == "pending"
    assert col(graph["tables"][0], "create_time")["default"] == "CURRENT_TIMESTAMP"


def test_decimal_with_comma_is_not_split():
    """NUMERIC(10,2) 里的逗号不能被当成列分隔符。"""
    graph = parse_ddl(PG_DDL)
    assert col(graph["tables"][1], "amount")["type"] == "NUMERIC(10,2)"
    assert len(graph["tables"][1]["columns"]) == 4


def test_line_comments_are_ignored():
    """`-- 状态：1正常，0禁用` 里的中文逗号不得影响切分。"""
    graph = parse_ddl(PG_DDL)
    assert len(graph["tables"][0]["columns"]) == 4


def test_inline_reference_produces_edge():
    graph = parse_ddl(PG_DDL)
    assert graph["edges"] == [{
        "from_table": "sys_order",
        "from_column": "user_id",
        "to_table": "sys_user",
        "to_column": "id",
    }]


def test_mysql_style_backticks_and_comment():
    graph = parse_ddl("""
    CREATE TABLE `order` (
      `id` INT PRIMARY KEY AUTO_INCREMENT,
      `user_id` INT NOT NULL,
      `note` VARCHAR(255) COMMENT '备注',
      CONSTRAINT `fk_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
    table = graph["tables"][0]
    assert table["name"] == "order"
    assert col(table, "note")["comment"] == "备注"
    assert graph["edges"] == [{
        "from_table": "order",
        "from_column": "user_id",
        "to_table": "user",
        "to_column": "id",
    }]


def test_composite_primary_key():
    graph = parse_ddl("""
    CREATE TABLE order_item (
        order_id INT NOT NULL,
        sku_id   INT NOT NULL,
        qty      INT NOT NULL,
        PRIMARY KEY (order_id, sku_id)
    );
    """)
    pks = [c["name"] for c in graph["tables"][0]["columns"] if c["primary_key"]]
    assert pks == ["order_id", "sku_id"]


def test_postgres_comment_on():
    graph = parse_ddl("""
    CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(10));
    COMMENT ON TABLE t IS '示例表';
    COMMENT ON COLUMN t.name IS '名称';
    """)
    assert graph["tables"][0]["comment"] == "示例表"
    assert col(graph["tables"][0], "name")["comment"] == "名称"


def test_schema_prefix_is_stripped():
    graph = parse_ddl("CREATE TABLE public.t (id INT PRIMARY KEY);")
    assert graph["tables"][0]["name"] == "t"


def test_returns_empty_when_no_create_table():
    assert parse_ddl("SELECT 1;") == {"tables": [], "edges": []}


def test_parses_project_own_schema():
    """回归：项目自己的 full_init.sql 必须能解析，且外键关系正确。"""
    graph = parse_ddl(DDL.read_text(encoding="utf-8"))
    names = {t["name"] for t in graph["tables"]}
    assert {"sys_user", "sys_order", "sys_config", "oauth_client", "oauth_code"} <= names

    order = next(t for t in graph["tables"] if t["name"] == "sys_order")
    assert col(order, "id")["primary_key"] is True
    assert col(order, "amount")["type"] == "INTEGER"
    assert col(order, "status")["default"] == "pending"

    fk = {(e["from_table"], e["from_column"], e["to_table"]) for e in graph["edges"]}
    assert ("sys_order", "user_id", "sys_user") in fk
    assert ("oauth_code", "user_id", "sys_user") in fk
    assert ("oauth_code", "client_id", "oauth_client") in fk


async def test_endpoint_returns_graph(client):
    r = await client.post("/tools/er-diagram", json={"ddl": PG_DDL})
    assert r.status_code == 200
    body = r.json()
    assert [t["name"] for t in body["tables"]] == ["sys_user", "sys_order"]
    assert len(body["edges"]) == 1


async def test_endpoint_is_public(client):
    """引流工具无需登录（与 /tools/ping 的受保护语义不同）。"""
    r = await client.post("/tools/er-diagram", json={"ddl": "CREATE TABLE t (id INT PRIMARY KEY);"})
    assert r.status_code == 200


async def test_endpoint_rejects_ddl_without_tables(client):
    r = await client.post("/tools/er-diagram", json={"ddl": "SELECT 1;"})
    assert r.status_code == 400


async def test_endpoint_rejects_empty_ddl(client):
    assert (await client.post("/tools/er-diagram", json={"ddl": ""})).status_code == 422
