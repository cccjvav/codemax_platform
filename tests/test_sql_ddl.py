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


# ---------- 解析器健壮性回归：每条都对应一个真实踩过的 bug ----------


def test_mysql_backslash_escaped_quote():
    r"""MySQL 的 \' 不能搞乱字符串状态 —— 曾经导致整张表解析不出来。"""
    graph = parse_ddl(r"CREATE TABLE t (name VARCHAR(50) COMMENT 'it\'s ok', age INT);")
    assert [t["name"] for t in graph["tables"]] == ["t"]
    assert col(graph["tables"][0], "name")["comment"] == "it's ok"
    assert col(graph["tables"][0], "age")["type"] == "INT"


def test_double_dash_inside_string_literal():
    """字面量里的 -- 不是注释 —— 曾经吃掉后半张表。"""
    graph = parse_ddl("CREATE TABLE t (status VARCHAR(20) DEFAULT 'a--b', note VARCHAR(50));")
    table = graph["tables"][0]
    assert col(table, "status")["default"] == "a--b"
    assert col(table, "note")["type"] == "VARCHAR(50)"


def test_slash_star_inside_string_literal():
    """字面量里的 /* 不能吞掉后文（即便后文真的有 */）。"""
    sql = "CREATE TABLE t (a INT COMMENT 'x /* y', b INT);\n/* 结束 */\nCREATE TABLE u (id INT);"
    graph = parse_ddl(sql)
    assert [t["name"] for t in graph["tables"]] == ["t", "u"]
    assert col(graph["tables"][0], "a")["comment"] == "x /* y"
    assert col(graph["tables"][0], "b")["type"] == "INT"


def test_line_comment_marker_inside_block_comment():
    """块注释里的 -- 不该先被行注释规则截断。"""
    graph = parse_ddl("CREATE TABLE t (a INT, /* -- 不是行注释 */ b INT);")
    assert [c["name"] for c in graph["tables"][0]["columns"]] == ["a", "b"]


def test_mysql_hash_comment():
    graph = parse_ddl("CREATE TABLE t (id INT PRIMARY KEY, # 这是主键\n name VARCHAR(20));")
    assert [c["name"] for c in graph["tables"][0]["columns"]] == ["id", "name"]


def test_create_table_inside_string_is_not_a_table():
    """字符串里的关键字不该凭空造出一张幻影表。"""
    graph = parse_ddl("CREATE TABLE t (a INT COMMENT '见 CREATE TABLE foo (id INT)', b INT);")
    assert [t["name"] for t in graph["tables"]] == ["t"]
    assert col(graph["tables"][0], "a")["comment"] == "见 CREATE TABLE foo (id INT)"


def test_quoted_column_named_like_constraint_keyword():
    """`key` / "index" 这种列名不能被当成表级约束而整列丢掉。"""
    pg = parse_ddl('CREATE TABLE t ("index" INT, id INT PRIMARY KEY);')
    assert [c["name"] for c in pg["tables"][0]["columns"]] == ["index", "id"]
    my = parse_ddl("CREATE TABLE t (`key` VARCHAR(20), id INT PRIMARY KEY);")
    assert [c["name"] for c in my["tables"][0]["columns"]] == ["key", "id"]


def test_signed_default_value():
    graph = parse_ddl("CREATE TABLE t (a INT DEFAULT -1, b INT DEFAULT +2);")
    assert col(graph["tables"][0], "a")["default"] == "-1"
    assert col(graph["tables"][0], "b")["default"] == "+2"


def test_backtick_table_with_schema_prefix():
    """`db`.`order` 的表名不能残留反引号 —— 否则外键指向不存在的表，图上会凭空少一条线。"""
    sql = (
        "CREATE TABLE `db`.`order` (`id` INT PRIMARY KEY, `uid` INT,"
        " CONSTRAINT `fk` FOREIGN KEY (`uid`) REFERENCES `db`.`user` (`id`));"
    )
    graph = parse_ddl(sql)
    assert graph["tables"][0]["name"] == "order"
    assert graph["edges"] == [
        {"from_table": "order", "from_column": "uid", "to_table": "db.user", "to_column": "id"}
    ]


def test_comment_on_with_double_dash_inside_literal():
    graph = parse_ddl("CREATE TABLE t (a INT);\nCOMMENT ON COLUMN t.a IS '状态：1--正常';")
    assert col(graph["tables"][0], "a")["comment"] == "状态：1--正常"


def test_standard_doubled_quote_still_unescaped():
    graph = parse_ddl("CREATE TABLE t (a INT DEFAULT 'it''s');")
    assert col(graph["tables"][0], "a")["default"] == "it's"


def test_string_with_comma_paren_and_semicolon():
    graph = parse_ddl("CREATE TABLE t (a INT DEFAULT 'x,y(z);w', b INT);")
    assert col(graph["tables"][0], "a")["default"] == "x,y(z);w"
    assert [c["name"] for c in graph["tables"][0]["columns"]] == ["a", "b"]


# ---------------------------------------------------------------- 方言边界（TD-269 / O-05）


def edges(graph):
    return sorted((e["from_table"], e["from_column"], e["to_table"], e["to_column"]) for e in graph["edges"])


def test_alter_table_add_foreign_key_is_an_edge():
    """pg_dump / mysqldump 都把外键放在 CREATE 之后的 ALTER 里；改前这类 DDL 一条边都出不来。"""
    graph = parse_ddl("""
        CREATE TABLE users (id INT PRIMARY KEY);
        CREATE TABLE orders (id INT PRIMARY KEY, user_id INT, buyer INT);
        ALTER TABLE orders ADD CONSTRAINT fk_o_u FOREIGN KEY (user_id) REFERENCES users(id);
        ALTER TABLE ONLY orders ADD FOREIGN KEY (buyer) REFERENCES public.users (id) ON DELETE CASCADE;
    """)
    assert edges(graph) == [("orders", "buyer", "users", "id"), ("orders", "user_id", "users", "id")]


def test_alter_for_unknown_table_or_inside_string_is_ignored():
    graph = parse_ddl("""
        CREATE TABLE t (id INT PRIMARY KEY, note TEXT DEFAULT 'ALTER TABLE t ADD FOREIGN KEY (id) REFERENCES x(id)');
        ALTER TABLE elsewhere ADD FOREIGN KEY (a) REFERENCES t(id);
    """)
    assert graph["edges"] == []


def test_implicit_parent_key_uses_single_column_primary_key():
    """`REFERENCES parent` 不写列 = 父表主键（SQL 标准）；列级与表级两种写法都要认。"""
    graph = parse_ddl("""
        CREATE TABLE users (id INT PRIMARY KEY, name TEXT);
        CREATE TABLE orders (id INT PRIMARY KEY, user_id INT REFERENCES users, buyer INT,
                             FOREIGN KEY (buyer) REFERENCES users);
    """)
    assert edges(graph) == [("orders", "buyer", "users", "id"), ("orders", "user_id", "users", "id")]


def test_implicit_parent_key_accepts_trailing_clauses_but_not_garbage():
    """省略列表的 REFERENCES 后面可以跟 ON/MATCH/DEFERRABLE/NOT NULL 等子句；但 `REFERENCES t-1(id)` 这种
    残缺写法（test_perf 里故意生成的首表）不能被截成 `REFERENCES t`——那是凭空多出一条边。"""
    graph = parse_ddl("""
        CREATE TABLE u (id INT PRIMARY KEY);
        CREATE TABLE t (id INT PRIMARY KEY, a INT REFERENCES u ON DELETE CASCADE, b INT REFERENCES u NOT NULL,
                        c INT REFERENCES u(id) DEFERRABLE INITIALLY DEFERRED, d INT, FOREIGN KEY (d) REFERENCES u MATCH FULL);
        ALTER TABLE t ADD FOREIGN KEY (a) REFERENCES u ON DELETE SET NULL;
        CREATE TABLE broken (id INT PRIMARY KEY, prev_id INT, FOREIGN KEY (prev_id) REFERENCES t-1(id), q INT REFERENCES t-1(id));
    """)
    assert edges(graph) == [("t", "a", "u", "id"), ("t", "a", "u", "id"), ("t", "b", "u", "id"),
                            ("t", "c", "u", "id"), ("t", "d", "u", "id")]


def test_implicit_parent_key_stays_blank_when_ambiguous():
    """父表是复合主键或不在 DDL 内：不猜列名，to_column 留空，边仍保留供前端按表连线。"""
    graph = parse_ddl("""
        CREATE TABLE pair (a INT, b INT, PRIMARY KEY (a, b));
        CREATE TABLE child (x INT REFERENCES pair, y INT REFERENCES outside);
    """)
    assert edges(graph) == [("child", "x", "pair", ""), ("child", "y", "outside", "")]


def test_unquoted_table_references_fold_case_but_quoted_ones_are_exact():
    """SQL 标准折叠（PostgreSQL 的做法）：不带引号的标识符折叠成小写再比较，带引号的按原样。

    改前只做逐字比较：`REFERENCES USERS(id)` 对不上 `CREATE TABLE Users`，前端当悬空外键丢掉。
    作图不替用户"修正"数据库会拒绝的写法：`"Mixed"` 定义、`mixed` 引用在 PG 里是表不存在，这里保持悬空。
    """
    graph = parse_ddl("""
        CREATE TABLE Users (ID INT PRIMARY KEY);
        CREATE TABLE orders (id INT PRIMARY KEY, user_id INT REFERENCES USERS(id), owner INT REFERENCES "users"(id),
                             other INT REFERENCES "Users"(id));
        CREATE TABLE "Mixed" (id INT PRIMARY KEY);
        CREATE TABLE ref (m INT REFERENCES "Mixed"(id), n INT REFERENCES mixed(id), o INT REFERENCES "mixed"(id));
    """)
    by_col = {(e["from_table"], e["from_column"]): e["to_table"] for e in graph["edges"]}
    assert by_col[("orders", "user_id")] == "Users", "USERS 与 Users 都折叠成 users"
    assert by_col[("orders", "owner")] == "Users", '"users" 精确等于定义折叠后的 users'
    # 定义 `Users` 不带引号 → 有效名 users；引用 `"Users"` 带引号 → 有效名 Users。两者不同，PG 会报表不存在。
    # 但解析器**先做逐字精确匹配**（写法与定义完全一致），所以这条仍连到 Users：保留的是原有行为，不是折叠规则。
    assert by_col[("orders", "other")] == "Users"
    assert by_col[("ref", "m")] == "Mixed"
    assert by_col[("ref", "n")] == "mixed", "不带引号的 mixed 折叠后与 Mixed 不同：悬空，与 PG 一致"
    assert by_col[("ref", "o")] == "mixed", '带引号的 "mixed" 与 "Mixed" 不同：悬空'


def test_dialect_type_modifiers_are_kept_or_dropped_predictably():
    """类型只保留名字/长度/精度/数组/WITH TIME ZONE；UNSIGNED、CHARACTER SET、GENERATED、SRID 等修饰不进类型串。"""
    graph = parse_ddl("""
        CREATE TABLE t (
          a INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          b ENUM('x','y') DEFAULT 'x',
          d DOUBLE PRECISION,
          e CHARACTER VARYING(20),
          f TIMESTAMP WITH TIME ZONE,
          g NUMERIC(10, 2) UNSIGNED ZEROFILL,
          h INT[],
          i BIGINT GENERATED ALWAYS AS IDENTITY,
          j VARCHAR(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
          l JSONB DEFAULT '{}'::jsonb
        );
    """)
    types = {c["name"]: c["type"] for c in graph["tables"][0]["columns"]}
    assert types == {"a": "INT", "b": "ENUM('X','Y')", "d": "DOUBLE PRECISION", "e": "CHARACTER VARYING(20)",
                     "f": "TIMESTAMP WITH TIME ZONE", "g": "NUMERIC(10,2)", "h": "INT[]", "i": "BIGINT",
                     "j": "VARCHAR(10)", "l": "JSONB"}
    assert col(graph["tables"][0], "a")["primary_key"] and not col(graph["tables"][0], "a")["nullable"]


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
