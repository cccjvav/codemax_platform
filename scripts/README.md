# `scripts/` 模块说明书

> **行号基准 commit：`d01d5cc`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`database init/README.md`（本脚本体检的对象）、`tests/README.md`、`app/README.md`、
> `app/routers/README.md`、`app/tools/README.md`、`app/templates/README.md`、
> `app/static/README.md`、`.github/workflows/README.md`、`docs/ROOT_FILES.md`。

---

## 1. 模块概述

### 1.1 定位

**只有一个文件**：`check_schema_pg.mjs`，68 行。它是建表脚本的**深度体检工具**。

**它解决的问题**（文件头 L4-L5 原文）：

> CI / 沙箱里常常没有 PostgreSQL，建表脚本的**语法、外键、索引、DROP 顺序**就永远没被真库执行过。
> 这个脚本补上这一段，且**不引入 Python 依赖**。

做法是**用 WASM 版的真 PostgreSQL（PGlite）在 node 进程里跑** —— 不需要装数据库服务、不需要网络端口，一个 `new PGlite()` 就是一个真的 PG 实例。

### 1.2 它在「三道校验」里的位置

| 校验 | 位置 | 什么时候跑 | 能抓到什么 |
| --- | --- | --- | --- |
| **ORM ↔ DDL 一致性** | `tests/test_schema_sync.py` | **每次 `pytest` 必跑** | 表名/列名对不上 |
| **真库执行（CI）** | `.github/workflows/ci.yml:149-155` | **每次 push 必跑** | PostgreSQL 不接受的语法；连跑两遍验幂等 |
| **深度体检（本脚本）** | `scripts/check_schema_pg.mjs` | **手动、可选** | 同上，**但无需 CI、无需装 PG** |

> **为什么它不接入 pytest**（TD-83，`TECH_DECISIONS.md:130`）：需要 node 与约 26 MB 的外部 npm 包，
> 属**可选的深度体检，不是每次改动的必经检查**。文件头 L11-L12 也写明了：
> 「`tests/test_schema_sync.py` 才是每次必跑的」。

### 1.3 实测结构

```
scripts/
└── check_schema_pg.mjs   68 行   ESM 模块（.mjs），用了顶层 await
```

---

## 2. 文件级详细说明书

### 📄 文件名：`check_schema_pg.mjs`（68 行）

- **文件职责**：起一个 WASM PostgreSQL，把 `database init/full_init.sql` 真执行两遍，然后查表/外键/索引，最后写一条数据读回冒烟。

#### 文件头注释（L1-L13）

- **L7-L9 用法**（含一次性装依赖）：
  ```bash
  cd /tmp && npm install @electric-sql/pglite
  cd - && node scripts/check_schema_pg.mjs /tmp/node_modules/@electric-sql/pglite
  ```
  - **L7 注明「约 26MB」**
- **L11-L12 为什么不接入 pytest** —— 见 1.2

#### 路径解析（L14-L19）

- **L18 `repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..")`**
  - **ESM 里没有 `__dirname`**，必须 `fileURLToPath(import.meta.url)` 转一次
  - **`..` 是因为脚本在 `scripts/` 下**，要回到仓库根
- **L19 `sqlPath = join(repoRoot, "database init", "full_init.sql")`**
  - **目录名带空格，所以必须用 `join` 分段拼** —— 写成字符串 `"database init/full_init.sql"` 再交给 shell 会被词分割

#### `loadPGlite(spec)`　L22-L31

- **L21 注释说明了这个函数存在的原因**：**ESM 不支持目录导入**，传目录（如 `/tmp/node_modules/@electric-sql/pglite`）得先解析到入口文件
- **L25-L26** —— 读该目录的 `package.json`，取 `module` 或 `main` 字段，用 `pathToFileURL` 转成 `file://` URL
  - **`pkg.module ?? pkg.main`** —— ESM 入口优先，退回 CJS 入口
- **L27-L29 `catch { /* 不是目录就按模块名导入 */ }`** —— **传包名（`@electric-sql/pglite`）时读 `package.json` 会失败，正好落进这个分支**，直接按模块名 import
- **L30 `return (await import(target)).PGlite`** —— 动态 import

#### 主流程（L33-L68）

**L33 起 PGlite**
```js
const PGlite = await loadPGlite(process.argv[2] ?? "@electric-sql/pglite");
```
- **`process.argv[2]`** —— 第 3 个参数（`node` 是 `[0]`、脚本名是 `[1]`）；不传就用包名

**L34 `const db = new PGlite()`** —— **顶层 await，所以文件必须是 `.mjs`**（`.js` 默认是 CJS，顶层 await 会语法错）。

**L36 打印 PG 版本** —— `show server_version`。**本机实测输出 `18.3`**（PGlite 内置的 WASM PG 版本，与 CI 的 `postgres:16` 不同，见 3.3）。

**L38-L42 连跑两遍 `full_init.sql`**
- **L38 注释是这个脚本的核心价值**：**第二次能过，说明 `DROP TABLE IF EXISTS` 的顺序与幂等性没问题**
- **`db.exec()`** —— 执行整个 SQL 文本（多语句）

**L44-L47 查建出的表**
- 从 `information_schema.tables` 查 `table_schema='public'`，`order by 1`
- **本机实测 7 张表**：`oauth_client` `oauth_code` `sys_article` `sys_config` `sys_diagram` `sys_order` `sys_user`

**L49-L57 查外键**
- **三张 `information_schema` 视图 join**：`table_constraints`（找 `constraint_type='FOREIGN KEY'`）+ `key_column_usage`（子表列）+ `constraint_column_usage`（父表列）
- **L57 打印成 `子表.列->父表.列` 的形式**
- **本机实测 4 条**：
  ```
  oauth_code.client_id->oauth_client.id
  oauth_code.user_id->sys_user.id
  sys_diagram.user_id->sys_user.id
  sys_order.user_id->sys_user.id
  ```

**L59-L60 数索引**
- 从 `pg_indexes` 查 `schemaname='public'`
- **本机实测 15** —— ⚠️ **这个数字与 `database init/README.md` 说的「2 个索引」不矛盾，口径不同**，见 3.2

**L62-L67 冒烟测试**
- **L62 注释**：**真写一条数据再读回，确认表可用**
- **L63 先查 `admin` 用户的 id** —— `full_init.sql` 的种子数据里有这个账号
- **L64 `if (uid)`** —— 查不到就跳过（**不硬失败**，因为种子数据可能被改）
- **L65 往 `sys_diagram` 插一条**，**用参数化查询 `$1 $2 $3`**（不是字符串拼接）
- **L66 读回打印**

**L68 `console.log("体检通过")`** —— **最后一行才打印这句**，前面任何一步抛错都不会走到这里。

---

## 3. 执行逻辑流

### 3.1 一次体检的完整过程

```
node scripts/check_schema_pg.mjs /tmp/node_modules/@electric-sql/pglite
  │
  ├─ L18-L19  定位 database init/full_init.sql（用 import.meta.url，与 cwd 无关）
  ├─ L22-L31  解析 PGlite 入口（目录 → 读 package.json 的 module/main → file:// URL）
  ├─ L34      new PGlite()                     ← WASM 版真 PostgreSQL 起在进程内
  ├─ L36      show server_version              → 18.3
  ├─ L39-L42  full_init.sql 跑第 1 遍          → OK
  │           full_init.sql 跑第 2 遍          → OK   ← 幂等 + DROP 顺序都对
  ├─ L44-L47  information_schema.tables        → 7 张表
  ├─ L49-L57  三视图 join 查外键                → 4 条
  ├─ L59-L60  pg_indexes                       → 15
  ├─ L62-L67  插一条 sys_diagram 再读回         → { id: 1, name: '冒烟测试' }
  └─ L68      体检通过                          （退出码 0）
```

### 3.2 ⚠️ 「15 个索引」与「2 个索引」为什么都对

`database init/README.md` 说的是**显式 `CREATE INDEX` 语句有 2 条**（`idx_sys_diagram_user`、`idx_article_site`）。
本脚本 L59-L60 查 `pg_indexes` 得到 **15** —— 因为 **PostgreSQL 会为每个 `PRIMARY KEY` 与 `UNIQUE` 约束各建一个索引**：

```
7 个 PRIMARY KEY  +  6 个 UNIQUE  +  2 条显式 CREATE INDEX  =  15
```

（实测 `full_init.sql`：`PRIMARY KEY` 出现 7 次、`UNIQUE` 6 次、`CREATE INDEX` 2 条。）

**所以两个数字量的不是一回事**：一个数「写了多少条建索引语句」，一个数「库里实际有多少个索引」。**引用时必须说清口径**，否则看着像矛盾。

### 3.3 三道校验的版本差异（一个要知道的局限）

| 校验 | PostgreSQL 版本 | 来源 |
| --- | --- | --- |
| 本脚本 | **18.3**（本机实测） | PGlite 内置的 WASM PG |
| CI `test-postgres` | **16** | `ci.yml:115` 的 `postgres:16` service |
| `docker-compose.yml` | **16** | L5 的 `postgres:16` |

> **本脚本的 PG 版本比生产高**。绝大多数 DDL 在两者上行为一致，但**如果将来用了 PG 16 与 18 之间有差异的语法，本脚本可能过了而生产挂**。
> **所以它不能替代 CI 的真库 job** —— 这也正是 TD-83 把它定为「可选」而非「必跑」的原因之一。

### 3.4 谁在引用它

| 位置 | 怎么提它 |
| --- | --- |
| `HANDOVER.md:63` | 「可选深度体检：用 WASM 版真 PostgreSQL 执行 `full_init.sql`」 |
| `HANDOVER.md:83` | 给出命令：`node scripts/check_schema_pg.mjs <pglite 包路径>` |
| `TECH_DECISIONS.md:130` | **TD-83**：不接入 pytest 的取舍（需手动跑，且要装约 26 MB 的 npm 包） |
| `database init/README.md:63` | 「深度体检（可选）：用 WASM 版真 PostgreSQL（PGlite）执行」 |

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `d01d5cc`。复核命令（在仓库根目录）：

```bash
# 1) 行数
python -c "import pathlib; print(len(pathlib.Path('scripts/check_schema_pg.mjs').read_text(encoding='utf-8').splitlines()))"
# 预期：68

# 2) 真跑一遍体检（需要 node 与 PGlite）
cd /tmp
npm install @electric-sql/pglite
cd -
node scripts/check_schema_pg.mjs /tmp/node_modules/@electric-sql/pglite
# 本机实测输出（PGlite 版本可能随 npm 包升级而变，表/外键/索引数应稳定）：
#   PG 版本: 18.3
#   第 1 次执行 full_init.sql：OK
#   第 2 次执行 full_init.sql：OK
#   建出的表: [ oauth_client, oauth_code, sys_article, sys_config, sys_diagram, sys_order, sys_user ]
#   外键: 4 条
#   索引数: 15
#   写入并读回: { id: 1, name: '冒烟测试' }
#   体检通过

# 3) 「15 vs 2」的口径核对（本文 3.2）
python -c "
import re, pathlib
sql = pathlib.Path('database init/full_init.sql').read_text(encoding='utf-8')
pk = len(re.findall(r'PRIMARY KEY', sql)); uq = len(re.findall(r'\bUNIQUE\b', sql))
ci = re.findall(r'CREATE INDEX (\w+)', sql)
print(f'PK={pk} UNIQUE={uq} 显式CREATE INDEX={len(ci)} → pg_indexes 应为 {pk+uq+len(ci)}')
"
# 预期：PK=7 UNIQUE=6 显式CREATE INDEX=2 → 15

# 4) 每次必跑的那道校验（不是本脚本）
python -m pytest tests/test_schema_sync.py -q
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动本目录任何文件后，
> 本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **改 `full_init.sql` 后要跑三道**：`tests/test_schema_sync.py`（必跑）、
> CI 的真库 job（自动）、本脚本（可选但便宜）。
