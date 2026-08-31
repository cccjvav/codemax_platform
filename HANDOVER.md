# 交接摘要（HANDOVER）

> 给下一个编码会话 / 协作者的快速上手指南。更新日期：2026-08-31

## 1. 当前状态

**分支**：`arena/01a05890-codemax-platform`（本会话工作分支）
**待办（必须优先做）**：本地分支领先 `origin/main` 2 个提交，**尚未推送、未合并到 main**：

| 提交 | 内容 |
| --- | --- |
| `687e60e` | feat: 阶段一 —— 多模块工程骨架 + JWT 认证与 SSO 基础 |
| `48622b1` | feat: 补全 OAuth2 授权码 SSO 流程（authorize + token） |

**下一步操作**（新会话中执行）：
1. `git push origin arena/01a05890-codemax-platform`
2. 从该分支向 `main` 开 PR 并合并（上个会话用 `gh pr create --base main --head arena/...` 成功过）

**历史**：此前 `a38e52f`（文档+数据库初始化完善）、`e72e917`（Agent Skills 引入）已通过 PR #1 合并进 main。

## 2. 项目技术栈

- Python 3.11 + **FastAPI 0.104** + **SQLAlchemy 2.0（async）** + **asyncpg** + **PostgreSQL**
- 认证：JWT（python-jose）+ bcrypt（passlib 1.7.4 + bcrypt==4.0.1 固定版本）
- 配置：pydantic-settings 读 `.env`（模板 `.env.example`；`.env` 已被 gitignore）
- 测试：pytest + pytest-asyncio + httpx + aiosqlite（内存 SQLite，无需外部 DB）
- 虚拟环境：`.venv/`（已 gitignore）

## 3. 工程结构

```
main.py                    # FastAPI 入口（在仓库根，不是 app/ 下！）
app/
├── config.py              # Settings（读 .env，sqlalchemy_url 属性）
├── database.py            # async engine / SessionLocal / get_db 依赖
├── models.py              # User / Order / SysConfig / OAuthClient / OAuthCode
├── schemas.py             # Pydantic：RegisterIn / UserOut / TokenOut
├── security.py            # bcrypt 哈希 + JWT 生成/解析
├── deps.py                # get_current_user（双平台共用鉴权）
└── routers/
    ├── auth.py            # /auth/register | /auth/login | /auth/me
    ├── oauth.py           # /oauth/authorize | /oauth/token（授权码 SSO）
    ├── tools.py           # 工具平台受保护端点（SSO 验证）
    └── shop.py            # 商业平台受保护端点（SSO 验证）
tests/
├── conftest.py            # 内存 SQLite + 依赖覆盖 + 种子客户端(工厂函数)
├── test_auth.py           # 9 个用例
└── test_oauth.py          # 7 个用例
database init/
├── db_init.py             # 自动建库 + 建表（幂等）
└── full_init.sql          # 建表 + 种子数据（需与 app/models.py 保持一致！）
.claude/skills/            # Agent Skills（fastapi-python / python-testing / codemax-workflow）
ROADMAP.md                 # 五阶段开发路线图（勾选进度）
AGENTS.md                  # 仓库级工作流说明
```

## 4. 工作流铁律（用户明确要求，务必遵守）

1. **代码尽量简洁**：不写多余代码、不提前抽象、不加用不到的配置
2. **写完必须测试**：每次改动后运行 `./.venv/bin/python -m pytest -q`
3. **不过则迭代**：测试失败必须修复重跑直到全绿；禁止跳过/注释/降标

## 5. 常用命令

```bash
./.venv/bin/python -m pytest -q          # 跑测试（当前 16 个，应全绿）
./.venv/bin/python -m pytest -v tests/test_oauth.py::test_full_auth_code_flow  # 单测
cd "database init" && ../.venv/bin/python db_init.py   # 初始化 PG（幂等）
./.venv/bin/uvicorn main:app --reload    # 启动服务，http://localhost:8000/docs
```

## 6. 已踩过的坑（避免重犯）

- **`main.py` 在仓库根**，不是 `app/main.py` —— 导入用 `from main import app`
- **测试种子 ORM 对象不能用模块级共享**（跨测试复用会状态泄漏，串行跑挂单跑过）—— 用工厂函数每次新建
- **SQLAlchemy async + SQLite 测试**：`StaticPool` + `check_same_thread=False`，且 `Base.metadata.create_all` 在 fixture 里每次重建
- **passlib 1.7.4 与 bcrypt 4.x**：需固定 `bcrypt==4.0.1`，否则报错/警告
- `.env` 已从版本控制移除，新环境需 `cp .env.example .env`
- 种子 bcrypt 哈希：工具平台 secret `codemax-tools-secret`、商业平台 `codemax-shop-secret`（库内只存哈希，明文见 README）

## 7. 已完成 / 未完成（对应 ROADMAP.md）

**已完成（阶段一 ✅）**
- [x] S1-01 多模块工程 + PostgreSQL 基础表（sys_user / sys_order / sys_config）
- [x] S1-02 OAuth2 授权码 SSO 完整流程 + JWT（tools/shop 双平台共享登录态）

**未开始**
- 阶段二：工具矩阵（SQL DDL→ER 图 / LLM→Mermaid / Drawio 嵌入 / POI 导出 Word）+ SEO
- 阶段三：微信支付 NATIVE + 订单状态机 + 回调验签/解密/幂等 + OSS/COS 预签名 URL 一次性下载
- 阶段四：爬虫 + LLM 内容解析 + 三层智能客服（FAQ<80ms / BERT 路由 / RAG / 转人工）
- 阶段五：全链路测试 / 压测 / 部署上线

## 8. 建议的下一步

1. 推送并合并 2 个待推送提交（见第 1 节）
2. 开始**阶段三支付闭环**（订单表 `sys_order` 已就绪，状态机 pending→paid→downloaded）或**阶段二工具矩阵**（前端交互多、易出效果）
