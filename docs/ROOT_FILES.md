# 根目录文件说明书

> **行号基准 commit：`1d5499c`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> **为什么这份文档在 `docs/` 而不是根目录的 `README.md`**：根目录的 `README.md` 是项目的
> **快速开始**（面向第一次跑起来的人），职责不同，不该被文件级说明书挤掉。
> 本文覆盖的是根目录那几个**代码与构建配置**文件。
>
> 姊妹篇：`app/README.md`、`app/routers/README.md`、`app/tools/README.md`、
> `app/templates/README.md`、`app/static/README.md`、`database init/README.md`、
> `.github/workflows/README.md`、`tests/README.md`。

---

## 1. 概述

根目录有 6 个「代码/构建」文件，共 242 行：

```
main.py              52 行   FastAPI 应用装配（唯一的应用入口）
ruff.toml            52 行   唯一的静态检查配置（TD-145）
requirements.txt     26 行   22 个依赖，全部钉死版本
Dockerfile           23 行   应用镜像
docker-compose.yml   37 行   本地/单机部署示例（app + db 两个服务）
pytest.ini            4 行   pytest 配置
───────────────────────────
                    242 行
```

另有几个非代码文件，本文只在第 3 节点到，不逐行展开：`.env.example`（38 项配置模板）、
`.dockerignore`、`.gitignore`、`.gitattributes`。

---

## 2. 文件级详细说明书

### 📄 文件名：`main.py`（52 行）

- **文件职责**：**整个应用的装配点**。52 行，没有任何业务逻辑 —— 它只做「配置 → 自检 → 建 app → 挂中间件 → 挂路由 → 挂静态资源」。

#### 结构（按执行顺序）

**L1-L12 import**
- **L8-L12 从 `app` 包导入 6 样东西**：`cpu_pool` / `settings` / 两个中间件 / **9 个 router** / `enforce_production_settings`

**L16-L19 日志配置**
- **L14-L15 注释说明了两个决定**：
  - **放在导入应用之前** —— 否则先导入的模块拿不到配置好的 logger
  - **只设定级别与格式，不去动 uvicorn 的 handler** —— **避免两边打架**（TD-165）
- **L17 `level=settings.LOG_LEVEL.upper()`** —— 从配置读，不是写死

**L22 `enforce_production_settings()`**
- **L21 注释是这个文件的灵魂**：**生产配置不合规就拒绝启动 —— 等到用户下单时才发现就晚了**
- 具体查哪四项见 `app/startup_checks.py`（mock 支付、默认密钥、关限流、不信任代理头）

**L24-L27 `lifespan` 异步上下文管理器**
- **L26 `yield`** —— 应用运行期间什么都不做
- **L27 `cpu_pool.shutdown()`** —— **L27 注释**：回收进程池子进程，**否则会留下孤儿进程**

**L30 建 `FastAPI` 实例** —— `title` / `version` / `lifespan`。

**L34-L35 挂两个中间件**
- **L32-L33 注释解释了一个反直觉的顺序**：中间件是**后加先执行**（洋葱模型），所以**日志放最后加，让它包在最外层** —— 这样连安全头中间件自己的耗时也算进去，且**异常也能被记录到**

**L37-L45 挂 9 个 router**
- 顺序：`health` → `auth` → `oauth` → `tools` → `diagrams` → `shop` → `site` → `support` → `admin`
- **顺序有一处是有意义的**：`site.router` 用 `add_api_route` 注册了 `/` 等页面路径，**必须在前面的 API router 之后**，否则可能吃掉别的路径

**L48-L52 挂静态资源**
- **L50 `directory=Path(__file__).resolve().parent / "app" / "static"`** —— **用 `__file__` 拼绝对路径**，所以从任何工作目录启动都能找到
- **`html=True`** —— 允许目录下的 `.html` 直接访问（本项目其实只有 `er.js`）
- **L47 注释划清边界**：静态资源是 JS 等；**HTML 页面走 Jinja2 SSR**，见 `app/routers/site.py`

---

### 📄 文件名：`ruff.toml`（52 行）

- **文件职责**：**本仓库唯一的静态检查工具**的配置（TD-145）。

#### 文件头注释（L1-L7）—— 为什么规则集要显式列出

> 实测 ruff 0.16.5 的**默认集是一批具体规则，不是按前缀整族启用** —— 它含 `B008` 却不含
> `B904`/`B905`，含 `RUF007` 却不含 `RUF001`。也就是说「按前缀 select」**比默认更严**
> （本仓库实测：默认 51 条，本配置 17 条）。
> **写死规则集，升级 ruff 时才不会静默引入新规则、CI 突然变红而没人知道为什么。**

#### 配置项

**L9 `target-version = "py311"`** —— 与 `Dockerfile:3` 的 `python:3.11-slim` 对齐。

**L13 `line-length = 120`**
- **L11-L12 注释说明为什么不启用 `E501`**：本仓库中文注释多，**实测超过 88 列的有 516 行、最长 189 列**。强行折行只会制造巨大且无信息量的 diff，**价值低于噪音**

**L16-L27 `[lint] select`（实测 10 个规则族）**

| 族 | 行 | 作用 |
| --- | --- | --- |
| `F` | L17 | pyflakes：未使用 import/变量、未定义名字 —— **真 bug 与死代码** |
| `E4` | L18 | import 相关的 pycodestyle 错误 |
| `E7` | L19 | 语句类错误（如 `==` 比较类型） |
| `E9` | L20 | 语法 / 运行时错误 |
| `I` | L21 | import 排序（可自动修，保持 diff 干净） |
| `B` | L22 | bugbear：可变默认参数、裸 except、函数调用做默认值等 |
| **`DTZ`** | L23 | **datetime 必须带时区 —— 就是它抓到了 `paid_at` 的问题（TD-145）** |
| `ASYNC` | L24 | async 里的阻塞调用 |
| `SIM` | L25 | 可简化的写法（数量少，可自动修） |
| `C4` | L26 | 推导式相关（数量少，可自动修） |

**L29-L33 刻意不选的三族**（每条都写了原因）
- **`E501`（行长）** —— 见上面
- **`PLR` 整个家族** —— 会引入大量**主观**规则（参数个数、分支数），**噪音大于收益**
- **`RUF` 整个家族** —— `RUF001/002/003` 会把**中文标点**判成「歧义 unicode 字符」，**本仓库全是中文注释，开了等于全屏红**

**L35-L44 `[lint.flake8-bugbear] extend-immutable-calls`（5 项）**
- **L36-L37 注释说明**：FastAPI 的 `Depends()` / `Query()` 等**必须写在默认参数位置，这是框架用法不是 bug**。**不加这个白名单会有 28 条 `B008` 误报（实测），淹没真正的问题**
- 白名单：`fastapi.Depends` / `Query` / `Header` / `Body` / `Security`

**L46-L52 `[lint.per-file-ignores]`（2 个文件，都忽略 `ASYNC221`）**
- **L47-L50 注释说明为什么**：这两个文件用 `subprocess` **真实执行 node 跑前端 JS**（**用户明确要求：测试要真的执行前端代码，不许退化成静态字符串检查**），所以阻塞式的 `subprocess.run` 是**有意的**
  - `tests/test_er_page.py` —— ER 图页面的 `app/static/er.js`
  - `tests/test_auth_cookie.py` —— drawio 页面的内联脚本（TD-44：证明前端读不到 token）

---

### 📄 文件名：`requirements.txt`（26 行）

- **文件职责**：**22 个依赖，全部钉死版本**（`==`）。分三段。

#### 运行时依赖（L1-L16，16 个）

| 包 | 行 | 版本 | 备注（原文注释） |
| --- | --- | --- | --- |
| `fastapi` | L1 | 0.104.1 | — |
| `uvicorn[standard]` | L2 | 0.24.0.post1 | — |
| `sqlalchemy` | L3 | 2.0.23 | — |
| `asyncpg` | L4 | 0.29.0 | **PostgreSQL 高性能异步驱动（应用运行时）** |
| `psycopg2-binary` | L5 | 2.9.9 | **PostgreSQL 驱动（数据库初始化脚本 `db_init.py` 使用）** |
| `python-dotenv` | L6 | 1.0.0 | 加载 `.env` 配置文件 |
| `pydantic` | L7 | 2.5.2 | — |
| `pydantic-settings` | L8 | 2.1.0 | — |
| `python-jose[cryptography]` | L9 | 3.3.0 | 用于 JWT 生成和解析 |
| `passlib[bcrypt]` | L10 | 1.7.4 | 用于密码哈希加密 |
| **`bcrypt`** | L11 | 4.0.1 | **固定版本，兼容 passlib 1.7.4** |
| `python-multipart` | L12 | 0.0.6 | 用于处理表单和 OAuth2 |
| `python-docx` | L13 | 1.2.0 | 导出 Word 数据字典（S2-01-4） |
| `beautifulsoup4` | L14 | 4.15.0 | 爬虫解析 HTML（S4-01-1，**选型已定：不是 Jsoup**） |
| `jieba` | L15 | 0.42.1 | 中文分词（S4-02-1 FAQ 检索用；**纯 Python，Windows 可直接装**） |
| `jinja2` | L16 | 3.1.6 | 页面 SSR 出 HTML 外壳与 TDK（S2-02-1） |

> **两个 PostgreSQL 驱动并存是刻意的**：`asyncpg` 给应用运行时（异步），
> `psycopg2-binary` 给 `database init/db_init.py`（同步脚本，要执行 `CREATE DATABASE`）。

> **`bcrypt` 为什么要单独钉版本**（L11）：`passlib 1.7.4` 与新版 `bcrypt` 不兼容，
> 不钉的话装上新版会在**运行时**报 `AttributeError`。

#### 测试依赖（L18-L23，5 个）

| 包 | 行 | 版本 | 备注 |
| --- | --- | --- | --- |
| `pytest` | L19 | 7.4.4 | — |
| `pytest-asyncio` | L20 | 0.23.6 | — |
| `httpx` | L21 | 0.25.2 | 测试用 `AsyncClient` |
| `aiosqlite` | L22 | 0.19.0 | **测试用内存 SQLite（验证逻辑，生产用 PostgreSQL）** |
| `pgserver` | L23 | 0.1.4 | **可选：PyPI 打包的真 PostgreSQL 16.2，用来跑真库集成测试（TD-121/123）** |

> **`pgserver` 就是 `Dockerfile:1-2` 必须钉 Python 3.11 的原因** ——
> 它在 PyPI 上**没有 Python 3.13 的发行版**。

#### 静态检查（L25-L26，1 个）

- **L26 `ruff==0.16.5`** —— **唯一的 linter，规则集见 `ruff.toml`（TD-145）。刻意不引入 mypy**
- **`.github/workflows/ci.yml:62` 就是从这个文件抠 ruff 版本号的**（`grep -oE '^ruff==[0-9.]+'`），避免两处漂移

> ⚠️ **`requirements-dev.txt` 不存在。** 测试与 lint 依赖都在这个文件里，
> 一条 `pip install -r requirements.txt` 装齐。

---

### 📄 文件名：`Dockerfile`（23 行）

- **文件职责**：应用镜像。

#### 逐段说明

**L1-L3 基础镜像**
- **`FROM python:3.11-slim`**
- **L1-L2 注释说明为什么必须钉 3.11**：`pgserver==0.1.4` 在 PyPI 上**没有 Python 3.13 的发行版**（实测 `pip download pgserver==0.1.4 --python-version 3.13` 报 `no matching distribution`）

**L5-L6 建非 root 用户**
- **L5 注释**：**不用 root 跑：容器逃逸时少一层损失**
- `groupadd --system app && useradd --system --gid app --home /srv/app app`

**L8 `WORKDIR /srv/app`**

**L10-L12 先拷 requirements 再装依赖**
- **L10 注释是这个 Dockerfile 最重要的一句**：**改代码不会让依赖层缓存失效**
- **L12 `pip install --no-cache-dir`** —— 不把 pip 缓存留在镜像里（省体积）

**L14 `COPY . .`** —— 依赖装完才拷代码（顺序不能反，否则 L10-L12 的缓存优化白做）。
> 拷进来的是什么由 `.dockerignore` 决定 —— **`.env`、`.venv`、`.git`、`docs`、`*.md`、`database init` 都被排除**。

**L16-L18 存储目录与切用户**
- **L16 注释**：本地存储后端的目录（`STORAGE_LOCAL_ROOT`），**要可写**
- **L17 `mkdir -p storage && chown -R app:app /srv/app`**
- **L18 `USER app`** —— **从这里之后的指令与运行时都不是 root**

**L20 `EXPOSE 8000`**

**L22-L23 启动命令**
- **L22 注释**：**容器里必须监听 `0.0.0.0`，绑 `127.0.0.1` 的话宿主机连不进来**
- `CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]`
- **用 exec 形式（JSON 数组）而不是 shell 形式** —— 这样 uvicorn 是 PID 1，能正确收到 `SIGTERM`，`lifespan` 的 `cpu_pool.shutdown()`（`main.py:27`）才有机会执行

---

### 📄 文件名：`docker-compose.yml`（37 行）

- **文件职责**：本地/单机部署示例。**两个服务 + 两个数据卷**。

#### 文件头注释（L1-L2）—— 一个关键的部署前提

> 生产上 **SSL 与域名解析在反向代理（nginx / 云 LB）那一层做**，应用本身只跑 http，
> 靠 `X-Forwarded-Proto` 感知 https —— **所以 `TRUST_PROXY_HEADERS` 要开**。

这解释了 L29 为什么要显式设 `TRUST_PROXY_HEADERS: "true"`。

#### `db` 服务（L4-L18）

- **L5 `image: postgres:16`** —— 与 CI 的 service 容器同版本
- **L9 `POSTGRES_PASSWORD: ${DB_PASSWORD:?必须在 .env 里设置 DB_PASSWORD}`**
  - **`:?` 是 compose 的语法**：**变量没设就直接报错退出**，而不是用空密码起一个不安全的库
- **L11 `pgdata:/var/lib/postgresql/data`** —— 数据持久化
- **L12-L13 挂载建表脚本**
  - **`./database init/full_init.sql:/docker-entrypoint-initdb.d/01_init.sql:ro`**
  - **L12 注释**：**首次启动自动建表；已存在的库不会被重复初始化**
  - **`:ro` 只读挂载**
  - ⚠️ **`full_init.sql` 开头是 `DROP TABLE ... CASCADE`** —— 所以**已有数据的库绝不能让它跑**。好在 PostgreSQL 镜像只在数据目录为空时执行 `docker-entrypoint-initdb.d`
- **L14-L18 `healthcheck`** —— `pg_isready`，5s 间隔、3s 超时、重试 10 次。**`app` 服务靠它决定何时启动**

#### `app` 服务（L20-L33）

- **L21 `build: .`** —— 用本目录的 `Dockerfile`
- **L22-L24 `depends_on` 带 `condition: service_healthy`**
  - **这是关键**：普通的 `depends_on` 只等**容器启动**，不等**数据库能接受连接**。加上 `condition` 才会等 healthcheck 通过
- **L25 `env_file: .env`** —— 把 `.env` 整个注入
- **L26-L29 三个覆盖项**：
  - **`DB_HOST: db`** —— **容器网络里用服务名当主机名**，不是 `localhost`
  - **`DB_PORT: "5432"`** —— **加引号**：不加会被 YAML 解析成整数，而 compose 要求字符串
  - **`TRUST_PROXY_HEADERS: "true"`** —— 见文件头注释
- **L30-L31 `ports: "8000:8000"`**
- **L32-L33 `uploads:/srv/app/storage`** —— 上传的商品文件持久化（**不挂这个卷，容器重建后商品文件就没了**）

#### L35-L37 两个命名卷

- `pgdata`（数据库数据）、`uploads`（商品文件）

---

### 📄 文件名：`pytest.ini`（4 行）

- **文件职责**：pytest 配置。**只有三行有效配置，但每行都省掉了一类麻烦**。

| 行 | 配置 | 作用 | 不写会怎样 |
| --- | --- | --- | --- |
| L2 | `asyncio_mode = auto` | 异步测试**不必逐个加 `@pytest.mark.asyncio`** | 每个 `async def test_*` 都要手写装饰器，漏一个就静默跳过 |
| L3 | `testpaths = tests` | 只在 `tests/` 下找测试 | 会去扫 `.venv`、`app/` 等目录，慢且可能误收集 |
| L4 | `pythonpath = .` | 把仓库根加进 `sys.path` | `import app.xxx` 会 `ModuleNotFoundError`，除非先 `pip install -e .` |

---

## 3. 其它根级文件（不逐行展开，只说职责与去处）

| 文件 | 职责 | 详细说明在哪 |
| --- | --- | --- |
| `.env.example` | **38 项配置模板**，与 `app/config.py` 的 `Settings` **1:1 对应** | `app/README.md` 的 `config.py` 一节 |
| `.dockerignore` | 排除 14 项不进镜像：**`.env` `.venv` `.git` `.github` `.claude` `__pycache__` `*.pyc` `.pytest_cache` `.ruff_cache` `storage` `docs` `*.md` `database init`** | — |
| `.gitignore` | 17 行，排除 `.env` / `.venv` / `storage` 等 | — |
| `.gitattributes` | 行尾与二进制属性 | — |
| `README.md` | **项目快速开始**（面向第一次跑起来的人） | 它自己 |
| `AGENTS.md` | **硬约束入口**（NEVER / ASK / ALWAYS / 「做完」的定义） | 它自己 |
| `HANDOVER.md` | 交接文档（含起真库的配方 §9） | 它自己 |
| `ROADMAP.md` / `TECH_DECISIONS.md` | 路线图 / 取舍台账（160 条 TD） | 它们自己 |

> ⚠️ **`.dockerignore` 排除了 `database init`，但 `docker-compose.yml:13` 照样能挂它** ——
> 因为 compose 是从**宿主机**挂载，不经过镜像构建上下文。这两件事不矛盾，但容易看糊涂。

> ⚠️ **数 `.env.example` 的配置项时别用 `grep -c "="`** —— 实测那样得到 **43**，
> 因为有 **5 行注释里含 `=`**（例如 `# 商品：… 金额单位是分（19900 = ¥199）`）。
> 真实配置项是 **38**。正确的数法见文末附录命令 ②。

---

## 4. 执行逻辑流

### 4.1 从 `docker compose up` 到第一个请求

```
docker compose up
  │
  ├─ db 服务启动（postgres:16）
  │    ├─ 数据卷为空？→ 执行 /docker-entrypoint-initdb.d/01_init.sql（= full_init.sql）
  │    └─ healthcheck: pg_isready 每 5s 一次，最多 10 次
  │
  ├─ app 服务**等 db 健康**（depends_on.condition: service_healthy）
  │    └─ 启动 CMD: uvicorn main:app --host 0.0.0.0 --port 8000
  │         │
  │         ├─ main.py:16-19   logging.basicConfig（只设级别与格式）
  │         ├─ main.py:22      enforce_production_settings()
  │         │                    └─ 不合规 → 抛 ProductionConfigError，**容器直接退出**
  │         ├─ main.py:30      FastAPI(lifespan=...)
  │         ├─ main.py:34-35   挂中间件（日志后加 ⇒ 包在最外层）
  │         ├─ main.py:37-45   挂 9 个 router
  │         └─ main.py:48-52   挂 /static
  │
  └─ 请求进来 → RequestLogging → SecurityHeaders → 路由
       ⋮
     容器停止 → lifespan 的 yield 之后 → cpu_pool.shutdown()（回收子进程）
```

### 4.2 四处「版本必须一致」

| 什么 | 出现在哪 | 谁保证一致 |
| --- | --- | --- |
| Python 3.11 | `Dockerfile:3`、`ruff.toml:9`（`py311`）、`.github/workflows/ci.yml`（3 处 `python-version: "3.11"`） | **人工**（没有自动检查） |
| ruff 版本 | `requirements.txt:26` | **`ci.yml:62` 从 requirements 里抠**，自动一致 |
| PostgreSQL 16 | `docker-compose.yml:5`、`ci.yml:115` | **人工** |
| 38 项配置 | `app/config.py`、`.env.example` | **`app/README.md` 附录命令 ② 的对称差检查** |

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `1d5499c`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# ① 六个文件的行数（本文 1 的那组数字）
python -c "import pathlib; [print(len(pathlib.Path(f).read_text(encoding='utf-8').splitlines()), f) for f in ['main.py','ruff.toml','requirements.txt','Dockerfile','docker-compose.yml','pytest.ini']]"
# 预期：52 / 52 / 26 / 23 / 37 / 4，合计 242

# ② 依赖数与 ruff 规则族数
python -c "
import re, pathlib
req = pathlib.Path('requirements.txt').read_text(encoding='utf-8')
print('依赖数:', len([l for l in req.splitlines() if l.strip() and not l.startswith('#') and '==' in l]))
print('ruff select:', re.findall(r'^\s+\"([A-Z0-9]+)\",', pathlib.Path('ruff.toml').read_text(encoding='utf-8'), flags=re.M))
"
# 预期：依赖数 22；ruff select 10 个族 ['F','E4','E7','E9','I','B','DTZ','ASYNC','SIM','C4']

# ③ .env.example 的真实配置项数（**不要用 grep -c "="，那会得到 43**）
python -c "
import pathlib
from app.config import Settings
lines = pathlib.Path('.env.example').read_text(encoding='utf-8').splitlines()
real = {l.split('=')[0].strip() for l in lines if l.strip() and not l.strip().startswith('#') and '=' in l}
print('配置项:', len(real), ' 与 Settings 的对称差:', set(Settings.model_fields) ^ real)
"
# 预期：配置项 38，对称差 set()（即 1:1）

# ④ 本地跑一遍 ruff（CI 的 lint job 做的事）
.venv/bin/ruff check .
# 预期：All checks passed!
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动这些文件后本文对应的行号与
> 计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **三条联动提醒**：
> ① 加配置项 → `app/config.py` 与 `.env.example` **必须同时改**（`AGENTS.md` 的 NEVER 段）；
> ② 换 Python 版本 → `Dockerfile`、`ruff.toml`、`ci.yml` 三处都要改，**且 `pgserver` 未必有新版本**；
> ③ 加依赖 → 确认它不会让 `ci.yml` 的 `lint` job 变慢（那个 job 刻意只装 ruff）。
