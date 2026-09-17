---
name: schema-sync
description: ORM、全量建表、增量迁移与业务约束同步；只在专用可丢弃库验证破坏性脚本。
version: 2.3
---

# 数据结构同步

先服从 AGENTS 的审批与数据边界。`app/models.py` 与 `database init/full_init.sql` 要同步；存量升级还要有对应增量迁移，不能用 full_init 代替升级。

## 修改前后逐项核对

- [ ] 阅读实际模型、SQL、路由事务和测试：列类型/长度、空值、默认值、索引、外键与业务状态一起核，不只比名字。
- [ ] `Mapped` 与 `mapped_column` 一致，时间列保持时区语义；外键引用和删除行为明确。
- [ ] 新表同步full_init的建表依赖顺序及必要增量迁移，按当前外键拓扑检查，不复制过期表名顺序。
- [ ] **当前full_init无DROP且拒绝非空库**；正常入口用维护CLI，第二次必须拒绝并保留数据。历史含DROP版本不得回退执行。迁移必须验证事务回滚、跨连接锁、校验和、升级保留数据及不重放。
- [ ] 增量迁移说明前置版本、备份/停写要求、会话失效与重复执行副作用；不能仅凭 IF EXISTS 就称完全幂等。
- [ ] fixture、权限/金额/状态/并发等业务用例同步，关键约束显式断言，而不是认为跑真 PG 就自动覆盖所有约束。
- [ ] 人工讲解、目录 README、架构/迁移文档和必要 TD 同步；当前阶段记结果，不向 HANDOVER 失效章节追加固定表数。

## 现有测试能证明什么

`tests/test_schema_sync.py` 比表集合与列集合，另有 `sys_diagram` 列清单、ORM 时间列时区与 SQL TIMESTAMPTZ/默认时间表达式检查。它不是完整 DDL 等价性证明，不覆盖任意类型、长度、全部默认值/索引/外键；新增这些约束要补对应测试。

```bash
.venv/bin/python -m pytest tests/test_schema_sync.py -q
.venv/bin/python -m pytest -q
```

以上是 Linux 沙箱示例，数量以本次结果为准。真 PostgreSQL 必须再验，步骤与专用测试角色见 `docs/ACCEPTANCE_GUIDE.md`；Windows 的 CMD/Conda/密码交互方式见 `Windows新手逐步验收.md`。绝不将业务 DATABASE_URL 复制给 TEST_DATABASE_URL，不在 Skill 示例硬编码超级用户业务连接串。

收尾按 codemax-workflow 与 finish-subitem，核对最终 SHA 全部 CI jobs。表结构变更需要的审批不因执行本 Skill 而自动获得。

资金与交付专项：使用独立数据库连接验证原子提交，不用共享单连接模拟隔离。旧基线列集合固定；新SQL函数体与顶层事务控制分别识别。验证真实迁移触发器、同单冲突/跨单唯一、坏快照恢复与历史核准，不用静默重绑或日志替代凭证来让测试通过。
