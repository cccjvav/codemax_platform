# codemax_platform

> 2026-09-12：第二批修复、站内人工客服和文档门禁已实现。先读 [当前验收与迁移](docs/SECOND_REPAIR_ACCEPTANCE.md)、[文档方案比较](docs/DOCUMENTATION_POLICY.md)。存量库需要 0007/0008；旧会话重新登录。管理员和客户均从 `/support/center` 进入站内会话。历史测试数字不是本批结果。


> **审查修复进行中（2026-09-11）**：进度、交叉验证、未解决风险及需要决策的事项见 [修复台账](docs/REVIEW_CROSSCHECK.md)。本批不代表全项目已修完或可直接上线。

毕设服务平台 (codemax.top)：免费工具平台 + 商业平台（双平台），统一认证（SSO）、支付闭环、云存储安全下载、AI 内容解析与智能客服。

## 从零理解代码

先读 [代码复盘入口](docs/CODE_READING_GUIDE.md)：术语与身份、客服、订单/支付、OAuth、图形工具、采集/模型及运维完整链路。文档站“精读覆盖与缺口”提供全部 136 个非空非生成源码文件的分段说明与原代码并排阅读；新增漏项会阻止构建。人工功能契约与 AST 语句导读明确区分，门禁不认证语义。生成物讲来源、空文件讲作用，历史报告不冒充现行教程。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2.0（异步）+ asyncpg
- 数据库：PostgreSQL
- 认证：JWT（python-jose）+ bcrypt（passlib）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制 .env.example 为 .env 并填入实际配置
cp .env.example .env

# 3. 初始化数据库（自动建库建表；测试账号 admin/123456）
#    ⚠️ 只对**空库**安全：full_init.sql 开头是 DROP TABLE ... CASCADE，对已有数据的库跑它会清库
cd "database init" && python db_init.py && cd ..

# 4. 启动服务
uvicorn main:app --reload   # http://localhost:8000/docs
```

## Windows：以 conda 为主要路径

你使用 conda 时，不创建 `.venv`，也不调用 `.venv\Scripts\python.exe`。

1. 打开 Anaconda / Miniconda Prompt（cmd），激活项目环境并核对 Python 3.11。
2. 按 [Windows + conda 完整指南](docs/WINDOWS_CONDA.md) 安装依赖、配置独立开发库并启动。
3. 按该指南在无业务配置的独立目录跑自动化测试；测试库会重建表，不能使用业务库。
4. 按 [人工及外部服务验收手册](docs/ACCEPTANCE_GUIDE.md) 检查真实浏览器、Word、Drawio、站内客服和订单等。

已经完成安装/配置后，每天从项目根目录启动：

```cmd
conda activate codemax
set PYTHONUTF8=1
python -c "import sys; print(sys.executable)"
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`codemax` 换成你的环境名。首次建环境和数据库的步骤不能跳过；已有库不要重跑 full_init。Conda 不会代替 PostgreSQL 服务或 Node 安装。仍想用标准库 venv 时见 [Windows 入口与 venv 备选](docs/WINDOWS_LOCAL_RUN.md)。

**剩余验收不全是 Windows 专属**：页面/编辑器可在其他系统的浏览器检查，数据库测试可在 CI，模型需要密钥，微信支付需要商户与可达 HTTPS 回调。你的 Windows 本机验收主要确认实际 conda 环境和客户端体验；CI 成功不自动代表这些项目通过。

## 运行测试

```bash
# Linux / macOS（沙箱内路径）；Windows 见上一节
./.venv/bin/python -m pytest -q
```

## 主要 API（完整路径与权限见路由说明及生成索引）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register` | 注册（返回用户信息，不含密码） |
| POST | `/auth/login` | 登录（OAuth2 表单）。**两条通道**：给浏览器下发 HttpOnly cookie（脚本读不到，防 XSS 偷 token），同时返回 JWT `access_token` 供 Swagger / API 客户端走 Bearer 头 |
| GET | `/auth/me` | 当前用户（需 Cookie 或 Bearer） |
| POST | `/auth/password` | 修改密码（需 Cookie 或 Bearer；**会吊销该用户此前签发的所有 token**，同时返回一个新 token） |
| POST | `/auth/logout` | 退出登录：清掉登录 cookie（204） |
| GET | `/oauth/authorize` | **授权同意页**：显示申请方与当前账号，由用户点「同意/拒绝」（不再直接签发 code） |
| POST | `/oauth/authorize` | 用户点同意后签发一次性 code 并 302 跳回调；表单需带同意页给出的签名 |
| POST | `/oauth/token` | **令牌端点**：客户端用 code + client_secret 换取 access_token |
| GET | `/tools/ping` | 工具平台受保护端点（SSO 验证） |
| GET | `/shop/ping` | 商业平台受保护端点（SSO 验证） |
| POST | `/admin/articles/ingest` | **仅管理员**（`role=1`）：抓取一个 URL → LLM 指认选择器 → 提取入库。同一 URL 重复抓是更新。`dynamic=true` 当前安全停用并返回 503，安装浏览器不能解除限制。失败分 400（抓不了/robots 不允许/目标站不可达）、422（提不出正文）、502（大模型不可用）、503（服务端浏览器不可用） |
| GET | `/` | 首页（Jinja2 SSR） |
| GET | `/tools/er` `/tools/mermaid` `/tools/drawio` | 三个工具页（SSR；旧的 `/static/*.html` 已下线，见 TD-94） |
| POST | `/tools/er-diagram` | DDL → ER 图 JSON（`parse_ddl` 跑在线程池里，不阻塞事件循环） |
| POST | `/tools/mermaid` | 自然语言/代码 → Mermaid 类图（LLM 可注入） |
| POST | `/tools/word-export` | 导出数据字典 Word（`python-docx`，跑在**进程**池，见 TD-168/186） |
| GET | `/sitemap.xml` `/robots.txt` | SEO（由集中的 `TOOLS` 常量驱动，见 TD-51） |
| GET | `/diagrams` | 流程图列表（需登录；`?deleted=true` 看回收站） |
| POST | `/diagrams` | 新建（受每用户配额 `DIAGRAM_QUOTA` 约束，超额 409） |
| GET | `/diagrams/{diagram_id}` | 取一张（带 `ETag`） |
| PUT | `/diagrams/{diagram_id}` | 保存（**乐观锁**：`If-Match` 缺失 428 / 不匹配 412，见 TD-65） |
| DELETE | `/diagrams/{diagram_id}` | 软删除（进回收站，不物理删） |
| POST | `/diagrams/{diagram_id}/restore` | 从回收站恢复（会重新检查配额） |
| POST | `/shop/orders` | 建订单 → 微信 NATIVE 下单 → 返回 `code_url`（未配齐微信支付则 503） |
| POST | `/shop/pay/notify` | 微信支付回调：验签 → AES-GCM 解密 → 校验金额 → 幂等迁移状态 |
| POST | `/shop/download/{order_no}` | 换取限时下载链接（POST 记录链接发放；paid/downloaded 均可重领） |
| GET | `/shop/dl` | 本地存储后端的实际出文件口（校验 HMAC 签名后再吐） |
| GET | `/shop/mock-pay` | 模拟收银台页面（仅 `SHOP_PAY_MODE=mock`，**开着等于免费发货**，见 TD-124） |
| POST | `/shop/mock-pay/confirm` | 模拟支付确认（复用与真实回调**完全相同**的状态机与幂等逻辑） |
| POST | `/support/ask` | 智能客服总入口：FAQ 秒回 / 闲聊 LLM / 专业问题 RAG，兜底转人工 |
| GET | `/health` | **只是 `/healthz` 的别名**，同样**不查库**（TD-164 保留它是因为既有文档与测试都在用） |
| GET | `/healthz` | 存活探针（**刻意不查库**，否则库一抖会被编排器全量重启，见 TD-167） |
| GET | `/readyz` | 就绪探针 |

> **SSO（OAuth2 授权码模式）流程**：用户登录认证中心拿会话 JWT → 携带 JWT 访问
> `/oauth/authorize?response_type=code&client_id=...&redirect_uri=...&state=...`
> → 显示同意表单，用户 POST 批准后 302 跳回回调地址携带一次性 code → 客户端用 `/oauth/token` 以
> `code + client_secret` 换取 access_token → 用 access_token 访问双平台受保护资源。
> 授权码一次性、10 分钟有效；客户端密钥在库中只存 bcrypt 哈希。

### 演示客户端（种子数据，仅演示用）

| client_id | client_secret | 回调地址 |
| --- | --- | --- |
| `tools` | `codemax-tools-secret` | `https://tools.codemax.top/callback` |
| `shop` | `codemax-shop-secret` | `https://shop.codemax.top/callback` |

## Agent Skills

本仓库内置 Agent Skills（`.claude/skills/`，SKILL.md 开放标准，Claude Code / Cursor / Copilot 等兼容）：

| Skill | 作用 |
| --- | --- |
| `codemax-workflow` | **项目专属工作流**：代码尽量简洁、写完必须测试、不过则迭代（硬性要求） |
| `fastapi-python` | FastAPI 开发规范（异步、Pydantic、函数式简洁写法） |
| `python-testing` | pytest 测试规范（TDD、fixtures、mock、覆盖率） |

## 文档

> 仓库里共 **21 份文档 / 约 10 000 行**。下面按「我想干什么」给入口 —— 
> 不知道从哪看起，就先开 **[总览.md](./总览.md)**。

### 🖥️ 想直接看网页版（推荐）

架构导读与代码级说明书有一个**可读 + 可视化**的静态站点：文档带目录、
代码位置可点击跳转、模块依赖图、路由地图、符号索引。**完全离线，双击即开。**

```cmd
pip install mistune
python scripts\build_docs_site.py
start docs\site\index.html
```

说明与 Windows 逐步指南：**[docs/site/README.md](./docs/site/README.md)**

### 📖 按需求找文档

| 我想…… | 去这里 |
| --- | --- |
| **一站式看懂整个项目**（目录树 / 技术栈 / 分层 / 数据流 / 子模块索引） | **[总览.md](./总览.md)** |
| **从零理解架构**（7 课讲解，面向没读过代码的人，大量生活比喻） | **[docs/ARCHITECTURE_GUIDE.md](./docs/ARCHITECTURE_GUIDE.md)** |
| 在 **Windows 本机把服务跑起来**（cmd 逐步命令） | **[docs/WINDOWS_LOCAL_RUN.md](./docs/WINDOWS_LOCAL_RUN.md)** |
| 在 **Linux / 服务器部署**（Docker、Nginx） | [docs/DEPLOY.md](./docs/DEPLOY.md) |
| 查某个**目录/文件/函数**的行级说明 | 见下方「模块说明书」表 |
| 看**接口清单** | 本文下方「当前 API」一节，或起服务后开 `/docs` |
| 了解**开发硬约束**（AI 助手与新成员都该先读） | [AGENTS.md](./AGENTS.md) |
| 了解**实现取舍与上线阻塞项**（176 个 TD 台账，编号至 TD-228） | [TECH_DECISIONS.md](./TECH_DECISIONS.md) |
| 看**开发路线图**与子项进度 | [ROADMAP.md](./ROADMAP.md) |
| **接手这个项目**（沙箱恢复配方、真库起法、已踩过的坑） | [HANDOVER.md](./HANDOVER.md) |
| 看**文档质量审查报告**（覆盖率 / 链接 / 格式） | [DOCUMENTATION_SUMMARY.md](./DOCUMENTATION_SUMMARY.md) |
| 看**根目录那几个文件**（`main.py` 等）的说明 | [docs/ROOT_FILES.md](./docs/ROOT_FILES.md) |

### 📂 模块说明书（逐文件、带行号）

每个含代码的目录内部都有一份 `README.md`，逐文件说明职责、类/函数清单、
核心逻辑的行级拆解（`Lxx-Lyy`）与执行流程。**行号会腐烂**，所以每份都在文首
钉了「行号基准 commit」，文末给了复核命令。

| 目录 | 说明书 | 内容 |
| --- | --- | --- |
| `app/tools/` | [README](./app/tools/README.md) | 业务逻辑层（10 个模块 / 1625 行） |
| `app/routers/` | [README](./app/routers/README.md) | HTTP 接口层（9 个 router / 1132 行） |
| `app/` 根 | [README](./app/README.md) | 根级基础设施（16 个文件 / 1328 行） |
| `database init/` | [README](<./database init/README.md>) | 建库建表 + 5 个迁移脚本 |
| `app/templates/` | [README](./app/templates/README.md) | 前端模板（7 个 Jinja2 模板） |
| `app/static/` | [README](./app/static/README.md) | 前端脚本（`er.js`） |
| `.github/workflows/` | [README](./.github/workflows/README.md) | CI 流水线（`ci.yml`） |
| 根目录 | [docs/ROOT_FILES.md](./docs/ROOT_FILES.md) | `main.py` 与 5 个构建/配置文件 |
| `tests/` | [README](./tests/README.md) | 测试策略与分组（36 个测试文件 / 565 个用例） |
| `scripts/` | [README](./scripts/README.md) | 建表脚本深度体检（WASM 版真 PostgreSQL） |

> 上面这些说明书在**文档站**里都有网页版（带目录、可跳转）：
> `python scripts\build_docs_site.py` 之后打开 `docs\site\index.html`。

## 新增 API 与操作入口

| 方法 | 路径 | 规则 |
| --- | --- | --- |
| GET | `/support/center` | 公开页面外壳，消息需登录 |
| GET / POST | `/support/messages` | 客户自己的对话；POST 带 UUID nonce，可安全重试 |
| GET | `/support/conversations` | 仅管理员会话列表 |
| GET / POST | `/support/conversations/{customer_id}/messages` | 仅管理员读取/回复指定客户 |
| GET | `/shop/orders` | 当前用户最近 50 个订单，不发起支付 |
| DELETE | `/diagrams/{diagram_id}/purge` | 仅所有者的回收站对象，必须 If-Match，成功 204 |

## 模块职责

项目入口：安装、配置、构建、运行与部署；代码导航见 docs/README.md。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`.coveragerc`](.coveragerc) | `36436fc1c69c` | L1–L34 |
| [`.dockerignore`](.dockerignore) | `9fe29d4eff0a` | L1–L25 |
| [`.env.example`](.env.example) | `6713c4cc4728` | L1–L113 |
| [`.gitattributes`](.gitattributes) | `1a1dbe176bc2` | L1–L2 |
| [`.gitignore`](.gitignore) | `903ed8828eee` | L1–L48 |
| [`Dockerfile`](Dockerfile) | `b702a9693af9` | L1–L42 |
| [`docker-compose.yml`](docker-compose.yml) | `1e5b48cf8873` | L1–L37 |
| [`main.py`](main.py) | `976473de883b` | L1–L71 |
| [`package-lock.json`](package-lock.json) | `1d584c7adee4` | 生成物，见模块构建说明 |
| [`package.json`](package.json) | `e7e67df85389` | L1–L16 |
| [`pytest.ini`](pytest.ini) | `4950b359cb81` | L1–L4 |
| [`requirements.txt`](requirements.txt) | `56bb140d8218` | L1–L65 |
| [`ruff.toml`](ruff.toml) | `c14a566fa6ec` | L1–L52 |
| [`vite.config.mjs`](vite.config.mjs) | `dad8237ecce1` | L1–L57 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

FastAPI 是生产运行时，Node 只用于 Vite 构建；生产配置与开发演示必须区分。

## 变更与验证

运行 pytest、ruff、npm run build 与文档构建；新增环境项同步 .env.example。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。
