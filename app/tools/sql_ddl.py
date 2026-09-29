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
    - TD-328：同一折叠规则也用于**列名引用**（表级 PRIMARY KEY / FOREIGN KEY 的列、REFERENCES 的父列）与
      `COMMENT ON TABLE/COLUMN` 的目标；输出里的列名一律换成定义时的写法，前端按列名定位连线才对得上。
    - 表级 MySQL COMMENT、CHECK/EXCLUDE 内容、分区/继承等仍不解析；未闭合字符串/注释延续到结尾。
    - 部分合法 DDL 仍可能遗漏结构，输出需人工复核。
不能再把这些限制概括为"只影响显示，不影响结构"；完整方言解析属于后续工作。
"""
from __future__ import annotations

import re
from collections import Counter

_QUOTES = "'\"`"
_IDENT_PART = r'(?:"(?:[^"\n]|"")*"|`(?:[^`\n]|``)*`|[\w$]+)'
_IDENT = rf"{_IDENT_PART}(?:\s*\.\s*{_IDENT_PART})*"

_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
# 标识符字符（与 _IDENT_PART 的 [\w$] 一致）。紧跟在它后面的 `$` 属于标识符本身，不是 dollar 引号开头（TD-314）。
_IDENT_CHAR = re.compile(r"[\w$]")
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
_MAX_FALLBACK_SCANS = 16  # 见 _iter_tables：表名后的 '(' 被全局扫描判在字符串里时的逐段重扫次数上限
_TABLE_HEAD = re.compile(rf"\s*({_IDENT})\s*\(")
_ESCAPED = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "0": "\0", "Z": "\x1a"}


def parse_ddl(sql: str) -> dict:
    """解析 DDL，返回 {"tables": [...], "edges": [...]}。"""
    sql = _strip_comments(sql)
    tables: list[dict] = []
    edges: list[dict] = []
    # 一次扫描同时得到「字符串内下标」与括号配对表，后面各步复用（TD-285）
    index = _paren_index(sql)
    in_string = index[2]
    definitions = list(_iter_tables(sql, index))
    short_counts = Counter(_unquote(_short(raw)) for raw, _ in definitions)
    mapping = {tuple(_identifier_parts(raw)): (_qualified(raw) if short_counts[_unquote(_short(raw))] > 1 else _unquote(_short(raw)))
               for raw, _ in definitions}
    label_counts = Counter(mapping.values())
    for parts, label in list(mapping.items()):
        if label_counts[label] > 1:
            mapping[parts] = ".".join('"' + part.replace('"', '""') + '"' if '.' in part else part for part in parts)
    origins = {}
    column_keys: dict[str, dict[str, str]] = {}  # 表标签 → {列的有效名: 定义时的列名}
    quoted = {tuple(_identifier_parts(raw)): _quoted_parts(raw) for raw, _ in definitions}
    for raw_name, body in definitions:
        full = tuple(_identifier_parts(raw_name))
        name = mapping[full]
        origins[name] = _identifier_parts(raw_name)[:-1]
        table, table_edges, keys = _parse_table(name, body)
        tables.append(table)
        edges.extend(table_edges)
        column_keys.setdefault(name, keys)
    folded = _folded_index(mapping, quoted)
    for m in _ALTER_FK.finditer(sql):
        if m.start() in in_string:
            continue
        owner = _resolve(tuple(_identifier_parts(m.group(1))), _quoted_parts(m.group(1)), mapping, quoted, (), folded)
        if owner is None:
            continue  # ALTER 的表不在这份 DDL 里：无处挂边
        _fk_edges(owner, m.group(2), m.group(3), m.group(4), edges)
    pk_by_table = {t["name"]: [c["name"] for c in t["columns"] if c["primary_key"]] for t in tables}
    for edge in edges:
        target = edge["to_table"]
        resolved = _resolve(target, edge.pop("to_quoted", (False,) * len(target)), mapping, quoted, origins[edge["from_table"]], folded)
        edge["to_table"] = resolved if resolved is not None else ".".join(target)
        # 列名按有效名对到定义时的写法：`FOREIGN KEY (UID) REFERENCES users(ID)` 对 `uid` / `id` 列（TD-328）
        from_key, to_key = edge.pop("from_key"), edge.pop("to_key")
        edge["from_column"] = column_keys.get(edge["from_table"], {}).get(from_key, edge["from_column"])
        if edge["to_column"]:
            edge["to_column"] = column_keys.get(edge["to_table"], {}).get(to_key, edge["to_column"])
        if edge["to_column"] == "":
            # `REFERENCES parent` 不写列 = 父表主键（SQL 标准）；只有单列主键才能无歧义补全（TD-269）
            pk = pk_by_table.get(edge["to_table"], [])
            edge["to_column"] = pk[0] if len(pk) == 1 else ""
    _apply_comments(sql, tables, mapping, in_string, quoted=quoted, folded=folded, column_keys=column_keys)
    return {"tables": tables, "edges": edges}


def _col_key(raw: str) -> str:
    """列引用的有效名：取开头的标识符，带引号按原样，不带引号折叠成小写（与表名同一规则，TD-269/TD-328）。

    只取开头一个标识符，所以 `id ASC`、MySQL 前缀索引 `name(10)` 也对得上 `id` / `name`。"""
    m = re.match(_IDENT_PART, raw.strip())
    if not m:
        return raw.strip().lower()
    token = m.group()
    return _unquote(token) if token[0] in "\"`" else token.lower()


def _quoted_parts(name: str) -> tuple[bool, ...]:
    """每一段标识符是否带引号：带引号的按原样精确匹配，不带的按大小写不敏感折叠（TD-269）。"""
    return tuple(m.group()[0] in "\"`" for m in re.finditer(_IDENT_PART, name))


def _effective(parts: tuple[str, ...], quoted: tuple[bool, ...]) -> tuple[str, ...]:
    """标识符的有效名：带引号的按原样，不带引号的折叠成小写（SQL 标准折叠，PostgreSQL 的做法）。"""
    return tuple(p if q else p.lower() for p, q in zip(parts, quoted, strict=True))


def _folded_index(mapping: dict, quoted: dict) -> dict[tuple[str, ...], str]:
    """有效名 → 表标签；同一有效名有多张表时保留 mapping 里的第一张（与逐个比对的旧写法一致）。

    预先建一次，`_resolve` 每条引用就是一次字典查找。原先每条引用都把全部表的有效名
    重算一遍，500 张表各带一条对不上的外键就要 0.4 s（TD-285）。
    """
    folded: dict[tuple[str, ...], str] = {}
    for full, label in mapping.items():
        folded.setdefault(_effective(full, quoted.get(full, (False,) * len(full))), label)
    return folded


def _resolve(target: tuple[str, ...], target_quoted: tuple[bool, ...], mapping: dict, quoted: dict, origin: tuple | list,
             folded: dict | None = None) -> str | None:
    """把 REFERENCES 里写的表名对到 DDL 里实际定义的表。

    先按写法精确匹配（含补 schema 前缀、去 schema 前缀两种候选），再按有效名匹配：
    `Users` 定义 / `USERS` 引用 → 都折叠成 users，匹配；`"Mixed"` 定义 / `mixed` 引用 → Mixed ≠ mixed，
    不匹配（PostgreSQL 也会报表不存在，作图不替用户"修正"）。
    """
    if folded is None:
        folded = _folded_index(mapping, quoted)
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
        label = folded.get(_effective(cand, cand_quoted))
        if label is not None:
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
            "from_key": _col_key(src),
            "to_table": to_table,
            "to_quoted": to_quoted,
            "to_column": _unquote(dst) if dst else "",
            "to_key": _col_key(dst) if dst else "",
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
        # PostgreSQL 与 MySQL 都允许标识符里带 `$`（a$b$、cost$$）：`$` 紧跟在标识符字符后面时是名字的一部分，
        # 只有前面不是标识符字符时才可能开始 dollar 引号（PostgreSQL 词法规则相同，TD-314）。
        starts_dollar = ch == "$" and not (i and _IDENT_CHAR.match(s[i - 1]))
        dollar = _DOLLAR_QUOTE.match(s, i) if starts_dollar else None
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


def _paren_index(sql: str) -> tuple[dict[int, int], set[int], set[int]]:
    """一次扫描：(字符串外每个 '(' → 配对 ')' 的下标, 字符串外 '(' 的下标集合, 字符串内字符的下标集合)。

    用栈配对与「从这个 '(' 起重新数深度」结果相同：两者在该位置都处于字符串外，之后的扫描
    完全一样。未闭合的 '(' 不在配对表里。
    """
    match: dict[int, int] = {}
    opens: set[int] = set()
    in_string: set[int] = set()
    stack: list[int] = []
    for i, ch, in_str in _scan(sql):
        if in_str:
            in_string.add(i)
        elif ch == "(":
            stack.append(i)
            opens.add(i)
        elif ch == ")" and stack:
            match[stack.pop()] = i
    return match, opens, in_string


def _iter_tables(sql: str, index: tuple[dict[int, int], set[int], set[int]] | None = None):
    """逐个取出 (表名, 括号内的列定义体)。

    括号配对查一次扫描建好的表（TD-285）。原先每张表都从自己的 '(' 往后重新扫到配对为止，
    未闭合时一路扫到输入末尾：20000 字符的 `CREATE TABLE a(` 重复串要 4 秒多 CPU。
    """
    match, opens, in_string = index if index is not None else _paren_index(sql)
    fallbacks = 0
    for m in _CREATE_TABLE.finditer(sql):
        if m.start() in in_string:
            continue  # 字符串里的 CREATE TABLE 不算（例如 COMMENT '别写 CREATE TABLE'）
        head = _TABLE_HEAD.match(sql, m.end())
        if not head:
            continue
        start = head.end() - 1
        if start in opens:
            if start in match:
                yield head.group(1), sql[start + 1 : match[start]]
            continue
        # 全局扫描认为这个 '(' 在字符串里（双引号名里带反斜杠这类怪写法；表名里带 `$x$` 自 TD-314 起
        # 已由 _scan 正确识别，不再走这里），只能从它起重新扫描。每次最坏扫到输入末尾，所以限定次数：正常 DDL 不会走到这里，
        # 超过上限的这类表不再解析，防止用重复串把单次解析拖成秒级（TD-285）。
        fallbacks += 1
        if fallbacks > _MAX_FALLBACK_SCANS:
            continue
        balanced = _read_balanced(sql[start:])
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


def _parse_table(name: str, body: str) -> tuple[dict, list[dict], dict[str, str]]:
    """返回 (表, 本表的外键边, {列的有效名: 定义时的列名})；同一有效名重复定义时保留第一列。"""
    columns: list[dict] = []
    edges: list[dict] = []
    pk_cols: list[str] = []  # 表级 PRIMARY KEY 里写的列引用原文，按有效名对列（TD-328）
    keys: dict[str, str] = {}

    for part in _split_top_level(body):
        raw_head = part.split()[0]
        # 带引号的首词是标识符（列名 `key` / "index"），不能当成约束关键字
        if raw_head[0] not in _QUOTES and raw_head.strip("`\"").upper() in _CONSTRAINT_HEADS:
            _parse_constraint(part, name, edges, pk_cols)
        else:
            col, edge = _parse_column(part, name)
            if col:
                columns.append(col)
                keys.setdefault(_col_key(part), col["name"])
            if edge:
                edges.append(edge)

    pk_names = {keys[k] for k in map(_col_key, pk_cols) if k in keys}
    for col in columns:
        if col["name"] in pk_names:
            col["primary_key"] = True
            col["nullable"] = False
    return {"name": name, "columns": columns, "comment": None}, edges, keys


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
            "from_key": _col_key(m.group(1)),
            "to_table": tuple(_identifier_parts(fk.group(1))),
            "to_quoted": _quoted_parts(fk.group(1)),
            "to_column": _unquote(fk.group(2)) if fk.group(2) else "",  # 空 = 父表主键，parse_ddl 补全
            "to_key": _col_key(fk.group(2)) if fk.group(2) else "",
        }
    return col, edge


def _parse_constraint(part: str, table: str, edges: list[dict], pk_cols: list[str]) -> None:
    upper = part.upper()
    if re.search(r"\bPRIMARY\s+KEY\b", upper):
        m = re.search(r"\(([^)]*)\)", part)
        if m:
            pk_cols.extend(_split_top_level(m.group(1)))  # 保留原文：带不带引号决定怎么对列（TD-328）
        return
    fk = re.search(
        rf"\bFOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({_IDENT})\s*(?:\(([^)]*)\)|{_AFTER_REFERENCES})", part, re.IGNORECASE
    )
    if not fk:
        return
    _fk_edges(table, fk.group(1), fk.group(2), fk.group(3), edges)


def _apply_comments(sql: str, tables: list[dict], mapping: dict | None = None, in_string: set[int] | None = None, *,
                    quoted: dict | None = None, folded: dict | None = None,
                    column_keys: dict[str, dict[str, str]] | None = None) -> None:
    """应用 PostgreSQL 风格的 COMMENT ON TABLE / COMMENT ON COLUMN。

    目标表与 REFERENCES 一样经 `_resolve` 对到定义（大小写折叠、补/去 schema 前缀），对不上再退回
    原来的逐字查找；列按有效名对（TD-328）。原先只逐字查，`CREATE TABLE Users` 配
    `COMMENT ON TABLE users`、或注释写了 `public.` 而定义没写，注释都会被静默丢掉。"""
    by_name = {t["name"]: t for t in tables}
    mapping = mapping or {}
    quoted = quoted or {}
    column_keys = column_keys or {}
    if folded is None:
        folded = _folded_index(mapping, quoted)
    if in_string is None:
        in_string = _in_string_positions(sql)

    def table_for(raw_parts: list[str], raw_quoted: tuple[bool, ...]) -> tuple[str, dict] | None:
        key = tuple(raw_parts)
        label = _resolve(key, raw_quoted, mapping, quoted, (), folded) or mapping.get(key, ".".join(key))
        return (label, by_name[label]) if label in by_name else None

    pattern = re.compile(
        rf"COMMENT\s+ON\s+(TABLE|COLUMN)\s+({_IDENT})\s+IS\s+({_QUOTED})", re.IGNORECASE
    )
    for m in pattern.finditer(sql):
        if m.start() in in_string:
            continue
        kind, target, text = m.group(1).upper(), m.group(2), _unquote(m.group(3))
        parts, flags = _identifier_parts(target), _quoted_parts(target)
        if kind == "TABLE":
            found = table_for(parts, flags)
            if found:
                found[1]["comment"] = text
        else:
            if len(parts) < 2:
                continue
            found = table_for(parts[:-1], flags[:-1])
            if not found:
                continue
            label, table = found
            column = parts[-1] if flags[-1] else parts[-1].lower()
            name = column_keys.get(label, {}).get(column, parts[-1])
            for col in table["columns"]:
                if col["name"] == name:
                    col["comment"] = text


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
