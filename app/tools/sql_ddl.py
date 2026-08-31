"""SQL DDL → ER 图数据解析（S2-01-1）。

纯正则 + 括号深度扫描，不引入第三方解析库；兼容 MySQL 与 PostgreSQL 常见写法。
输出结构可直接喂给 D3.js 渲染 ER 图。
"""
from __future__ import annotations

import re

_IDENT = r"[`\"]?[\w$]+[`\"]?(?:\.[`\"]?[\w$]+[`\"]?)?"
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?", re.IGNORECASE)
_TYPE = re.compile(r"^([A-Za-z_]\w*(?:\s*\([^)]*\))?)")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_QUOTED = r"'(?:[^']|'')*'"
_CONSTRAINT_HEADS = {"PRIMARY", "FOREIGN", "UNIQUE", "KEY", "INDEX", "CONSTRAINT", "CHECK", "EXCLUDE"}


def parse_ddl(sql: str) -> dict:
    """解析 DDL，返回 {"tables": [...], "edges": [...]}。"""
    sql = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql))
    tables: list[dict] = []
    edges: list[dict] = []
    for raw_name, body in _iter_tables(sql):
        table, table_edges = _parse_table(_short(_unquote(raw_name)), body)
        tables.append(table)
        edges.extend(table_edges)
    _apply_comments(sql, tables)
    return {"tables": tables, "edges": edges}


def _iter_tables(sql: str):
    """逐个取出 (表名, 括号内的列定义体)。"""
    for m in _CREATE_TABLE.finditer(sql):
        rest = sql[m.end():]
        head = re.match(rf"\s*({_IDENT})\s*\(", rest)
        if not head:
            continue
        balanced = _read_balanced(rest[head.end() - 1:])
        if balanced:
            yield head.group(1), balanced[0]


def _read_balanced(s: str) -> tuple[str, int] | None:
    """从 s[0] == '(' 起读取配对括号内的内容，忽略引号内的括号。"""
    depth, quote = 0, ""
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[1:i], i + 1
    return None


def _split_top_level(body: str) -> list[str]:
    """按顶层逗号切分列定义，跳过括号内与引号内的逗号。"""
    parts, buf, depth, quote = [], [], 0, ""
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
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
        head = part.split()[0].strip("`\"").upper()
        if head in _CONSTRAINT_HEADS:
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

    default_m = re.search(rf"\bDEFAULT\s+({_QUOTED}|[\w.]+(?:\s*\(\s*\))?)", rest, re.IGNORECASE)
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
            "to_table": _short(_unquote(fk.group(1))),
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
    to_table = _short(_unquote(fk.group(2)))
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
    pattern = re.compile(
        rf"COMMENT\s+ON\s+(TABLE|COLUMN)\s+({_IDENT})\s+IS\s+({_QUOTED})", re.IGNORECASE
    )
    for m in pattern.finditer(sql):
        kind, target, text = m.group(1).upper(), m.group(2), _unquote(m.group(3))
        if kind == "TABLE":
            table = by_name.get(_short(_unquote(target)))
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
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"`":
        return s[1:-1].replace("''", "'")
    return s


def _short(name: str) -> str:
    """去掉 schema 前缀：public.sys_user -> sys_user。"""
    return name.split(".")[-1]
