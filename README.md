# codemax_platform

毕设服务平台 (codemax.top)：免费工具平台 + 商业平台（双平台），统一认证（SSO）、支付闭环、云存储安全下载、AI 内容解析与智能客服。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2.0（异步）+ asyncpg
- 数据库：**PostgreSQL**
- 认证：JWT + bcrypt

## 数据库初始化（PostgreSQL）

1. 安装依赖：`pip install -r requirements.txt`
2. 复制 `.env.example` 为 `.env` 并填入实际配置（`DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD`）
3. 执行初始化：

```bash
cd "database init"
python db_init.py
```

脚本会自动：连接维护库 `postgres` → 创建目标库 `codemax_db`（若不存在）→ 执行 `full_init.sql` 建表并插入测试账号（admin / 123456）。可重复执行（幂等）。

## Agent Skills

本仓库内置 Agent Skills（`.claude/skills/`，SKILL.md 开放标准，Claude Code / Cursor / Copilot 等兼容）：

| Skill | 作用 |
| --- | --- |
| `codemax-workflow` | **项目专属工作流**：代码尽量简洁、写完必须测试、不过则迭代（硬性要求） |
| `fastapi-python` | FastAPI 开发规范（异步、Pydantic、函数式简洁写法） |
| `python-testing` | pytest 测试规范（TDD、fixtures、mock、覆盖率） |

## 文档

- 📋 开发路线图（To-Do List）：[ROADMAP.md](./ROADMAP.md)
