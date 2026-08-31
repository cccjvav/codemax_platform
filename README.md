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

## 文档

- 📋 开发路线图（To-Do List）：[ROADMAP.md](./ROADMAP.md)
