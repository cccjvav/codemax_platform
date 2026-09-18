# 公共后端模块

## 模块职责

这里提供配置、数据库会话、鉴权、订单状态转换、存储和运行时边界。它不全是“无业务逻辑的基础设施”：`order_state.py` 就是共享业务规则，`site.py` 也维护商品/工具展示信息。
路由层仍包含查询与事务编排；不能把理想分层写成已经做到的事实。

阅读顺序：`config` → `database/models` → `security/deps` → 对应业务路由。本文给出人工核对的函数契约；完整签名、源码 docstring 和动态行号见离线站符号索引。

## 文件与入口

Settings 的当前模型默认是 Agnes 基址与 agnes-2.5-flash；LLM_EMBED_ENABLED=false、向量模型为空，0.55 仅是启用前待标定阈值。Settings固定读取仓库根.env，环境变量仍可显式覆盖，不自动改写用户私有配置。详细迁移见 [Agnes 接入](../docs/AGNES_AI.md)。

### 配置与会话

| 入口 | 输入与结果 | 副作用、失败与调用要求 |
| --- | --- | --- |
| `Settings.sqlalchemy_url` | 有 `DATABASE_URL` 时直接使用，否则按 DB 字段构造 asyncpg URL；用户名、密码编码；DB_NAME 当前原样拼入路径 | `settings` 与 engine 在导入时创建，不是每次请求重新载入 `.env`。变更部署配置要重启；不要记录完整连接串 |
| `Base` / `SessionLocal` | ORM 元数据基类／异步会话工厂 | `expire_on_commit=False` 不等于自动刷新，写入后需要显式 refresh 或 populate_existing |
| `get_db()` | 向 FastAPI 依赖注入一个 AsyncSession，退出上下文时关闭 | 不会代业务代码 commit；未提交事务随关闭回滚 |
| `lock_user(db, user_id)` | 对用户执行无值变化 UPDATE，再返回刷新后的用户对象，找不到则 None | 调用者拥有 commit/rollback。PostgreSQL 是行级写锁，SQLite 写锁更粗；不是一个仅在 Python 内生效的锁 |
| `as_utc(dt)` | 有时区值原样返回；无时区值按 UTC 解释 | 不做本地时区推断。主要统一 SQLite 测试与 PostgreSQL 时间读回行为 |

### 数据模型与输入模型

| 模型 / 文件 | 需要理解的字段与约束 |
| --- | --- |
| `User` | username 唯一；password 是哈希；status 决定可否登录，role 决定管理员权限；credential_version 是持久凭据版本；password_changed_at 保留时间证据 |
| `Order` | amount 单位为分；order_no 唯一；数据库部分唯一索引约束每用户最多一个 pending；付款状态与 transaction_id/paid_at 必须原子写入 |
| `Article` | URL 唯一；标题/作者/日期/域名有字段长度；正文 Text。日期保留来源字符串，不承诺已解析成统一时间 |
| `SysDiagram` | 用户所有权、XML 正文、version 乐观锁、deleted_at 回收站标记；删除不等于释放全部存储预算 |
| `OAuthClient` / `OAuthCode` | 客户端密钥存哈希；注册回调精确匹配；授权码有 used、过期时间、用户和凭据版本 |
| `SupportMessage` | customer_id 定义会话；sender_id 与 sender_role 记录发送方；(sender_id, client_nonce) 唯一，用于丢失响应后的重试 |
| `SchemaMigration` | 四位版本主键、SQL校验和与应用时间；仅离线CLI写入，Web启动只读核对 |
| `SysConfig` | 持久配置表；不能因此推断应用已经把所有 Settings 从此表热加载 |
| `schemas.py` | RegisterIn、PasswordChangeIn 校验密码 UTF-8 字节上限与空字符；用户名全匹配；DDL、文本、XML 有长度约束，DiagramIn 额外拒绝 PostgreSQL 不允许的 NUL；DiagramSummary 不返回正文，DiagramOut 返回正文 |

### 密码与凭据

| 函数 | 输入 → 返回 | 边界 |
| --- | --- | --- |
| `hash_password` / `ahash_password` | 明文 → bcrypt 哈希字符串 | async 版卸载到线程池；哈希函数不是完整输入校验器，应先走 schema；不得把明文写日志 |
| `verify_password` / `averify_password` | 明文、哈希 → bool | 空字符、超过 72 UTF-8 字节、无效哈希返回 False；HTTP 请求中用 async 版，避免阻塞事件循环 |
| `adummy_verify` | 失败登录的输入 → None | 用户不存在时仍执行校验以减小明显耗时差，不保证所有输入与部署条件下恒定时间 |
| `create_access_token` | username、当前改密时间、当前 credential_version → JWT | 无数据库查询；调用者必须传当前库值，不要对已改密用户使用默认版本 0 |
| `decode_token` | JWT 字符串 → TokenClaims 或 None | 验签、过期和声明类型检查；缺少合法 ver 的旧 JWT 被拒绝。这里不查用户是否存在/被禁用/被撤销 |
| `get_current_user` | 请求和会话 → 当前数据库 User | Bearer 优先于 Cookie；校验数据库状态、版本和改密时间；失败 401；角色不从 JWT 里取 |
| `require_admin` | 已验证 User → 同一 User | role 不为 1 返回 403；降权下一次查库即生效，不靠隐藏接口路径 |

HttpOnly 降低脚本直接读取令牌的风险，但不阻止注入脚本借当前会话发请求，不能把它写成“防住所有 XSS”。退出浏览器只清本端 Cookie，不撤销已复制的 Bearer；改密才递增凭据版本。

### 订单状态机

```text
pending → paid → downloaded
pending → closed → paid
```

`downloaded` 表示曾发放下载链接，不表示客户确已收完文件，也不消灭重领权益。

| 函数 | 返回 / 异常 | 事务语义 |
| --- | --- | --- |
| `check_transition(current, target)` | 合法返回 None，非法抛 IllegalTransition | 纯检查，不写库 |
| `is_expired(order, ttl_minutes, now)` | bool；仅 pending 且有 create_time 才判超时 | 纯计算，不顺手关闭订单；修改 TTL 会影响既有待支付单的判定 |
| `mark_paid` | 内部兼容入口，流水必填 | 委托payment_ledger.settle，不能绕过凭证/渠道；未知历史渠道拒绝。精确重复False，冲突抛IllegalTransition |
| `mark_closed` | pending → closed；已 closed 返回 False | 非法起点报错；内部提交并刷新传入对象 |
| `mark_downloaded` | paid → downloaded；已 downloaded 返回 False | 幂等发放记录，不提供一次性访问控制；内部提交 |
| `_cas` | UPDATE 命中一行则 True | 自己 commit 和 refresh，不是可自由嵌套在更大原子事务里的无提交 helper |

### 存储与支付协议

| 入口 | 输入与返回 | 失败与限制 |
| --- | --- | --- |
| `build_storage(base_url)` | 当前配置 → Storage | 目前只实现 LocalStorage；其他后端抛 StorageError，不能因接口叫“云存储策略”就宣称 OSS/COS 已接入 |
| `sign_download` / `verify_download` | secret、key、到期秒 → 十六进制签名 / bool | 签 key 与 expires；过期或非 ASCII 签名拒绝；这不是订单所有权校验 |
| `LocalStorage._path` / `local_path` | key → 限制在 root 内的解析路径 | 拒绝越界；root 是服务器受控目录，不是用户可任意改写的共享目录 |
| `put` / `read` / `exists` | bytes 写入 / 全量读出 / 是否存在 | put 建父目录；read 会把全文件读入内存；下载端点用 local_path + FileResponse 而不是 read |
| `presigned_url` | key、有效秒数 → `/shop/dl` 链接 | 不查数据库、不保证文件存在、不主动验证购买资格；路由先验证这些条件 |
| `PayConfig.configured` / `notify_ready`、`pay_config` | 配置快照 → 是否具备下单/回调所需字段 | 字段齐全不证明密钥可用或商户已联调；pay_config 每次调用读取 settings |
| `new_order_no` | 可选当前时间 → 订单号 | 不含用户身份；数据库唯一约束仍是最终兜底 |
| `canonical_string` / `sign` / `auth_header` | 请求方法、路径、时间、nonce、原始 body → 签名材料 / RSA 签名 / Authorization | body 序列化必须和实际发出的字节一致；不要用解码重排后的 JSON 验签 |
| `native_prepay` | 商户配置、订单号、描述、分金额 → code_url | 发出外部请求；总deadline20秒、响应64KiB上限；配置/协议/网络错误为WeChatPayError且不透传正文；不写本地订单 |
| `assert_notify_identity` / `assert_notify_fresh` / `verify_notify_signature` / `decrypt_resource` | 回调头、原始正文与密文 → 校验或解密对象 | 本地固定平台证书/公钥ID与证书有效期、新鲜度、签名、AES-GCM是不同检查；解密成功仍须由路由核对商户、订单、金额和支付状态 |

### 运行时与站点

| 入口 | 结果与关键约束 |
| --- | --- |
| `run_cpu_bound(fn, *args)` | 返回任务结果；最多 2 个在途任务，响应等待 30 秒。满额或超时抛 CPUQueueFull；取消/超时不杀掉实际任务，完成前继续占槽 |
| `_get_executor` / `_execute_cpu` / `shutdown` | 懒建单 worker 进程池；基础设施故障才回落线程池；普通任务错误向外传播；shutdown(wait=False) 不意味着已强杀运行中的任务 |
| `Limiter.allow` / `prune` / `reset` | 滑动窗口返回 (是否允许, Retry-After秒数)；请求最多维护32键，默认16384桶上限，满时拒绝新键；prune为显式全扫描维护，reset清理全部状态 |
| `client_key` / `rate_limit` | 可信直接对端才允许解析 XFF，从右侧跳过可信代理；依赖按 scope + IP 限流，超额 429。内存状态不跨进程共享 |
| `trusted_proxy` / `public_base_url` | 精确 CIDR 控制转发头信任；生产链接固定为已校验 SITE_BASE_URL，开发链接可按可信头派生。应用端口仍须阻止绕过代理访问 |
| `SecurityHeadersMiddleware` | 设置 CSP、HSTS、安全响应头和私人响应 no-store；生产 API 文档关闭；开发文档和 OAuth 同意页有局部例外 |
| `RequestLoggingMiddleware` | 记录请求方法、路径、状态、耗时等；ID 只接受安全128字符格式，路径转义限长、不含查询；这不是独立防篡改审计存储 |
| `check_production_settings` / `check_production_warnings` | 分别返回阻断问题／告警列表；配置检查不进行实际商户、文件或模型连通性验收 |
| `enforce_database_safety` | production在lifespan中10秒内只读校验迁移账本、已知演示凭据与管理员；失败不接流量、不自动改库 |
| `enforce_production_settings` | 记录告警，有硬错误抛 ProductionConfigError；由 main 在导入装配阶段调用 |
| `Tool` / `page_title` / `page_context` | 统一页面元数据与模板上下文；auth_ui 控制登录界面是否装配，不授予 API 权限 |

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/__init__.py`](__init__.py) | `e3b0c44298fc` | 空文件（无源码行） |
| [`app/config.py`](config.py) | `eaaf3ac74b46` | L1–L139 |
| [`app/cpu_pool.py`](cpu_pool.py) | `9b56d12ebe6e` | L1–L103 |
| [`app/database.py`](database.py) | `31f23a8fcc1e` | L1–L28 |
| [`app/db_admin.py`](db_admin.py) | `462f5b06dc73` | L1–L288 |
| [`app/delivery.py`](delivery.py) | `8af0a7803df2` | L1–L110 |
| [`app/deps.py`](deps.py) | `28ba9deac195` | L1–L88 |
| [`app/middleware.py`](middleware.py) | `c18fb3475e6d` | L1–L180 |
| [`app/models.py`](models.py) | `ce0a2b1aad56` | L1–L317 |
| [`app/order_state.py`](order_state.py) | `9ee748300c73` | L1–L100 |
| [`app/payment_ledger.py`](payment_ledger.py) | `06b421e1a65b` | L1–L85 |
| [`app/payment_review.py`](payment_review.py) | `2870967a24f0` | L1–L127 |
| [`app/ratelimit.py`](ratelimit.py) | `968a3f9ac373` | L1–L128 |
| [`app/refund_notifications.py`](refund_notifications.py) | `744c462ad740` | L1–L203 |
| [`app/refund_requests.py`](refund_requests.py) | `32d8de600e87` | L1–L92 |
| [`app/refund_submissions.py`](refund_submissions.py) | `4d1fe29b2f10` | L1–L186 |
| [`app/refunds.py`](refunds.py) | `68f29f266a1b` | L1–L80 |
| [`app/schemas.py`](schemas.py) | `cbeef376aa96` | L1–L153 |
| [`app/security.py`](security.py) | `8e4614d7561f` | L1–L109 |
| [`app/site.py`](site.py) | `ecfecdc0484d` | L1–L126 |
| [`app/startup_checks.py`](startup_checks.py) | `8a89346a1a07` | L1–L153 |
| [`app/storage.py`](storage.py) | `9e9d10602f79` | L1–L124 |
| [`app/timeutil.py`](timeutil.py) | `63bad13bfe2e` | L1–L19 |
| [`app/wechat_pay.py`](wechat_pay.py) | `8d6ddfeb4f1b` | L1–L453 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

HTTP 输入先由 schema 校验，再进入身份依赖与业务处理。状态变更由数据库约束/CAS/事务协调，缓存只是加速，不是权限来源。
当前默认单进程；共享数据库并不会使内存限流、CPU 槽位或缓存变成跨副本全局机制。金额必须保持整数分，订单商品文件仍使用全局配置 key，不能直接用换 key 的方式销售新 SKU。

## 变更与验证

修改模型同步审查建表和迁移；修改 token 同步 auth、OAuth 和 deps；修改状态机检查 shop 的幂等与提交边界。
重点测试：`test_auth_cookie.py`、`test_oauth.py`、`test_download.py`、`test_ops.py`、`test_review_regressions.py`、`test_second_review_regressions.py`。路径均位于 tests；完整执行方法见 [测试指南](../tests/README.md)。
源码变动后先更新本说明，再从仓库根运行 `python scripts/check_docs_contract.py --write`；普通检查不刷新指纹。


## 离线数据库维护

`db_admin`不是Web路由，所有写入由显式CLI触发：`migration_manifest/verify_ledger`检查连续版本及文件摘要；`connect_target`复用配置并核对确认库名；`maintenance_lock`设固定search_path和超时、持事务级PG锁；`initialize`只接受空库；`adopt_legacy`检查已声明的0008结构后登记并执行新迁移；`_record/_migrate/migrate`让SQL和账本原子提交；`status`只读；`seed_demo`开发显式且不覆盖；`bootstrap_admin`只创建首个启用管理员，不提权既有账号。连接由调用者关闭，错误不携带DSN。详见[数据库指南](../database%20init/README.md)，它不是完整DDL等价或生产角色授权工具。

## 第三批：收款证据与固定文件权益

`payment_ledger.py`把“钱属于哪张单”与paid状态一起提交：lock_order用无副作用UPDATE持写锁并刷新，settle校验渠道/商户/金额并写一条PaymentReceipt，精确重复不再写收款凭证，但可提交本次核查事件；唯一冲突或提交失败全部回滚。不是外部查单/退款，也不是日志代替数据库。

`delivery.py`的Snapshot保存key/hash/size；snapshot_product在两个复制槽位、512MiB/30秒约束下分块复制、校验源变化、无覆盖发布，调用方卸载线程。file_digest分块校验，verify_snapshot绑定内容寻址key和实际字节。POSIX同步发布目录，Windows实际硬链接/恢复未验收。LocalStorage.put不能覆盖快照；主机管理员仍是可信边界。

Order冻结支付/交付合同；PaymentReceipt记录来源流水、金额、提供方支付时间/本地收到时间与人工依据，PaymentEvent记录尝试或历史绑定。0010和full_init安装PG不可覆盖合同/只追加证据触发器；ORM create_all不安装这些触发器，不能拿普通ORM测试当SQL触发器证据。完整范围、历史绑定和未结项见[第三批](../review/RELEASE_BLOCKERS_PHASE3.md)。

## 第四批：可信应答与主动核查

`wechat_pay._request_json`统一Native POST/查单GET：精确签名和发送字节、固定域名、禁止跳转/压缩、64KiB及20秒总预算。`assert_response_signature`检查单值有界四头、平台身份/有效期、5分钟窗口及原文签名，不能先反序列化再验；非200即便可信也不当未付。`QueryResult`只暴露受验证状态/流水/时间，不存payer原文；`query_order`绑定订单号、商户/app和提供的金额，SUCCESS强制CNY/NATIVE/完整带时区凭证。未付可缺官方可选金额，REFUND仅观察。sign要求RSA至少2048位。

`settle(audit_event=...)`检查事件属于原单，在锁内把成功事件与凭证/paid一起提交；精确重试也可有新的核查事件，原凭证/首次人不变，失败全部回滚。`deps.require_finance_origin`服务九种管理员财务写操作（含复核、两个核验、准备、独立授权和发送）：Cookie来源/Fetch Metadata防护与有效Bearer通道分开，无来源头非浏览器客户端兼容；不声称完整token型CSRF系统。路由持久开始/未知/冲突等事件，底层支付客户端不自行操作数据库。

## 第五批：payment_review.py的只读复核投影

review_payload把动作/资料摘要/上轮版本/最多160字说明编码为500字符内的v1 JSON，decode_review检查格式与原发起人，坏记录不当完成。overdue_start严格按同单同attempt对应预付/查单结束种类并等待60秒；review_candidates只是SQL候选条件，不是最终状态。review_states对最多50单做四次批量读取：非复核事件count/max/异常数、孤立开始数、最新复核、凭证ID，再计算包含订单状态/流水的SHA256摘要。count不可省，序列号不是提交顺序；旧close只有匹配当前资料才呈现reviewed，其他新进展重新待办。它不写库、调渠道或改变收入/权益，也不是资金问题结案。

## 第六批：refunds.py与退款证据

`RefundReceipt`不是申请状态，而是单笔全额退款成功凭证；订单与原PaymentReceipt各唯一，来源/退款ID唯一，来源/原商户/商户退款号唯一。金额是合同退款金额，不是微信实际现金退款额、净结算额或手续费会计分录。0011的真实PG触发器还核验原单/收款的归属、渠道、金额和时间，并禁止改写/删除；ORM建表只有列级/唯一约束，不能证明触发器。

`refund_for(db, order_id)`每次发SELECT查退款事实，不依赖旧ORM关系缓存。`original_receipt`要求真实manual/wechat且付款凭证与冻结合同一致，拒绝mock/无凭证历史单。`record_refund`由调用方传渠道已验签结果或实际人工证据，调用方锁住并重查管理员；它锁订单、检查全额/成功时间/归属，把凭证和审计一起commit，异常rollback。精确重复不覆盖第一人/依据/收到时间；查询重试仍追加本次终态事件。保留paid/downloaded与原付款时间，所以迟到付款通知仍可幂等，但下载门禁另读退款表，不会恢复退款权益。

`query_full_refund`复用有界HTTPS/平台应答验签，只GET普通商户退款查询；绑定原商户配置、商户订单号、原交易号、商户退款号、ORIGINAL、CNY和整数全额；SUCCESS必须有带时区时间。RefundResult只是内部验证后的观察，非SUCCESS不生成退款凭证。详见[操作与边界](../docs/PAYMENTS_ADMIN_GUIDE.md)。

付款/退款时间写入前统一UTC，避免SQLite丢失偏移后同一+02/+08证据重试冲突；PG亦保持一致。成功时间不早于已知原付款时间，最多容忍服务端当前时间之后五分钟的时钟偏差。已有SQLite错误偏移数据不能凭空推断原时区，需受控核账；生产目标是PostgreSQL。


## 第七批：refund_notifications.py仅保存可信线索

RefundNotice是不可变的已解析字段，不是RefundReceipt。parse_notice要求四头各恰一条、大小/新鲜度/可信平台身份/RSA原文验签，然后检查refund/AEAD_AES_256_GCM、AES解密、内外三态一致及商户；_identifier拒绝越界标识，_moment拒绝无时区/过远未来并归一UTC。金额严格整数，允许合同范围内部分通知；payer金额不保存为会计分录。输入上限由路由负责；调用方不可绕过parse_notice自行构造“可信”对象。

notice_evidence把明确接收的业务字段正规JSON摘要化，保存用于显示的标识/状态/合同退款额/部分标记/时间与SHA256指纹，最多500字符。不保存完整正文、账号或签名包，指纹不是独立密码学证据。save_notice拥有事务：锁单后核对冻结商户/app/CNY和original_receipt，成功时间不早于原付款；商户+通知ID用途域SHA256前32位填既有attempt_id，固定refund_notify_signal，唯一约束与订单锁覆盖重复/跨单竞争。精确重发不追加；不同事实/归属冲突，不吞数据库失败；异常和取消rollback，commit后才让路由ACK。

notice_view只读最新记录、验证版本/标识/金额/系统归属等显示合同，坏摘要返回None，不回退旧通知。payment_review把通知种类计入issues，新事件自动作废旧复核；幂等重发不动资料摘要。通知模块不提供请求台账、自动查询消费者或权益变更；本地准备台账见下节；更完整请求工作流不能一直塞进任意JSON冒充金融账本。


## 第八批：refund_requests.py与本地准备台账

RefundRequest通过唯一order_id/payment_receipt_id/request_id/商户退款号固定一笔全额微信准备，不是退款成功记录或自动发送授权。prepare_request由调用方先锁并复核活跃管理员，再锁订单、验证原微信收款及严格全额CNY。客户端request_id绑定原单/原凭证/发起人/金额/依据/商户/app；精确重发先返回首次行，不因后来的通知/成功退款丢失恢复能力。不同内容或同单换ID均409，不修改首笔信息。

首次准备还拒绝既有RefundReceipt或prior_refund_activity发现的refund_notify_signal/refund_query_started（即使未知/关闭也不能据此新造号码）。服务器生成CMR+UUID十六进制号，flush通过PG校验后追加prepared事件，同事务commit后才返回；IntegrityError回滚为归属冲突，其他错误/取消回滚再抛。仅恢复本地提交丢应答，不是渠道发送未知结果的恢复；没有网络、退款权限预授权或资金预留记账。

request_for独立读原单准备；request_view仅以成功凭证给prepared/confirmed/completed_elsewhere展示状态。prepared只说明有本地记录，不能推断渠道没发送；通知SUCCESS不会把它升级。实际退款号不同则保留原准备并提示已有其他成功凭证，禁止另发。内部依据最多160字符，不是微信面向客户的reason字段（80字节），不得以后直接透传。

payment_review仍四次批量读取：最后一次收款/退款查询再LEFT JOIN唯一准备行，资料摘要带准备ID；无准备保持旧摘要编码。候选及最终state同时考虑准备行，缺过程事件也可发现；精确重试不追加事件、不重新打开已完成的同一轮复核。


## 第九批：refund_submissions.py与显式发送

RefundAuthorization唯一绑定原准备，另有全局授权request_id；冻结完整JSON字节及SHA256、首次授权人/依据/时间。build_body只取原交易流水/全额CNY/固定商户退款号，客户原因单独输入且最多80 UTF-8字节，回调由SITE_BASE_URL固定拼出/shop/refunds/notify。内部依据不能透传为reason。authorize持用户→订单锁，完整授权和事件同事务；精确同人同事实重放保留原回调，即使配置后来改变。

begin_send需要当前管理员再次确认授权ID/摘要/原退款号/全额，默认WX_REFUND_SEND_ENABLED=false。每次新尝试先持久refund_send_started，再释放事务调用固定微信端点；相同尝试ID仅读回，绝不再次发送。未知需新显式尝试且至少60秒、同正文同号；任一已受理观察、独立通知/查询或成功凭证阻止新发送。60秒不是分布式任务租约；进程暂停也只能重复原商户号，不能新造号码。

finish_send只追加有界观察，保留发起人，即使网络期间角色撤销也保存已发生事实；不发新请求/不创建RefundReceipt/不撤权。崩溃、取消或结果落库失败留下unknown，不捏造未发送。submission_view独立读授权/最近尝试；复核摘要独立包含授权ID，缺过程事件仍能发现。没有后台扫描器、自动重试、授权取消/改写、部分退款或真实商户签收。

wechat_pay.submit_full_refund复用有界签名POST传输，逐字节发送冻结body；parse_full_refund是查询与申请共用的可信应答字段校验，调用方决定权限：申请即便SUCCESS也只记观察，独立查询才记成功凭证。
