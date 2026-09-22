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

语法子集，不是 MySQL/PostgreSQL 的完整解析器：
    - 支持常见 CREATE（含临时/UNLOGGED）、列/表内外键、PG COMMENT ON。
    - 保留常见多词/数组/限定类型及括号 DEFAULT；识别 dollar string 与嵌套注释。
    - TD-269 起：`ALTER TABLE [ONLY] t ADD [CONSTRAINT x] FOREIGN KEY (...) REFERENCES p (...)` 计入外键
      （pg_dump / mysqldump 都这么写）；`REFERENCES parent` 不写列时按父表主键补全（单列主键；复合主键或
      父表不在 DDL 内则留空列名）；不带引号的表名引用按大小写不敏感匹配（SQL 标准折叠），带引号的精确匹配。
    - 表级 MySQL COMMENT、CHECK/EXCLUDE 内容、分区/继承等仍不解析；未闭合字符串/注释延续到结尾。
    - 部分合法 DDL 仍可能遗漏结构，输出需人工复核。
不能再把这些限制概括为"只影响显示，不影响结构"；完整方言解析属于后续工作。
"""
from __future__ import annotations

import re

_QUOTES = "'\"`"
_IDENT_PART = r'(?:"(?:[^"\n]|"")*"|`(?:[^`\n]|``)*`|[\w$]+)'
_IDENT = rf"{_IDENT_PART}(?:\s*\.\s*{_IDENT_PART})*"

_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_CREATE_TABLE = re.compile(r"CREATE\s+(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?", re.IGNORECASE)
# ALTER TABLE [ONLY] [IF EXISTS] t ADD [CONSTRAINT name] FOREIGN KEY (cols) REFERENCES p [(cols)]（TD-269）
_ALTER_FK = re.compile(
    rf"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?({_IDENT})\s+ADD\s+(?:CONSTRAINT\s+{_IDENT}\s+)?"
    rf"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({_IDENT})\s*(?:\(([^)]*)\)|(?=\s*;|\s*$|\s+(?:ON|MATCH|DEFERRABLE|NOT|INITIALLY)\b))",
    re.IGNORECASE,
)
_TYPE = re.compile(rf"^({_IDENT}(?:\s+(?:PRECISION|VARYING))?(?:\s*\([^)]*\))?(?:\s+(?:WITH|WITHOUT)\s+TIME\s+ZONE)?(?:\s*\[\s*\])*)", re.IGNORECASE)
# 单引号字面量：允许 MySQL 反斜杠转义（\' \\）与 SQL 标准的 '' 双写
_QUOTED = r"'(?:\\.|''|[^'\\])*'"
_CONSTRAINT_HEADS = {"PRIMARY", "FOREIGN", "UNIQUE", "KEY", "INDEX", "CONSTRAINT", "CHECK", "EXCLUDE"}
# `REFERENCES parent` 省略列表时，后面只能是子句结束或这些关键字；其他残留（如 `t-1(id)` 里的 `-1`）不算合法引用
_AFTER_REFERENCES = r"(?=\s*$|\s*[,)]|\s+(?:ON|MATCH|DEFERRABLE|NOT|INITIALLY|CONSTRAINT|DEFAULT|NULL|CHECK|UNIQUE|PRIMARY|COMMENT|COLLATE|GENERATED|ENABLE|DISABLE|USING)\b)"
_ESCAPED = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "0": "\0", "Z": "\x1a"}


def parse_ddl(sql: str) -> dict:
    """解析 DDL，返回 {"tables": [...], "edges": [...]}。"""
    sql = _strip_comments(sql)
    tables: list[dict] = []
    edges: list[dict] = []
    definitions = list(_iter_tables(sql))
    short_names = [_unquote(_short(raw)) for raw, _ in definitions]
    mapping = {tuple(_identifier_parts(raw)): (_qualified(raw) if short_names.count(_unquote(_short(raw))) > 1 else _unquote(_short(raw)))
               for raw, _ in definitions}
    labels = list(mapping.values())
    for parts, label in list(mapping.items()):
        if labels.count(label) > 1:
            mapping[parts] = ".".join('"' + part.replace('"', '""') + '"' if '.' in part else part for part in parts)
    origins = {}
    quoted = {tuple(_identifier_parts(raw)): _quoted_parts(raw) for raw, _ in definitions}
    for raw_name, body in definitions:
        full = tuple(_identifier_parts(raw_name))
        name = mapping[full]
        origins[name] = _identifier_parts(raw_name)[:-1]
        table, table_edges = _parse_table(name, body)
        tables.append(table)
        edges.extend(table_edges)
    in_string = _in_string_positions(sql)
    for m in _ALTER_FK.finditer(sql):
        if m.start() in in_string:
            continue
        owner = _resolve(tuple(_identifier_parts(m.group(1))), _quoted_parts(m.group(1)), mapping, quoted, ())
        if owner is None:
            continue  # ALTER 的表不在这份 DDL 里：无处挂边
        _fk_edges(owner, m.group(2), m.group(3), m.group(4), edges)
    pk_by_table = {t["name"]: [c["name"] for c in t["columns"] if c["primary_key"]] for t in tables}
    for edge in edges:
        target = edge["to_table"]
        resolved = _resolve(target, edge.pop("to_quoted", (False,) * len(target)), mapping, quoted, origins[edge["from_table"]])
        edge["to_table"] = resolved if resolved is not None else ".".join(target)
        if edge["to_column"] == "":
            # `REFERENCES parent` 不写列 = 父表主键（SQL 标准）；只有单列主键才能无歧义补全（TD-269）
            pk = pk_by_table.get(edge["to_table"], [])
            edge["to_column"] = pk[0] if len(pk) == 1 else ""
    _apply_comments(sql, tables, mapping)
    return {"tables": tables, "edges": edges}


def _quoted_parts(name: str) -> tuple[bool, ...]:
    """每一段标识符是否带引号：带引号的按原样精确匹配，不带的按大小写不敏感折叠（TD-269）。"""
    return tuple(m.group()[0] in "\"`" for m in re.finditer(_IDENT_PART, name))


def _effective(parts: tuple[str, ...], quoted: tuple[bool, ...]) -> tuple[str, ...]:
    """标识符的有效名：带引号的按原样，不带引号的折叠成小写（SQL 标准折叠，PostgreSQL 的做法）。"""
    return tuple(p if q else p.lower() for p, q in zip(parts, quoted, strict=True))


def _resolve(target: tuple[str, ...], target_quoted: tuple[bool, ...], mapping: dict, quoted: dict, origin: tuple | list) -> str | None:
    """把 REFERENCES 里写的表名对到 DDL 里实际定义的表。

    先按写法精确匹配（含补 schema 前缀、去 schema 前缀两种候选），再按有效名匹配：
    `Users` 定义 / `USERS` 引用 → 都折叠成 users，匹配；`"Mixed"` 定义 / `mixed` 引用 → Mixed ≠ mixed，
    不匹配（PostgreSQL 也会报表不存在，作图不替用户"修正"）。
    """
    if len(target_quoted) != len(target):
        target_quoted = (False,) * len(target)
    candidates: list[tuple[tuple[str, ...], tuple[bool, ...]]] = [(target, target_quoted)]
    if len(target) == 1 and origin:
        candidates.insert(0, ((*origin, *target), (False,) * len(origin) + target_quoted))
    if len(target) > 1:
        candidates.append((target[-1:], target_quoted[-1:]))  # `public.users` 引用未带 schema 定义的 `users`
    for cand, _ in candidates:
        if cand in mapping:
            return mapping[cand]
    for cand, cand_quoted in candidates:
        wanted = _effective(cand, cand_quoted)
        for full, label in mapping.items():
            if len(full) == len(cand) and _effective(full, quoted.get(full, (False,) * len(full))) == wanted:
                return label
    return None


def _fk_edges(table: str, sources_text: str, target_name: str, targets_text: str | None, edges: list[dict]) -> None:
    to_table = tuple(_identifier_parts(target_name))
    to_quoted = _quoted_parts(target_name)
    sources = [c.strip() for c in _split_top_level(sources_text) if c.strip()]
    targets = [c.strip() for c in _split_top_level(targets_text) if c.strip()] if targets_text else [""] * len(sources)
    # strict=False：用户 DDL 写错列数时按短的一边配对，尽力出图而不是抛错
    for src, dst in zip(sources, targets, strict=False):
        edges.append({
            "from_table": table,
            "from_column": _unquote(src),
            "to_table": to_table,
            "to_quoted": to_quoted,
            "to_column": _unquote(dst) if dst else "",
        })


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
        dollar = _DOLLAR_QUOTE.match(s, i) if ch == "$" else None
        if dollar:
            delimiter = dollar.group()
            end = s.find(delimiter, i + len(delimiter))
            end = n if end < 0 else end + len(delimiter)
            for pos in range(i, end):
                yield pos, s[pos], True
            i = end
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
            depth = 1
            i += 2
            while i < n and depth:
                if s.startswith("/*", i):
                    depth += 1
                    i += 2
                elif s.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
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
    hidden = {i for i, _, quoted in _scan(rest) if quoted}
    upper = "".join(" " if i in hidden else c for i, c in enumerate(rest)).upper()

    def outside(pattern):
        return next((m for m in re.finditer(pattern, rest, re.IGNORECASE) if m.start() not in hidden), None)

    default_m = outside(
        rf"\bDEFAULT\s+({_QUOTED}|[+-]?[\w.]+(?:\s*\(\s*\))?)"
    )
    default_value = _unquote(default_m.group(1)) if default_m else None
    expression = outside(r"\bDEFAULT\s*(?=\()")
    if expression:
        balanced = _read_balanced(rest[expression.end():])
        if balanced:
            default_value = "(" + balanced[0] + ")"
    comment_m = outside(rf"\bCOMMENT\s+({_QUOTED})")
    primary_key = bool(re.search(r"\bPRIMARY\s+KEY\b", upper))

    type_text = re.sub(r"\s+", " ", type_m.group(1)).upper()
    type_text = re.sub(r"\s*([(),\[\]])", r"\1", type_text)
    type_text = re.sub(r"([(\[,])\s*", r"\1", type_text)
    col = {
        "name": name,
        "type": type_text,
        "primary_key": primary_key,
        "nullable": not primary_key and not re.search(r"\bNOT\s+NULL\b", upper),
        "default": default_value,
        "comment": _unquote(comment_m.group(1)) if comment_m else None,
    }

    fk = outside(rf"\bREFERENCES\s+({_IDENT})\s*(?:\(\s*({_IDENT})\s*\)|{_AFTER_REFERENCES})")
    edge = None
    if fk:
        edge = {
            "from_table": table,
            "from_column": name,
            "to_table": tuple(_identifier_parts(fk.group(1))),
            "to_quoted": _quoted_parts(fk.group(1)),
            "to_column": _unquote(fk.group(2)) if fk.group(2) else "",  # 空 = 父表主键，parse_ddl 补全
        }
    return col, edge


def _parse_constraint(part: str, table: str, edges: list[dict], pk_cols: list[str]) -> None:
    upper = part.upper()
    if re.search(r"\bPRIMARY\s+KEY\b", upper):
        pk_cols.extend(_paren_list(re.search(r"\(([^)]*)\)", part)))
        return
    fk = re.search(
        rf"\bFOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({_IDENT})\s*(?:\(([^)]*)\)|{_AFTER_REFERENCES})", part, re.IGNORECASE
    )
    if not fk:
        return
    _fk_edges(table, fk.group(1), fk.group(2), fk.group(3), edges)


def _apply_comments(sql: str, tables: list[dict], mapping: dict | None = None) -> None:
    """应用 PostgreSQL 风格的 COMMENT ON TABLE / COMMENT ON COLUMN。"""
    by_name = {t["name"]: t for t in tables}
    mapping = mapping or {}
    in_string = _in_string_positions(sql)
    pattern = re.compile(
        rf"COMMENT\s+ON\s+(TABLE|COLUMN)\s+({_IDENT})\s+IS\s+({_QUOTED})", re.IGNORECASE
    )
    for m in pattern.finditer(sql):
        if m.start() in in_string:
            continue
        kind, target, text = m.group(1).upper(), m.group(2), _unquote(m.group(3))
        if kind == "TABLE":
            table = by_name.get(mapping.get(tuple(_identifier_parts(target)), _qualified(target)))
            if table:
                table["comment"] = text
        else:
            parts = _identifier_parts(target)
            if len(parts) < 2:
                continue
            key = tuple(parts[:-1])
            table = by_name.get(mapping.get(key, ".".join(key)))
            if table:
                for col in table["columns"]:
                    if col["name"] == parts[-1]:
                        col["comment"] = text


def _paren_list(m: re.Match | None) -> list[str]:
    if not m:
        return []
    return [c.strip() for c in (_unquote(x) for x in _split_top_level(m.group(1))) if c]


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in _QUOTES:
        value = _unescape(s[1:-1])
        return value.replace(s[0] * 2, s[0]) if s[0] != "'" else value
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


def _identifier_parts(name: str) -> list[str]:
    return [_unquote(m.group()) for m in re.finditer(_IDENT_PART, name)]


def _qualified(name: str) -> str:
    return ".".join(_identifier_parts(name))


def _short(name: str) -> str:
    """Return the last identifier token, not the last dot inside quoted identifiers."""
    matches = list(re.finditer(_IDENT_PART, name))
    return matches[-1].group() if matches else name
