# CodeMax Platform 全面只读审计报告

**审计目标：** `arena/01a08bf5-codemax-platform`，提交 `889e3ebbee1a3e6f9b3eb67d0a660ef16c5f65f4`  
**审计日期：** 2026-09-15（Europe/Copenhagen）  
**仓库：** `cccjvav/codemax_platform`  
**审计方式：** 只读代码审查与测试；未对目标实现进行修复，未提交、未推送、未创建拉取请求  
**审计检出方式：** Arena 固定检出始终停留在 `arena/01a0852c-codemax-platform`；目标 Git 对象单独导出并测试。

> **目标完整性说明：** 本报告审计的准确 SHA 为 `889e3ebbee1a3e6f9b3eb67d0a660ef16c5f65f4`。固定会话分支中与本次目标无关的未跟踪文件从未被计入目标文件清单。

---

## 1. 执行摘要与结论

以项目规模衡量，本仓库具有异常扎实的自动回归测试：共收集 838 个测试；除有明确说明且依赖环境的跳过项外，SQLite 与 PostgreSQL 测试全部通过；Ruff、Python 编译、依赖审计、Vite 构建及产物漂移检查、文档契约与构建、PostgreSQL 直接执行架构 SQL 均通过。实现中也有多项应当保留的真实防护：参数化 ORM 访问、把密码哈希移出事件循环、私有图表所有权检查、原子状态迁移、OAuth 重定向地址精确匹配、严格模式 Mermaid 渲染、流式下载，以及总体可靠的爬虫 DNS 固定机制。

但这些绿色检查**不能**证明当前应用适合无人值守的公开商业支付或外部 OAuth 客户端。主要发布阻断项属于架构问题，现有 CI 基本没有覆盖：

1. 新数据库会故意创建已知账号 `admin / 123456`，并创建两个秘密值公开的 OAuth 客户端。默认 Compose/示例配置仍为 `ENV=development`；若运维人员没有修改，JWT/HMAC 密钥也是公开已知值。
2. 发起支付时，在本地订单事务提交前就跨越了支付服务商边界。系统没有支付事件账本、对账任务、退款/争议生命周期、服务商关单/查单流程，也没有不可变商品与权益快照。真实付款可能成为孤儿支付、被错误拒绝，或按照当前已变化的文件/配置发货。
3. 数据库初始化与升级没有迁移账本或迁移锁。`full_init.sql` 本身具有破坏性；多条迁移路径只实现了部分原子性；迁移 0008 每次重跑都会删除全部 OAuth 授权码。
4. 启动检查和 `/readyz` 只验证了实际就绪条件中的很小一部分。生产进程可能显示“就绪”，但选定支付模式、回调证书、商品文件、架构版本、存储后端、代理链路或外部模型实际不可用。
5. 进程内、按 IP 限流既不是分布式保护，计算复杂度也没有界限。专项基准在接纳 20,000 个不同键时遍历字典 199,485,200 次，耗时 10.5422 秒。
6. 签名下载能力被放在查询字符串里，默认 Uvicorn/代理访问日志会记录它。应用日志还会原样反射 5,000 字节请求 ID；路径中的百分号编码回车被解码后可以拆分日志记录。
7. OAuth 签发的访问令牌与第一方直接登录令牌没有区别：不包含客户端、受众或作用域，并会获得用户数据库中的当前角色。低信任客户端一旦由管理员授权，就得到等同管理员的 Bearer 权限。
8. 对外宣称支持“MySQL/PostgreSQL”的 SQL 解析器会对常见合法 DDL 静默丢失或凭空制造架构事实，也没有诊断或明确声明支持的语法子集。
9. 用户文本和爬取材料会被发送给外部处理方，但相关页面没有可访问的用户告知、目的/保留政策、来源与许可证记录或发布前人工审批。Draw.io iframe 同样会把图表内容交给外部源处理。
10. 前端切换账号/文档时可能丢弃尚未保存的 Draw.io 内容；已确认的延迟轮询响应会在用户返回商品首页后重新打开支付界面。

### 1.1 按功能划分的部署建议

| 功能/部署方式 | 目标 SHA 下的结论 |
|---|---|
| 使用一次性数据且设置 `SHOP_PAY_MODE=mock` 的隔离本地演示 | 在明确“模拟支付等于免费发货”、种子凭据公开的前提下可以使用；不得暴露给不可信网络。 |
| 公开信息页面 | 只有完成生产模式、Host/代理、种子账号、日志及滥用控制后，才可有条件部署。 |
| 公共 ER/Word 工具 | 在明确 SQL 支持子集、并使不支持输入返回诊断而不是看似合理的错误结果前，不应宣称可靠。 |
| 登录后的 Draw.io 存储 | 在处理脏状态/账号切换、PostgreSQL NUL、架构/版本就绪、备份和真实浏览器测试前，不适合对数据丢失敏感的用途。 |
| 公共 LLM/支持接口 | 在具备全局及服务商预算、响应字节上限、并发/熔断、用户告知和保留规则前，不适合开放式生产流量。 |
| 面向真实第三方客户端的 OAuth | 在实现作用域/受众绑定令牌、PKCE/公共客户端策略、同意新鲜度、HTTPS 重定向策略和客户端配置流程前，禁止上线。 |
| 微信或人工真实资金交易 | **禁止上线。** 收款前必须解决 C-02 及其验收测试。 |
| 多 worker 或多实例部署 | 当前限流、爬虫状态、RAG 缓存和本地存储明确不支持。共享协调/存储完成前保持单 worker、单副本。 |

### 1.2 严重性与置信度

- **严重（Critical）：** 存在可信的账号接管、不可逆数据破坏或资金/权益故障路径；对受影响模式构成发布阻断。
- **高（High）：** 显著的安全、可用性、正确性、隐私或运维风险。
- **中（Medium）：** 影响有边界的缺陷，或重要的加固/质量缺口。
- **低（Low）：** 当前直接影响有限的优化或可维护性机会。
- **已确认：** 已复现，或可从执行结果/源码语义直接确定。
- **高置信度分析：** 源码路径清晰，但最终外部端点未实际运行。
- **风险：** 最终影响取决于部署、服务商或法律环境。

下列编号按根因分组，并非 CVE 数量；一个组可能包含多个可独立测试的缺陷。

---

## 2. 范围、清单与准确基线

### 2.1 目标分支跟踪文件清单

对 `889e3ebb...` 执行 `git ls-tree -r --name-only` 得到 **293 个跟踪文件**：

| 类型 | 数量 | 说明 |
|---|---:|---|
| Python | 97 | 应用、脚本、数据库辅助代码，以及 `tests/` 下 53 个文件 |
| JavaScript | 107 | 9 个手写前端/源码文件，加 `app/static/js/` 下 98 个生成文件 |
| MJS | 2 | Vite 配置及可选 PGlite 架构检查器 |
| Markdown | 48 | 当前指南、带日期的审计/验收记录、管理材料和 Agent skills |
| HTML/Jinja 模板 | 9 | 8 个应用模板及 1 份文档 HTML 材料 |
| SQL | 9 | 破坏性完整初始化及迁移 0001–0008 |
| YAML | 3 | 两个 GitHub 工作流及 Compose |
| CSS | 2 | 应用 CSS 与生成文档站点的源 CSS |
| JSON | 5 | 锁文件、配置及阅读笔记数据 |
| 其他文本/配置/二进制 | 12 | Dockerfile、环境示例、ini/toml/txt、attributes/ignore、SVG 和 PNG |
| TypeScript | 0 | 不存在 |
| 被 Git 跟踪的 Shell 脚本 | 0 | 工作流/文档中存在 Shell 逻辑，已在对应位置审查 |

审计覆盖 Python、手写 JS/MJS、模板、CSS、SQL、Docker/Compose、工作流、配置、脚本、测试、生成资产图、锁文件、文档、二进制用途记录及可访问历史。对压缩的第三方 bundle 审查了来源、导入闭包、大小和依赖/许可证清单；本报告不会假装逐 token 逆向所有压缩供应商代码能提供可靠的语义保证。

### 2.2 运行时与生成清单

- 开发环境共有 **51 个方法/路径注册，覆盖 43 条路径**，其中包含 4 个框架文档注册；排除 `/docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect` 后为 **47 个注册、39 条路径**。
- 另挂载 `/static`。
- `full_init.sql` 创建 **8 张数据库表**。
- 51 个 `tests/test_*.py` 模块共收集 **838 个 pytest 节点**。
- 文档构建生成 **41 个文档页面 + 244 个源码页面**，另有首页、可视化页和阅读页。
- 该 SHA 的阅读笔记数据：**140 个非空且非生成文件、1,489 个连续区块、22,310 行源码**；方式分布为 **60 个手工/默认手工 + 80 个引导式**。
- 244 个源码页面由 **140 个已注释源码文件 + 99 个生成文件 + 4 个空包标记 + 1 个阅读笔记数据文件**构成。
- 生成的浏览器 JavaScript 合计 **3,394,937 字节**。Mermaid 入口基础静态闭包为 **684,947 字节**；典型类图闭包约 **737,355 字节**，均为 HTTP 压缩前体积；仓库未配置压缩。
- `package-lock.json` 有 154 条 package 记录（含根包），即 **153 条依赖包记录**。

### 2.3 测试与工具结果

| 检查 | 结果 | 限定说明 |
|---|---|---|
| Python compileall / JS 语法解析 | 通过 | 仅证明语法，不证明类型或浏览器行为 |
| Ruff | 通过 | Ruff 是唯一配置的 Python 分析器；无 mypy/pyright、Bandit、Semgrep 等 |
| SQLite 全量 pytest | **832 通过，6 跳过** | 共 838；普通应用 fixture 全局关闭限流，限流由专门测试覆盖 |
| PostgreSQL 16.2 全量 pytest | **837 通过，1 跳过** | 一次性非超级用户角色；仅真实 embedding 校准跳过 |
| 语句覆盖率 | **95.3%** | `.coveragerc` 的 source 为 `app, main`；无分支阈值；脚本、数据库辅助、前端不计入 |
| PostgreSQL 执行 `full_init.sql` | 两次均成功；8 表 | 第二次会删除并重建全部数据，只证明可重复执行，**不证明幂等或数据保留** |
| PGlite 执行 `full_init.sql` | 两次均成功；8 表 | 可选兼容性冒烟检查，不能替代 PostgreSQL 或迁移测试 |
| ORM/DDL 静态一致性测试 | 通过 | 只比较表名/列名，且使用产品自身不完整的解析器；见 H-08 |
| Vite `npm ci` + 构建 + 已提交产物漂移 | 通过 | 构建产生了上述大型 Mermaid chunk |
| 本地 `npm audit` | **0 个漏洞** | 仅代表当前快照；文档称 CI 会运行，但 CI 实际未运行 |
| `pip-audit --strict -r requirements.txt` | 通过，但有一项显式忽略 | `ecdsa 0.19.2 / PYSEC-2026-1325` 无上游修复版本，属于已记录债务 |
| 文档契约 | 通过 | 证明清单归属、指纹和连续范围，不证明语义仍然正确 |
| 完整文档渲染/链接/范围检查 | 通过 | 41 个文档页面及 244 个源码页面 |
| ASGI/Uvicorn 运行时探针 | 按设计呈混合结果 | 已确认请求 ID 反射、解码 CR 拆日志、Host/转发 Host 派生和代理链行为 |
| SQL 解析器专项语料 | 语义失败 | 复现已保留；见 H-07 |
| Docker 镜像构建 | **未运行** | 审计环境无 Docker，不得从 Compose 或 CI 推断镜像构建成功 |
| 真实浏览器、Word、Draw.io 网络、微信商户、真实回调、生产代理、备份恢复 | **未运行** | 现有 mock/Node VM 不能替代这些测试 |
| 当前目标 GitHub 运行 | 按预期通过/跳过 | CI `34769654353`：6 个 job 成功；Agnes `34769654328`：连通性通过，含秘密的 `live-chat` 跳过 |

早期 SHA 文档记录的 Agnes 聊天/Mermaid 成功仍是有效的历史证据，但并非当前 SHA 的含秘密运行。GitHub 分支保护查询返回 403，因此保护状态未知。

---

## 3. 严重问题

### C-01 — 全新初始化会创建公开已知的特权身份，默认部署还可能保留公开签名密钥

**严重性：** 严重  
**置信度：** 已确认  
**影响位置：** `database init/full_init.sql:64-100`、`README.md`、`.env.example`、`docker-compose.yml`、`app/config.py`、`app/startup_checks.py`

#### 证据与影响

- `full_init.sql` 每次都会插入角色为 `1` 的 `admin`，其 bcrypt 哈希对应已公开密码 `123456`。
- 同时插入 `tools / codemax-tools-secret` 和 `shop / codemax-shop-secret`；SQL 注释与 `README.md` 中均有明文。
- Compose 将该 SQL 挂载到 `/docker-entrypoint-initdb.d`，所以这是默认新数据库结果，不是仅测试 fixture。
- `.env.example` 默认 `ENV=development`、`SECRET_KEY=dev-secret-change-me`，Compose 不覆盖 `ENV`。若仅复制示例并填写必需数据库密码，应用将使用非 Secure 登录 Cookie、开发文档、无生产启动检查，以及已知的 JWT/下载/同意 HMAC 密钥。
- `get_current_user` 会从数据库加载角色/状态，这本是良好控制；但默认密钥泄露时，伪造一个指向已知种子管理员的 JWT 仍会解析到该特权行。

因此，全新或意外重新初始化的公网部署可以被立即接管。只轮换管理员密码并不能解决已知 OAuth 客户端凭据和已知应用签名密钥。

#### 复现

1. 在一次性 PostgreSQL 数据库执行 `full_init.sql`。
2. 使用示例开发配置启动应用。
3. 以表单 `username=admin&password=123456` 请求 `POST /auth/login`，可获得 Bearer 令牌与登录 Cookie。
4. 该令牌可访问管理员文章摄取、支持收件箱/回复和人工支付确认。

#### 必需修复

- 从生产初始化路径删除所有业务身份。将演示种子放入单独、显式且失败关闭的命令，例如 `seed_demo_data --i-understand-this-is-disposable`，并在 `ENV=production` 时拒绝执行。
- 通过一次性 bootstrap 秘密、运维命令或身份管理流程创建首位管理员；强制高熵凭据并记录创建人。
- 提供迁移/事件响应步骤，查找并禁用或轮换**已经部署**的种子账号与客户端。仅修改 `full_init.sql` 不能修复已有数据库。
- 让生产 Compose profile 强制 `ENV=production`、要求高熵密钥；JWT、同意表单、下载链接使用独立用途密钥或 HKDF 派生密钥。
- 启动时查询并拒绝生产数据库中已知种子哈希、客户端 ID/秘密。
- 添加回归测试：初始化生产架构后，所有仓库公开凭据均无法登录或换取令牌。

不得只从文档隐藏密码却保留同一个哈希：凭据已经公开，Git 历史也仍可访问。

---

### C-02 — 真实资金支付与发货不是可恢复、不可变、可审计的状态机

**严重性：** 对 `wechat` 或 `manual` 为严重；若商业功能禁用则为高  
**置信度：** 已确认的源码分析与运行时故障注入  
**影响位置：** `app/routers/shop.py`、`app/order_state.py`、`app/wechat_pay.py`、`app/storage.py`、`app/models.py`、架构/迁移、部署/验收文档

这是一个包含五项相互影响缺陷的架构问题。

#### A. 本地订单提交前就调用了服务商预支付

`create_order()` 添加并 flush 订单，调用 `native_prepay()`，最后才 `db.commit()`。其 docstring 声称“订单先落库再去下单”，但 flush 并不持久。故障注入中，假服务商接受预支付后，强制 commit 失败；结果是服务商已有订单，本地却没有持久订单。

后果：

- 服务商回调可能在本地行尚未提交时到达，收到 `404 order not found`。
- 服务商接受后若进程崩溃、死锁、连接丢失或序列化/提交失败，会留下孤儿服务商订单。
- 重试可能在没有持久支付尝试记录的情况下复用或制造不一致状态。
- 10 秒外部调用期间数据库事务/连接一直打开。

**正确模式：** 先提交持久的支付尝试；取得数据库租约/幂等键；用稳定商户订单号调用服务商；持久记录结果或“结果未知”；通过服务商查单对账恢复未知结果。不能通过把更多网络操作包进更长数据库事务来“修复”。

#### B. 回调验证能认证字节，却未完整绑定业务事件

已有良好控制应保留：原始请求体签名验证、时间戳新鲜度、AES-GCM 认证、与持久订单金额比较，以及原子/幂等状态转换。

缺失项：

- `await request.body()` 和 UTF-8 解码前无请求体字节上限；无效 UTF-8 当前会产生未认证的 500。
- 每个时间戳有效请求都重新解析证书/公钥并做 RSA 验证；回调没有专属限流/准入，也没有缓存密钥对象。
- `Wechatpay-Serial` 未与选定密钥/版本绑定。
- 解密后仅检查 `event_type`、`trade_state`、`out_trade_no` 和金额 total；未检查完整期望元组：`appid`、`mchid`、币种、交易 ID 格式/唯一性、resource 算法/原始类型、交易类型、服务商成功时间。
- `decrypt_resource()` 尽管注解为 `dict`，仍可返回 JSON 标量；路由随后调用 `.get` 并返回 500。
- `native_prepay()` 完整缓冲响应，无法安全处理 malformed `200` JSON；也未在后续字符串操作前约束 `code_url` 的类型、scheme 和长度。
- 回调记录本地接收时间，而不是已认证的服务商 `success_time`。
- 已支付订单收到第二个真实交易 ID 时，被当成普通重复而不是财务异常。

应使用严格 Pydantic/服务商模型，按 serial 缓存验证后的密钥，在代理和应用层实施入口限制；先保存完整认证事件，再投影业务状态；矛盾事件必须告警。

#### C. 没有事件账本、对账、服务商过期/关单、退款或争议生命周期

`sys_order` 只保存当前状态与一个交易 ID。非成功/退款通知会被确认后丢弃。没有不可变通知 ID/载荷摘要、支付尝试、对账游标、退款/争议状态、关单结果或运维工单记录。

`ORDER_EXPIRE_MINUTES` 是可变的当前配置，且仅当用户创建另一订单时才会关闭 pending 行。轮询虽然显示“expired”，但不会关闭本地或服务商订单，也从不调用服务商关单。代码有意允许 `CLOSED -> PAID`，所以 UI 的“请勿扫码”并非服务商强制过期。

外部退款、拒付、服务商支付成功但无回调、证书故障、事件期间拒绝的回调，都可能让应用状态无限期错误。

应实现 append-only `payment_event` 表（保存服务商事件 ID/摘要并定义加密原文保留策略）、`payment_attempt` 表、查单/关单/退款 API、定时对账，以及明确的 `REFUNDED`、`PARTIALLY_REFUNDED`、`DISPUTED` 和发货状态，并向运维展示告警。事件插入与投影变更必须原子提交。

#### D. 已付款权益依赖可变的当前配置

订单只快照商品显示名和金额。下载时读取当前 `STORAGE_BACKEND`、`STORAGE_PRODUCT_KEY`、`STORAGE_LOCAL_ROOT`、`DOWNLOAD_URL_TTL`、`SECRET_KEY`；状态/历史则报告当前 `SHOP_PAY_MODE`。没有 `code_url` 的 pending 订单可以从人工模式切到微信，并用**当前**金额/名称预下单；后续回调仍比较持久金额，可能永久无法完成。

修改商品 key/文件会追溯替换或删除所有买家的交付物。系统没有 SKU/版本、不可变对象摘要、权益行、签发历史、撤销原因或保留承诺。`downloaded` 仅表示“签发过链接”，不表示字节成功送达；系统有意允许重新签发，这一点应保留。

应建立不可变 offer/商品版本与权益快照，包含 SKU/版本、金额/币种、支付模式/服务商、对象 key/版本/摘要、条款版本及交付政策。按承诺周期保留旧制品。把链接签发视为事件，而不是销毁权益。

#### E. 人工支付可在没有持久证据时确认错误款项

通用个人二维码无法机器绑定订单引用。管理员仅凭订单号即可把任意 pending/closed 订单标记为已支付；交易 ID 自动生成 `MANUAL-<order>`。没有收款引用、付款人/金额证据、双人复核、唯一银行/支付交易、纠正/撤销或持久审计行。所谓审计记录在状态提交**之后**才写入同一普通 stdout 日志；进程崩溃或日志丢失会使资金状态与证据分离。

若保留人工模式，应提供受控运维 UI，要求 MFA、金额/订单/收款凭证字段、外部引用唯一约束、附件与脱敏政策、原因、操作者 ID、时间戳、纠错流程，并在同一事务写入审计行。公开已提交个人收款二维码前，应确定适用的支付账号、消费者和法律要求。

#### C-02 验收测试

- 在每个边界注入崩溃/故障：本地尝试提交前后、服务商响应前后、投影提交、回调事件写入、权益签发。
- 回调早于预支付响应，以及重复/冲突事件测试。
- 服务商查单对账可恢复 paid/closed/unknown，且不会重复扣款。
- 修改价格、模式、商品 key 不会改变已有订单条款或交付物。
- 退款/争议按照明确政策取消或标注权益，同时不删除证据。
- 人工确认不能重复使用同一收款引用，也不能在缺少持久操作者/证据字段时提交。
- 无效 UTF-8、超大请求体、解密后 JSON 标量、错误 app/mchid/币种/serial、畸形服务商 JSON、密钥轮换，都按服务商规范返回安全响应，不产生 500 或数据变更。

在这些测试通过 staging 商户验证前，两个真实支付模式都必须保持不可用。

---

## 4. 高严重性问题

### H-01 — 架构初始化与升级具有破坏性、部分非原子、无锁且无版本账本

**置信度：** 已确认

- `full_init.sql` 以 8 条 `DROP TABLE ... CASCADE` 开头。`db_init.py` 会在一个事务内执行整个文件，但 Docker 入口通过 `psql` 执行 SQL，没有外围 `BEGIN`；中途报错可能留下只删除或只重建一部分的数据库。
- CI 与 `check_schema_pg.mjs` 连续执行两次并称其“幂等”。第二次其实删除并重建全部数据与种子身份，只证明“可重复破坏”，不证明幂等。
- 迁移 0002–0006 并非统一包在事务中。默认 `psql` 下，0003 的多个逻辑步骤会分别提交；中断可造成列/索引漂移。
- 0007 使用 `CREATE TABLE IF NOT EXISTS`；如果已经存在结构错误的同名表，预期定义会被静默跳过。
- 0008 每次运行都会执行 `DELETE FROM oauth_code`，即使凭据版本列早已存在。
- 没有 `schema_migrations` 账本、校验和、advisory lock、单一迁移执行者、启动所需版本检查、降级/回滚方案，也未测试从每个受支持部署版本升级。
- 文档要求运维人员先理解当前基线，再自行选择要运行的迁移；这不是确定性的部署机制。

**修复建议：** 采用 Alembic 或同类顺序迁移器，加入校验和与 PostgreSQL advisory lock。新建初始化不得破坏数据；reset/drop 必须移动到名称醒目的独立测试/演示命令。一次性数据迁移由账本保护。使用脱敏架构快照测试各版本升级，并在每步后验证数据/约束。`/readyz` 应拒绝不受支持的架构版本。

不要让每个应用副本在启动时自动执行破坏性迁移。

---

### H-02 — 配置验证和就绪检查把关键故障推迟到真实流量或付款时才暴露

**置信度：** 已确认

正面项：`ENV` 正确声明为 `Literal["development", "production"]`；数值限制边界合理；生产模式会拒绝 mock 支付、默认/过短密钥、关闭限流及空数据库凭据；未知存储后端在实际使用时失败关闭。

缺口：

- `SettingsConfigDict(..., extra="ignore")` 会静默忽略拼错的 `.env` 键。
- `SHOP_PAY_MODE`、`STORAGE_BACKEND`、`ALGORITHM`、`LOG_LEVEL` 是自由字符串；无效值可能分别在 import、创建令牌或购买时才失败。
- 应用允许 `LLM_BASE_URL` 使用明文 HTTP，而独立探针工具反而会拒绝。
- 数据库 Host/库名及显式 `DATABASE_URL` 未限定受支持异步驱动，也未结构化验证；构造 URL 时只转义用户名/密码。
- 生产环境对 `SITE_BASE_URL` 仅检查字符串是否以 `https://` 开头。
- 默认生产支付模式为微信，但启动不要求或解析商户私钥、APIv3 密钥、平台密钥/证书、通知 URL 或预期回调身份。
- 启动不构造/检查所选存储，不解析本地根目录及权限，不验证商品对象、ZIP/摘要或人工二维码路径。
- 启动不检查数据库连接和架构版本；`/readyz` 只执行 `SELECT 1`。
- 代理就绪仅检查布尔开关，无法确认直接 peer 是否会匹配 `TRUSTED_PROXY_CIDRS`。
- 外部 LLM 就绪有意 best-effort，但没有 feature state/熔断器告知付费或公共模型接口已禁用。

**修复建议：** 部署验证时拒绝未知环境键；使用枚举及严格 URL/密钥/证书模型；启动时验证当前选择模式的全部资源；分别暴露存活、依赖就绪和功能就绪状态；安全展示 build SHA、架构版本与无秘密配置指纹。可选功能应明确禁用，而不是等买家请求后才首次报配置错误。

---

### H-03 — OAuth 令牌实质是无作用域的一方会话，同意凭据可重复提交

**置信度：** 已确认

- OAuth 换取的 JWT 与直接登录完全同形：`sub`、`exp`、密码 stamp、凭据版本；没有 `iss`、`aud`、`client_id`/authorized party、scope、grant ID 或 token ID。
- 所有受保护端点随后读取用户当前数据库角色。管理员授权普通客户端后，该 Bearer 可调用管理员接口；“tools”令牌也能访问商店、图表、消息及其他所有认证资源。
- 没有 scope 请求/展示/强制，也没有资源服务器受众隔离。
- 签名同意表单不含签发时间、过期、随机 nonce 或一次性记录。在同一浏览器会话仍认证时重复 POST 同一批准，可再签发一枚 code；已复现。
- 未实现 PKCE。当前设计假设 confidential client，但文档广泛描述平台/浏览器 SSO，并公开演示客户端秘密。
- 注册重定向 URI 可为 HTTP。精确匹配能防开放重定向，却不能让明文传递授权码变安全。
- 授权码明文存储，只在下次授权时清理旧码；闲置安装会无限期保留。
- OAuth 错误被嵌在 FastAPI `detail` 中，而不是可互操作的 OAuth 错误结构、状态和响应头。

**修复建议：** 建立 grant/client/scope 模型；签发绑定受众和客户端的最小权限令牌；除非显式管理员 scope 与政策允许，OAuth 令牌不得继承管理员 API。加入 PKCE S256（公共客户端强制）、有 nonce/过期/一次性的同意事务、HTTPS 重定向政策及严格的 loopback 开发例外、授权码哈希、定时清理、撤销、标准错误和 `Cache-Control: no-store`。对每个受保护域加入跨客户端负向测试。

现有的重定向精确匹配、授权码一次性原子消费、凭据 revision、禁用用户检查、重定向不携带 access token 都是正确控制，应保留。

---

### H-04 — 准入控制只在进程内生效，而且清理算法本身会阻塞服务

**置信度：** 已确认的基准与源码分析  
**证据：** `AUDIT_RATELIMIT_PRUNE_PROBE_01a08bf5.txt`

当键超过 1,024 个时，`Limiter.allow()` 会在每次允许请求后扫描整个键字典。键未过期时无项可删。专项结果：

```text
5,000 keys  -> 遍历 11,977,700 项，0.6381 秒
10,000 keys -> 遍历 49,480,200 项，2.5758 秒
20,000 keys -> 遍历 199,485,200 项，10.5422 秒
```

同步扫描运行在事件循环线程上，因此不同来源/IP 可以把限流器本身武器化成近似二次复杂度 CPU 停顿。其他边界缺口：

- 状态为进程内；`N` 个 worker/副本会把每项额度扩大 `N` 倍。
- 只有按派生 IP 的限制；没有全局/服务商预算、认证用户配额、商户账号上限或熔断器。
- 免费注册可让攻击者扩大每用户图表/消息/订单存储；配额按账号而非租户/系统。无验证、CAPTCHA/邀请、账号总量上限、生命周期清理或总存储预算。
- `/shop/orders`、图表变更、OAuth 授权、状态/历史轮询和若干重数据库读取没有专门限制。
- ER 解析使用通用线程池；Word 的 DDL 解析发生在双任务 CPU 准入计数器之前。来自许多 IP 的公开请求可在 `run_cpu_bound()` 拒绝前耗尽线程。
- 超时 CPU 任务被 shield 后继续执行。一直占用 `_inflight` 槽直到结束比继续接纳新任务更安全，但卡死任务可永久占容量，且无法干净终止。
- 认证 bcrypt 与支付回调 RSA 都有每请求成本，却没有全局并发保护。

**修复建议：** 在边缘实施请求体/请求数/连接限制；单进程使用常数时间、有界的本地结构，多实例使用 Redis/API Gateway 原子桶；设置分层的全局 + 端点 + IP + 认证用户 + 服务商预算；增加队列深度、deadline、取消指标；隔离可终止 CPU worker；增加账号/存储总量控制。可信代理网段应在启动时规范化并缓存。

不能通过信任任意 `X-Forwarded-For` 来“修复”。当前从右向左遍历可信代理的算法实质正确，应保留。

---

### H-05 — 日志设计没有保护能力 URL 和需要审计的事件

**置信度：** 已确认

- 本地签名下载使用 `/shop/dl?key=...&expires=...&signature=...`。应用自定义日志只用 `scope.path`，但默认 Uvicorn 访问日志会记录完整查询目标，常见反向代理也一样。任何能读日志的人可在过期前重放 Bearer URL。
- 客户端 `X-Request-ID` 被原样接受并反射到响应/日志。真实 Uvicorn 探针成功发送并回显 5,000 字节；纯空白 ID 也接受。
- 请求路径中的百分号编码回车到 ASGI 时变成 `\r`；直接和真实服务探针均显示它会把普通文本日志拆成伪造行。
- 直接 ASGI 也说明任意控制字符应编码；不过真实 Uvicorn 拒绝了原始 ANSI 请求头。不要夸大原始 header 的可利用性，已确认的路径 CR 与超长反射已经足够。
- `extra={"rid": ...}` 和人工支付 `extra={...}` 字段被当前普通 formatter 丢弃。输出是行文本，并非文档所称“结构化日志”。
- 人工支付审计、包含原始用户问题的意图标签样本、应用错误和访问记录共享 stdout。没有独立、持久、权限隔离、append-only 的审计 sink，也没有保留、完整性或脱敏契约。
- 日志中间件位于 Starlette 外层 server-error 层以内；需要端到端测试确认意外异常时仍保留响应请求 ID/安全头和关联日志。

**修复建议：** 将不透明下载令牌放入路径/请求头，或在每层服务器/代理脱敏查询参数；保持短 TTL，并记录签发/撤销。只接受有长度上限的安全 request-ID 语法（例如 1–128 个可打印 token 字符），否则生成替代值；所有不可信字段用 JSON 编码。真正配置 JSON 日志，把财务/安全审计分离，明确保留与访问规则，并针对真实服务器/代理测试日志伪造与脱敏。不得记录回调请求体、JWT、Cookie、服务商密钥或完整签名 URL。

---

### H-06 — 爬虫 SSRF 防护较好，但重定向政策、总资源期限和来源记账不完整

**置信度：** 已确认的源码与聚焦 transport 探针

应保留的控制：仅 HTTP(S)、URL 禁止 userinfo、所有解析地址都必须是公网地址、每次重定向重新校验；生产 transport 固定连接到批准 IP，同时保留 Host/TLS SNI；禁用环境代理；固定 Host 间禁用 keepalive；限制重定向；以流方式读取解码响应并设置大小阈值。

剩余缺陷：

- `robots.txt` 与节流准入只应用于初始 origin/path。被允许 URL 可重定向到同源 robots 禁止路径，或另一个从未查询 robots/crawl-delay 的源。
- 重定向请求仍记在初始 origin 的节流状态，而非每个目标 origin。
- robots 获取发生在全局 semaphore 之前；大量不同 origin 可制造无界并发 DNS/robots 请求。
- 全局 semaphore 在 `Crawl-delay` 睡眠**之前**取得。四个声明极大 delay 的站点可占满四个抓取槽，而无任何网络请求在执行。
- 无 crawl-delay 上限、总操作 deadline、每跳预算或客户端断开取消政策。十次各 15 秒的重定向/网络窗口，再加 robots 和 delay，可能占用 HTTP 请求数分钟。
- 文档中的 `ROBOTS_TIMEOUT=10` 未使用；robots 继承普通 `_request` 的 15 秒超时。
- origin 键直接使用 `(scheme, netloc)`，大小写、默认端口、IDN 等等价形式可获得不同缓存/节流状态。
- `_states` 无淘汰。当前仅管理员可用可降低风险，但无法消除运维耗尽或管理员账号被攻陷后的风险。
- 解码输出有上限，但压缩输入大小/压缩比/CPU 无独立政策；后解码大小比较看到 chunk 前，解码器可能已分配资源并做大量工作。
- 解析前不检查 Content-Type。BeautifulSoup 解析、skeleton 生成、模型选择的 CSS selector 均同步运行在异步请求中；复杂 selector/2 MB 页面可阻塞事件循环。
- 外部页面文本是不可信 prompt，可影响 selector 选择。验证虽能防代码执行，却不能防止选择错误或恶意内容容器。

**修复建议：** 对每个重定向目标重新执行 robots 和按源调度；稀缺网络槽仅在实际 I/O 时占用；限制 delay 与总墙钟时间；规范化 origin；淘汰状态；分别限制编码与解码表示；限制 selector 语法/长度/复杂度；把解析/选择移动到隔离且有界的 worker；记录来源与审核状态。robots 是政策/合规控制，不是授权机制。

---

### H-07 — SQL 解析器会静默生成看似合理但实际错误的 ER/数据字典

**置信度：** 语料复现确认  
**证据：** `AUDIT_SQL_PARSER_PROBES_01a08bf5.txt`

代表性合法输入及观察到的损失：

| 输入类别 | 实际结果 |
|---|---|
| `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` | 两张表出现，但边列表为空 |
| `REFERENCES parent` 隐式引用父表主键 | 关系边被遗漏 |
| PostgreSQL dollar-quoted 函数体内含 `CREATE TABLE phantom...` | 输出一张并不存在的 phantom 表 |
| 嵌套块注释中含 DDL | 输出 phantom 表 |
| PostgreSQL 未引号 `Foo` / `ID`，并引用 `foo(id)` | 错误保留表/列大小写，关系端点悬空 |
| schema-qualified/custom type `public.mytype` | 类型退化成 `PUBLIC` |
| `TIMESTAMP(6) WITH TIME ZONE`、`DOUBLE PRECISION`、`INTEGER[]` | 精度、修饰符、第二个词、数组信息丢失 |
| 复合主键含带引号标识符 `"a,b"` | 该列没有被标为主键成员 |
| 多个 schema 存在同名未限定表 | schema 身份丢失并发生碰撞 |

解析器还忽略常见表约束、索引、生成列/identity 语义、方言修饰符及许多 `ALTER` 形式。公共文案宣称支持 MySQL 与 PostgreSQL，“包括注释、复合主键和外键”，但没有方言选择、支持语法契约、警告清单、未解析关系诊断或“部分结果”标记。看似可信的错误图和 DOCX 比明确拒绝更危险。

**修复路径：**

1. 优先采用维护中的方言感知解析器，保留带引号/不带引号标识符语义、schema 身份、源码范围、诊断和未支持节点；或
2. 明确定义狭窄语法，拒绝所有超出范围的输入，并把输出明确标为不完整。

无论采用哪条路径，都应校验关系边必须指向已输出实体，不得静默返回悬空引用。建立差分语料：在一次性 PostgreSQL/MySQL 兼容引擎真正执行 DDL，反查系统 catalog，再比较规范化图。为注释、引号、dollar string、数组、自定义类型、嵌套表达式和畸形输入加入性质测试/模糊测试。保留输入上限并隔离解析 CPU。

---

### H-08 — ORM 测试与生产 DDL 并非同一架构，而一致性门禁只检查名称

**置信度：** 已确认的源码/catalog 分析

`tests/test_schema_sync.py` 仅比较表名与排序后的列名。它用产品自身的不完整解析器解析 `full_init.sql`，另检查 ORM `DateTime` 是否带时区；不会比较 nullable、默认值、check、唯一性语义、外键动作、索引、类型/长度、identity/sequence 或服务端更新行为。

PostgreSQL 全量测试使用 `Base.metadata.create_all()` 建表，不使用 `full_init.sql`；独立 SQL job 只证明 `full_init.sql` 能执行。实际漂移包括：

- 很多 ORM non-null 标注（`status`、`role`、若干时间戳）对应可空 DDL 列。
- 若干 ORM 值有 Python 端默认而 DDL 有服务端默认，或反之；原始 SQL 与 ORM insert 行为不同。
- `full_init.sql` 有 `idx_sys_diagram_user(user_id, deleted_at)` 和 `idx_article_site`，相应 ORM metadata 没有表示。
- SQL 的 `update_time` 只有创建默认值，没有数据库 trigger/on-update 表达式；ORM 更新与原始/运维更新语义不一致。
- 数据库没有 check 强制正数订单金额、合法用户 role/status、合法订单状态、`version >= 1`、sender-role 一致性或 paid 状态字段组合。
- `transaction_id` 不唯一，数据库无法阻止矛盾支付记录。
- SQLite 接受 NUL 文本，PostgreSQL 拒绝，所以只在 SQLite 走到的路径可保持绿色而生产报数据错误。

薄弱数据库完整性会放大回调、人工操作、迁移和未来代码错误。目前测试失败消息“full_init.sql 与 app/models.py 不一致”会让人误以为它能检测更多内容。

**修复建议：** 建立唯一权威迁移 metadata；反查完整迁移链创建出的架构，并把所有相关 catalog 属性与显式预期比较。应用测试应运行于迁移后的架构，而不只是 `create_all()`。有计划地增加 check/FK/index；应用前验证现有数据；支持原始 SQL 时使用明确服务端默认。SQLite 保留为快速逻辑后端，但 NUL、锁、约束、部分索引、时区和并发必须通过 PostgreSQL 测试。

---

### H-09 — 外部处理、文章来源、用户隐私与保留没有治理

**置信度：** 事实缺口已确认；法律严重性依司法管辖区而定

相关用户页面没有披露的数据流包括：

- Mermaid prompt 和支持问题发送给配置的 Agnes/OpenAI-compatible 端点。
- 启用语义模式时，用户问题可能同时发送给 embedding 和聊天/分类服务商。
- 启用预热时 FAQ 文本发送做 embedding。
- 第三方抓取页面的 DOM skeleton/文本发送给模型选择 selector。
- Draw.io 内容及浏览器/网络元数据通过 iframe/postMessage 与 `https://embed.diagrams.net` 交换。
- 私有图表、支持消息、订单、标识符和意图标签日志被保留，但无面向用户的保留/删除/导出政策。

内部阅读指南正确指出标签日志含原始问题，需要隐私/保留政策；产品 UI 却没有可访问的隐私告知或同意/目的边界。标签 logger 把原始问题写入普通日志。系统没有数据主体导出/账号删除、处理方清单/DPA 记录、区域路由选择、脱敏层或按功能关闭外部处理的能力。

文章摄取保存复制的标题/作者/日期/正文，却不保存来源快照哈希、抓取时间、最终/初始 URL、许可证/授权、robots 决策、审核人、批准状态、修订历史、下架状态或保留依据。管理员可以让未审核抓取文本立即影响 RAG。外部文本可以 prompt-inject selector 模型并改变提取结果；响应验证防代码执行，但不能防错误发布或版权/来源问题。

**修复建议：** 在启用相关功能前建立数据流清单及面向用户的隐私/处理方告知；最小化并脱敏 prompt；定义保留/删除/导出和日志政策；把训练/标签数据与运维日志分开；文章进入 RAG 前必须记录来源/许可证并人工批准；保留版本和下架历史；外部处理方按目的可配置。应进行针对司法管辖区的审查，不能仅凭代码宣称合规。

---

### H-10 — 文档描述的生产拓扑既未加固，也无法自证就绪

**置信度：** 配置审查已确认；未实际构建 Docker

- Compose 不强制 `ENV=production`；复制的示例默认 development。
- 应用有意只绑定宿主 loopback，但未提供 Nginx/LB 服务，运维必须手工构建真实入口链。
- Compose 强制 `TRUST_PROXY_HEADERS=true`，同时保留仅 loopback 的可信 CIDR。宿主代理通过 Docker bridge 进入时通常表现为 bridge/gateway 地址，导致转发头被忽略、HSTS 缺失、所有客户端都按代理 IP 限流。文档提到该问题，但启动无法验证。
- PostgreSQL 使用 `postgres` 超级用户，应用也使用同一账号。虽然未找到直接 SQL 注入，一旦凭据泄露或未来引入注入，集群权限过高。
- 镜像标签（`python:3.11-slim`、`postgres:16`）与 GitHub runner/action 主版本标签可变；无镜像 digest、SBOM、签名/来源验证、漏洞扫描或可复现构建记录。
- 生产镜像安装 pytest、Ruff、coverage、pgserver、SQLite 测试支持和文档工具；虽然 `.dockerignore` 正确排除了测试/文档源码，这仍增加体积与攻击面。这是已记录的明确债务，而非隐藏误包含。
- `chown -R app:app /srv/app` 让运行用户可写应用源码/资源；真正需要可写的只有存储与临时路径。
- 未提供只读根文件系统、drop Linux capability、资源/PID/内存/CPU 限制、`no-new-privileges`、秘密文件挂载规范、网络出口政策或备份进程。
- 镜像 health check 只打 liveness；Compose 无应用 readiness/health 依赖，也无发布/回滚策略。
- 数据库卷能持久化，但没有自动备份、恢复校验、PITR 或经过测试的灾难恢复制品。

**修复建议：** 分离开发与生产 Compose/部署清单；强制生产设置；应用 DB 角色最小权限，迁移角色另设；镜像固定 digest；使用哈希锁定的 runtime 依赖构建最小运行阶段；源码由 root 所有且只读；加入资源与安全上下文；在 staging 校验入口 peer/Host/TLS；自动加密备份并演练恢复。CI 增加真实容器构建、启动、冒烟与扫描门禁。

---

### H-11 — CI 和依赖控制把重要供应链/发布门禁留在 CI 之外

**置信度：** 工作流与依赖审查已确认

当前 CI 有价值，目标 SHA 的 6 个 job 均通过，但仍有以下缺口：

- `.github/workflows/README.md` 称前端 job 运行 `npm audit`；`.github/workflows/ci.yml` 实际只执行 `npm ci`、build、drift。本地审计通过，但未来含漏洞 lockfile 可以在没有该门禁的情况下合并。
- 普通 CI 不强制 coverage/branch coverage、前端 lint/type、浏览器 E2E/无障碍、容器构建/扫描、迁移链、秘密扫描、SBOM/许可证通知或性能上限。
- 普通 CI job 没有 `timeout-minutes`，挂死时可占用平台最大时长。
- `actions/checkout@v7`、`actions/setup-python@v7`、`actions/setup-node@v7`、`postgres:16`、`ubuntu-latest` 是可变引用，而非审查过的 digest/SHA。
- Python 顶层版本固定，但传递解析和 wheel/hash 未锁定。每次 CI 都升级 `pip`，`pip-audit` 自身也未固定；同一提交未来可解析出不同依赖。
- checkout 默认保留凭据，即使 job 会执行仓库控制的测试/安装 hook。工作流全局授予 `pull-requests:write` 以便失败评论；同仓库不可信分支代码得到比多数 job 所需更宽的 token。
- 失败评论发布最多 300 行测试输出，没有脱敏或 mention 政策。
- Agnes 工作流在满足工作流变更/提交标记条件的 push 中，可能把仓库秘密暴露给目标分支代码。用户曾授权先前探针，但 commit-message 标记不是持久人工批准、environment protection 或花费控制。
- 文档生成器以 Mistune `escape=False` 渲染 Markdown。当前仓库 Markdown 受信，CI 也不部署结果；但未来若公开预览/部署贡献者可控 Markdown，在没有净化或 CSP 约束时会允许原始 HTML/script。
- 无 Dependabot/Renovate 政策、制品 attestation，或把源码 SHA、锁文件、生成 JS、架构版本、镜像 digest 绑定的发布清单。

**已接受 advisory：** `ecdsa 0.19.2 / PYSEC-2026-1325` 是 `python-jose` 的传递依赖且没有修复版本。项目不直接使用 ECDSA，但永久忽略不是修复；应跟踪上游或迁移到不依赖该包的 JOSE 实现。

**修复建议：** 加入缺失门禁并设置明确超时；action 固定 commit、镜像固定 digest；runtime/dev 依赖使用 hash lock；设置 `persist-credentials: false`；仅在安全且不执行仓库代码的评论 job 授 PR 写权限；含真实秘密的运行使用 `workflow_dispatch`、环境审批、执行者 allowlist 和预算；若发布文档则转义/净化 HTML；生成 SBOM/来源证明；定期测试依赖升级。不得用 `npm audit fix --force` 或盲目 major upgrade 代替审查。

---

### H-12 — Draw.io 文档/账号切换可能丢稿；商店取消操作会输给延迟轮询

**置信度：** 已确认源码与可执行 Node 竞态探针  
**证据：** `AUDIT_SHOP_CANCEL_RACE_PROBE_01a08bf5.txt`

#### Draw.io

当用户选择另一云端文档、新建、导入文件、删除打开项、退出或更换账号时，`drawio-page.js` 会重置 iframe 与内存 XML。没有 dirty flag、before-unload 警告、切换确认、本地草稿或恢复缓冲。账号身份变化时还明确执行 `xml = BLANK`，因此普通导航/认证动作就可能丢失未保存编辑内容。

代码已有良好的 epoch/source/origin 检查，保存/下载前也会向外部编辑器请求新鲜且关联的 XML；这些应保留。应从编辑器事件跟踪脏状态，在每个破坏性切换前要求保存/导出确认；适用时提供加密且账号隔离的草稿恢复；针对退出、瞬时认证失败、账号切换、打开/新建/导入/删除、iframe 重载、离线保存和冲突恢复运行真实浏览器测试。

#### 商店延迟轮询竞态

“返回商品页”处理器会停止 interval 并显示首页，但不会递增 `buySeq` 或清除 `currentNo`。已经在途的 `poll()` 保留相同 stamp；返回后会调用 `render()` 并再次启动轮询。

探针结果：

```json
{"afterCancel":{"landing":true,"pending":false,"timer":false},
 "afterLatePoll":{"landing":false,"pending":true,"timer":true}}
```

离开/取消视图必须作为状态迁移处理：使 sequence 失效、清除当前订单并 abort 在途 fetch。测试延迟成功、延迟错误、账号变化、历史选择和快速重新购买。支持前端已经正确采用 AbortController + epoch，可作为本地范例。

---

## 5. 中严重性问题

### M-01 — 图表 API 的输入、ETag 和查询仍存在正确性与 PostgreSQL 故障路径

**置信度：** 混合；超大 ETag 已复现，NUL 端点为高置信度源码/数据库分析

- `DiagramIn` 限制名称/内容长度，但不拒绝 `\x00`。PostgreSQL 拒绝文本 NUL，SQLite 接受。JSON 中名称/内容含 `\u0000` 时，预计会在 PostgreSQL 触发数据错误/500；本轮未再次通过连接 PostgreSQL 的真实 HTTP 端点复现该特定路由。
- `_parse_if_match()` 对无界数字字符串直接调用 `int()`。Python 3.11 的整数文本安全上限使足够长且语法合法的 `If-Match` 触发未处理 500；已复现。
- 解析器在 `.strip('"')` 后接受非规范重复引号；未实现完整实体标签/列表语法；错误中还回显攻击者提供的原始 header。使用简单版本 token 没问题，但必须有界且确定地拒绝 malformed 输入。
- 仅 PUT 和永久 purge 要求版本前置条件。软删除/恢复可能与编辑动作竞态，调用方无需证明操作的是哪个版本。
- `list_diagrams()` 查询完整 `SysDiagram` ORM 行，虽然 `DiagramSummary` 不返回 `content`。列出最多 50 个活动/200 个总图表时，可能仅为元数据就从 PostgreSQL 向应用传约 20 MB。
- 服务端接受任何非空字符串作为 `content`；XML/root/DOCTYPE 检查仅在浏览器，可被 API 客户端绕过，最终持久化不可用文档。
- XML 语义验证有意很浅；任意 Draw.io XML 可能携带敏感或意外结构，之后还会发送给外部编辑器。

**修复建议：** 集中实现数据库安全文本验证；严格且有界地解析已文档化版本 token；对重要破坏性竞态增加前置条件；列表只投影摘要列；使用安全解析器在服务端验证 XML 及声明的 Draw.io 子集/大小；全部在 PostgreSQL 测试。不得启用外部实体或 XML 网络解析。

---

### M-02 — Cookie 认证的变更操作主要依靠 SameSite，而不是显式请求意图

**置信度：** 已确认的设计风险；可利用性取决于浏览器和域名拓扑

登录 Cookie 为 HttpOnly、明确 `SameSite=Lax`、生产环境 Secure、host-only，都是良好默认值。但 Cookie 认证的状态变更没有 CSRF token，也不校验 Origin/Referer/Sec-Fetch-Site。普通跨站 POST 通常**不会**携带显式 Lax Cookie，因此把所有变更描述为可被简单跨站利用是不准确的。

剩余暴露：

- 登录本身不需要已有 Cookie。跨站表单可提交攻击者凭据，让受害者浏览器进入攻击者账号（login CSRF），之后受害者创建的数据可能被攻击者看见。
- 同站点兄弟子域虽跨 origin，但 Cookie 视角仍 same-site。文档中的 `tools.codemax.top` / `shop.codemax.top` 拓扑，使兄弟域被攻陷或托管不可信内容的影响更大。
- `/shop/orders` 接受空 body POST，没有 JSON/custom-header 带来的预检信号。其他 Cookie 变更包括图表、密码、下载签发、消息、退出和同意提交。
- consent HMAC 能绑定字段，但没有过期/nonce，在认证浏览器上下文中可重放。

**修复建议：** 区分 Bearer 与 Cookie 认证；Cookie 变更要求 Origin/Fetch-Metadata 政策和 CSRF token；防 login CSRF；维护狭窄可信 origin 列表；服务商回调与服务器间 OAuth token exchange 应明确豁免。用真实浏览器测试跨站及同站兄弟请求。不能靠开启宽泛 CORS 修复 CSRF。

---

### M-03 — 认证已有多项正确控制，但缺少生产级账号与会话治理

**置信度：** 已确认

优点：bcrypt 工作移出事件循环；注册/改密 schema 强制 bcrypt 72 字节上限；拒绝 NUL 密码；登录错误统一且使用 dummy verification；改密增加持久 credential revision；每次请求从数据库读取 role/status；Bearer 明确优先于 Cookie。

缺口：

- 登录使用 `OAuth2PasswordRequestForm` 而不是 username schema。含 NUL 的用户名到达 asyncpg，并在真实探针中造成 PostgreSQL 500。
- 最短密码仅 6 字符，无泄露密码检查、MFA、恢复、已验证联系方式、锁定/风险告警或管理员 step-up。管理员还能确认付款、摄取内容，风险尤高。
- 退出仅删除浏览器 Cookie；Bearer 在过期或 credential-version 变化前仍有效。无会话清单、单设备撤销、key ID、轮换重叠或改密/禁用之外的紧急撤销。
- JWT 缺 issuer/audience/token ID，所有 HMAC 用途共享 `SECRET_KEY`。
- Unicode `\w` 用户名允许视觉混淆脚本。这不会直接授予角色——授权从不依赖名称——但会造成支持/运维界面冒充风险。
- 无界免费注册且无账号验证，会扩大按账号配额及商户/LLM 暴露（另见 H-04）。
- 认证/请求体大小主要靠代理宽泛的 20 MB 上限，而非按路由限制。

**修复建议：** 登录复用规范化标识符验证；增强密码/MFA/管理员政策；增加 session/grant ID 与撤销；分离并轮换不同用途密钥；建立账号生命周期、恢复、验证及滥用控制；定义 Unicode 身份/显示政策。保留统一登录错误与数据库角色检查。

---

### M-04 — Host 派生链接与薄弱 `SITE_BASE_URL` 校验会生成攻击者控制或畸形 URL/XML

**置信度：** 运行时/源码探针确认

- 应用没有 trusted-host allowlist。若边缘代理没有正确拒绝，任意 `Host` 都会被接受。
- `public_base_url()` 使用请求 Host 或可信 `X-Forwarded-Host` 构造 mock checkout 和本地签名下载 URL。探针在相应信任条件下生成了 `http://evil.example:444` 和 `https://evil.example:444`。
- 未验证 forwarded-host 语法/allowlist。辅助函数只在直接 peer 可信时接收转发头，这是正确的；缺失的是目标 Host 校验，不是从右向左 XFF 算法。
- 生产环境只要字符串以 `https://` 开头，`SITE_BASE_URL` 仍可包含 userinfo、path、query、fragment、畸形 Host/port、XML 元字符或控制数据。
- canonical HTML 经 Jinja 转义，所以这**不是** canonical attribute XSS。
- Sitemap `<loc>` 通过手工拼接且未 XML 转义；robots 内容也手工插值。错误配置可生成畸形/注入的 sitemap/robots。
- 公共页面全局 `Cache-Control: no-store`，削弱 SEO/CDN 效率；支持中心出现在公共导航，却无 canonical 且不在 `PAGES`/sitemap。是否索引应明确决定。

**修复建议：** 代理和 ASGI 两层都执行 allowed host；生产绝对链接只来自一个经过验证的 canonical origin，不来自请求 Host；严格解析 HTTPS origin，不允许 credentials/query/fragment，path 只能是允许前缀；对 sitemap XML 转义；测试 IDNA、IPv6、端口和转发链。为每个公共/私有页面明确索引、canonical 与缓存政策。

---

### M-05 — LLM/RAG 的资源、相关性及数据库行为不能安全扩展

**置信度：** 已确认

- `LLMClient` 每次聊天/embedding 都新建 `httpx.AsyncClient`，失去连接池、集中关闭和指标。
- `client.post()` 在 100,000 字符内容检查前完整缓冲并解压响应。embedding 数组在 JSON 解析前没有响应字节、向量数量/维度或总内存预算。
- 聊天请求不设置输出 token 上限。没有全局并发、花费/token 预算、重试/退避、熔断或服务商额度协调；公开成本边界只有进程内 IP 限流。
- 统一 60 秒 timeout 未区分 connect/write/read/pool，也不构成端到端总预算。
- `/support/ask` 一次请求可依次执行 embedding、分类、文章检索和答案生成，没有共享 deadline。
- 每个专业问题都会按 ID 读取**所有文章全文**，并对 `(id,title,content)` 计算哈希。缓存只避免重复分词/索引构建，不避免 O(语料总字节) 的数据库传输/哈希。源码文档承认此点，却没有规模阈值。
- 请求的 SQLAlchemy session 在等待外部 LLM I/O 时可能一直占用连接/事务；支付和密码路径也存在类似连接占用。
- 检索接受任何正 fused lexical score，无校准相关性下限；仅共享一个弱 token 的无关来源也可能进入 prompt。
- 缓存是进程内的。它每次重新读整表的全文指纹确实可跨进程发现更新；不能只在摄取后清一个进程缓存就声称已支持多实例。
- Mermaid 最多接受 100,000 个生成字符，服务端只验证识别前缀；过度复杂图仍可消耗浏览器 CPU/内存。

**修复建议：** 复用受管理客户端；以流式或其他方式限制编码/解码响应字节、token、维度；使用共享并发/花费预算与熔断；外部等待前释放 DB session；引入持久 corpus revision 和增量索引/搜索服务；校准相关性并允许返回“无证据”；限制图复杂度；增加服务商混沌/负载测试。队列增长前以稳定重试语义返回 429/503。

---

### M-06 — API/服务商/客户端协议错误经常退化为误导前端故障或 500

**置信度：** 源码及既有 Node/运行时探针确认

- 多个前端路径无条件调用 `response.json()`，成功和失败均如此。代理若返回 `200 text/html`、畸形 JSON 或空 body，会抛解析异常而不是有界协议错误。Draw.io、Mermaid、ER、mock-pay、商店下载/历史及认证各自有不一致实现。
- 商店轮询忽略非 OK 状态而不显示有效状态；其他处理器直接展示 `Error` 字符串，如 `网络错误：TypeError...`。
- 很多 API 返回 ad-hoc 字典，没有 response model。OpenAPI 无法断言关键不变量，内部/服务商畸形值会传播到更深处才失败。
- 错误结构混杂 FastAPI `detail` 字符串/列表、嵌套 OAuth 错误、微信 `{code,message}` 和任意前端消息。UI 通过状态码与文案而非稳定机器码判断。
- `native_prepay()` 把服务商错误文本前 200 字符放进客户端可见异常，而 LLM 错误会脱敏；服务商诊断可能泄露不必要内部信息且关联不一致。
- NUL 登录、超大数字 ETag 等数据库/输入错误成为 500，而非有界 4xx。
- 路由级 Content-Type 与 body-byte 检查稀少；Pydantic 字符限制发生在请求完整缓冲/JSON 解码之后。

**修复建议：** 定义响应/错误 schema 与稳定错误码；集中实现安全前端响应解析；验证 `Content-Type` 和必填字段；在昂贵解码前限制字节；服务商详情只写入内部关联日志；测试 2xx HTML、空/畸形 JSON、无效 UTF-8、截断流、超时与代理错误页。微信要求的回调响应 envelope 可作为明确例外保留。

---

### M-07 — 静态交付与浏览器 bundle 产生不必要的带宽和缓存成本

**置信度：** 资产、导入与响应头分析确认

- 生成 JS 为 3.395 MB；典型 Mermaid 类图闭包约 737 KB 未压缩；Vite 生成 662 KB 基础 chunk 并发出大 chunk 警告。
- 无 GZip/Brotli 中间件，也没有仓库内代理压缩配置；除非外部运维另加，浏览器传输不压缩。
- 静态文件从 Starlette 获得 ETag/Last-Modified，却无明确缓存政策。`mermaid-page.js` 等固定入口名会原地变内容，盲目加 `immutable` 会产生陈旧代码。
- 所有非静态响应，包括公共 SSR 页面，都得到 `Cache-Control: no-store`，阻止有价值的浏览器/CDN 缓存。
- 98 个生成 chunk 和 1.8 MB 阅读笔记 JSON 显著增加仓库/构建审查成本；这不等于运行时内存问题，但提高制品与供应链审查负担。
- 生产排障无 source map；若增加，应私有/认证发布，因为会暴露源码。
- 已提交支付二维码指向固定 URL 与业务身份；替换时没有明确缓存失效机制。

**修复建议：** 边缘压缩；分析/拆分或懒加载实际需要的 Mermaid renderer；通过 manifest 使用 content-hash 入口 URL；只有哈希资产使用 `immutable`；可变入口和二维码使用 `no-cache`/短缓存；认证无关 HTML 允许有界公共缓存。增加 bundle 预算和真实网络传输测试。不得手改生成 chunk。

---

### M-08 — 健康检查、指标、审计和恢复证据不足以支撑所宣称的监控能力

**置信度：** 已确认

- `/healthz` 有意不访问数据库，这是正确的 liveness 设计，不应改变。
- `/readyz` 只检查 `SELECT 1`，不检查架构版本、当前存储/商品、支付回调就绪、代理假设、迁移状态或关键 worker 容量。
- 没有 Prometheus/OpenTelemetry 指标、trace、dashboard、告警资源、队列/连接池饱和指标、支付对账告警或合成 checkout/download 探针。
- `docs/DEPLOY.md` 罗列建议阈值，但没有代码/配置收集所需 5xx、升级、429、支付或发货序列；`ROADMAP.md` 却把“监控告警配置”勾为完成。
- 日志缺 build SHA、架构版本、认证操作者/用户 ID（安全时）、规范化路由模板、字节数、上游类别和持久事件 ID，也没有高基数/敏感字段政策。
- 未执行或自动化备份制品/恢复演练。volume 是持久化，不是备份。
- 部署/关闭前没有优雅 readiness drain 文档，也不对在途服务商调用进行对账。

**修复建议：** 分离 liveness、核心 readiness、feature readiness；记录延迟/错误/队列/连接池/服务商/支付/发货指标；提供真实告警规则与 runbook；附加安全关联/事件 ID；自动备份和恢复测试；暴露 build/schema metadata；测试优雅 drain 与未知服务商结果。不能因可选服务商或数据库短暂不可用而让 liveness 失败。

---

### M-09 — 无障碍及真实浏览器行为没有发布级测试

**置信度：** 标记与测试缺口已确认；视觉严重性仍需浏览器审计

- 登录 modal 缺 `role=dialog`、`aria-modal`、labelled-by/described-by、focus trap、Escape、背景 inert、焦点恢复；打开后会聚焦用户名，这是良好起点。
- 多个动态错误/状态区域没有 `role=alert`/`aria-live`；商店与 Draw.io 变化可能不会被辅助技术播报。
- ER SVG 和 Mermaid 预览缺有意义的可访问名称、文本/表格替代和键盘可访问图语义。
- 支付/状态 emoji 与颜色并非处处有明确实时文本语义支撑。
- 无 skip link 和当前页面导航状态。
- 没有 axe/Lighthouse/Playwright、键盘脚本、reduced-motion、缩放/重排、色彩对比或屏幕阅读器验收。
- Node VM 测试能测生命周期逻辑，但不是浏览器布局、导航、Cookie、焦点、CSP、iframe、下载或无障碍环境。

**修复建议：** 建立面向 WCAG 的验收标准；实现 dialog/focus/live-region 语义和图表文本替代；再以 Chromium 及至少一个独立引擎执行自动 axe 与人工键盘、屏幕阅读器、200% 缩放、移动端测试。继续用纯文本节点渲染不可信消息。

---

### M-10 — 文档结构门禁全部通过，但若干当前事实和产品声明已过期

**置信度：** 交叉核对确认

结构质量很高：指纹、owner README、链接、标题、范围、源码页面及连续阅读区块均通过。契约本身明确“不证明语义”。当前漂移恰好证明该限制：

| 位置/声明 | 目标实际状态/修正 |
|---|---|
| `README.md`：136 个已注释文件 | 实际 140 |
| `docs/CODE_READING_GUIDE.md`：136 文件、1,446 区块、21,768 行、240 source item、56 manual | 140 文件、1,489 区块、22,310 行、244 source item、60 manual；guided 仍为 80 |
| `docs/README.md`、`docs/DOCUMENTATION_POLICY.md`、TD-234 | 仍写旧 136/1,446 基线；应使用当前生成数据或避免手工维护总数 |
| `docs/CODE_READING_GUIDE.md`：33 document / 240 source pages | 当前完整渲染为 41 / 244 |
| `README.md`：测试“36 文件 / 565 用例” | 51 个 `test_*.py` 模块 / 838 节点 |
| `README.md`：176 个 TD，截至 TD-228 | 实际 202 个唯一 TD ID，最高 TD-237 |
| `.github/workflows/README.md`：前端运行 npm audit | CI 前端 job 没有运行 |
| CI 注释：full init 两次证明幂等 | 只证明可重复**删除/重建**，不证明保留数据 |
| CI 依赖注释：“624 passed” | 活跃工作流中嵌入的历史快照，不是目标当前 838 节点基线 |
| TD-163：五个模板依赖 inline script 和 script unsafe-inline | 当前 `script-src` 仅 self，业务脚本外置；仍允许 inline **style** |
| TD-90/91 与 roadmap：结构化日志/监控告警已解决 | 普通 formatter 丢弃结构化 extra；指标/告警仅是建议 |
| `ROADMAP.md`/TD-158：一次下载/CAS winner | 当前政策有意允许已付款用户重新签发；`mark_downloaded()` 返回值不再表示唯一 winner |
| `ROADMAP.md`：跨域 SSO 已集成/测试 | 测试只在一个应用/ASGI 进程中验证授权服务器及合成回调 URL；仓库没有独立部署的客户端应用/浏览器域 |
| 根产品简介：支付闭环/云存储 | 未验证真实商户、无对账/退款，只有本地存储；深层文档反而更诚实 |
| `docs/DEPLOY.md`：生产自检列出全部问题 | 缺当前支付凭据/证书、商品、存储、架构、Host 就绪 |
| `docs/DEPLOY.md`：爬虫 robots/节流已解决 | 只完成初始 hop；重定向目标政策/记账仍不完整 |

带日期的历史报告（`CONSOLIDATED_ERROR_SUMMARY.md`、`REVIEW_CROSSCHECK.md`、验收/stage 记录）往往标注 SHA/日期，不应被改写成当前状态。正确做法是把不可变历史证据与小型、自动生成的当前状态页分开，而不是抹掉历史。

**修复建议：** 易变数字由单一数据源生成；只对关键活跃声明添加语义断言；醒目标记历史快照；按部署模式更新功能矩阵；路由/配置/状态语义变更必须同步审阅文档。指纹通过绝不能表述为“文案仍正确”。

---

### M-11 — 缺项目许可证、第三方通知、商业条款和可接受使用政策

**置信度：** 缺失事实已确认；法律结论须由专业人员判断

- 没有项目 `LICENSE` 授予接收者使用、修改或再分发权限。公开可见不等于开源许可。
- 没有 SBOM、第三方许可证清单、署名/notice bundle，或针对 Python/容器及 153 条 npm 依赖/打包代码的 source-offer 流程。
- 本次审计没有认定具体许可证违规；仅出现依赖不等于发生某种分发。确认的是治理与证据缺口。
- 商店没有销售条款、交付定义/版本、退款/取消政策、隐私告知、发票/税务、争议渠道、服务承诺或司法管辖/联系主体。
- 产品文案提供毕业设计系统/定制开发和论文指导，却无学术诚信/可接受使用政策来界定辅导/模板与代写提交、院校规则、原创性和禁止用途。
- 人工模式使用公开提交的个人收款二维码；其商业用途、账号、消费者、税务和平台规则影响在内部有承认，却未成为发布门禁。

**修复建议：** 只有获得所有者授权后才选择并添加项目许可证；生成/审查 SBOM 与 notices；盘点已分发镜像/资产义务；发布隐私、条款、退款、交付和可接受使用政策；收款前取得司法管辖区及平台专门意见。不能因为添加模板文案就宣称“合规”。

---

### M-12 — 可维护性/工具缺口让接口与状态缺陷绕过强测试

**置信度：** 已确认

- Ruff 是唯一分析器。没有静态类型检查器发现 `decrypt_resource() -> dict` 实际接受 JSON 标量，或 `Storage` protocol 用户调用 LocalStorage 专属方法等事实。
- Router 混合 HTTP 解析、授权、ORM 事务、服务商调用、二维码生成、状态投影和日志。支付变更因此跨可变 settings 与多个模块，却没有统一事务领域模型。
- 很多公共函数返回未类型化/ad-hoc 字典。前端为全局 vanilla JS，无 TypeScript 或 lint 配置。
- 注释/docstring 异常冗长，经常包含过期 benchmark、旧 bug 叙事、行数或被当前代码推翻的断言（如“服务商前已提交”）。有价值原理与易变状态混在一起，增加审查成本。
- 测试虽有大量有价值可执行行为，也包含许多源码字符串、注释和实现形状断言；语义遗漏时它们可能保持绿色，安全重构时又可能代价高。
- 使用私有框架/内部符号（`original_router`、FAQ `_Index`、politeness `_get_semaphore`、CPU globals），增加升级耦合。
- 没有正式架构边界/层级测试防止事务中进行服务商 I/O，或财务提交后才写日志。

**修复建议：** 增量引入严格 pyright/mypy、JS lint/type、类型化响应/服务商模型、支付/存储 service boundary 及依赖规则。把有日期实验移入决策记录，代码注释只保留局部且当前事实；优先行为/契约测试而非文案/字符串测试。在用本报告严重测试锁定现有语义前，不做大规模重写。

---

### M-13 — 人工支持具备私有性和幂等性，但缺少运维生命周期与总量控制

**置信度：** 已确认

正面项：存在认证后的所有权/管理员检查；客户历史相互隔离；不可信正文通过 `textContent` 渲染；UUID nonce 加唯一约束使重试幂等；有 cursor pagination。

缺口：

- 无未读/已读、分派、优先级、SLA、关闭/重开、审核/封禁、附件/证据、通知、搜索、导出、删除/保留或操作者审计。
- “等待管理员”仅由最后发送者角色推导，不是持久工单状态。
- 每 4 秒轮询，让每个打开客户端无限期产生一个 DB 查询；长时间无活动不退避，也无全局连接/负载预算。
- 消息按请求和 IP/window 限制，却不按账号每日/终身或系统存储限制；免费创建账号可放大容量。
- 管理员可以主动向任何已有用户 ID 发起对话；这可能是预期功能，但没有操作者原因/审计。
- 支持数据与支付争议没有关联 case/event 模型。

**修复建议：** 定义最小工单生命周期与保留政策；增加总量配额/滥用响应、未读/分派/关闭和持久操作者审计；只在确有需要时采用可扩展轮询退避或 push。财务异常可关联工单，但不要把敏感服务商载荷复制到聊天中。

---

## 6. 低严重性优化与质量机会

| ID | 机会 | 证据/安全方向 |
|---|---|---|
| L-01 | 查询与索引剖析 | 订单历史需要复合 `(user_id,id)` 索引；OAuth 清理需要支持 `used/expires_at` 的索引；RAG 需要 revision/search 设计。添加索引前先用 PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 验证。 |
| L-02 | 缓存重复二维码生成 | `_payload()` 在每次 3 秒轮询/历史响应中为相同内容重新生成 SVG。可按不可变 `code_url`/订单缓存，或提供稳定二维码端点/对象，同时禁止任意二维码生成。 |
| L-03 | 复用解析后的密码学/服务商客户端 | 商户私钥和回调公钥被反复解析；应按 serial/密钥轮换缓存已验证不可变对象，并复用带有界连接池的 HTTP 客户端。 |
| L-04 | 明确调优数据库连接池 | `create_async_engine()` 使用库默认值。依据部署容量配置 pool size/overflow/timeout/recycle/pre-ping 并暴露饱和指标；不得脱离数据库容量盲目增大。 |
| L-05 | 公共页面本地化与错误 UX | 中英文文案硬编码分散于后端/前端。若计划多语言，应分离稳定错误码与翻译文本，不能解析文案决定逻辑。 |
| L-06 | 源码/发布元数据 | 应用版本仍是 `0.1.0`，响应/日志缺 commit/build/schema version。应由构建生成不可变 metadata，而非手工编辑。 |
| L-07 | 动态浏览器死边界 | Chromium 渲染当前正确地失败关闭，且未安装 Playwright。应继续禁用或移到出口隔离的 renderer；不能只安装依赖就开启。 |
| L-08 | 文档站原始 HTML | 若未来公开生成文档站或从不可信贡献构建，应让 Mistune 转义/净化并设置严格 CSP，同时保留代码/渲染测试。 |
| L-09 | 响应头政策细化 | 启用 `Cross-Origin-Opener-Policy`、`Cross-Origin-Resource-Policy`、CSP reporting 前应针对 Draw.io iframe/下载验证；一刀切响应头会破坏预期功能。 |
| L-10 | 状态命名 | 将 `downloaded` 拆分/改名为 `link_issued` 与 delivery event，使运维/用户语义符合可重复签发权益；必须迁移数据，不能静默重定义持久字符串。 |

---

## 7. 已确认且不得在修复中回退的控制

本审计刻意记录优点，防止后续修复破坏现有正确防线。

### 7.1 认证与授权

- ORM 查询参数化，未发现已确认 SQL 注入。
- 密码使用 bcrypt；注册、改密、验证路径明确处理 72 字节上限。
- 用户不存在与密码错误使用统一响应；dummy verification 减少常规时序枚举。
- 昂贵 bcrypt 在线程池运行，不阻塞事件循环。
- 浏览器认证 Cookie 为 HttpOnly、host-only、Path `/`、明确 SameSite Lax，准确生产模式下 Secure。
- 改密增加持久 `credential_version`；即便同秒变更或时钟回退，旧令牌也会被拒绝。
- 每次认证请求都从数据库取得当前用户状态/角色，不信任 JWT 中的角色。
- 图表、订单、支持所有权检查在适当位置统一返回 not-found；未发现已确认 IDOR。

### 7.2 OAuth

- 重定向 URI 与活动客户端精确匹配；拒绝 fragment、userinfo 和畸形端口。
- GET 授权只展示同意，POST 批准才签发 code。
- 授权码高熵，绑定 client/redirect/user/revision，有过期且原子一次性消费。
- access token 不放入重定向 URL，`state` 原样回传。
- 客户端秘密在数据库中为 bcrypt 哈希；但演示秘密明文公开这一问题另算。

### 7.3 支付与存储

- 回调签名覆盖原始字节及 timestamp/nonce；RSA 前检查新鲜度；AES-GCM 认证加密资源数据。
- 回调金额与持久订单金额比较，不能误报该检查不存在。
- 核心状态转换使用数据库 CAS，重复成功事件幂等。
- 订单状态/下载强制所有权；预期架构中每用户一条 pending 由数据库保证。
- 下载 key 与过期时间由 HMAC 绑定，并用常数时间比较。
- 本地路径 traversal 和解析后逃逸存储根目录的 symlink 会被拒绝。
- `FileResponse` 流式传文件，不会把整个对象载入内存。
- 已付款/已签发用户可有意重新签发短期链接；这是当前权益政策，不是残留的一次性竞态。

### 7.4 浏览器与内容

- Jinja 自动转义动态 canonical、metadata 和模板 HTML 值。
- D3 标签使用 `.text`，支持消息使用 `textContent`，Mermaid 使用 `securityLevel: "strict"`。
- 业务 JavaScript 自托管，`script-src` 仅 self；运行时不依赖 npm CDN。
- Draw.io postMessage 同时校验精确 origin 与当前 iframe source，export 请求有关联 ID/epoch。
- 浏览器 XML import 在使用前拒绝常见 DOCTYPE、畸形和非 Draw.io root。
- 正常受处理路径中的静态/API 响应具有 CSP、nosniff、frame、referrer 和 permissions 头。

### 7.5 爬虫与限制

- 未复现“普通 DNS rebinding/重定向私网”说法：生产 transport 会重新解析、验证并固定首个批准 IP，且每次请求保留原 Host/SNI。
- 每个 DNS 答案必须是公网；拒绝 localhost、私网、link-local、metadata、multicast/reserved 地址及 URL credentials。
- 重定向数与解码响应大小有界；超大流会提前关闭。
- 初始 origin 的 robots 401/403 拒绝、404/410 允许、5xx/网络失败保守失败。
- 公共 schema 有合理字符/项目上限、每用户图表配额及正数配置边界。

### 7.6 工程与文档

- 目标 SHA 在两个数据库后端均通过全量测试。
- Vite 输出与已提交生成制品完全一致。
- 审计日期的 npm audit 干净；pip audit 明确暴露一个未解决例外，而不是静默跳过所有失败。
- 文档源码归属、哈希、范围、本地链接、anchor 和生成均确定且受测试。
- 生产 API docs 被禁用；开发 docs 有独立 CSP 例外。
- Docker 使用非 root 运行应用；按当前配置，构建上下文/镜像内容排除 `.env`、`.git`、storage、tests 及 docs 源码。

---

## 8. 误报、夸大表述与已接受债务

下列事项已经调查，不应在后续报告中重新作为未经支持的问题：

1. **爬虫 SSRF：** 不得声称“只先验证然后 HTTP 自由重解析”“丢失 TLS SNI”“跟随私网重定向”或“重试全部地址”。自定义 transport 实质固定首个批准地址并保留原 Host/SNI。真实剩余问题是重定向 robots/记账和资源政策（H-06）。
2. **响应大小：** 普通爬虫/robots 解码 body 有上限。剩余解压风险是瞬时压缩输入/CPU 比例，不是“无限下载解码字节”。
3. **XFF：** 限流器不会盲取第一个值；它从右向左越过配置的可信代理。畸形链回退到 socket peer。Host allowlist 与部署 CIDR 仍是问题。
4. **CSRF：** 明确 SameSite Lax 会阻止普通跨站 POST 附带 Cookie。应报告 login CSRF、同站兄弟域及纵深防御，而不是“所有操作都能轻易 CSRF”。
5. **bcrypt 截断：** 注册与改密强制 72 UTF-8 字节，验证拒绝更长输入。不能声称用户能注册两个仅 72 字节之后不同的密码。
6. **注册会话：** API 有意返回用户数据但不建立 session；浏览器随后登录。这本身不是缺陷。
7. **支付金额：** 回调将解密后的 total 与持久金额比较。缺陷是更广的身份/币种/事件/对账及可变预支付配置。
8. **微信公钥：** 受支持的裸支付公钥不天然无效；问题是密钥/serial 轮换与绑定，而不是“必须证书”。
9. **下载重签发：** 已付款所有者重复签发是有意的恢复行为。未经产品决策，不得恢复过时的“一生只有一个并发赢家”政策。
10. **Canonical XSS：** Jinja 会转义 canonical `href`；原始插值问题在 sitemap XML/URL 有效性，不是已确认 HTML attribute XSS。
11. **Mermaid/支持 XSS：** Mermaid strict、Jinja 转义和文本节点渲染实质降低直接存储/反射 XSS。仍应继续测试，但未发现已确认应用 XSS。
12. **SQL/命令/路径注入：** 未发现已确认的路由级 SQL 注入、Shell 命令注入、模板注入、pickle 反序列化或本地下载 traversal。
13. **原始 ANSI request ID：** 直接 ASGI 接受，但真实 Uvicorn 拒绝原始 ANSI header。超长反射和路径解码 CR 拆日志已独立确认。
14. **Robots 授权：** robots 规则是抓取政策指引，绝不是访问控制边界。
15. **动态抓取：** 安装 Playwright 不会自动启用；当前代码有意失败关闭。
16. **Npm/Python 许可证：** 缺 notices/SBOM 是治理风险，不证明某依赖许可证已经被违反。
17. **Immutable 缓存：** 固定资产名是可变的。URL content-hash/version 化前不能加一年 immutable 缓存。
18. **健康检查：** liveness 不检查 DB 是正确做法；应增强 readiness/feature readiness。

### 8.1 仓库中明确可见的已知债务

- 当前设计为单应用进程/副本；内存限流、缓存和 politeness 不能支持多实例。
- 仅实现本地存储；OSS/COS 明确尚未实现。
- mock 支付仅用于演示；真正启用生产模式时启动会拒绝 mock。
- 人工支付本来就依赖人工确认且无服务商回调；C-02 说明为何仍需财务控制。
- 语义 embedding 默认关闭；Agnes embedding 尚未验证。不得猜模型 ID 或降低阈值来伪称成功。
- Chromium 动态抓取在网络隔离完成前保持禁用。
- `ecdsa` advisory 因无上游修复版本而显式忽略。
- 生产镜像携带测试/lint/doc 依赖是明确决策。
- CSP 仍允许 inline **style**；业务 inline script 已不再需要 unsafe-inline。
- 个人收款二维码被有意提交用于公开显示；运维/法律接受仍需发布决策。
- 带日期的审计、验收、manager 文件是历史证据，不应把每个旧数字改写成当前事实。

---

## 9. 按优先级划分的修复计划

### 第 0 阶段——实施开发前先停止不安全暴露

1. 保持真实微信/人工支付关闭，不得宣传发货已达到生产标准。
2. 从每个已部署数据库删除/禁用已知种子管理员及 OAuth 凭据；轮换 `SECRET_KEY` 与服务商/客户端凭据。
3. 强制并验证 `ENV=production`；拒绝未知配置；对 Host/代理拓扑实施 allowlist。
4. 在所有入口/应用日志中脱敏签名查询能力，并限制请求 ID。
5. 共享限流、存储、就绪完成前保持单 worker/副本及仅本地入口。
6. 修改架构前备份每个现有数据库；绝不能对保留数据运行 `full_init.sql`。

### 第 1 阶段——建立持久架构与资金基础

1. 引入迁移账本、校验和与锁，安全迁移现有安装。
2. 新增支付尝试、事件、对账、退款、争议及不可变 offer/权益表。
3. 配置最小权限的应用/迁移数据库角色，强制数据库不变量。
4. 启动/就绪检查应断言架构及所选商业/存储资源有效。
5. 实现服务商查单、关单和对账后，才重新启用支付。

### 第 2 阶段——封闭安全与资源边界

1. 增加分层边缘/共享限流、body、队列、服务商花费和总存储控制。
2. 增加 Cookie Origin/CSRF/login-CSRF；OAuth 增加 scope/audience、PKCE 和同意新鲜度。
3. 修正爬虫重定向 robots/节流记账及总期限；隔离解析器/selector。
4. 引入受管理外部客户端、响应字节/token 限制、熔断器和 DB session 边界。
5. 配置结构化/脱敏日志、持久审计事件、指标、告警及备份恢复。

### 第 3 阶段——纠正产品与数据行为

1. 替换 SQL 解析器，或严格限定支持范围并显式报告未支持结构。
2. 为 Draw.io 增加 dirty/recovery、严格服务端 XML/文本验证及摘要投影。
3. 修复商店延迟响应取消竞态，集中实现健壮前端响应解析。
4. 重构 RAG corpus revision/search/relevance 及文章审批/来源。
5. 只按真实运维需要增加支持工单生命周期。

### 第 4 阶段——可复现性、浏览器质量、文档与治理

1. 对依赖做哈希锁定；固定 action/镜像；构建、扫描、attest 容器；生成 SBOM/notices。
2. 增加类型检查、JS lint/type、浏览器 E2E、无障碍、代理、迁移及混沌测试。
3. 对资产 content-hash、压缩与正确缓存，并设置 bundle 预算。
4. 经审查后发布隐私、外部处理方、条款/退款/交付及可接受使用政策。
5. 自动生成当前指标/能力状态，修正活跃文档，但不重写带日期的历史记录。

### 9.1 可直接交给下一位实现助手的任务表

| 优先级 | 根任务 | 主要文件/新领域 | 完成定义 |
|---:|---|---|---|
| 0 | 消除已知凭据/默认开发暴露 | `full_init.sql`、provision 命令、Compose/env/startup、已部署数据迁移 | 新生产 DB 无仓库公开 login/client；已有种子禁用；生产拒绝弱/默认状态 |
| 0 | 重构完成前关闭真实商业功能 | config/startup/routes/UI | release feature flag/readiness 未真时，买家无法创建/确认真实资金订单 |
| 1 | 迁移框架 | Alembic/同类、架构账本、所有现有 SQL | 升级快照保留数据；加锁仅执行一次；校验 checksum/version；记录回滚/恢复 |
| 1 | 支付账本、Saga 与对账 | 新 models/migrations/services/workers/admin UI | 每种服务商结果可恢复；回调事件不可变/幂等；unknown/refund/dispute 有处理；混沌测试通过 |
| 1 | 不可变 offer/权益 | product/object/version/digest/terms 表 | 已有购买始终取得承诺制品，不受当前 env 影响；签发/退款可审计 |
| 1 | OAuth 最小权限 | security/deps/oauth/client registry | client/audience/scope 强制；不隐式继承 admin；PKCE 和 consent nonce/expiry 测试通过 |
| 2 | 共享且有界的准入 | edge + limiter + provider budget + CPU worker | 每请求/键工作量常数或有界；多 worker 测试强制全局限制；有队列/连接池指标 |
| 2 | 日志/审计隐私 | server/proxy 日志配置、audit table/sink | 不泄露 capability/token/body；CR/超长 ID 编码或替换；财务事件持久且可关联 |
| 2 | 就绪/部署 | 严格 Settings、ready 端点、生产清单 | 拒绝未知 env；可见架构/支付/存储/商品/代理状态；加固镜像以最小权限运行 |
| 3 | 解析器正确性契约 | 替换或限制 `sql_ddl.py`、ER/Word UI | catalog 差分语料通过；不支持语法返回诊断；无 phantom/悬空实体 |
| 3 | Draw.io/浏览器完整性 | diagram schema/router/frontend + Playwright | NUL/畸形 XML 返回有界 4xx；列表不取正文；所有切换都保留未保存工作或明确确认 |
| 3 | RAG/隐私 | article/retrieval/logging/UI/policy | 只用已批准且有许可语料；每问不再扫描全库；披露处理方/保留并提供删除流程 |
| 4 | 供应链/文档/法律/无障碍 | CI、锁、容器、文档生成器、政策、模板 | 可复现并有 attestation 的制品、notices、浏览器/无障碍门禁，以及从目标事实生成的活跃文档 |

### 9.2 防止表面修复或有害修复

- 不能在持有数据库事务时调用服务商并称为“原子”。网络与数据库不能共享 ACID 事务，应使用持久 Saga/事件/对账状态。
- 不能为了停止重试就确认畸形/未知支付成功，也不能对永久 poison event 永远返回失败。必须持久分类并遵循服务商协议。
- 首次签发 URL 后不能删除已付款权益；中断下载必须可按明确政策恢复。
- 不得对保留数据反复运行 `full_init.sql`、全局 `DROP` 或迁移 0008。
- `readyz`/liveness 不得泄露秘密或服务商详情；外部返回安全状态，内部记录诊断。
- 不得信任所有私网代理 CIDR 或 XFF 第一个值；固定真实直接入口 peer。
- 不能仅靠来源 IP allowlist 认证回调；保留密码学验证。
- 不得关闭 TLS 验证、带凭据跟随服务商重定向，或记录原始服务商响应/密钥。
- 解析 XML 时不得启用实体或网络解析。
- 不得静默规范化带引号 SQL 标识符或全部转小写；带引号与不带引号语义不同。
- 不能以“有图总比没有好”为由，在不警告的情况下持续返回部分 SQL 结果。
- 不能用单进程缓存清理宣称多实例一致性。
- 可变文件名和支付二维码不得使用 immutable 缓存。
- 在审查生成 bundle、许可证、Python 兼容性及两个 DB 测试套件前，不得强制自动修依赖。
- 文档指纹刷新不等于语义审查。

---

## 10. 缺失或具有误导性的测试覆盖

| 领域 | 现有覆盖 | 必须补充 |
|---|---|---|
| 支付 | 密码学向量、mock HTTP、金额、状态 CAS、重复事件 | 崩溃/提交歧义、回调早于提交、服务商查单/关单、事件账本、冲突交易、退款/争议、密钥轮换、staging 商户端到端 |
| 人工支付 | 角色与状态测试 | 收款凭据唯一性/证据、操作者审计原子性、双人/纠错流程、运维 UI、条款/对账 |
| 迁移 | 静态脚本检查和隔离的 0007/0008 历史测试 | 每个支持版本快照完整升级；账本下重跑；并发 runner；中断恢复；checksum；迁移架构上运行应用全套 |
| 架构一致性 | 表/列名相等 | catalog 级 type/null/default/check/FK/index/identity 一致性及原始 SQL 行为 |
| PostgreSQL 文本 | 已知登录 NUL；文章/消息有验证 | 图表及每个 JSON/服务商文本字段、非法编码、DB 异常安全映射 |
| OAuth | 同意、精确重定向、exchange 时 code 防重放 | 同意表单重放/过期、PKCE、scope/audience、跨资源/admin 负向、生产拒绝 HTTP redirect、撤销/互操作 |
| CSRF/浏览器认证 | Cookie flags 与 ASGI 请求 | 真实浏览器 login CSRF、跨站 POST、同站兄弟 origin、Origin/Fetch-Metadata、Cookie/Bearer 分流 |
| 限流 | 滑动窗口功能用例 | 高基数复杂度、内存上限、多进程共享语义、全局/用户/服务商预算、代理拓扑 |
| CPU/DB pool | 部分进程/线程行为 | 队列饥饿、硬超时/worker kill、客户端断开、LLM/支付/bcrypt 期间 DB pool 耗尽 |
| 爬虫 | 强 mock transport/SSRF/基础 robots | 重定向目标 robots/节流、超大 delay、总 deadline、状态淘汰/canonical origin、解压 CPU、复杂 selector、取消 |
| SQL 解析器 | 常用示例/回归 | 引擎反查差分、未支持诊断、schema/case/dollar/nested comment/ALTER/type、fuzz/property 语料 |
| 前端 | Node VM 源码/bundle 生命周期 | 实际浏览器 Playwright/WebDriver：延迟响应、非 JSON 代理页、Cookie、CSP、iframe、焦点、下载、离线/冲突/账号切换 |
| 无障碍 | 无 | axe 加人工键盘、屏幕阅读器、缩放、对比度、重排 |
| 静态交付 | build/drift | 压缩传输、跨版本缓存正确性、bundle 预算、陈旧入口防止 |
| 日志 | 基础断言/探针 | 真实 Uvicorn+代理脱敏、CR/control/超长 ID、500 关联/安全头、审计持久性/权限 |
| 隐私/RAG | 功能检索/模型 mock | 来源/审批/下架、prompt injection 质量、删除/保留、处理方 opt-out、相关性校准、大语料 |
| 容器 | 本审计/CI 仅静态 Dockerfile | build、非 root/只读冒烟、health/readiness、SBOM/CVE/secret scan、资源限制、升级/回滚 |
| 恢复 | 文档步骤 | 自动加密备份与破坏性恢复演练，含完整性/应用检查 |
| 文档 | 指纹、链接、生成数量 | 关键语义：CI 步骤、功能就绪、下载政策、当前生成指标、迁移安全 |

95.3% 覆盖率有价值，但不应通过低价值行执行继续抬高。只有在审查阈值后才增加 branch/condition coverage；优先级应是业务不变量、故障和浏览器测试，而不是追逐百分比。

---

## 11. 目标 SHA 的完整路由面

开发环境注册如下（51 个方法/路径组合；生产禁用框架文档）：

```text
GET    /
POST   /admin/articles/ingest
POST   /auth/login
POST   /auth/logout
GET    /auth/me
POST   /auth/password
POST   /auth/register
GET    /diagrams
POST   /diagrams
GET    /diagrams/{diagram_id}
PUT    /diagrams/{diagram_id}
DELETE /diagrams/{diagram_id}
DELETE /diagrams/{diagram_id}/purge
POST   /diagrams/{diagram_id}/restore
GET    /docs
GET    /docs/oauth2-redirect
GET    /health
GET    /healthz
GET    /oauth/authorize
POST   /oauth/authorize
POST   /oauth/token
GET    /openapi.json
GET    /readyz
GET    /redoc
GET    /robots.txt
GET    /shop
GET    /shop/dl
POST   /shop/download/{order_no}
GET    /shop/mock-pay
POST   /shop/mock-pay/confirm
GET    /shop/orders
POST   /shop/orders
GET    /shop/orders/{order_no}
POST   /shop/orders/{order_no}/confirm
POST   /shop/pay/notify
GET    /shop/ping
GET    /sitemap.xml
POST   /support/ask
GET    /support/center
GET    /support/conversations
GET    /support/conversations/{customer_id}/messages
POST   /support/conversations/{customer_id}/messages
GET    /support/messages
POST   /support/messages
GET    /tools/drawio
GET    /tools/er
POST   /tools/er-diagram
GET    /tools/mermaid
POST   /tools/mermaid
GET    /tools/ping
POST   /tools/word-export
```

另挂载 `/static`。Mock 端点始终注册，但非 mock 模式返回 404；人工确认非 manual 模式也返回 404。支付回调与签名下载有意不采用 session 认证，分别依赖服务商签名和 URL capability。公共工具、AI、管理员、商业路由的限流覆盖仍不一致，详见前文。

---

## 12. 复现指南与保留证据

### 12.1 安全的分离审计环境

只使用一次性副本/worktree 与数据库，绝不能让测试/初始化指向保留数据：

```bash
git rev-parse 889e3ebbee1a3e6f9b3eb67d0a660ef16c5f65f4
git archive 889e3ebbee1a3e6f9b3eb67d0a660ef16c5f65f4 | tar -x -C /path/to/disposable-audit
python3.11 -m venv /path/to/audit-venv
/path/to/audit-venv/bin/pip install -r requirements.txt
npm ci
```

### 12.2 核心检查命令

```bash
python -Wall -m compileall -f -q app tests scripts main.py "database init/db_init.py"
ruff check .
python -m pytest --collect-only -q
python -m pytest -q

# 只能使用专门的一次性 PostgreSQL：
TEST_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST/throwaway_db' python -m pytest -q

coverage run -m pytest -q
coverage report
pip-audit --strict -r requirements.txt --ignore-vuln PYSEC-2026-1325
npm audit --audit-level=high
npm run build
git diff --exit-code -- app/static/js
python scripts/check_docs_contract.py
python scripts/build_docs_site.py
```

对 `full_init.sql` 应记录第二次执行前后的数据行数；结果可证明“执行两次成功”只是破坏性重复，而不是幂等。`psql -v ON_ERROR_STOP=1` 也只能指向一次性数据库。

### 12.3 聚焦缺陷复现方法

- **种子接管：** 全新 full init 后，以 `admin/123456` 表单登录。
- **预支付/提交歧义：** 用记录接受结果的 fake 替换 `native_prepay`，在它返回后强制 `db.commit()` 失败；断言服务商已接受而本地没有持久订单。
- **同意重放：** 认证后 GET authorize，提取 `sig`，把同一批准 POST 两次，观察两次有效 302 code 响应。
- **NUL 登录：** PostgreSQL 下提交用户名表单编码 `%00`；观察该 SHA 的未处理 DB 失败。
- **超大 ETag：** 认证并创建图表，然后 PUT 一个带引号数字 `If-Match`，其长度超过 Python integer-string 上限；观察 500 而非有界 4xx。
- **商店竞态：** 运行保留 Node 探针；延迟响应后 landing 再次变为 pending。
- **限流复杂度：** 接纳不同且未过期的键并记录字典遍历数；结果见 H-04。
- **日志拆分：** 向真实 Uvicorn 发送路径含 `%0D` 的请求，检查 ASGI 百分号解码后的自定义访问行；再发送 5,000 字节可打印 request ID，检查反射 header/日志长度。
- **Host：** 使用任意 Host 请求；从可信测试 peer 使用任意 X-Forwarded-Host；调用 `public_base_url()` 或生成 mock/download 链接。
- **Sitemap：** 配置一个以 HTTPS 开头但含 `&`/XML 元字符的 base，检查原始 `<loc>` 输出。
- **SQL 解析器：** 运行 `AUDIT_SQL_PARSER_PROBES_01a08bf5.txt` 中保留用例，并与 PostgreSQL catalog 语义比较。

### 12.4 工作区保留的证据

- `AUDIT_RUNTIME_PROBES_01a08bf5.txt`：直接 ASGI 与真实 Uvicorn 的 path/request-ID/Host/proxy 观察。
- `AUDIT_SQL_PARSER_PROBES_01a08bf5.txt`：ALTER/隐式 FK、dollar quoting、嵌套注释、标识符语义、类型和复合键的准确输出。
- `AUDIT_SHOP_CANCEL_RACE_PROBE_01a08bf5.txt`：可执行的延迟轮询竞态状态。
- `AUDIT_RATELIMIT_PRUNE_PROBE_01a08bf5.txt`：高基数复杂度测量。
- 最终验证后生成本报告的 `.sha256` sidecar。

这些制品不包含生产秘密。运行时日志制品有意包含 5,000 字符测试 ID，只有需要详细证据时才打开。

---

## 13. 审计限制

- 审计环境无 Docker，因此镜像构建/运行时加固仅静态审查，未声称构建成功。
- 未测试真实微信商户、真实签名回调、生产 DNS/TLS/代理、OSS/COS、浏览器群、Microsoft Word、diagrams.net 会话或备份恢复。
- 目标 SHA 当前 Agnes 运行只测试未认证连通性；含秘密 job 被跳过。文档中的早期 secret-backed chat/Mermaid 证据属于早期 SHA。
- 依赖审计只代表特定日期的数据库快照，不保证未来没有披露或恶意包。
- GitHub API 权限返回 403，因此无法读取分支保护状态。
- 法律、隐私、许可证观察只指出事实和发布问题，不构成法律意见。
- 图表 NUL 路由是高置信度 PostgreSQL/源码分析；不同于已直接复现的 NUL 登录，应在修复轮通过端点实际复现。
- 生成的压缩第三方代码按结构与来源审计，没有逐行做语义逆向。
- 目标及可访问 17 个 revision 扫描中未发现高置信度仍有效秘密。但公开演示凭据和有意提交的支付二维码仍是运维敏感的已知材料，不能据此证明秘密管理完整。

---

## 14. 最终结论

在 `889e3ebbee1a3e6f9b3eb67d0a660ef16c5f65f4`，CodeMax 是一个测试充分、带有多项认真防线的演示/单进程应用，但还不是生产级商业支付平台、外部 OAuth 平台或具备隐私治理的平台。最有价值的下一步不是再做一次大范围重构或增加更多说明文案，而是建立持久迁移、支付与权益基础，清除已知身份，严格验证所选模式的就绪条件，并增加以故障为中心的测试。随后立即处理安全/资源边界与浏览器数据完整性。

本轮没有实施任何代码修复。后续修复轮应把验收证据绑定到新的准确 SHA，同时运行两个数据库套件以及新增的故障、浏览器和容器测试；重新生成活跃文档事实；并把本报告保留为带日期的审计证据，不能静默改成“全部成功”的声明。
