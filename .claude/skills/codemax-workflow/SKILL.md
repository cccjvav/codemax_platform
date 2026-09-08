---
name: codemax-workflow
description: 本项目（codemax_platform，FastAPI + PostgreSQL 毕设服务平台）的编码工作流规范。任何写代码/改代码/加功能的请求都应遵守：代码尽量简洁、写完必须测试、测试不过则迭代直到通过。
---

# codemax_platform 项目工作流

## 铁律（用户明确要求，必须遵守）

1. **代码尽量简洁**：不写多余代码、不提前抽象、不加用不到的配置；能用标准库/框架原生能力就不造轮子。
2. **写完必须测试**：任何代码改动（含新功能、修复、重构）完成后，必须运行相关测试验证。
3. **不过则迭代**：测试失败必须修复后重跑，直到全绿；不得跳过、注释掉或降低标准。

## 项目技术栈与约定

- Python 3.11 + **FastAPI** + **SQLAlchemy 2.0（async）** + **asyncpg** + **PostgreSQL**
- 密码哈希 bcrypt（passlib）；认证 JWT（python-jose）
- 数据库初始化：`cd "database init" && python db_init.py`（自动建库建表；测试账号 admin/123456）
  ⚠️ **只对空库安全** —— `full_init.sql` 开头是 `DROP TABLE ... CASCADE`，对已有数据的库跑它等于清库。
  「CI 连跑两遍验证幂等」验的是**空库上的 schema 幂等**，不是「对已有数据安全」，两者别混。
- 配置在 `.env`（模板 `.env.example`）；`.env` 已被 gitignore，不得提交
- 虚拟环境 `.venv/`（已被 gitignore）

## 开发 → 测试 → 迭代 标准循环

1. 写代码（保持简洁，遵循 `fastapi-python` skill）
2. 安装依赖：`./.venv/bin/pip install -r requirements.txt`
3. 写/更新测试（pytest，遵循 `python-testing` skill）
4. 跑测试：
   ```bash
   ./.venv/bin/python -m pytest -q
   ```
5. 失败 → 读报错 → 修复 → 重跑；通过 → 结束。

## 需要数据库的测试

- 本地启动 PostgreSQL 后执行 `database init/db_init.py` 建库建表；
- 或用嵌入式 PG（`pip install pgserver`）做临时验证，用完即停。

## Do This / Not This

### Do This
- 改完代码立刻跑测试，并在回复中贴出测试结果（通过/失败）
- 测试失败时先复现、再修复、再重跑，循环直到通过
- 优先 async 写法（`async def` + `await`），避免阻塞 I/O

### Not This
- 不要"写完代码但没测试就说完成"
- 不要用 `# 跳过`、`pytest.mark.skip` 掩盖失败
- 不要提交多余文件（如 `.env`、`__pycache__`、`.venv`）
