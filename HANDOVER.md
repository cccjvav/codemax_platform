# 交接摘要（HANDOVER）

> 给下一个编码会话 / 协作者的快速上手指南。更新日期：2026-09-01（本文件已重写，第 1 节旧内容作废）

## 1. 当前状态

- **main**：`9622a38`（含 PR #1、PR #2 合并进来的阶段一全部内容 —— 旧的"推送 687e60e / 48622b1"说法已作废，那两个提交不在本仓库历史里）
- **本会话分支**：`arena/01a0599b-codemax-platform`，HEAD `9b3ec21`，已 push（本地 = 远端）
- **PR #3**：`feat: 阶段二 —— 工具矩阵（ER 图 / LLM→Mermaid / Word 导出 / Drawio）+ SEO SSR`，**state=OPEN，未合并**
  - 7 个提交，按 ROADMAP 子项分开，可逐个回滚
  - 用户要求：**未经他明确授权不得合并**（合并后沙箱内后续改动无法同步，等于无效工作）
- **测试基线**：`.venv/bin/python -m pytest -q` → **89 passed**（唯一 warning 是 passlib 的 `crypt` 弃用，无害）
- **合并 PR #3 之后要做的事**：删除 main 上 4 个一次性传输文件
  `PHASE1_TRANSFER.txt` / `APPLY_INSTRUCTIONS.md` / `PHASE2_TRANSFER.txt` / `APPLY_PHASE2.md`

## 2. 技术栈

Python 3.11 + FastAPI 0.104 + SQLAlchemy 2.0（async）+ asyncpg + PostgreSQL；
Jinja2 SSR（页面外壳与 TDK，**不引入 Node 运行时 / Nuxt / Next**）；
JWT（python-jose）+ bcrypt（passlib 1.7.4 + bcrypt==4.0.1 固定版本）；
`python-docx`（Word 导出）、`httpx`（LLM 调用 / 测试客户端）。
测试：pytest + pytest-asyncio + httpx + aiosqlite（内存 SQLite，无需外部库）。

**技术选型（已定，不要重新论证）见 `AGENTS.md`**：Apache POI → `python-docx`；
HttpClient + Jsoup → `httpx` + `BeautifulSoup4`；动态页面阶段四再定 Selenium/Playwright。

## 3. 工程结构

```
main.py                    # FastAPI 入口（在仓库根，不是 app/ 下！）
app/
├── config.py              # Settings（读 .env）：DB_* / SECRET_KEY / LLM_* / SITE_BASE_URL
├── database.py            # async engine / SessionLocal / get_db
├── models.py              # User / Order / SysConfig / SysDiagram / OAuthClient / OAuthCode
├── schemas.py             # Pydantic 模型
├── security.py            # bcrypt 哈希 + JWT 生成/解析
├── deps.py                # get_current_user（双平台共用鉴权）
├── site.py                # ★ TOOLS/HOME/PAGES 清单：一处新增，路由+导航+sitemap 自动跟上
├── routers/
│   ├── auth.py            # /auth/register | /auth/login | /auth/me
│   ├── oauth.py           # /oauth/authorize | /oauth/token（授权码 SSO）
│   ├── tools.py           # /tools/ping(鉴权) + er-diagram / mermaid / word-export(公开)
│   ├── diagrams.py        # /diagrams CRUD（★ 全部需鉴权，只能读写自己的）
│   ├── shop.py            # /shop/ping（SSO 验证）
│   └── site.py            # 页面路由（由 PAGES 生成）+ /sitemap.xml + /robots.txt
├── tools/
│   ├── sql_ddl.py         # DDL 解析（纯标准库）
│   ├── llm.py             # OpenAI 兼容客户端（可注入）+ Mermaid 生成
│   └── word.py            # DDL → Word 数据字典
├── static/er.js           # ER 图 D3.js 渲染（layoutEr 是纯函数，node 可直接 require）
└── templates/             # base / index / er / mermaid / drawio（Jinja2 SSR）
tests/                     # 9 个测试文件，74 用例
database init/             # db_init.py（幂等）+ full_init.sql（★ 必须与 models.py 同步）
scripts/check_schema_pg.mjs # 可选深度体检：用 WASM 版真 PostgreSQL 执行 full_init.sql
```

页面地址：`/`（工具清单）、`/tools/er`、`/tools/mermaid`、`/tools/drawio`、`/sitemap.xml`、`/robots.txt`。

## 4. 工作流铁律（用户明确要求）

1. **代码尽量简洁**：不写多余代码、不提前抽象、不加用不到的配置
2. **写完必须测试**：每次改动后 `.venv/bin/python -m pytest -q`
3. **不过则迭代**：失败必须修复重跑到全绿；禁止跳过/注释/降标
4. **每次 `git commit` 之后立刻 `git push`** —— 阶段一曾因此差点永久丢代码
5. **不要擅自合并 PR**：等用户明确发话

## 5. 常用命令

```bash
.venv/bin/python -m pytest -q                        # 跑测试（当前 89 个，应全绿）
.venv/bin/python -m pytest tests/test_sql_ddl.py -v  # 单文件
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000  # 起服务（沙箱预览需 0.0.0.0）
cd "database init" && ../.venv/bin/python db_init.py   # 初始化 PG（幂等，需真库）
node scripts/check_schema_pg.mjs <pglite 包路径>        # 无 PG 环境时体检建表脚本
```

## 6. 已踩过的坑（避免重犯）

- **`main.py` 在仓库根**，不是 `app/main.py` —— 导入用 `from main import app`
- **`app/models.py` 与 `database init/full_init.sql` 必须同步**：`tests/test_schema_sync.py`
  会逐表逐列比对，改一边不改另一边就是红灯
- **回归测试读项目自身文件**：`test_sql_ddl.py` / `test_word_export.py` / `test_er_page.py`
  以 `database init/full_init.sql` 为输入。**期望值一律从解析结果推导，不要硬编码表数/外键数**，
  否则加表就误报（加 `sys_diagram` 时已踩过一次）
- **测试种子 ORM 对象不能用模块级共享**（状态泄漏）—— 用工厂函数每次新建
- **SQLAlchemy async + SQLite 测试**：`StaticPool` + `check_same_thread=False`，fixture 里每次重建表
- **passlib 1.7.4 与 bcrypt 4.x**：需固定 `bcrypt==4.0.1`
- **LLM 客户端必须可注入**（`Depends(get_llm)` + `generate_mermaid(text, llm=...)`），否则测试真打网络
- **沙箱环境**：无 PostgreSQL、apt 的 Debian 源不可达（只有 PyPI/GitHub 代理）；
  `node` 可用（测试期允许），`gh pr edit` 会被 GitHub 的 Projects-classic 弃用报错挡住且**静默失败**，
  改用 `gh api -X PATCH repos/<owner>/<repo>/pulls/<n>`；`pkill -f <模式>` 的模式若出现在自己的
  命令行里会**杀掉自己**（用 `mock_[l]lm.py` 这种写法规避）；uvicorn 访问日志里挂载子应用显示的是
  **去掉挂载前缀后**的路径（`/static/er.js` 会记成 `/er.js`），不是 bug
- **`sql_ddl.py` 的一切结构性判断（括号配对、逗号切分、找 CREATE TABLE / COMMENT ON）
  都必须先认得字符串字面量与注释**，否则 `COMMENT 'it\'s'`（MySQL 反斜杠转义）、
  `DEFAULT 'a--b'`、`COMMENT 'x /* y'`、`/* -- x */`、字符串里的 `CREATE TABLE`、
  `"index"` / `` `key` `` 这类列名都会解析错乱（曾导致整张表丢失）。改这个文件前先看
  模块 docstring 的清单，改完必须跑 `tests/test_sql_ddl.py`
- **改解析/生成逻辑前先写"旧代码会红"的测试**：本项目的 bug 回归测试都验证过
  "在修复前的实现上确实失败"，避免写出永远绿的假测试
- **浅克隆会让 `git merge-base --is-ancestor` 误判**：先 `git rev-parse --is-shallow-repository`

## 7. 已完成 / 未完成（对应 ROADMAP.md）

**已完成**
- 阶段一：S1-01 工程骨架 + PostgreSQL 基础表；S1-02 OAuth2 授权码 SSO + JWT
- 阶段二：S2-01-1 ER 图（DDL 解析 + D3.js）；S2-01-2 LLM→Mermaid；S2-01-3 Drawio 嵌入 + 云端保存；
  S2-01-4 python-docx 导出 Word；S2-02-1 SEO（Jinja2 SSR + TOOLS 清单 + sitemap/robots + TDK）

**未开始**
- S2-02-2 引流→变现转化路径（按选型本轮跳过；可复用阶段一 SSO 跳 `/oauth/authorize?client_id=shop`）
- 阶段三：微信支付 NATIVE + 订单状态机 + 回调验签/AES-GCM 解密/幂等 + OSS/COS 预签名 URL 一次性下载
- 阶段四：爬虫（httpx + BeautifulSoup4）+ LLM 内容解析 + 三层智能客服
- 阶段五：全链路测试 / 压测 / 部署上线

## 8. 建议的下一步

1. 等用户授权后合并 PR #3，随即删掉 4 个一次性传输文件
2. **阶段三 S3-01 支付闭环**（P0，`sys_order` 表已就绪）。注意沙箱没有商户号/证书/公网回调，
   只能做到「签名验证 + AES-GCM 解密 + 幂等」的单元级验证，真实下单跑不通 —— 开工前先和用户对齐验收口径
3. 或补 S2-02-2 转化路径（工作量小，复用现成 SSO）
