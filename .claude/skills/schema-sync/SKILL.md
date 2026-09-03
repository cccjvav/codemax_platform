---
name: schema-sync
description: >
  改数据库表结构时的跨文件同步检查单：ORM 模型、建表脚本、测试三处必须一起改。
  Trigger: 新增列、删除列、重命名列、新增表、改列类型、加索引或外键。
---

# schema-sync

> **本项目的表结构有两个权威来源**：`app/models.py`（ORM）与
> `database init/full_init.sql`（建表脚本）。两边手工同步，靠
> `tests/test_schema_sync.py` 兜底。**它只比对「表名 + 列名」**
> （见该文件的 `_sql_schema()` / `_orm_schema()`），
> **不比类型、长度、NOT NULL、DEFAULT、索引** —— 类型写错测试不会报，必须自己核。

## 检查单（按顺序）

### 1. ORM 模型 — `app/models.py`
- [ ] `Mapped[...]` 类型注解与 `mapped_column(...)` 一致
- [ ] 新列要不要 `server_default=func.now()`（时间列的既有写法）
- [ ] 外键用 `ForeignKey("表.列")`，不要手写裸列名
- [ ] 列名 snake_case，与 SQL 侧完全一致（大小写也要一致）

### 2. 建表脚本 — `database init/full_init.sql`
- [ ] `CREATE TABLE` 里加同名列，类型与 ORM 对得上（`VARCHAR(n)` 的长度也要对）
- [ ] **新表**要在文件开头的 `DROP TABLE IF EXISTS ... CASCADE` 段落里补一行，
      且**子表排在父表前面**（现有顺序：article → diagram → oauth_code →
      oauth_client → order → config → user）
- [ ] 索引写在对应 `CREATE TABLE` 之后，命名 `idx_<表>_<列>`
- [ ] 保持**幂等**：CI 会把整个脚本**连跑两遍**，第二遍必须同样成功
      （`.github/workflows/ci.yml` 的「建表脚本在真 PostgreSQL 上执行」那一步）

### 3. 测试
- [ ] `tests/test_schema_sync.py::test_columns_match_for_every_table` 会自动比列名，
      两边不同步这里立刻红
- [ ] ⚠️ `test_sys_diagram_shape` **硬编码了 sys_diagram 的完整列清单**——
      动这张表必须同时改那行断言，否则会红
- [ ] 用到该列的测试 fixture（`tests/conftest.py` 里的建对象辅助）要跟上
- [ ] 新列若参与业务判断（状态、金额、时间），补一条针对它的用例

### 4. 文档
- [ ] 若这次改动是个取舍（例如「类型用 VARCHAR 不用 PG enum」），
      去 `TECH_DECISIONS.md` 追加 TD-xx
- [ ] `HANDOVER.md` §3 工程结构里若提到表数量，同步更新

## 验证

```bash
.venv/bin/python -m pytest tests/test_schema_sync.py -q
.venv/bin/python -m pytest -q          # 全量：417 passed, 2 skipped
```

真库那一遍（起库配方见 `HANDOVER.md` §9）——**改表结构必须跑**，
因为 SQLite 对类型和约束比 PostgreSQL 宽松得多：

```bash
TEST_DATABASE_URL="postgresql+asyncpg://postgres@/codemax_test?host=/tmp/pgdata" \
  .venv/bin/python -m pytest -q        # 418 passed, 1 skipped
```

## 已知会漏的（别指望测试）

| 改了什么 | 测试会不会报 |
| --- | --- |
| 加/删/改**列名** | ✅ 会 |
| 加/删**表** | ✅ 会 |
| 改**列类型 / 长度** | ❌ 不会 |
| 改 `NOT NULL` / `DEFAULT` | ❌ 不会 |
| 加/删**索引** | ❌ 不会 |
| 外键指向错表 | ❌ 不会（SQLite 默认不校验外键） |

下面这几行只能靠真 PostgreSQL 跑一遍来兜。
