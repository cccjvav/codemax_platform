"""SQL DDL → ER 图数据解析（S2-01-1）。

正则 + 单遍字符扫描，不引入第三方解析库；兼容 MySQL 与 PostgreSQL 常见写法。
输出结构可直接喂给 D3.js 渲染 ER 图。

**所有结构性判断（括号配对、逗号切分、找 CREATE TABLE / COMMENT ON）都必须在
认得字符串字面量与注释的前提下进行**，否则下列写法会解析错乱：

    COMMENT 'it\\'s'                    MySQL 反斜杠转义 → 引号状态错乱，整张表丢失
    DEFAULT 'a--b'                     字面量里的 -- 被当行注释 → 后半张表被吃掉
    COMMENT 'x /* y'  ... 后文有 */     字面量里的 /* 吞掉后文 → 整张表丢失
    /* -- 不是行注释 */                 行注释规则先跑 → 块注释被截断，整张表丢失
    COMMENT '见 CREATE TABLE foo'       字符串里的关键字 → 凭空多出一张幻影表
    "index" INT / `key` VARCHAR(20)     带引号的列名撞约束关键字 → 该列丢失
    CREATE TABLE `db`.`t`              schema 前缀 + 反引号 → 表名残留反引号

以上每条都有回归测试（tests/test_sql_ddl.py）。

已知简化（只影响显示精度，不影响结构正确性）：
    - 多词类型只取首词：DOUBLE PRECISION → DOUBLE、CHARACTER VARYING(50) → CHARACTER
    - DEFAULT / COMMENT 只认单引号字面量（PostgreSQL 里双引号是标识符，不能当字符串）
    - 不支持 PostgreSQL 美元引用字符串（$$ ... $$）
    - 未闭合的字符串/块注释视为一直延续到结尾
"""
from __future__ import annotations

import re

_QUOTES = "'\"`"
_IDENT = r"[`\"]?[\w$]+[`\"]?(?:\.[`\"]?[\w$]+[`\"]?)?"
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?", re.IGNORECASE)
_TYPE = re.compile(r"^([A-Za-z_]\w*(?:\s*\([^)]*\))?)")
# 单引号字面量：允许 MySQL 反斜杠转义（\' \\）与 SQL 标准的 '' 双写
_QUOTED = r"'(?:\\.|''|[^'\\])*'"
_CONSTRAINT_HEADS = {"PRIMARY", "FOREIGN", "UNIQUE", "KEY", "INDEX", "CONSTRAINT", "CHECK", "EXCLUDE"}
_ESCAPED = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "0": "\0", "Z": "\x1a"}


def parse_ddl(sql: str) -> dict:
    """解析 DDL，返回 {"tables": [...], "edges": [...]}。"""
    sql = _strip_comments(sql)
    tables: list[dict] = []
    edges: list[dict] = []
    for raw_name, body in _iter_tables(sql):
        table, table_edges = _parse_table(_unquote(_short(raw_name)), body)
        tables.append(table)
        edges.extend(table_edges)
    _apply_comments(sql, tables)
    return {"tables": tables, "edges": edges}


def _scan(s: str):
    """唯一的字符扫描器，产出 (下标, 字符, 是否在字符串字面量内)。

    注释（-- 到行尾、MySQL 的 # 到行尾、/* */）整体折叠成一个空格，
    且注释里的引号不会污染字符串状态；字符串内正确处理 \\' 与 '' 两种转义。
    """
    quote = ""
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if quote:
            if ch == "\\" and quote != "`" and i + 1 < n:  # MySQL 反斜杠转义（反引号内不适用）
                yield i, ch, True
                yield i + 1, s[i + 1], True
                i += 2
                continue
            if ch == quote:
                if s[i + 1 : i + 2] == quote:  # 同字符双写转义：'' "" ``
                    yield i, ch, True
                    yield i + 1, ch, True
                    i += 2
                    continue
                quote = ""
            yield i, ch, True
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
            yield i, ch, True
            i += 1
            continue
        if s.startswith("--", i) or ch == "#":
            start = i
            end = s.find("\n", i)
            i = n if end == -1 else end
            yield start, " ", False
            continue
        if s.startswith("/*", i):
            start = i
            end = s.find("*/", i + 2)
            i = n if end == -1 else end + 2  # 未闭合的块注释吃掉剩余全部
            yield start, " ", False
            continue
        yield i, ch, False
        i += 1


def _strip_comments(sql: str) -> str:
    """剥掉注释，字符串字面量里的内容原样保留。"""
    return "".join(ch for _, ch, _ in _scan(sql))


def _in_string_positions(sql: str) -> set[int]:
    """字符串字面量内部字符的下标集合，用于跳过字符串里的 SQL 关键字。"""
    return {i for i, _, in_str in _scan(sql) if in_str}


def _iter_tables(sql: str):
    """逐个取出 (表名, 括号内的列定义体)。"""
    in_string = _in_string_positions(sql)
    for m in _CREATE_TABLE.finditer(sql):
        if m.start() in in_string:
            continue  # 字符串里的 CREATE TABLE 不算（例如 COMMENT '别写 CREATE TABLE'）
        rest = sql[m.end() :]
        head = re.match(rf"\s*({_IDENT})\s*\(", rest)
        if not head:
            continue
        balanced = _read_balanced(rest[head.end() - 1 :])
        if balanced:
            yield head.group(1), balanced[0]


def _read_balanced(s: str) -> tuple[str, int] | None:
    """从 s[0] == '(' 起读取配对括号内的内容，忽略字符串里的括号。"""
    depth = 0
    for i, ch, in_str in _scan(s):
        if in_str:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[1:i], i + 1
    return None


def _split_top_level(body: str) -> list[str]:
    """按顶层逗号切分列定义，跳过括号内与字符串内的逗号。"""
    parts, buf, depth = [], [], 0
    for _, ch, in_str in _scan(body):
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _parse_table(name: str, body: str) -> tuple[dict, list[dict]]:
    columns: list[dict] = []
    edges: list[dict] = []
    pk_cols: list[str] = []

    for part in _split_top_level(body):
        raw_head = part.split()[0]
        # 带引号的首词是标识符（列名 `key` / "index"），不能当成约束关键字
        if raw_head[0] not in _QUOTES and raw_head.strip("`\"").upper() in _CONSTRAINT_HEADS:
            _parse_constraint(part, name, edges, pk_cols)
        else:
            col, edge = _parse_column(part, name)
            if col:
                columns.append(col)
            if edge:
                edges.append(edge)

    for col in columns:
        if col["name"] in pk_cols:
            col["primary_key"] = True
            col["nullable"] = False
    return {"name": name, "columns": columns, "comment": None}, edges


def _parse_column(part: str, table: str) -> tuple[dict | None, dict | None]:
    m = re.match(rf"({_IDENT})\s+(.+)", part, re.DOTALL)
    if not m:
        return None, None
    name, rest = _unquote(m.group(1)), m.group(2).strip()

    type_m = _TYPE.match(rest)
    if not type_m:
        return None, None
    upper = rest.upper()

    default_m = re.search(
        rf"\bDEFAULT\s+({_QUOTED}|[+-]?[\w.]+(?:\s*\(\s*\))?)", rest, re.IGNORECASE
    )
    comment_m = re.search(rf"\bCOMMENT\s+({_QUOTED})", rest, re.IGNORECASE)
    primary_key = bool(re.search(r"\bPRIMARY\s+KEY\b", upper))

    col = {
        "name": name,
        "type": re.sub(r"\s+", "", type_m.group(1)).upper(),
        "primary_key": primary_key,
        "nullable": not primary_key and not re.search(r"\bNOT\s+NULL\b", upper),
        "default": _unquote(default_m.group(1)) if default_m else None,
        "comment": _unquote(comment_m.group(1)) if comment_m else None,
    }

    fk = re.search(rf"\bREFERENCES\s+({_IDENT})\s*\(\s*({_IDENT})\s*\)", rest, re.IGNORECASE)
    edge = None
    if fk:
        edge = {
            "from_table": table,
            "from_column": name,
            "to_table": _unquote(_short(fk.group(1))),
            "to_column": _unquote(fk.group(2)),
        }
    return col, edge


def _parse_constraint(part: str, table: str, edges: list[dict], pk_cols: list[str]) -> None:
    upper = part.upper()
    if re.search(r"\bPRIMARY\s+KEY\b", upper):
        pk_cols.extend(_paren_list(re.search(r"\(([^)]*)\)", part)))
        return
    fk = re.search(
        rf"\bFOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({_IDENT})\s*\(([^)]*)\)", part, re.IGNORECASE
    )
    if not fk:
        return
    to_table = _unquote(_short(fk.group(2)))
    sources = [c.strip() for c in fk.group(1).split(",") if c.strip()]
    targets = [c.strip() for c in fk.group(3).split(",") if c.strip()]
    for src, dst in zip(sources, targets):
        edges.append({
            "from_table": table,
            "from_column": _unquote(src),
            "to_table": to_table,
            "to_column": _unquote(dst),
        })


def _apply_comments(sql: str, tables: list[dict]) -> None:
    """应用 PostgreSQL 风格的 COMMENT ON TABLE / COMMENT ON COLUMN。"""
    by_name = {t["name"]: t for t in tables}
    in_string = _in_string_positions(sql)
    pattern = re.compile(
        rf"COMMENT\s+ON\s+(TABLE|COLUMN)\s+({_IDENT})\s+IS\s+({_QUOTED})", re.IGNORECASE
    )
    for m in pattern.finditer(sql):
        if m.start() in in_string:
            continue
        kind, target, text = m.group(1).upper(), m.group(2), _unquote(m.group(3))
        if kind == "TABLE":
            table = by_name.get(_unquote(_short(target)))
            if table:
                table["comment"] = text
        else:
            parts = [_unquote(p) for p in target.split(".")]
            if len(parts) < 2:
                continue
            table = by_name.get(parts[-2])
            if table:
                for col in table["columns"]:
                    if col["name"] == parts[-1]:
                        col["comment"] = text


def _paren_list(m: re.Match | None) -> list[str]:
    if not m:
        return []
    return [c.strip() for c in (_unquote(x) for x in m.group(1).split(",")) if c]


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in _QUOTES:
        return _unescape(s[1:-1])
    return s


def _unescape(s: str) -> str:
    """还原字面量内容：MySQL 反斜杠转义 + SQL 标准的 '' 双写。"""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append(_ESCAPED.get(nxt, nxt))
            i += 2
        elif ch == "'" and s[i + 1 : i + 2] == "'":
            out.append("'")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _short(name: str) -> str:
    """去掉 schema 前缀：public.sys_user -> sys_user。"""
    return name.split(".")[-1]
