# `scripts/` 模块说明书

> **行号基准 commit：`3f74718`**（2026-09-05）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`database init/README.md`（本脚本体检的对象）、`tests/README.md`、`app/README.md`、
> `app/routers/README.md`、`app/tools/README.md`、`app/templates/README.md`、
> `app/static/README.md`、`.github/workflows/README.md`、`docs/ROOT_FILES.md`。

---

## 1. 模块概述

### 1.1 定位

**两个脚本，职责完全不同，互不依赖**：

| 文件 | 行数 | 干什么 | 什么时候跑 |
| --- | --- | --- | --- |
| `check_schema_pg.mjs` | 68 | 建表脚本的**深度体检**（用 WASM 版真 PostgreSQL 执行 `full_init.sql`） | 改了建表脚本时，**可选** |
| `build_docs_site.py` | 935 | **文档站构建**：从代码里提取依赖图/路由表/符号表，把 21 份 Markdown 渲染成静态网站 | 改了文档或代码后想看网页版时 |

下面 §1.2 与 §2.1 讲第一个，§2.2 与 §3.5 讲第二个。

**它解决的问题**（文件头 L4-L5 原文）：

> CI / 沙箱里常常没有 PostgreSQL，建表脚本的**语法、外键、索引、DROP 顺序**就永远没被真库执行过。
> 这个脚本补上这一段，且**不引入 Python 依赖**。

做法是**用 WASM 版的真 PostgreSQL（PGlite）在 node 进程里跑** —— 不需要装数据库服务、不需要网络端口，一个 `new PGlite()` 就是一个真的 PG 实例。

### 1.2 `check_schema_pg.mjs` 在「三道校验」里的位置

| 校验 | 位置 | 什么时候跑 | 能抓到什么 |
| --- | --- | --- | --- |
| **ORM ↔ DDL 一致性** | `tests/test_schema_sync.py` | **每次 `pytest` 必跑** | 表名/列名对不上 |
| **真库执行（CI）** | `.github/workflows/ci.yml:149-155` | **每次 push 必跑** | PostgreSQL 不接受的语法；连跑两遍验幂等 |
| **深度体检（本脚本）** | `scripts/check_schema_pg.mjs` | **手动、可选** | 同上，**但无需 CI、无需装 PG** |

> **为什么它不接入 pytest**（TD-83，`TECH_DECISIONS.md:130`）：需要 node 与约 26 MB 的外部 npm 包，
> 属**可选的深度体检，不是每次改动的必经检查**。文件头 L11-L12 也写明了：
> 「`tests/test_schema_sync.py` 才是每次必跑的」。

### 1.3 实测结构

```text
scripts/
├── check_schema_pg.mjs    68 行   ESM 模块（.mjs），用了顶层 await
└── build_docs_site.py    935 行   文档站构建（32 个函数，需 mistune）
```

> **两个脚本都不接入 pytest** —— 它们是开发工具，不是每次改动的必经检查。
> 每次必跑的建表脚本校验是 `tests/test_schema_sync.py`。

---

## 2. 文件级详细说明书

### 2.1 📄 文件名：`check_schema_pg.mjs`（68 行）

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

### 2.2 📄 文件名：`build_docs_site.py`（935 行）

- **文件职责**：从代码里**真实提取**站点数据（模块依赖图 / 路由表 / 符号表），
  再把 21 份 Markdown 预渲染成**完全离线**的静态网站。
- **依赖**：只有渲染 HTML 时需要 `mistune`（纯 Python、零传递依赖）；
  加 `--data-only` 时**只用标准库**。

#### 常量　L22-L27

| 常量 | 行 | 作用 |
| --- | --- | --- |
| `ROOT` / `SITE` / `DATA` | L22-L24 | 仓库根、`docs/site/`、`docs/site/data/` |
| `EXCLUDE_DIRS` | L26 | 扫描时排除的目录（`.venv` `.git` `__pycache__` 等） |
| `CODE_EXT` | L27 | 认定为「代码文件」的后缀集合 |

#### 数据提取（四个函数，都用 `ast` 而不是正则）

**`build_import_graph()`　L55-L109** —— 提取内部依赖边。

- **关键：必须解析相对导入**。本仓库大量使用 `from ..models import X`、`from .config import Y`。
  **只看 `n.module.startswith("app")` 会漏到只剩 5 条边，实测真实是 179 条。**
- **L87 `if p.name != "__init__.py"`** —— 相对导入的层级要按「当前文件是不是包」退一层
- `_layer_of()`　L112-L126 —— 按路径给模块归层（API 层 / 业务逻辑层 / 基础设施 / 测试…）

**`build_routes()`　L132-L191** —— 提取路由表。

- **为什么用 AST 而不是正则**：鉴权有**两种写法**，正则只能抓到第一种 ——
  ① 装饰器里 `@router.post("/x", dependencies=[Depends(require_admin)])`
  ② **函数签名里** `async def f(user: User = Depends(get_current_user), ...)`
  **只看装饰器会得出「需鉴权 0 条」这种明显错误的结论**，实测真实是 16 条。
- **L168-L172** —— ⚠️ 不能用 `ast.get_source_segment(src, fn.args)`：
  `ast.arguments` 节点**实测返回 `None`**。改成按行区间取「装饰器起 → 函数体第一句」。
- `_file_anchor()`　L194-L204 —— 复刻 GitHub 的标题→锚点算法（含中文与 emoji）

**`build_symbols()`　L210-L238** —— 遍历 `FunctionDef` / `ClassDef`，
记录每个符号的文件、起止行、所属模块，以及**它对应哪份说明书**（`_DOC_MAP` L241 + `_doc_for()` L253-L259）。

**`build_manifest()`　L286-L310** —— 扫 21 份文档，记录标题、行数、小节数、代码块数。
分组顺序由 `DOC_GROUPS`（L265）决定，也就是侧边栏的顺序。

#### 静态渲染

**`render_site()`　L342-L438** —— 主渲染流程：21 份文档页 + 91 个源码页 + 首页 + 3 个可视化页。

几个必须知道的辅助函数：

| 函数 | 行 | 为什么不能省 |
| --- | --- | --- |
| `slug()` | L316-L322 | **必须保留中文**：仓库里有 `总览.md`，用 `[^A-Za-z0-9._-]` 会把汉字全删成空，产出 `__.md.html` 这种既难看又撞车的名字 |
| `_inject_heading_ids()` | L485-L503 | **mistune 默认不给标题加 `id`**（实测 `<h2 id=...>` 一个都没有），不注入则右侧目录与跨文档锚点**全部失效** |
| `_mark_mermaid()` | L472-L482 | 给 mermaid 代码块加提示条（本站刻意不加载 mermaid.js，见 `docs/site/README.md`） |
| `_postprocess()` | L506-L541 | 改写链接：`.md` → 文档页；`file.py:12` → 源码页对应行。**必须 `unquote`** —— mistune 的 url 插件会把中文与空格百分号编码 |
| `_rebase_nav()` | L559-L563 | 文档页与源码页在 `d/` `s/` 子目录下，站点级链接要加 `../` 前缀 |
| `_render_graph_page()` | L668-L752 | **依赖图直接生成 SVG**（按拓扑深度分层），不依赖 D3 |

**`main()`　L844-L894** —— 生成 `data/*.json`，然后渲染；`--data-only` 只到第一步。

---

## 3. 执行逻辑流

### 3.1 一次体检的完整过程

```text
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

```text
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

### 3.5 一次文档站构建的完整过程

```text
python scripts/build_docs_site.py
  │
  ├─ L851-L854   四个提取函数
  │     ├─ build_import_graph()   ast 解析 import（含相对导入）  → 78 模块 / 206 条边
  │     ├─ build_routes()         ast 解析装饰器 + 函数签名，
  │     │                         外加 site.py 的 Tool(path=)  → 39 条路由（18 需鉴权）
  │     ├─ build_symbols()        ast 遍历 FunctionDef/ClassDef → 250+ 个符号
  │     └─ build_manifest()       扫 21 份 .md                  → 行数随文档变
  │
  ├─ L867-L872   写 docs/site/data/{graph,routes,symbols,manifest,meta}.json
  │
  ├─ --data-only 到此为止（只要数据、不渲染，此时**不需要 mistune**）
  │
  ├─ L884-L887   检查 mistune 是否可用，没有就报清楚的错并退出
  │
  └─ L890        render_site()
        ├─ 20 份文档页   mistune 渲染 → 注入标题 id → 改写链接 → 写 docs/site/d/
        ├─ 91 个源码页   带行号 + 该文件全部符号的跳转条 → 写 docs/site/s/
        ├─ 首页          统计卡片 + 可视化入口 + 分组文档卡片
        ├─ graph.html    依赖图 SVG（按拓扑深度分层，40 核心模块 / 85 条边）
        ├─ routes.html   路由表（可按方法/鉴权/限流/路径过滤）
        ├─ symbols.html  符号卡片（可搜索、可按类型过滤）
        └─ data/search.json   侧栏搜索用的标题索引

结果：docs/site/ 共 116 页，**完全离线、双击 index.html 即开**
```

> **生成物不入 Git**：`docs/site/` 下只有 `README.md`、`style.css`、`site.js` 是手写源文件，
> 其余（`index.html`、`graph.html`、`routes.html`、`symbols.html`、`d/`、`s/`、`data/`）
> 都已在 `.gitignore` 排除。改完文档或代码后重跑本脚本即可（约 1 秒）。

> **本站为什么不做浏览器端渲染**：本沙箱实测 `cdn.jsdelivr.net` 不可达（HTTP 000），
> mermaid 的 ESM 要带 206 个 chunk / 17 MB，highlight.js 的 npm 包没有现成浏览器包。
> 三条都写在 `docs/site/README.md` 里。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `d01d5cc`。复核命令（在仓库根目录）：

```bash
# 1) 行数
python -c "import pathlib; print(len(pathlib.Path('scripts/check_schema_pg.mjs').read_text(encoding='utf-8').splitlines()))"
# 预期：68（另一个脚本 build_docs_site.py 是 935 行，见下面第 4 条）

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

# 4) build_docs_site.py 的行数与函数数
python -c "
import ast, pathlib
s = pathlib.Path('scripts/build_docs_site.py').read_text(encoding='utf-8')
t = ast.parse(s)
fns = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
print('行数', len(s.splitlines()), ' 函数', len(fns))
"
# 预期：行数 935  函数 32

# 5) 构建文档站并核对提取结果（需要 mistune）
pip install mistune
python scripts/build_docs_site.py --data-only
# 预期：模块 78 个 · 依赖边 206 条 · 路由 39 条 · 符号 268 个 · 文档 21 份
# （文档行数会随文档增改而变，本次实测 10066 行；模块/边/路由数只随代码变）

# 6) 每次必跑的那道校验（不是本脚本）
python -m pytest tests/test_schema_sync.py -q
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动本目录任何文件后，
> 本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **改 `full_init.sql` 后要跑三道**：`tests/test_schema_sync.py`（必跑）、
> CI 的真库 job（自动）、本脚本（可选但便宜）。


## 2026-09-11 文档清单补充

构建器补登记 `app/frontend/README.md`，源码归属在 `app/` 前优先匹配 `app/frontend/`。新增“审查记录”分组，保留上传的 `CONSOLIDATED_ERROR_SUMMARY.md` 及 `docs/REVIEW_CROSSCHECK.md`。测试将 DOC_GROUPS 与 git ls-files 的 Markdown 双向比较（排除 .claude Agent Skills），登记项要提交或暂存后才属于 tracked 清单。

当前文档页24份：原21 + 前端说明1 + 审查记录2，不应继续硬编码22作为所有未来提交的目标。源码 inventory 的 tracked／archive策略、依赖图别名边、搜索与锚点仍在待修范围。

## 模块职责

开发期构建、文档检查和数据库结构核对脚本。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`scripts/build_docs_site.py`](build_docs_site.py) | `b797ee4660e6` | L1–L1031 |
| [`scripts/check_docs_contract.py`](check_docs_contract.py) | `83f435863def` | L1–L148 |
| [`scripts/check_schema_pg.mjs`](check_schema_pg.mjs) | `0246b7b3475a` | L1–L68 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

文档门禁从 Git 文件清单发现代码，收集 AST 与 SHA-256；不会导入应用或读取密钥。

## 变更与验证

脚本本身要有反例测试；新增目录不能通过遗漏清单逃过检查。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。
