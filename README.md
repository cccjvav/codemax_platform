# codemax_platform

毕设服务平台 (codemax.top)：免费工具平台 + 商业平台（双平台），统一认证（SSO）、支付闭环、云存储安全下载、AI 内容解析与智能客服。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2.0（异步）+ asyncpg
- 数据库：PostgreSQL
- 认证：JWT（python-jose）+ bcrypt（passlib）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制 .env.example 为 .env 并填入实际配置
cp .env.example .env

# 3. 初始化数据库（自动建库建表，幂等；测试账号 admin/123456）
cd "database init" && python db_init.py && cd ..

# 4. 启动服务
uvicorn main:app --reload   # http://localhost:8000/docs
```

## 运行测试

```bash
./.venv/bin/python -m pytest -q
```

## 当前 API（阶段一：认证与 SSO 骨架）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register` | 注册（返回用户信息，不含密码） |
| POST | `/auth/login` | 登录（OAuth2 表单，返回 JWT access_token） |
| GET | `/auth/me` | 当前用户（需 Bearer Token） |
| GET | `/tools/ping` | 工具平台受保护端点（SSO 验证） |
| GET | `/shop/ping` | 商业平台受保护端点（SSO 验证） |
| GET | `/health` | 健康检查 |

> SSO 说明：统一认证中心签发 JWT，工具平台与商业平台共享同一登录态（一次登录，全平台可用）。
> 阶段一基于 OAuth2 密码模式 + JWT 无状态 Token 实现；完整的 OAuth2 授权码流程（第三方应用授权）作为后续迭代。

## Agent Skills

本仓库内置 Agent Skills（`.claude/skills/`，SKILL.md 开放标准，Claude Code / Cursor / Copilot 等兼容）：

| Skill | 作用 |
| --- | --- |
| `codemax-workflow` | **项目专属工作流**：代码尽量简洁、写完必须测试、不过则迭代（硬性要求） |
| `fastapi-python` | FastAPI 开发规范（异步、Pydantic、函数式简洁写法） |
| `python-testing` | pytest 测试规范（TDD、fixtures、mock、覆盖率） |

## 文档

- 📋 开发路线图（To-Do List）：[ROADMAP.md](./ROADMAP.md)
