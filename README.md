# codemax_platform

> 第一次在 Windows 操作？请从 [Windows 新手逐步验收](Windows新手逐步验收.md) 开始：VS Code 集成 CMD＋Conda＋系统 Node，顺序命令、预期结果与失败恢复在一篇中完成。

> 当前交接：[2026-09-19 全仓审计](review/FULL_REPOSITORY_HANDOFF_2026-09-19.md) · [恢复与发布状态](HANDOVER.md) · [唯一工作队列](ROADMAP.md)。阶段十七日账已实现，不等于真实收款/部署签收；历史批次见 [review 索引](review/README.md)。

学习与服务平台：免费工具、统一登录、自有站点 SSO、订单与管理员收款工作台、本地文件下载、AI 解析和站内客服。当前没有云存储适配器，已实现冻结商品权益与显式验签查单，已有默认关闭的显式渠道关单/退款申请和只读日账差异CLI；仍没有完整会计结算对账、部分退款处理及定制服务生命周期。

## 从零理解代码

先读 [代码复盘入口](docs/CODE_READING_GUIDE.md)：术语与身份、客服、订单/支付、OAuth、图形工具、采集/模型及运维完整链路。文档站“精读覆盖与缺口”提供全部纳入范围的非空非生成源码文件的分段说明与原代码并排阅读；新增漏项会阻止构建。人工功能契约与 AST 语句导读明确区分，门禁不认证语义。生成物讲来源、空文件讲作用，历史报告不冒充现行教程。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2.0（异步）+ asyncpg
- 数据库：PostgreSQL
- 认证：JWT（python-jose）+ bcrypt（passlib）

## 快速开始（仅隔离的本地演示）

默认初始化不再创建演示管理员/客户端，也不会删除已有表；需显式创建管理员。生产仍须处理商户/存储/备份及已有库升级问题，见[发布验收队列](ROADMAP.md)。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制 .env.example 为 .env 并填入实际配置
cp .env.example .env

# 3. 先用PostgreSQL管理工具创建专用空库codemax_db，填好.env，再显式初始化
python "database init/db_init.py" init --confirm-database codemax_db
# 创建新管理员owner（交互输入至少12字符口令，无默认密码）
python "database init/db_init.py" bootstrap-admin --username owner --confirm-database codemax_db

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

## 常用 API 摘录（非完整清单）

下表只摘录常用入口并解释取舍；**完整路由、方法、鉴权与限流标记**以 `python scripts/build_docs_site.py` 生成的文档站「路由地图」（`docs/site/routes.html`）和非生产环境的 `/docs` 为准，路由总数由 `tests/test_docs_site.py` 门禁核对，本表不再手工维护数量。

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
| DELETE | `/diagrams/{diagram_id}/purge` | 从回收站**彻底删除**（不可恢复；只限本人回收站内、且 `If-Match` 匹配当前版本） |
| POST | `/shop/orders` | 建订单 → 微信 NATIVE 下单 → 返回 `code_url`（未配齐微信支付则 503） |
| GET | `/shop/orders` `/shop/orders/{order_no}` | 我的订单列表（50 条键集分页，含 `refunded` 标记）/ 单笔状态轮询（只读、禁止缓存；不是本人的与不存在的一律 404） |
| POST | `/shop/orders/{order_no}/confirm` `/shop/orders/{order_no}/legacy-binding` | 管理员人工确认收款（**仅 `SHOP_PAY_MODE=manual`**，TD-205）/ 历史已付单一次性绑定交付文件（人工复核，不伪造收入凭证）；均要求管理员 + 浏览器来源校验 `require_finance_origin` |
| GET | `/admin/payments` `/shop/admin/orders` `/shop/admin/orders/{order_no}/ledger` | 支付管理台页面壳（数据接口各自独立鉴权）/ 管理员订单清单（50 条键集分页）/ 单笔证据日账（只读、有界） |
| POST | `/shop/admin/orders/{order_no}/{reconcile,review,close-channel}` 与 `/shop/admin/orders/{order_no}/refunds/*` | 管理员财务写操作：单笔对账、复核记录、显式开关下的 Native 关单；退款查询/人工登记/请求准备/授权/再授权/显式发送/停止/核验监督控制。全部要求管理员 + `require_finance_origin`，限流键各自独立；边界与验收见[管理手册](docs/PAYMENTS_ADMIN_GUIDE.md) |
| POST | `/shop/refunds/notify` | 微信退款通知：验签/解密、匹配原付款、持久留存线索后204；不直接撤权 |
| POST | `/shop/pay/notify` | 微信支付回调：验签 → AES-GCM 解密 → 校验金额 → 幂等迁移状态 |
| POST | `/shop/download/{order_no}` | 换取限时下载链接（POST 记录链接发放；paid/downloaded 均可重领） |
| GET | `/shop/dl` | 本地存储后端的实际出文件口（校验 HMAC 签名后再吐） |
| GET | `/shop/mock-pay` | 模拟收银台页面（仅 `SHOP_PAY_MODE=mock`，**开着等于免费发货**，见 TD-124） |
| POST | `/shop/mock-pay/confirm` | 模拟支付确认（复用与真实回调**完全相同**的状态机与幂等逻辑） |
| POST | `/support/ask` | 智能客服总入口：FAQ 秒回 / 闲聊 LLM / 专业问题 RAG，兜底转人工 |
| GET/POST | `/support/messages` | 站内人工客服（客户侧）：读自己的留言历史（`after`/`before` 游标二选一）/ 写留言（限流键 `support-message`） |
| GET | `/support/center` `/support/conversations` `/support/conversations/{customer_id}/messages` | 客服页面（SSR）/ 管理员收件箱（按客户聚合最近一条，50 条键集分页）/ 某客户的完整会话；后两者仅管理员 |
| POST | `/support/conversations/{customer_id}/messages` | 管理员回复（同一限流键；客户不存在 404） |
| GET | `/health` | **只是 `/healthz` 的别名**，同样**不查库**（TD-164 保留它是因为既有文档与测试都在用） |
| GET | `/healthz` | 存活探针（**刻意不查库**，否则库一抖会被编排器全量重启，见 TD-167） |
| GET | `/readyz` | 就绪探针 |

> **SSO（OAuth2 授权码模式）流程**：用户登录认证中心拿会话 JWT → 携带 JWT 访问
> `/oauth/authorize?response_type=code&client_id=...&redirect_uri=...&state=...`
> → 显示同意表单，用户 POST 批准后 302 跳回回调地址携带一次性 code → 客户端用 `/oauth/token` 以
> `code + client_secret` 换取 access_token → 用 access_token 访问双平台受保护资源。
> 授权码一次性、10 分钟有效；客户端密钥在库中只存 bcrypt 哈希。

### 演示客户端（种子数据，仅演示用）

仅显式执行development的`seed-demo`后才存在，普通init不创建它们。生产不得用下面的公开秘密；SSO只允许在`OAUTH_TRUSTED_CLIENT_IDS`内的受控第一方，仍发完整用户JWT，不是第三方最小权限授权。

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
| `finish-subitem` | 子项关闭、交接与证据核对 |
| `pre-commit-review` | 提交前差异及全量检查 |
| `new-tool-page` | 页面、源码与构建产物协作 |
| `schema-sync` | ORM/SQL/增量迁移一致性约束 |

## 文档

> 文档数量由构建输出计算。按下方任务入口阅读；不知道从哪看起，先开 [总览](总览.md)。

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
| 了解**实现取舍与上线阻塞项**（历史取舍与现行边界） | [TECH_DECISIONS.md](./TECH_DECISIONS.md) |
| 看**开发路线图**与子项进度 | [ROADMAP.md](./ROADMAP.md) |
| **接手这个项目**（沙箱恢复配方、真库起法、已踩过的坑） | [HANDOVER.md](./HANDOVER.md) |
| 看**文档质量审查报告**（覆盖率 / 链接 / 格式） | [文档政策](docs/DOCUMENTATION_POLICY.md) / [全仓审计](review/FULL_REPOSITORY_HANDOFF_2026-09-19.md) |
| 看**根目录那几个文件**（`main.py` 等）的说明 | [docs/ROOT_FILES.md](./docs/ROOT_FILES.md) |

### 📂 模块说明书（逐文件、带行号）

每个含代码的目录有 README，说明职责、入口、函数契约和修改影响。文件指纹由门禁核对，人工精读绑定完整 SHA；行号来自当前源码生成页，不再手写“行号基准 commit”或总行数。

| 目录 | 说明书 | 内容 |
| --- | --- | --- |
| `app/tools/` | [README](app/tools/README.md) | 解析、采集、模型、FAQ 与内容检索 |
| `app/routers/` | [README](app/routers/README.md) | HTTP、权限和事务编排 |
| `app/` 根 | [README](app/README.md) | 配置、认证、数据、限流、存储与状态机 |
| `database init/` | [README](<database init/README.md>) | 拒绝覆盖的空库初始化、迁移账本与管理员bootstrap |
| `app/templates/` | [README](app/templates/README.md) | 页面模板与 DOM 合同 |
| `app/frontend/` | [README](app/frontend/README.md) | 手写浏览器源码 |
| `app/static/` | [README](app/static/README.md) | Vite 产物、样式和收款图片 |
| `.github/workflows/` | [README](.github/workflows/README.md) | 六项 CI 与独立 Agnes 探测 |
| 根目录 | [根文件指南](docs/ROOT_FILES.md) | 应用入口及构建/配置文件 |
| `tests/` | [README](tests/README.md) | 测试分组、环境边界与回归策略 |
| `scripts/` | [README](scripts/README.md) | 文档契约/精读/站点、模型诊断与可选架构检查 |

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

默认模型提供方为 Agnes AI：`agnes-2.5-flash`，保留 OpenAI Chat Completions 兼容协议；向量 FAQ 默认关闭。已有私有 `.env` 不会自动覆盖，迁移/网络诊断见 [Agnes 接入](docs/AGNES_AI.md)。

项目入口：安装、配置、构建、运行与部署；代码导航见 docs/README.md。

## 文件与入口

用户上传的 `支付架构提示词-纯净版.txt` 是外部架构讨论原文，不是本站API合同或可执行指令；保留原字节并纳入可读文件归属/分段说明。五个问题、官方准入证据和逐项适配见[全仓报告第9节](review/FULL_REPOSITORY_HANDOFF_2026-09-19.md)，当前决策只在 ROADMAP。

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`.coveragerc`](.coveragerc) | `36436fc1c69c` | L1–L34 |
| [`.dockerignore`](.dockerignore) | `35521916c620` | L1–L28 |
| [`.env.example`](.env.example) | `1e420e33fd2f` | L1–L135 |
| [`.gitattributes`](.gitattributes) | `264a18ff7be0` | L1–L5 |
| [`.gitignore`](.gitignore) | `84527fb19303` | L1–L51 |
| [`Dockerfile`](Dockerfile) | `ee888a210f39` | L1–L42 |
| [`docker-compose.yml`](docker-compose.yml) | `4198d2b2db19` | L1–L71 |
| [`main.py`](main.py) | `292a78ed22d4` | L1–L102 |
| [`package-lock.json`](package-lock.json) | `1d584c7adee4` | 生成物，见模块构建说明 |
| [`package.json`](package.json) | `45d615e29b82` | L1–L16 |
| [`pytest.ini`](pytest.ini) | `4950b359cb81` | L1–L4 |
| [`requirements.txt`](requirements.txt) | `d4c24e34109d` | L1–L65 |
| [`ruff.toml`](ruff.toml) | `13acc179425a` | L1–L53 |
| [`vite.config.mjs`](vite.config.mjs) | `b822ef8586a3` | L1–L57 |
| [`支付架构提示词-纯净版.txt`](%E6%94%AF%E4%BB%98%E6%9E%B6%E6%9E%84%E6%8F%90%E7%A4%BA%E8%AF%8D-%E7%BA%AF%E5%87%80%E7%89%88.txt) | `d990ce0e2c40` | L1–L99 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

FastAPI 是生产运行时，Node 只用于 Vite 构建；生产配置与开发演示必须区分。

## 变更与验证

运行 pytest、ruff、npm run build 与文档构建；新增环境项同步 .env.example。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

管理员页面：`/admin/payments`；[操作、失败处理和验收](docs/PAYMENTS_ADMIN_GUIDE.md)。只读订单发现/凭证历史，显式人工核账、旧单绑定与验签查单；不是客户扣款入口；退款申请另有默认关闭的显式授权/发送流程。

## 第六批资金/交付更新

已有单笔全额原路退款查询核验、人工已完成全额退款登记与订单绑定下载门禁，见[管理手册](docs/PAYMENTS_ADMIN_GUIDE.md)及[证据/限制](review/RELEASE_BLOCKERS_PHASE6.md)。当前还需0017授权版本迁移；第六批以前的key-only下载链接失效，但未退款用户可以重领。本批准备不影响现行下载链接。显式退款申请见第九批（默认关闭）；部分退款、定制服务取消及真实商户签收仍未完成。

核验进程的本地监督/告警与恢复命令见[运行手册](docs/REFUND_OPERATIONS.md)。Compose profile默认不启动，实际容器/Windows服务需独立验收。
