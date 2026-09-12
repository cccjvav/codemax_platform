# 数据库初始化与升级

## 模块职责

本目录包含**破坏性新库初始化**与**保留数据的增量迁移**，两者绝不能混用。
应用不会在启动时自动运行这些 SQL，也不会凭 ORM 定义自动把存量库升到新版本。

## 文件与入口

### db_init.py 与 full_init.sql

`db_init.py` 从执行目录的 `../.env` 读取 DB_HOST/PORT/NAME/USER/PASSWORD，不复用 app.Settings，也不使用 DATABASE_URL。因此按下面方式从本目录运行：

```bash
cd "database init"
python db_init.py
```

**只对新建或可丢弃数据库运行。** 目标库已存在时，脚本只跳过 CREATE DATABASE，仍会继续执行会 DROP TABLE 的 full_init，不是“已有库就安全跳过”。

| 函数 | 输入与返回 | 副作用/失败 |
| --- | --- | --- |
| `database_exists(conn, db_name)` | 维护库连接与库名 → bool | 查询 pg_database，值参数化 |
| `create_database(conn, db_name)` | 维护库连接与库名 → None | 使用 Identifier 处理库名；CREATE DATABASE 需 autocommit，不能放事务块 |
| `main` | 环境配置 → 初始化目标库 | 先连 postgres 维护库，再连目标库；建表成功提交，失败回滚；连接最终关闭；调用者必须有相应权限 |

full_init 重建 User、Order、Article、SysConfig、SysDiagram、OAuthClient、OAuthCode、SupportMessage 对应表、约束与种子数据。重复执行会成功，但会再次删除原数据；“可重复运行”不等于“无损升级”。
演示管理员/客户端只为开发初始化；生产更换或停用默认身份与凭据，不能仅修改网页文案。

### 存量升级清单

| SQL | 结构目的 | 特别注意 |
| --- | --- | --- |
| 0001 timestamptz | 历史时间字段转换为时区时间 | 先明确旧值以什么时区解释，不靠迁移猜测 |
| 0002 password_changed_at | 增加改密时间 | 与用户凭据验证一起部署 |
| 0003 diagram_deleted_at | 增加回收站时间 | 旧行按未删除处理 |
| 0004 diagram_version | 增加编辑版本 | 客户端需使用 ETag/If-Match |
| 0005 user_role | 增加角色 | 不自动把任意老用户提为管理员；由维护者核准 |
| 0006 order_single_pending | 核准清理重复 pending 后加部分唯一索引 | 清理 SQL 只是注释示例，不自动执行；先备份并人工核准旧订单 |
| 0007 support_messages | 新增持久会话消息表及去重约束 | 依赖已有用户表；客户/管理员权限由 API 另行检查 |
| 0008 credential_revision | 增加用户和授权码 credential_version | 清除尚未兑换的授权码；无 ver 的旧 JWT 失效；重复运行仍清码，不能称为零副作用 |

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`database init/db_init.py`](db_init.py) | `deb20fa1d930` | L1–L95 |
| [`database init/full_init.sql`](full_init.sql) | `3e52a5321c6f` | L1–L141 |
| [`database init/migrate_0001_timestamptz.sql`](migrate_0001_timestamptz.sql) | `6bddef3865dd` | L1–L86 |
| [`database init/migrate_0002_password_changed_at.sql`](migrate_0002_password_changed_at.sql) | `d59858043773` | L1–L38 |
| [`database init/migrate_0003_diagram_deleted_at.sql`](migrate_0003_diagram_deleted_at.sql) | `cba56902df3e` | L1–L61 |
| [`database init/migrate_0004_diagram_version.sql`](migrate_0004_diagram_version.sql) | `a82a7b2a4357` | L1–L41 |
| [`database init/migrate_0005_user_role.sql`](migrate_0005_user_role.sql) | `ae5fd29e1f90` | L1–L53 |
| [`database init/migrate_0006_order_single_pending.sql`](migrate_0006_order_single_pending.sql) | `eba74f798bbd` | L1–L22 |
| [`database init/migrate_0007_support_messages.sql`](migrate_0007_support_messages.sql) | `312f75458084` | L1–L14 |
| [`database init/migrate_0008_credential_revision.sql`](migrate_0008_credential_revision.sql) | `ad4f7d2d7903` | L1–L6 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

升级前备份并确认可恢复 → 停写/停止旧进程 → 核对已有迁移基线 → 按顺序执行缺失迁移 → 部署匹配版本 → 重新登录并验证。
已处于 0006 结构的用户本次只执行 0007/0008。不要在旧认证进程仍接流量时单独换表，再让新旧协议混跑。
用户会话、订单和消息是持久业务数据；本地测试库能重建不说明生产数据可丢弃。

## 变更与验证

模型结构变化同步修改 models.py、新库 SQL 和增量 SQL，不能只改一处。test_schema_sync 比较结构，真实 PostgreSQL 测试验证脚本执行与并发，旧库迁移回归验证数据保留；三者解决不同问题。
SQL 在专门测试库重复执行，不在生产库“验证幂等”。生产操作见 [部署指南](../docs/DEPLOY.md)；本轮结果见 [验收记录](../docs/SECOND_REPAIR_ACCEPTANCE.md)。
