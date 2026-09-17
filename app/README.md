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
| `as_utc(dt)` | 有时区值转为 UTC；无时区值按 UTC 解释 | 不做本地时区推断。主要统一 SQLite 测试与 PostgreSQL 时间读回行为 |

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
| [`app/config.py`](config.py) | `1e150dc23bfd` | L1–L138 |
| [`app/cpu_pool.py`](cpu_pool.py) | `9b56d12ebe6e` | L1–L103 |
| [`app/database.py`](database.py) | `31f23a8fcc1e` | L1–L28 |
| [`app/db_admin.py`](db_admin.py) | `aff882f475eb` | L1–L288 |
| [`app/delivery.py`](delivery.py) | `8af0a7803df2` | L1–L110 |
| [`app/deps.py`](deps.py) | `358144652b38` | L1–L55 |
| [`app/middleware.py`](middleware.py) | `c18fb3475e6d` | L1–L180 |
| [`app/models.py`](models.py) | `ccd8d213c81e` | L1–L248 |
| [`app/order_state.py`](order_state.py) | `9ee748300c73` | L1–L100 |
| [`app/payment_ledger.py`](payment_ledger.py) | `98ff2d7d0deb` | L1–L78 |
| [`app/ratelimit.py`](ratelimit.py) | `968a3f9ac373` | L1–L128 |
| [`app/schemas.py`](schemas.py) | `cbeef376aa96` | L1–L153 |
| [`app/security.py`](security.py) | `8e4614d7561f` | L1–L109 |
| [`app/site.py`](site.py) | `ecfecdc0484d` | L1–L126 |
| [`app/startup_checks.py`](startup_checks.py) | `8a89346a1a07` | L1–L153 |
| [`app/storage.py`](storage.py) | `edc0b127e612` | L1–L119 |
| [`app/timeutil.py`](timeutil.py) | `63bad13bfe2e` | L1–L19 |
| [`app/wechat_pay.py`](wechat_pay.py) | `c8f7c1c087c9` | L1–L305 |

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

`payment_ledger.py`把“钱属于哪张单”与paid状态一起提交：lock_order用无副作用UPDATE持写锁并刷新，settle校验渠道/商户/金额并写一条PaymentReceipt，精确重复不再写，唯一冲突或提交失败全部回滚。不是外部查单/退款，也不是日志代替数据库。

`delivery.py`的Snapshot保存key/hash/size；snapshot_product在两个复制槽位、512MiB/30秒约束下分块复制、校验源变化、无覆盖发布，调用方卸载线程。file_digest分块校验，verify_snapshot绑定内容寻址key和实际字节。POSIX同步发布目录，Windows实际硬链接/恢复未验收。LocalStorage.put不能覆盖快照；主机管理员仍是可信边界。

Order冻结支付/交付合同；PaymentReceipt记录来源流水、金额、提供方支付时间/本地收到时间与人工依据，PaymentEvent记录尝试或历史绑定。0010和full_init安装PG不可覆盖合同/只追加证据触发器；ORM create_all不安装这些触发器，不能拿普通ORM测试当SQL触发器证据。完整范围、历史绑定和未结项见[第三批](../review/RELEASE_BLOCKERS_PHASE3.md)。
