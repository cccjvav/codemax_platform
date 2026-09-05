# 交接摘要（HANDOVER）

> 给下一个编码会话 / 协作者的快速上手指南。更新日期：2026-09-01（本文件已重写，第 1 节旧内容作废）

## 1. 当前状态

- **main**：`9622a38`（含 PR #1、PR #2 合并进来的阶段一全部内容 —— 旧的"推送 687e60e / 48622b1"说法已作废，那两个提交不在本仓库历史里）
- **本会话分支**：`arena/01a0599b-codemax-platform`（会话固定，不要切别的分支）。
  **这里刻意不写 HEAD 的 commit** —— 每提交一次它就过期，写过期的 SHA 比不写更糟。
  要确认当前状态就跑：`git log --oneline -1` 与 `git ls-remote origin arena/01a0599b-codemax-platform`，
  两者一致即「本地 = 远端」。
- **PR #3**：`feat: 阶段二 工具矩阵+SEO、阶段三 支付+下载防护、阶段四 内容冷启动、限流、CI、真库集成测试；fix: 解析器 13 个 bug`，**state=OPEN，未合并**
  - 28 个提交，按 ROADMAP 子项分开，可逐个回滚（会话固定在此分支，故子项累积在同一个 PR）
  - 用户要求：**未经他明确授权不得合并**（合并后沙箱内后续改动无法同步，等于无效工作）
- **测试基线**（2026-09-05 实测）：`.venv/bin/python -m pytest -q` → SQLite：**532 passed + 4 skipped**；真 PostgreSQL 16.2：**534 passed + 2 skipped** （3 条 skip 是真库专用的并发用例，SQLite 单连接下测不了，见 TD-199）
  另有 **GitHub Actions CI**（`.github/workflows/ci.yml`）：每次 push / PR 自动跑三个 job —— 静态检查（ruff）、SQLite、真 PostgreSQL 16；PG job 还把建表脚本连跑两遍验证幂等（已实跑通过）；actions 已升到 `checkout@v7` / `setup-python@v7`（Node 20 弃用告警已消，见 TD-144）。本会话的 GitHub App 已于 2026-09-01 拿到 Workflows 写权限，workflow 改动可直接 push
  （跳过的那条是真并发测试，SQLite 的 StaticPool 复现不了竞态，见 TD-85）（唯一 warning 是 passlib 的 `crypt` 弃用，无害）
- **用户环境是 Windows + cmd.exe**：需要他执行命令时，必须给 cmd 语法——分行写、不用 shell 通配符展开（`git am dir\000*.patch` 在 cmd 里不可靠，要逐个列文件名）、不用 `ls`/`cat`/`grep`（对应 `dir`/`type`/`findstr`）、路径用反斜杠、venv 里的解释器是 `.venv\Scripts\python.exe` 而不是 `.venv/bin/python`。（本文档与提交信息里的 `.venv/bin/python` 都是**沙箱内**的路径，不是给他用的。）
- **一次性传输文件已删除**：`PHASE1_TRANSFER.txt` / `APPLY_INSTRUCTIONS.md` /
  `PHASE2_TRANSFER.txt` / `APPLY_PHASE2.md` 已在本分支删掉（它们描述的任务全部完成，
  继续留着只会误导接手的人）。PR #3 合并后 `main` 上也会一并消失，无需再单独处理。

## 2. 技术栈

Python 3.11 + FastAPI 0.104 + SQLAlchemy 2.0（async）+ asyncpg + PostgreSQL；
Jinja2 SSR（页面外壳与 TDK，**不引入 Node 运行时 / Nuxt / Next**）；
JWT（python-jose）+ bcrypt（passlib 1.7.4 + bcrypt==4.0.1 固定版本）；
`python-docx`（Word 导出）、`httpx`（LLM 调用 / 测试客户端）。
测试：pytest + pytest-asyncio + httpx + aiosqlite（内存 SQLite，无需外部库）。

**技术选型（已定，不要重新论证）见 `AGENTS.md`**：Apache POI → `python-docx`；
HttpClient + Jsoup → `httpx` + `BeautifulSoup4`；动态页面**已选 Playwright**（TD-03/191，可选依赖）。

**实现层面的取舍（160 条，带编号 TD-xx）集中在 `TECH_DECISIONS.md`**，
其中开头的「上线阻塞项」表原有 8 条，已解决 6 条（划掉留痕），**现仅剩 2 条**：TD-113（微信支付未真机联调）、TD-124（模拟支付通道误开＝免费发货，已由启动自检大幅缓解）；
「限流」「CI」「真库集成测试」三条已解决（TD-15 / TD-84 / TD-80）。
「真库集成测试」那条已经解决（TD-80）。

## 3. 工程结构

```text
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
tests/                     # 35 个测试文件（37 个 .py），536 用例
database init/             # db_init.py + full_init.sql（★ 必须与 models.py 同步；开头是 DROP TABLE ... CASCADE，**只对空库安全**）
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
.venv/bin/python -m pytest -q                        # 跑测试（536 个：SQLite 上 532 绿 + 4 跳过）
.venv/bin/python -m pytest tests/test_sql_ddl.py -v  # 单文件
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000  # 起服务（沙箱预览需 0.0.0.0）
cd "database init" && ../.venv/bin/python db_init.py   # 初始化 PG（需真库；**会 DROP 重建，只对空库安全**）
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
- **微信支付的签名必须覆盖「实际发出的字节」**：先 `json.dumps` 成字符串 → 拿这串签名 →
  把同一串 `encode` 后用 `content=` 发出。改用 `httpx` 的 `json=` 会让它重新序列化
  （键序 / 空格 / 中文转义），本地照样 200，线上必然验签失败。
  `test_native_prepay_sends_request_whose_bytes_match_the_signature` 守着这条（已用变异验证过）
- **沙箱里没有商户号、API 证书与公网回调地址**：支付只能做算法级验证
  （自签 RSA 密钥验签名 + `httpx.MockTransport` 断言请求），真机联调是上线前必做项（TD-113）
- **微信回调的应答体不是 FastAPI 默认格式**：验签失败要回 `{"code":"FAIL","message":"..."}` + 4XX/5XX，
  用 `HTTPException` 会发出 `{"detail": ...}`；而且 **4XX/5XX 会被微信重推**，所以"重试也没用"的情况
  （非支付成功通知、已处理过）必须回 200。回调验签要用 `await request.body()` 的**原始字节**
- **FastAPI 的 `dependencies=[...]` 要传 `Depends(...)` 对象，不是裸函数**：
  传裸函数会在导入期就炸 `AttributeError: 'function' object has no attribute 'dependency'`，
  整个 conftest 都导入失败（限流挂载时踩过）
- **限流不能无条件信任 `X-Forwarded-For`**：那是客户端可以随便填的头，信了等于攻击者
  每次换一个 XFF 就有无限配额。默认只取 socket 对端地址，部署在可信反代后才打开
  `TRUST_PROXY_HEADERS`（TD-142）
- **别让 LLM 直接「提取正文」**：它会改写、删节甚至杜撰原文，而且每次结果都不一样、
  没法写回归测试。正确做法是让模型**只指认 CSS 选择器**，正文仍由 BeautifulSoup 按选择器取
  （`app/tools/extract.py`）
- **HTTP 请求头只能 latin-1 编码**：`User-Agent` 里写中文会在发请求时抛 `UnicodeEncodeError`
  （爬虫模块踩过）。中文说明放注释或正文，不要放头里
- **任何"让服务器去访问用户给的 URL"的功能都必须防 SSRF**：只允许 http/https，
  并把解析出的**每一个** IP 都用 `ipaddress.is_global` 检查（`169.254.169.254` 是云厂商元数据）
- **测试连真 PostgreSQL 必须用 `NullPool`**：asyncpg 的连接绑死在创建它的事件循环上，
  而 pytest-asyncio 每个用例开一个新 loop；用默认连接池会复用到上个 loop 的连接，
  报 `got Future attached to a different loop`（SQLite 因为是 StaticPool 单连接才没暴露）
- **`cryptography` 的 `InvalidTag` 不是 `ValueError` 子类**（实测 MRO 只有 Exception）：
  解密失败想统一成业务异常，必须显式 `except InvalidTag`，光catch ValueError 会漏
- **浅克隆会让 `git merge-base --is-ancestor` 误判**：先 `git rev-parse --is-shallow-repository`

### S2-02-2 这一轮新踩的（2026-09-05）

1. **`base.html` 的共享脚本必须放在 `<main>` **之前****。
   页面脚本写在 `{% block content %}` 里，文档顺序上**先执行**；
   共享模块若定义在 `</body>` 前，页面脚本引用它就是 `CodeMaxAuth is not defined`。
   这个 bug 在浏览器里才会暴露，是 `tests/test_auth_cookie.py` 那条 **node 真跑测试**抓到的
   —— 又一次证明「测试要真的执行前端代码」这条要求的价值。

2. **Jinja 在 HTML 注释里照样解析标签。**
   我在注释里写了字面量 `{% block content %}` 当说明，结果整个模板报
   `Unexpected end of template ... needs to be closed is 'block'`。注释里别写 Jinja 标签。

3. **`auth_headers(client)` 会在同一个 client 上留下 cookie。**
   它内部就是 `client.post("/auth/login", ...)`，Set-Cookie 进了 client 的 cookie jar。
   所以「先登录拿 header、再去掉 header 测 401」这种写法**测不出来**（我第一版就返回了 200）。
   要测未登录，必须**直接造数据**、完全不碰 client。

4. **node 桩里 `global.window` 必须就是 `global` 本身。**
   浏览器里 window 即全局对象；桩成独立对象时，`window.CodeMaxAuth = ...` 只是往那个对象挂属性，
   页面脚本用裸标识符 `CodeMaxAuth` 取不到。

5. **往 `base.html` 加内联脚本会破坏 OAuth 同意页的零脚本不变式。**
   同意页是发放授权码的安全关键页，`tests/test_oauth_consent.py` 断言它渲染后一个 `<script>` 都没有。
   解法不是放宽测试，而是：共享模块做成外部文件 `app/static/auth.js`（走 `script-src 'self'`，
   连 `unsafe-inline` 都不需要）+ `base.html` 加 `{% if auth_ui %}` 开关，同意页传 `False`（TD-204）。

6. **测微信支付分支必须先桩掉 `pay_config`。**
   沙箱没有商户号，`pay_config().configured` 是 False，`POST /shop/orders` 会先撞 503 门禁，
   根本走不到 `native_prepay`，看起来像"二维码功能坏了"。

7. **`POST /shop/download/{order_no}` 是一次性的，绝不能拿它当状态查询。**
   它成功后订单永久变 `downloaded`。查状态只能走只读的 `GET /shop/orders/{order_no}`。
   `tests/test_shop_page.py::test_status_polling_does_not_burn_the_one_time_download` 钉住这条。

### S4-02-5 / S5-04 这一轮新踩的（2026-09-05）

1. **`/embeddings` 只保证每条带 `index`，不保证数组有序。**
   语料和向量错一位的后果是「检索永远返回错的那条」，而它**不会抛异常**，只会安静地答错。
   必须按 `index` 排序后再取，并断言条数与输入相等。

2. **语义索引是进程内全局变量 ⇒ 测试必须显式隔离。**
   `warm_semantic_index()` 写的 `_SEMANTIC` 会跨用例残留。
   `tests/test_faq_semantic.py` 用 `autouse` fixture 前后各 `reset_semantic_index()` 一次。
   另一面：**预热与查询必须传同一个客户端** —— `semantic_search()` 默认走 `default_llm`，
   而测试环境里它没有 `api_key`，查询向量化会直接失败返回 `None`，看起来像"语义没生效"。

3. **`classify()` 是同步的、LLM 是异步的，别硬塞进同一个 Protocol。**
   把 `IntentRouter.classify` 改成 `async` 会波及全部同步调用点，还会丢掉
   「确定性、可离线测试、将来接 BERT 也是同步」这三个好处。
   正解：快车道保持同步，LLM 那层单独做成 `async llm_classify()`，级联由 `support.answer()` 编排。

4. **`require_admin` 是依赖，比函数体里的模式判断先跑。**
   所以 `POST /shop/orders/{no}/confirm` 在非 manual 模式下，非管理员拿到的是 **403 而不是 404**。
   这与 `require_admin` 文档里写明的设计一致（「端点存在与否不是本站的秘密」），
   写测试时别想当然断言 404 —— 我第一版就写错了。

5. **删函数时别把装饰器留下。**
   我把 `db` fixture 从 `test_support.py` 上移到 `conftest.py`，删了函数体却漏了
   `@pytest.fixture` 那一行 ⇒ 它挂到了下一个函数 `_seed_articles` 上，
   报的是 `Failed: Fixture "_seed_articles" ...`，完全看不出真因。

6. **`full_init.sql` 的种子 `admin` 漏写 `role` 列**（真 bug，已修）。
   DDL 默认 `0`、`require_admin` 要 `1` ⇒ 预置管理员进不了任何管理端点。
   测试一直发现不了，因为 `test_admin_ingest.py` 每个用例都显式 `_set_role(..., 1)`，
   而测试库用 `create_all` 建表、根本不读这个 SQL 文件。
   **教训：种子 SQL 的字段完整性没有测试覆盖，只能靠直接钉住 SQL 文本的用例。**

7. **多段替换的 heredoc 必须每段 `assert count == 1` 且只在末尾 `write_text`。**
   这一轮又救了一次：第二段匹配失败（`support.py` 里 `_escalate` 的实参是「两参一行」的排版，
   我按「一参一行」写），因为断言在写入之前，整个文件没被写坏。

### 第 3 轮复审之后（2026-09-05）

外部复审提了三条，**逐条实测后三条全部成立**（这一轮没有假阳性）：

1. **`onChange` 没有退订能力 ⇒ 业务动作被重放。**
   `shop.html` 里那行 `CodeMaxAuth.onChange(() => {})` 注释写着「避免重复绑定」，
   实际只是往列表里再追加一个空函数。**注释与实际行为不符的代码最危险** ——
   读代码的人会相信注释，静态 grep 也看不出问题。
   只有把 `auth.js` 与页面脚本按浏览器顺序拼起来用 node 真跑一遍才暴露。
   见 TD-208。

2. **模板上下文没有收口 ⇒ 页面渲染成空壳却返回 200。**
   `mock_pay_page()` 自己拼字典、漏了 `site_name`/`tools`/`shop_path`，
   页面渲染出 `<h1><a href="/"></a></h1>` 与 `<nav></nav>`。
   而原先的测试只断言「页面能开 + 有订单号」—— **断言太弱等于没测**。
   现在收口到 `app/site.py:page_context()`，并用集合比对钉住契约。见 TD-209。

3. **文档数字大面积漂移。** 复审列了 10 个文件、30+ 处。根因是同一类：
   我上一轮只改了「本轮改动直接涉及」的数字，没有全仓反扫所有引用同一事实的地方。
   这次的做法是**先取实测真值、再全仓 grep 每一个旧值**，包括
   `.claude/skills/` 下的 SKILL.md（那里也有行数与测试基线）。

**两条方法论教训：**

- **「测试通过」不等于「行为正确」。** 上面两个 bug 都在 472→532 条测试全绿的状态下存活了很久。
  前端行为必须真跑（node 执行真实脚本），页面渲染必须断言**渲染结果**而不只是状态码。
- **改完要做变异验证。** 这次撤掉修复重跑，两条新测试立刻变红 —— 这才证明它们真的在守这个 bug，
  而不是碰巧通过。

## 7. 已完成 / 未完成（对应 ROADMAP.md）

**已完成**
- 阶段一：S1-01 工程骨架 + PostgreSQL 基础表；S1-02 OAuth2 授权码 SSO + JWT
- 阶段二：S2-01-1 ER 图（DDL 解析 + D3.js）；S2-01-2 LLM→Mermaid；S2-01-3 Drawio 嵌入 + 云端保存；
  S2-01-4 python-docx 导出 Word；S2-02-1 SEO（Jinja2 SSR + TOOLS 清单 + sitemap/robots + TDK）

**未开始**
- S2-02-2 引流→变现转化路径（按选型本轮跳过；可复用阶段一 SSO 跳 `/oauth/authorize?client_id=shop`）
- 阶段三：**S3-01 支付闭环代码已完成**（下单 `POST /shop/orders`、状态机 `app/order_state.py`、
  回调 `POST /shop/pay/notify`），仅缺真机联调（TD-113，需商户号 + 证书 + 公网回调）。
  **S3-02 下载防护也已完成**（策略模式 + 预签名 URL + 一次性下载双重校验，`app/storage.py`）；
  仅缺 OSS/COS 真适配器（需密钥，TD-128）
- 阶段四：爬虫（httpx + BeautifulSoup4）+ LLM 内容解析 + 三层智能客服
- 阶段五：全链路测试 / 压测 / 部署上线

## 8. 建议的下一步

1. 等用户授权后合并 PR #3，随即删掉 4 个一次性传输文件
2. ~~TD-138：管理员角色 + 抓取入库 HTTP 端点~~ **已完成**（`app/routers/admin.py`
   + `POST /admin/articles/ingest`，迁移 `migrate_0005_user_role.sql`）。
   **提权要人工执行**：`UPDATE sys_user SET role = 1 WHERE username = '...';`
   —— 迁移脚本刻意不做，注册出来的号一律是普通用户。
3. 其余 **6 个叶子项**（ROADMAP 共 11 项未勾 = 5 个父标题 + 6 个叶子）**都需要外部资源或已被选型排除**，不要在没有资源时硬做：
   - S3-01 真机联调 ×2（TD-113，需商户号 / API 证书 / 公网 https 回调）
   - S3-02-1 与 S5-03-1 的 OSS/COS（TD-128，需密钥；无密钥写出的适配器无法验证签名）
   - S4-02-2 BERT 意图路由（TD-150，无标注数据；**不要**拿公开问句匹配语料硬凑本项目的 FAQ 标签）
   - S2-02-2 引流→变现（按选型跳过）
4. 上线阻塞项仍有 2 条：TD-113（支付未真机联调）、TD-124（模拟支付误开，`ENV=production` 会拒绝启动）

## 9. 在沙箱里起一个真 PostgreSQL（可选，用于真库集成测试）

沙箱的 apt 源不可达，但 PyPI 可达，所以用 `pgserver`（自带 PG 16.2 二进制）：

```bash
.venv/bin/pip install pgserver
.venv/bin/python -c "import pgserver; pgserver.get_server('/tmp/pgdata', cleanup_mode=None)"
BIN=.venv/lib/python3.11/site-packages/pgserver/pginstall/bin
$BIN/createdb -h /tmp/pgdata -U postgres codemax_db
$BIN/createdb -h /tmp/pgdata -U postgres codemax_test
$BIN/psql -h /tmp/pgdata -U postgres -d codemax_db -f "database init/full_init.sql"

# 真库跑全量测试
TEST_DATABASE_URL="postgresql+asyncpg://postgres@/codemax_test?host=/tmp/pgdata" \
  .venv/bin/python -m pytest -q

# 让预览服务连真库（注册/登录/流程图/下单都能真跑）
DATABASE_URL="postgresql+asyncpg://postgres@/codemax_db?host=/tmp/pgdata" \
  .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

数据目录在 `/tmp/pgdata`，不在工作区快照里 —— 沙箱重启后要重新执行上面几步（TD-123）。

## 10. 模拟支付通道（答辩演示用，TD-124）

没有微信商户号时，用 `SHOP_PAY_MODE=mock` 走本站的模拟收银台：

```bash
DATABASE_URL="postgresql+asyncpg://postgres@/codemax_db?host=/tmp/pgdata" \
SHOP_PAY_MODE=mock \
  .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

流程：登录后 `POST /shop/orders` → 返回的 `code_url` 指向 `/shop/mock-pay?order_no=...`
→ 页面上点「模拟支付成功」→ `POST /shop/mock-pay/confirm` 把订单置为 `paid`。

三点必须知道：
1. 模拟支付走的是与真实回调**完全相同**的状态机与幂等逻辑（`mark_paid`），演示路径＝生产路径
2. 模拟流水号是 `MOCK-<订单号>`，一眼可辨，上线前要清演示数据
3. **`SHOP_PAY_MODE=wechat`（默认）时这两个端点一律 404**；生产误开 mock 等于免费发货

## 11. 一次性下载（S3-02-4）

两重校验，缺一不可：

1. **数据库状态**（主力，防倒卖）：`POST /shop/download/{order_no}` 走状态机
   `paid → downloaded`，同一订单第二次领链接直接 403
2. **预签名 URL**（缩小转发窗口）：`HMAC-SHA256(SECRET_KEY, "<key>\n<expires>")`，
   `/shop/dl` 校验签名与过期时间；签名**绑定 key**，所以换一个 key 就失效

本地演示要把商品文件放到 `STORAGE_LOCAL_ROOT` 下（默认 `storage/`，已 gitignore）：

```bash
mkdir -p storage/product && echo "商品内容" > storage/product/codemax_package.zip
```

注意语义：**标记为已下载发生在发出链接时**，不是文件真的被下载时 ——
云存储是客户端直连对象存储，应用看不到那次下载（TD-129）。
