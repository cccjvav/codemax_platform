# AGENTS.md

codemax_platform — FastAPI + SQLAlchemy 2.0(async) + PostgreSQL 的毕设服务平台。

## 硬性工作流（用户要求）

1. **代码尽量简洁** —— 不写多余代码、不提前抽象、不加用不到的配置。
2. **写完必须测试** —— 每次代码改动后运行 `.venv/bin/python -m pytest -q`。
3. **不过则迭代** —— 测试失败必须修复后重跑，直到全绿；禁止跳过/注释/降标。

## 项目速览

- 数据库初始化：`cd "database init" && python db_init.py`（幂等；测试账号 admin/123456）
- 配置：`.env`（模板 `.env.example`），`.env` 不提交
- Skills：`.claude/skills/`（fastapi-python、python-testing、codemax-workflow）
- 详细路线图：`ROADMAP.md`
