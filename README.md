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

## 当前 API（阶段一：认证 + OAuth2 授权码 SSO）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register` | 注册（返回用户信息，不含密码） |
| POST | `/auth/login` | 登录（OAuth2 表单，返回 JWT access_token） |
| GET | `/auth/me` | 当前用户（需 Bearer Token） |
| GET | `/oauth/authorize` | **授权码端点**：已登录用户向第三方应用签发一次性 code（302 跳回调） |
| POST | `/oauth/token` | **令牌端点**：客户端用 code + client_secret 换取 access_token |
| GET | `/tools/ping` | 工具平台受保护端点（SSO 验证） |
| GET | `/shop/ping` | 商业平台受保护端点（SSO 验证） |
| GET | `/health` | 健康检查 |

> **SSO（OAuth2 授权码模式）流程**：用户登录认证中心拿会话 JWT → 携带 JWT 访问
> `/oauth/authorize?response_type=code&client_id=...&redirect_uri=...&state=...`
> → 认证中心校验后 302 跳回回调地址携带一次性 code → 客户端用 `/oauth/token` 以
> `code + client_secret` 换取 access_token → 用 access_token 访问双平台受保护资源。
> 授权码一次性、10 分钟有效；客户端密钥在库中只存 bcrypt 哈希。

### 演示客户端（种子数据，仅演示用）

| client_id | client_secret | 回调地址 |
| --- | --- | --- |
| `tools` | `codemax-tools-secret` | `https://tools.codemax.top/callback` |
| `shop` | `codemax-shop-secret` | `https://shop.codemax.top/callback` |

## Agent Skills

本仓库内置 Agent Skills（`.claude/skills/`，SKILL.md 开放标准，Claude Code / Cursor / Copilot 等兼容）：

| Skill | 作用 |
| --- | --- |
| `codemax-workflow` | **项目专属工作流**：代码尽量简洁、写完必须测试、不过则迭代（硬性要求） |
| `fastapi-python` | FastAPI 开发规范（异步、Pydantic、函数式简洁写法） |
| `python-testing` | pytest 测试规范（TDD、fixtures、mock、覆盖率） |

## 文档

- 📋 开发路线图（To-Do List）：[ROADMAP.md](./ROADMAP.md)
