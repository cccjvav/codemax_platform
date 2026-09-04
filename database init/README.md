# `database init/` 模块说明书

> **行号基准 commit：`f63ce49`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/README.md`（`models.py` 是这里的 ORM 对偶）、`app/routers/README.md`、`app/tools/README.md`、
> `app/templates/README.md`（前端模板）、`app/static/README.md`、`.github/workflows/README.md`、`docs/ROOT_FILES.md`、`tests/README.md`、`scripts/README.md`。

---

## 1. 模块概述

### 1.1 定位

这个目录是**数据库的真相来源**：7 个脚本文件、489 行（不含本 README）。

> ⚠️ **目录名带一个空格**（`database init`）。Windows `cmd.exe` 里必须加引号：
> `cd "database init"`。这是本目录最容易踩的坑，`docs/WINDOWS_LOCAL_RUN.md:142-150` 专门讲过。

它回答两个不同的问题，因此分成两类文件：

| 类别 | 文件 | 回答的问题 |
| --- | --- | --- |
| **全新部署** | `db_init.py` + `full_init.sql` | 「从零开始，库长什么样？」 |
| **已有数据升级** | `migrate_0001` ~ `migrate_0005` | 「线上已经有数据了，怎么加一列而不丢东西？」 |

**两类不能混用**，每个迁移脚本的头部都用同一句话强调（如 `migrate_0001_timestamptz.sql:15-16`）：

> ⚠️ 本脚本针对**已有数据的生产库**。全新部署直接跑 `full_init.sql` 即可，不需要本脚本。

### 1.2 实测结构

```
database init/                     489 行
├── db_init.py                      95 行   建库 + 建表的两步引导脚本
├── full_init.sql                  115 行   7 表 / 2 索引 / 2 处种子数据（★ 必须与 app/models.py 同步）
├── migrate_0001_timestamptz.sql    86 行   TD-146  时间列 TIMESTAMP → TIMESTAMPTZ
├── migrate_0002_password_changed_at.sql  38 行   TD-70   加 sys_user.password_changed_at
├── migrate_0003_diagram_deleted_at.sql   61 行   TD-64   加 sys_diagram.deleted_at + 改索引
├── migrate_0004_diagram_version.sql      41 行   TD-65   加 sys_diagram.version（乐观锁）
└── migrate_0005_user_role.sql            53 行   TD-138  加 sys_user.role（管理员）
```

`full_init.sql` 实测计数：

```
CREATE TABLE  7      DROP TABLE  7      CREATE INDEX  2      INSERT INTO  2
TIMESTAMPTZ  12 列   裸 TIMESTAMP 类型  0      CURRENT_TIMESTAMP 默认值  8
REFERENCES（外键）  4      UNIQUE  6
表：sys_user  sys_order  sys_config  oauth_client  oauth_code  sys_diagram  sys_article
索引：idx_sys_diagram_user (user_id, deleted_at)      idx_article_site (source_site)
```

### 1.3 谁在用这些文件（实测 `grep -rn`）

| 使用方 | 位置 | 怎么用 |
| --- | --- | --- |
| **开发者本地** | `README.md:21`、`AGENTS.md:14` | `cd "database init" && python db_init.py` |
| **Docker 首次启动** | `docker-compose.yml:13` | 挂载成 `/docker-entrypoint-initdb.d/01_init.sql`，**只在库为空时执行** |
| **CI** | `.github/workflows/ci.yml:152-153` | **连跑两遍**验证幂等（见 3.2） |
| **ORM 一致性测试** | `tests/test_schema_sync.py:13` | 与 `Base.metadata` 逐表逐列比对 |
| **DDL 解析器的真实输入** | `tests/test_sql_ddl.py:5`、`test_er_page.py:18`、`test_word_export.py:16` | 拿项目自己的建表脚本当测试数据 |
| **深度体检（可选）** | `scripts/check_schema_pg.mjs:19` | 用 WASM 版真 PostgreSQL（PGlite）执行 |
| **状态常量回归** | `tests/test_order_state.py:52` | 「改常量必须同时改 full_init.sql 与既有数据」 |

> **`.dockerignore:14` 排除了 `database init`** —— 镜像里不含这个目录。但 `docker-compose.yml:13` 是从**宿主机**挂载进去的，所以照样能用。这两件事不矛盾，但容易看糊涂。

### 1.4 全目录统一的三条约定

**① 幂等，但两种幂等的代价完全不同。**

| | `full_init.sql` | 5 个迁移脚本 |
| --- | --- | --- |
| 手法 | 开头 `DROP TABLE IF EXISTS ... CASCADE` | `DO $$` 块 + 查 `information_schema` 判断 |
| 重复执行 | **安全，但会清空全部数据** | 安全，且**不动数据** |
| 适用 | 全新部署 / CI | 已有数据的生产库 |

**② 时间列一律 `TIMESTAMPTZ`。** 12 个时间列全部带时区，**裸 `TIMESTAMP` 类型 0 个**。由 `tests/test_schema_sync.py:61-73` 的 `test_full_init_sql_uses_timestamptz` 钉住（它专门排除了 `CURRENT_TIMESTAMP` 字样，不误报）。

**③ 迁移脚本刻意不做的事，写在脚本里而不是口头。** 最典型的是 `migrate_0005_user_role.sql:11-12`：**本次迁移不会让任何现存用户变成管理员 —— 提权必须由运维显式执行，不能靠迁移脚本顺带发生**。提权语句以注释形式放在 L47-L53。

---

## 2. 文件级详细说明书

### 📄 文件名：`db_init.py`（95 行）

- **文件职责**：从零建库建表。**必须分两步**，原因写在模块 docstring L13-L14：`CREATE DATABASE` **不能在事务块内执行**，也不能在已连接的目标库上执行。

#### 模块级常量（L23-L30）

| 常量 | 行 | 值/来源 |
| --- | --- | --- |
| `load_dotenv(dotenv_path="../.env")` | L23 | **相对路径** —— 见下方⚠️ |
| `DB_HOST` | L25 | 环境变量，默认 `localhost` |
| `DB_PORT` | L26 | 默认 `5432` |
| `DB_NAME` | L27 | 默认 `codemax_db` |
| `DB_USER` | L28 | 默认 `postgres` |
| `DB_PASSWORD` | L29 | 默认空串 |
| `SCHEMA_FILE` | L30 | **用 `__file__` 拼绝对路径**，所以从任何 cwd 都能找到 SQL |

> ⚠️ **L23 是全脚本唯一的路径陷阱**：`../.env` 是相对路径，取决于**当前工作目录**。
> 所以在项目根目录直接跑 `python "database init/db_init.py"` 会**读不到 `.env`**，
> 报 `FATAL: password authentication failed`（密码明明是对的）。
> **必须先 `cd "database init"` 再跑** —— 这条已写进 `docs/WINDOWS_LOCAL_RUN.md:142-150` 与该文件排错表的第 305 行。
>
> 对比 L30 的 `SCHEMA_FILE`：它用 `os.path.abspath(__file__)` 拼，**不受 cwd 影响**。
> 同一文件里两种做法并存，这就是为什么只有 `.env` 会出问题。

#### 核心函数

**`database_exists(conn, db_name) -> bool`　L35-L38**
- **L37 查 `pg_database` 系统表**，用**参数化查询**（`%s`）

**`create_database(conn, db_name) -> None`　L41-L46**
- **L43 在函数内部 import `psycopg2.sql.Identifier`**
- **L46 用 `Identifier(db_name).as_string(conn)` 而不是字符串拼接**。L42 注释说明原因：**避免库名拼接 SQL 注入 —— `CREATE DATABASE` 不支持参数化**，所以只能靠 `Identifier` 做转义

**`main() -> None`　L49-L91**
- **第一步（L50-L67）：连维护库 `postgres` 建库**
  - **L55 连的是 `database="postgres"`**，不是目标库
  - **L59 `admin_conn.autocommit = True`** —— **L51 注释说明**：为了让 `CREATE DATABASE` 不在事务块内执行
  - **L61-L65 分支**：已存在就打印「跳过创建」，否则建库
  - **L66-L67 `finally` 里关连接** —— 无论成败都关
- **第二步（L69-L91）：连目标库执行 SQL**
  - **L76 `options="-c client_encoding=UTF8"`** —— 保证中文注释不乱码
  - **L78 `conn.autocommit = False`** —— 这一步**要**事务：整个建表脚本要么全成、要么全滚
  - **L80-L84 用括号式 `with` 同时管游标与文件**，`cursor_factory=RealDictCursor`
  - **L84 `cur.execute(sql_file.read())`** —— 整个文件一次执行（脚本内部自己分语句）
  - **L85 commit** / **L87-L89 异常则 rollback 后 `raise`（不吞）** / **L90-L91 `finally` 关连接**

**L94-L95 `if __name__ == "__main__": main()`**

---

### 📄 文件名：`full_init.sql`（115 行）

- **文件职责**：完整建表脚本。**★ 必须与 `app/models.py` 保持一致**（L7 的注释就写着这句），由 `tests/test_schema_sync.py` 自动比对。

#### 结构

**L1-L8 文件头注释** —— 说明本文件由 `db_init.py` 自动执行，并给出手动执行的 `psql` 命令（L5-L6）。

**L10-L17 第 1 段：删表**
- **7 条 `DROP TABLE IF EXISTS ... CASCADE`**
- **删除顺序是反的**（先 `sys_article`、最后 `sys_user`）—— 虽然有 `CASCADE` 兜底，但按依赖反序删是更稳妥的习惯
- **L10 注释写明目的**：「便于重复执行」。**代价是数据全没**，所以这个文件只能用于全新部署

**L19-L31 第 2 段：`sys_user`**
- **L26 `status SMALLINT DEFAULT 1`** —— 1 正常 / 0 禁用
- **L27 `role SMALLINT DEFAULT 0`** —— 0 普通 / 1 管理员（TD-138）
- **L28 `password_changed_at TIMESTAMPTZ`** —— 最近改密码时刻，JWT 校验用（TD-70）
- **L29-L30 `create_time` / `update_time`** 都用 `DEFAULT CURRENT_TIMESTAMP`

**L33-L46 第 3 段：`sys_order`**
- **L36 `order_no VARCHAR(32) UNIQUE NOT NULL`** —— 与 `wechat_pay.new_order_no()` 生成的 28 字符对齐
- **L37 `user_id ... REFERENCES sys_user(id)`** —— 外键
- **L39 `amount INTEGER NOT NULL`** —— **金额单位是分**（整数，不用浮点）
- **L40 `status VARCHAR(20) DEFAULT 'pending'`** —— 与 `app/order_state.py:22` 的 `PENDING` 常量对应
- **L41-L42 `code_url` / `transaction_id`**

**L48-L54 第 4 段：`sys_config`** —— 键值配置表，`config_key` 唯一。

**L56-L58 第 5 段：种子管理员**
- **插入 `admin` 账号，密码 `123456`**，库里只存 **bcrypt(rounds=12) 哈希**
- **L56 注释明确了明文只在注释里**，不落库

**L60-L68 第 6 段：`oauth_client`**
- **L64 `client_secret_hash`** —— **只存哈希，不存明文**

**L70-L80 第 7 段：`oauth_code`**
- **L73 `code VARCHAR(64) UNIQUE NOT NULL`** —— 一次性授权码
- **L75 `client_id ... REFERENCES oauth_client(id)`** —— 注意这是**外键指向 oauth_client**，与 `sys_user.id` 无关
- **L77 `expires_at TIMESTAMPTZ NOT NULL`**
- **L78 `used BOOLEAN DEFAULT FALSE`** —— 配合 `app/routers/oauth.py` 的原子消费

**L82-L87 第 8 段：种子 OAuth 客户端**
- 两个：`tools`（工具平台）、`shop`（商业平台）
- **L82-L84 注释写明**：明文密钥仅存于本注释与 README，**库内只存 bcrypt 哈希**
- `redirect_uri` 分别是 `https://tools.codemax.top/callback` 与 `https://shop.codemax.top/callback`

**L89-L101 第 9 段：`sys_diagram` + 索引**
- **L95 `deleted_at TIMESTAMPTZ`** —— 软删除（TD-64），NULL = 存活
- **L96 `version INTEGER NOT NULL DEFAULT 1`** —— 乐观锁（TD-65），每次保存 +1
- **L100-L101 索引 `(user_id, deleted_at)`**。**L100 注释说明为什么把 `deleted_at` 并进去**：列表与配额统计都是「某个用户的**存活**行」，把过滤列并进索引才不用回表再筛一遍

**L103-L113 第 10 段：`sys_article`**
- **L106 `url VARCHAR(500) UNIQUE NOT NULL`** —— 同一篇不重复入库
- **L109 `published_at VARCHAR(50)`** —— **L109 行内注释**：源站原文，格式各异，**不强行解析**（TD-137）
- **L111 `source_site VARCHAR(200)`** —— 域名，便于按站分组

> ⚠️ **L103 的段号注释写的是「7. 文章表」，与前面 L70 的「7. 一次性授权码表」重号了。**
> 这是历史遗留的编号漂移（`sys_diagram` 是后来插进来的第 9 段），**不影响执行**，
> 但读的时候别被段号误导 —— 以 `CREATE TABLE` 的顺序为准。

**L115 `CREATE INDEX idx_article_site ON sys_article(source_site)`**
- 注意这里 `sys_article(source_site)` **括号前没有空格** —— 与 L101 的写法不同，正则匹配时要留意

---

### 📄 文件名：`migrate_0001_timestamptz.sql`（86 行）

- **文件职责**：时间列 `TIMESTAMP` → `TIMESTAMPTZ`（TD-146）。**5 个迁移里最复杂的一个**，因为它要处理**历史数据的语义**。

#### 为什么需要（L4-L11，这段是全文件的价值所在）

改类型之前，本仓库有**三种互不一致的时钟基准**写在同一批无时区 `TIMESTAMP` 列里：

| 列 | 原来的基准 | 来源 |
| --- | --- | --- |
| `create_time` / `update_time` | **数据库服务器本地时间** | `server_default=CURRENT_TIMESTAMP` |
| `paid_at` | **应用服务器本地时间** | 应用侧 `datetime.now()` |
| `oauth_code.expires_at` | **裸 UTC**（抹掉了 tzinfo） | 应用侧 UTC 后 `.replace(tzinfo=None)` |

**L10-L11 说明后果**：裸值之间无法正确比较 —— 数据库不在 UTC 时区时，`expires_at` 会比 `create_time` 早若干小时，`paid_at` 也可能早于同表 `create_time`。

#### 结构

**L23 `BEGIN;` / L79 `COMMIT;`** —— **5 个迁移里唯一显式开事务的**（其余 4 个靠 `DO` 块自身原子性）。

**L27-L77 `DO $$ ... $$` 块，三段转换，每段的 `USING` 子句不同**

> **L25-L26 的注释是本脚本最关键的一句**：**关键在 `USING` 子句** —— 它告诉 PostgreSQL「这个裸值原本该按哪个时区解读」，**解读错了就会把历史数据整体平移若干小时**。

- **① L32-L45 `create_time` / `update_time`**
  - **L33-L37 用 `information_schema.columns` 找出所有还是 `timestamp without time zone` 的这两列**（跨表通用，不写死表名）
  - **L39-L43 `EXECUTE format(...)` 动态 ALTER**，`USING %I AT TIME ZONE current_setting('TimeZone')` —— **按数据库时区解读**
  - **L44 `RAISE NOTICE` 逐列报告**
- **② L48-L57 `oauth_code.expires_at`**
  - **L55 `USING expires_at AT TIME ZONE 'UTC'`** —— **这一列按 UTC 解读**，因为它原本就是 UTC，只是被抹掉了 tzinfo
- **③ L64-L75 `sys_order.paid_at`**
  - **L72 按数据库时区解读**
  - **L59-L63 的注释诚实说明了一个无法自动修复的情况**：这一列原本是**应用服务器**本地时间。单机部署（应用与数据库同区）时按数据库时区解读即为正确；**若两者不同区，这批历史值在改类型之前就已经是错的，无法从数据本身恢复** —— 只能按当时应用所在时区手工修正
  - **L74 `RAISE WARNING`** —— 主动把这个隐患喊出来，不静默

**L82-L86 末尾校验** —— 列出所有时间列的 `data_type`，**应全部为 `timestamp with time zone`**。

**幂等性**：`information_schema` 查询条件里带 `data_type = 'timestamp without time zone'`，已转过的列自然查不出来（L20 注释）。

---

### 📄 文件名：`migrate_0002_password_changed_at.sql`（38 行）

- **文件职责**：`sys_user` 加 `password_changed_at`（TD-70）。

#### 为什么需要（L4-L9）

改密码之后，此前签发的 JWT 仍然有效，直到自然过期。**若用户是因为密码泄露才改的密码，这段窗口正好是攻击者还能用的时间。**

**做法是「令牌版本化」而不是 jti 黑名单**（L7-L9）：把「改密码的时刻」写进 JWT 声明，校验时与库里当前值比对。**好处是不用建黑名单表、不用每次登录写库，而且一次就能吊销该用户所有旧 token（黑名单得先把它们枚举出来）**。

**L11-L12 语义约定**：`NULL` 表示「从未改过密码」。此时任何不带 `pwd` 声明的旧 token 仍然有效 —— **这是刻意的，否则本次上线会让全站已登录用户瞬间掉线**。

#### 结构

**L21-L33 `DO $$` 块**
- **L23-L26 查 `information_schema.columns` 判断列是否已存在**
- **L27 `ALTER TABLE sys_user ADD COLUMN password_changed_at TIMESTAMPTZ`**（可空，无默认值）
- **L28 / L30 两条 `RAISE NOTICE`** —— 加了还是跳过，都报告

**L36-L38 末尾校验** —— 查这一列的 `column_name, data_type`。

---

### 📄 文件名：`migrate_0003_diagram_deleted_at.sql`（61 行）

- **文件职责**：`sys_diagram` 加 `deleted_at` + 改索引（TD-64）。

#### 为什么需要（L4-L5）

流程图原本**没有配额也没有软删除** —— 任何人都能无限建图把库刷满，而删除是硬删（`DELETE` 一行），**用户手滑删掉一张图就再也找不回来**。

#### 结构（两个 `DO $$` 块）

**① L24-L36 加软删除列** —— 与 0002 同一套手法（查 `information_schema` → `ADD COLUMN` → `RAISE NOTICE`）。

**② L41-L54 索引补上 `deleted_at`**
- **L47 `DROP INDEX IF EXISTS idx_sys_diagram_user`**
- **L48 `CREATE INDEX idx_sys_diagram_user ON sys_diagram (user_id, deleted_at)`**
- **L39-L40 的注释 + L43-L46 的判断条件是这个脚本最值得看的一处**：判断依据是**索引定义里有没有这一列**（L45 `indexdef ILIKE '%deleted_at%'`），**而不是索引存不存在** —— 旧库上 `idx_sys_diagram_user` 是**存在的**（只含 `user_id`），**只判存在就会跳过升级**

**L57-L61 末尾两条校验** —— 查列定义 + 查索引定义。

> **⚠️ L13-L14 划清了边界**：应用侧的配额上限是 `settings.DIAGRAM_QUOTA`（默认 50），**不在数据库里**；本脚本只负责把列和索引准备好。

---

### 📄 文件名：`migrate_0004_diagram_version.sql`（41 行）

- **文件职责**：`sys_diagram` 加 `version`（TD-65 乐观锁）。

#### 为什么需要（L4-L6）

保存流程图是直接覆盖 `content` 的。用户开两个标签页（或手机和电脑同时开着）编辑同一张图，**后保存的会静默覆盖先保存的，先改的那份一个字都不留 —— 而且用户完全不知道**。这和「删除不可恢复」（TD-64）是同一类数据丢失。

**做法（L8-L10）**：加 `version` 列，每次保存 +1。客户端把它当 ETag 拿着，保存时用 `If-Match` 带回来；服务端用**原子 CAS**（`UPDATE ... WHERE version = 期望值`）判定，对不上就 **412 且一个字都不写**。

#### 结构

**L22-L34 `DO $$` 块**
- **L28 `ADD COLUMN version INTEGER NOT NULL DEFAULT 1`**
- **L12-L13 的注释解释了 `DEFAULT 1 NOT NULL` 的效果**：**会把现存所有行都填成 1，这正是想要的**（它们都算「第 1 版」，客户端第一次 GET 就会拿到 `"1"`）
- **L29 `RAISE NOTICE` 明确说「现存行全部填为 1」**

**L37-L41 末尾两条校验**
- 查列定义（`is_nullable` / `column_default`）
- **L41 `SELECT count(*) ... WHERE version IS NULL OR version < 1`** —— **不应存在 NULL 或 <1 的行**

---

### 📄 文件名：`migrate_0005_user_role.sql`（53 行）

- **文件职责**：`sys_user` 加 `role`（TD-138 管理员角色）。

#### 为什么需要（L4-L7）

抓取 + 解析（`app/tools/crawler.py` + `app/tools/extract.py`）此前只是服务层函数，没有 HTTP 端点。要开放成「后台一键抓取」，前提是能区分管理员 —— **否则等于给任何人一个「让服务器去抓任意 URL + 烧 LLM token」的入口（既是 SSRF 面，也是烧钱面）**。

#### 结构

**L26-L38 `DO $$` 块**
- **L32 `ADD COLUMN role SMALLINT NOT NULL DEFAULT 0`**
- **L33 `RAISE NOTICE` 明确说「现存用户全部为 0（普通用户）」**

**L41-L45 末尾两条校验**
- 查列定义
- **L45 `SELECT count(*) ... WHERE role IS NULL OR role NOT IN (0, 1)`**

**L47-L53 提权说明（刻意以注释形式存在，脚本不执行）**

> **L11-L12 是本脚本最重要的设计决定**：**本次迁移不会让任何现存用户变成管理员 —— 提权必须由运维显式执行，不能靠迁移脚本顺带发生。**

```sql
-- L50（注释，需人工执行）
UPDATE sys_user SET role = 1 WHERE username = '你的管理员账号';
```

**L52 补充**：降权同理改回 0。改完**立即生效**，该用户不必重新登录 —— 因为 **L14-L16 说明角色不写进 JWT**，`get_current_user` 每个请求都从库里读用户（本来就要读 `status` 和 `password_changed_at`）。

**L18-L19 还点明了同步关系**：全新部署直接跑 `full_init.sql` 即可（那边已含 `role` 列），**两者由 `tests/test_schema_sync.py` 钉住不许跑偏**。

---

## 3. 执行逻辑流

### 3.1 两条部署路径

```
【全新部署】
  开发者本地：
    cd "database init"          ← 必须先 cd，否则读不到 ../.env
    python db_init.py
      ├─ 第 1 步  连 postgres 维护库（autocommit=True）
      │            database_exists? ─是→ 跳过
      │                            └否→ CREATE DATABASE（用 Identifier 转义）
      └─ 第 2 步  连 codemax_db（autocommit=False，client_encoding=UTF8）
                   执行 full_init.sql → commit / 异常 rollback+raise

  Docker：
    docker-compose.yml:13  挂载成 /docker-entrypoint-initdb.d/01_init.sql
    → 容器**首次启动**（数据卷为空）时自动执行；已有数据的库不会被重复初始化

【已有数据升级】
  按编号顺序跑，每个都幂等、都不动数据：
    migrate_0001_timestamptz.sql          时间列改类型（USING 子句决定语义）
    migrate_0002_password_changed_at.sql  加列（可空）
    migrate_0003_diagram_deleted_at.sql   加列 + 索引升级
    migrate_0004_diagram_version.sql      加列（现存行填 1）
    migrate_0005_user_role.sql            加列（现存行填 0，不提权）

  容器里从宿主机喂进去（compose 只挂了 full_init.sql，没挂迁移脚本）：
    docker compose exec -T db psql -U postgres -d codemax_db \
      -v ON_ERROR_STOP=1 < "database init/migrate_000N_xxx.sql"
```

### 3.2 CI 为什么要「连跑两遍」

`.github/workflows/ci.yml:149-155`：

```yaml
# 建表脚本与 ORM 模型的一致性由 tests/test_schema_sync.py 保证；
# 这里再验一次它真的能在 PostgreSQL 上执行，而且可以重复执行（幂等）。
- name: 建表脚本在真 PostgreSQL 上执行（连跑两遍验证幂等）
  run: |
    psql -d postgres -c "CREATE DATABASE codemax_ddl"
    psql -d codemax_ddl -v ON_ERROR_STOP=1 -f "database init/full_init.sql"
    psql -d codemax_ddl -v ON_ERROR_STOP=1 -f "database init/full_init.sql"   # ← 第二遍
    psql -d codemax_ddl -tAc "select count(*) from pg_tables where schemaname='public'"
```

**两遍验的是两件不同的事**：

- **第一遍**证明脚本**语法正确、能在真 PostgreSQL 上跑通**（`test_schema_sync.py` 只用项目自己的解析器读它，**不执行**，所以抓不到「PostgreSQL 其实不接受这句」）
- **第二遍**证明它**幂等** —— 靠的是开头那 7 条 `DROP TABLE IF EXISTS ... CASCADE`

**`-v ON_ERROR_STOP=1` 是关键**：没有它，`psql` 遇到错误会继续往下跑并以 0 退出，CI 会绿着放行一个建不出表的脚本。

### 3.3 三处「一致性」由谁守住

本目录的内容有三个对偶，各自有专门的守卫：

| 对偶 | 守卫 | 位置 |
| --- | --- | --- |
| `full_init.sql` ↔ `app/models.py` 表结构 | `test_schema_sync.py` 逐表逐列比对 | `tests/test_schema_sync.py:24-32` |
| `full_init.sql` 时间列必须是 `TIMESTAMPTZ` | `test_full_init_sql_uses_timestamptz` | `tests/test_schema_sync.py:61-73` |
| 状态常量的字面值 ↔ 落库的值 | `test_order_state.py` 钉住三个字符串 | `tests/test_order_state.py:52` |

**`AGENTS.md:115` 把第一条写成了硬约束**：改表结构时 `app/models.py` 与 `database init/full_init.sql` 必须一起改。

> **注意 `test_schema_sync.py` 只比「表名 + 列名」**（`_sql_schema()` 只取 `c["name"]`），
> **不比类型、不比默认值、不比索引**。所以「列名对但类型改了」这类漂移它抓不到 ——
> 只有 `test_full_init_sql_uses_timestamptz` 专门补了时间类型这一项。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `f63ce49`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) 目录行数与文件清单
python -c "import pathlib; [print(len(p.read_text(encoding='utf-8').splitlines()), p.name) for p in sorted(pathlib.Path('database init').iterdir()) if p.name != 'README.md']"
# 预期 7 行，共 489 行（排除本 README）

# 2) full_init.sql 的实测计数（本文 1.2 的那组数字）
python -c "
import re, pathlib
s = pathlib.Path('database init/full_init.sql').read_text(encoding='utf-8')
print('CREATE TABLE', len(re.findall(r'CREATE TABLE', s)),
      '| DROP', len(re.findall(r'DROP TABLE', s)),
      '| INDEX', len(re.findall(r'CREATE INDEX\s+\w+\s+ON', s)),
      '| INSERT', len(re.findall(r'INSERT INTO', s)))
print('TIMESTAMPTZ', len(re.findall(r'\bTIMESTAMPTZ\b', s)),
      '| 裸TIMESTAMP类型', len(re.findall(r'(?<!CURRENT_)\bTIMESTAMP\b(?!TZ)', s)),
      '| CURRENT_TIMESTAMP默认值', len(re.findall(r'CURRENT_TIMESTAMP', s)),
      '| 外键', len(re.findall(r'REFERENCES', s)))
print('表', re.findall(r'CREATE TABLE (\w+)', s))
"
# 预期：CREATE TABLE 7 | DROP 7 | INDEX 2 | INSERT 2
#       TIMESTAMPTZ 12 | 裸TIMESTAMP类型 0 | CURRENT_TIMESTAMP默认值 8 | 外键 4
#       表 ['sys_user','sys_order','sys_config','oauth_client','oauth_code','sys_diagram','sys_article']

# 3) 表结构与 ORM 是否真的一致（本文 3.3 的第一条守卫）
python -m pytest tests/test_schema_sync.py tests/test_sql_ddl.py tests/test_word_export.py -q
# 本机实测：38 passed
# 这三个文件都以 full_init.sql 为输入

# 4) 迁移脚本的幂等手法统计（本文 1.4 的表格依据）
# 预期：0001 DO块=1 info_schema=4 事务=2；0003 DO块=2 pg_indexes=2；其余 DO块=1 info_schema=2
python -c "
import pathlib, re
for p in sorted(pathlib.Path('database init').glob('migrate_*.sql')):
    t = p.read_text(encoding='utf-8')
    # 注意：$$ 在 shell 双引号里会被展开成进程号，所以用 chr(36)*2 拼出来
    do = 'DO ' + chr(36) * 2
    print(f'{p.name:<40} DO块={t.count(do)} info_schema={t.count("information_schema")} pg_indexes={t.count("pg_indexes")} 事务={len(re.findall(chr(94) + "(BEGIN|COMMIT);", t, flags=re.M))}')
"
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动本目录任何文件后，
> 本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
> **改 `full_init.sql` 时还要同时改 `app/models.py`** —— 那是 `AGENTS.md:115` 的硬约束，
> 由 `tests/test_schema_sync.py` 把关。
>
> 背景（为什么文档里的数字比代码更容易腐烂、以及一次真实的漏改事故）见
> `docs/ARCHITECTURE_GUIDE.md` 第 7 课 7.10。
