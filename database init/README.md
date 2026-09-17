# 数据库初始化与升级

## 模块职责

本目录现在提供**空库初始化、显式旧库接入、带账本的增量升级**。不自动创建/删除数据库，也不在 Web 启动时执行迁移。历史版本的 full_init 曾含 DROP，不能回退到旧脚本“修环境”。

大白话：初始化是往空房间放家具，迁移是在保留家具的前提下改造房间。`schema_migration` 是签收本，记录版本及 SQL 校验和；事务锁保证同一时间只有一组维护工人操作，失败时改造和签收一起回滚。

## 文件与入口

### 维护 CLI

在项目根目录、已安装依赖的 Python 环境运行（Windows 用 Conda＋CMD）。Settings 固定读取仓库根 `.env`，环境变量优先，`DATABASE_URL` 非空优先于 DB_*；不再从当前工作目录的 `../.env` 猜配置。口令不作为命令行参数。

先用 PostgreSQL 管理工具创建一个**专用空库**并授予维护角色必要权限，填写私有配置。以下 `codemax_db` 必须换成实际确认的目标名；确认参数与实际连接库名不符时拒绝。

```text
python "database init/db_init.py" init --confirm-database codemax_db
python "database init/db_init.py" bootstrap-admin --username owner --confirm-database codemax_db
python "database init/db_init.py" status --confirm-database codemax_db
```

`bootstrap-admin` 两次不回显输入新口令，至少12字符且最多72 UTF-8字节；用户名沿用注册校验，不使用保留名 admin。只允许创建第一个启用管理员，不覆盖密码、不提升已存在普通用户。若用户名已被占用，另选新的管理身份，不把该用户直接提权。没有默认管理员/客户端；生产启动要求已有非演示管理员。

| 命令 / 文件 | 输入、结果与失败边界 |
| --- | --- |
| `db_init.py main` | 解析显式命令/确认库名后才连接；无参数退出2并显示帮助；可理解的维护拒绝消息不含凭据，驱动/验证错误脱敏并退出1，最终关闭连接 |
| `init` / `full_init.sql` | public 中有任何用户表、视图或序列则拒绝；不含 DROP 或身份 INSERT。建表和当前基线账本在同一事务提交。直接执行 SQL 不会生成完整账本，正常入口只能用 CLI |
| `adopt-legacy-0008` | 操作者确认已完成0008结构；检查表列与关键索引、拒绝覆盖已有账本；登记0001–0008历史基线，再在同一事务执行0009及后续迁移。它不是完整DDL等价或所有旧版本自动升级器 |
| `migrate` | 仅执行账本中缺失的0009及后续迁移；拒绝校验和变化、版本缺口、未知更高版本或旧事务包装脚本；DDL/DML和账本一起提交/回滚 |
| `status` | 只读核对校验和并列待执行版本；不自动初始化/迁移。返回none表示账本当前，不证明外部商户或备份已验收 |
| `seed-demo` / `seed_demo.sql` | 仅development下显式调用且用户/客户端表都空时允许。包含公开演示身份，禁止公网使用；生产检查会拒绝启用的演示凭据。不会覆盖已有账号 |
| `migrate_0009_retire_demo.sql` | 停用仍具有原始公开哈希的管理员/客户端，递增该用户凭据版本并清理已停用身份的授权码；保留订单/消息/图表。重新哈希过的相同弱密码由生产启动检查另外拦截 |

`app/db_admin.py` 是可测试实现：`migration_manifest/verify_ledger` 验证连续历史与 SHA-256；`connect_target` 校验确认库名、复用应用配置；`maintenance_lock` 固定 public search_path，事务级 PG advisory lock，锁等待10秒、单语句120秒；`_record/_migrate` 在同一连接写变更和账本；其他函数分别实现表中命令，调用者负责关闭连接。数据库角色必须有对应权限，工具不绕过 PostgreSQL 授权；生产应区分维护与应用角色，本地示例不是最小权限部署证明。

### 已有库：停写、备份后操作

1. 先验证备份可恢复，停止旧应用写入。不要把业务库传给 pytest。
2. 已有0008结构但无账本：运行 `adopt-legacy-0008`，不是 `init`。若结构检查失败，先调查旧版本；工具不猜测旧时间字段时区、不自动关闭重复pending单。
3. 已有账本：先 `status`，确认部署版本匹配，再 `migrate`。不得删账本或修改历史SQL来消除错误。
4. 若公开种子被停用且无其他启用管理员，再 `bootstrap-admin` 创建新身份。原数据不删除；停用身份需重新治理，不自动把旧客户数据转给新身份。
5. 用新版本启动应用。production先检查账本、已知弱管理员/演示客户端和管理员存在性；失败拒绝启动，绝不边接流量边迁移。

```text
python "database init/db_init.py" adopt-legacy-0008 --confirm-database codemax_db
python "database init/db_init.py" status --confirm-database codemax_db
```

同一 SHA 文件内容须稳定，`.gitattributes` 为SQL强制LF，避免Windows换行导致校验和漂移。已登记迁移只新增、不编辑；若恢复旧备份，应配套恢复相应应用版本，并明确核对其账本，不能拿新应用强行越过版本检查。

### 历史升级参考（不是批量重跑列表）

0001时区、0002改密时间、0003软删、0004版本、0005角色、0006单pending、0007消息、0008凭据版本。早于0008的库必须逐项核对缺失结构与每份旧SQL头部要求；0008重跑仍会清授权码。新工具故意不盲目重放这些带BEGIN/COMMIT或业务副作用的旧脚本。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`database init/db_init.py`](db_init.py) | `7fe7a09d405c` | L1–L59 |
| [`database init/full_init.sql`](full_init.sql) | `666636ff56c8` | L1–L206 |
| [`database init/migrate_0001_timestamptz.sql`](migrate_0001_timestamptz.sql) | `6bddef3865dd` | L1–L86 |
| [`database init/migrate_0002_password_changed_at.sql`](migrate_0002_password_changed_at.sql) | `d59858043773` | L1–L38 |
| [`database init/migrate_0003_diagram_deleted_at.sql`](migrate_0003_diagram_deleted_at.sql) | `cba56902df3e` | L1–L61 |
| [`database init/migrate_0004_diagram_version.sql`](migrate_0004_diagram_version.sql) | `a82a7b2a4357` | L1–L41 |
| [`database init/migrate_0005_user_role.sql`](migrate_0005_user_role.sql) | `ae5fd29e1f90` | L1–L53 |
| [`database init/migrate_0006_order_single_pending.sql`](migrate_0006_order_single_pending.sql) | `eba74f798bbd` | L1–L22 |
| [`database init/migrate_0007_support_messages.sql`](migrate_0007_support_messages.sql) | `312f75458084` | L1–L14 |
| [`database init/migrate_0008_credential_revision.sql`](migrate_0008_credential_revision.sql) | `ad4f7d2d7903` | L1–L6 |
| [`database init/migrate_0009_retire_demo.sql`](migrate_0009_retire_demo.sql) | `0e673c162089` | L1–L11 |
| [`database init/migrate_0010_payment_ledger.sql`](migrate_0010_payment_ledger.sql) | `90fb5053ca49` | L1–L75 |
| [`database init/seed_demo.sql`](seed_demo.sql) | `1efd73f0a51b` | L1–L23 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

专用目标确认 → 维护锁 → 基线/校验和检查 → SQL与账本单事务 → 提交 → 应用只读启动检查。默认不再有用户名/密码种子；开发演示需显式选择，不能由Compose或普通init暗中执行。

## 变更与验证

模型、新库SQL与增量迁移同步。`tests/test_db_admin.py` 自建一次性PG并使用非超级用户，覆盖拒绝覆盖、并发、事务锁、升级保留数据、失败回滚、校验和及管理员bootstrap；不连接传入的业务DSN。`tests/test_schema_sync.py` 仍只做部分结构对照，不是完整catalog等价证明。当前证据及剩余阻断见 [第二批交付](../review/RELEASE_BLOCKERS_PHASE2.md)。

## 0010：资金与交付合同升级

0009停写并备份数据库及storage后运行 `migrate --confirm-database 实际库名`，不要重跑init。0010保留旧状态/金额/流水，新增支付与交付列留NULL，不自动根据当前.env猜历史合同，不捏造旧收款凭证。新建payment_receipt的订单唯一/来源流水唯一及金额、币种、人工证据CHECK；payment_event记录尝试和核准行为。

PG触发器禁止已绑定合同覆盖及凭证/事件UPDATE或DELETE；数据库所有者仍可改触发器，不是WORM。纠错/退款要另走追加工作流，不能直接改旧账。新应用会要求账本包含0010。

旧0008基线列清单已固定，不随新ORM变化；has_transaction_control屏蔽PG引号、dollar函数体和嵌套注释，再检查顶层事务命令，允许函数内BEGIN，拒绝顶层同一行COMMIT。它不负责审计任意SQL，新迁移仍是必须人工审查的可信仓库代码。

历史已付款订单权益保留，首次交付前按[第三批的管理员核准接口](../review/RELEASE_BLOCKERS_PHASE3.md)核对原文件并绑定一次。禁止批量套用当前商品或把未确认旧流水标成真实收入。新库SQL/升级SQL均须由真PG验证触发器，SQLite/ORM建表不是替代证据。
