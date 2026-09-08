# `tests/` 模块说明书

> **行号基准 commit：`393a113`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/README.md`、`app/routers/README.md`、`app/tools/README.md`、
> `app/templates/README.md`、`app/static/README.md`、`database init/README.md`、
> `.github/workflows/README.md`、`docs/ROOT_FILES.md`、`scripts/README.md`。

---

## 1. 总览

### 1.1 规模（实测）

```text
41 个 .py 文件（其中 `test_*.py` 39 个）/ 9 145 行 / 511 个测试函数
> ⚠️ 口径说明：**测试函数** 511 个是 `def test_` 的个数；pytest 实际**收集到的用例**
> 是 603 条（`parametrize` 会展开）。两个数都对，引用时说清是哪个。
  ├─ conftest.py      178 行   全局 fixture（`client` `db` `mock_mode` `product` + SSO 辅助）
  ├─ __init__.py        0 行
  └─ 39 个 test_*.py  8 967 行  511 个测试
```

本机实测：**650 passed, 4 skipped**（SQLite 后端，654 collected）；真 PostgreSQL 16 上 **626 passed, 2 skipped**（依赖大升级后那次实测，早于本轮新增用例）。

> **为什么本文不逐个测试函数写**：488 个函数逐个写既写不完也没人看。
> 本文按**测试策略 → 分组 → 每组守住的不变量**组织，只对**代表性用例**给行号。
> 想找某个具体用例，用文末附录的索引命令。

### 1.2 五条贯穿全目录的测试策略

**① 一律走真实 HTTP 层，不直接调业务函数。**
25 / 33 个文件用 `client` fixture（`httpx.AsyncClient` + `ASGITransport`），请求真的经过中间件、依赖注入、鉴权、异常处理器。**只有 5 个不用**：`test_crawler.py`、`test_faq.py`、`test_politeness.py`、`test_schema_sync.py`、`test_config_validation.py` —— 它们测的是不依赖 HTTP 的纯逻辑。

**② 前端代码要被真执行，不许退化成静态字符串检查。**
`test_er_page.py` 与 `test_auth_cookie.py` 用 `subprocess` 真跑 `node`：

| 文件 | 做法 |
| --- | --- |
| `test_er_page.py:79` / `:127` / `:151` | 拿 `/tools/er-diagram` 的**真实返回**喂给 `app/frontend/er-layout.js` 的 `layoutEr`，断言坐标 |
| `test_auth_cookie.py:219` | 把 drawio 页面的**内联脚本**抽出来在 node 里跑，`localStorage` 的每个方法都换成抛错，**并真的点一次登录按钮** |

> `ruff.toml:51-52` 为这两个文件开了 `ASYNC221` 白名单 —— 阻塞式的 `subprocess.run` 是**有意的**。

**③ 关键策略值写死数值，不用相对断言。**
相对断言（`assert x == MAX_BYTES + 1`）**取任何值都能过**，等于没测。所以有 `test_crawler.py` 的 `test_max_bytes_budget_is_pinned_at_2mb` 这类**钉死数值**的用例。同类：`test_order_state.py:53` 的状态常量字面值、`app/tools/` 的 `_BM25_SATURATION=3.0`。

**④ 同一套测试跑两种后端。**
`conftest.py:30` 读 `TEST_DATABASE_URL`：不设 → 内存 SQLite；设了 → 真 PostgreSQL。**CI 两个 job 分别跑一遍**（`.github/workflows/ci.yml`）。

**⑤ 会跳过的用例必须写清为什么、以及怎么才能跑起来。**
3 处 `skipif`，每条 reason 都给了可执行的解决办法（见 2.3）。

---

## 2. 公共基础设施

### 📄 文件名：`conftest.py`（178 行）

- **文件职责**：**全局唯一的 fixture 来源**（`client` `db` `mock_mode` `product`），加上 SSO 流程辅助函数。

#### 结构

**L7 `sys.path.insert(0, ...)`** —— 把仓库根加进 `sys.path`（`pytest.ini:4` 的 `pythonpath = .` 是另一道保险）。

**L22-L24 全局关限流**
- **L22-L23 注释说明了原因**：所有用例共用同一个客户端 IP，**开着的话几十个注册/登录会互相挤爆配额**
- **限流本身由 `tests/test_ratelimit.py` 显式打开后测试**

**L26-L44 数据库引擎（本文件最关键的一段）**
- **L32 `TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite://")`**
  - **L26-L31 注释**：设 `TEST_DATABASE_URL` 就能整套跑在真 PostgreSQL 上，用来消掉「集成测试只跑 SQLite」这个上线阻塞项（TD-80）
  - **L31 有一句警告**：**别指向正在用的业务库 —— fixture 每个用例都会 `create_all` / `drop_all`**
- **L34-L37 SQLite 分支** —— `poolclass=StaticPool`，**保证所有连接共享同一内存库**（否则每个连接各自一个空库）
- **L38-L43 真库分支** —— `poolclass=NullPool`。**L39-L42 的注释是一个真实踩过的坑**：
  > 真库必须用 `NullPool`：**asyncpg 的连接绑死在创建它的事件循环上**，而 pytest-asyncio **每个用例开一个新 loop**。用默认连接池会复用到上一个 loop 的连接，报 `"got Future attached to a different loop"`。**SQLite 那边因为是 StaticPool 单连接才没暴露这个问题。**

**L46-L60 `seed_clients()`**
- **L47 注释**：**必须每次调用新建实例，否则 ORM 对象跨测试复用会泄漏状态**
- 造两个 SSO 接入平台（`tools` / `shop`），**与 `database init/full_init.sql` 一致**

**L63-L103 SSO 流程辅助**
- **L66-L67 注释解释了为什么必须走两步**：TD-78 之后 `GET /oauth/authorize` **只渲染同意页、不签发授权码**，签发在 POST。所以「拿到一个 code」必须 **GET 取同意页 → 从隐藏表单里抠出 `sig` → POST 提交**。
  > **这也正是浏览器真实做的事，测试跟着走一遍才不会把签名校验测成摆设。**
- **L72 `sso_authorize()`** —— 走完整同意流程，返回 POST 的响应；**断言消息带实际响应片段**，失败时不用再去复现
- **L101-L103 `sso_code()`** —— 从 302 的 `Location` 里取出 code

**L105-L123 `client` fixture**
```text
L107-L108  create_all              建表
L110-L112  灌种子数据（两个 OAuth 客户端）
L114-L118  用 dependency_overrides 把 get_db 换成测试 session
L119-L120  yield AsyncClient(ASGITransport(app=app), base_url="http://test")
L121       清掉 dependency_overrides
L122-L123  drop_all                拆表
```
- **每个用例都是干净的库** —— 这是 603 个用例能任意顺序跑的前提
- **L121 的 `clear()` 不能省** —— 否则下一个用例会拿到上一个用例的 session 工厂

**L126-L150 `db` fixture** —— 一个能直接用的 `AsyncSession`（建表 → yield → 拆表）。**从 `test_support.py` 上移到这里**：`test_intent_cascade.py` 也要用它。

**L153-L169 `mock_mode` fixture** —— 走模拟收银台（TD-124）。⚠️ **打 `/shop/orders` 的测试必须带它**：默认 `SHOP_PAY_MODE=wechat` 而沙箱没有商户号，下单会直接 503，很容易被误判成代码 bug。**原本在 `test_shop_page.py` 里，`test_shop_polling.py` 也要用，所以按「共享 fixture 放 conftest」的约定上移。**

**L171-L178 `product` fixture** —— 把存储后端指到临时目录并造出商品文件。

### 2.1 分组总览（39 个测试文件）

> 行数为 `wc -l` 实测值（2026-09-05）。**别手抄**：历史上这张表大面积过期过 ——
> 本次一核对，9 组里有 8 个文件的行数都是旧的（如 `test_e2e` 427 → 530、
> `test_crawler` 239 → 299）。改完测试必须重跑 `wc -l` 再写。

| 组 | 文件数 | 行数 | 文件 |
| --- | --- | --- | --- |
| **认证与授权** | 7 | 1230 | `test_auth`(67) `test_auth_cookie`(314) `test_auth_crypto`(101) `test_username_validation`(109) `test_oauth`(248) `test_oauth_consent`(154) `test_token_revocation`(237) |
| **工具功能** | 4 | 674 | `test_sql_ddl`(254) `test_er_page`(159) `test_mermaid`(169) `test_word_export`(92) |
| **电商与支付** | 8 | 2087 | `test_order_state`(134) `test_wechat_pay`(279) `test_wechat_notify`(379) `test_mock_pay`(157) `test_download`(200) `test_shop_page`(450) `test_shop_polling`(182) `test_manual_pay`(306) |
| **流程图** | 3 | 414 | `test_diagrams`(115) `test_diagram_quota`(153) `test_diagram_concurrency`(146) |
| **爬虫** | 5 | 1517 | `test_crawler`(299) `test_politeness`(284) `test_extract`(240) `test_admin_ingest`(333) `test_dynamic_crawl`(361) |
| **智能客服** | 4 | 1189 | `test_faq`(131) `test_support`(240) `test_faq_semantic`(605) `test_intent_cascade`(213) |
| **运维与横切** | 5 | 1054 | `test_ops`(347) `test_ratelimit`(152) `test_schema_sync`(74) `test_perf`(360) `test_config_validation`(121) |
| **页面与文档站** | 2 | 272 | `test_site`(64) `test_docs_site`(208) |
| **端到端** | 1 | 530 | `test_e2e`(530) |

### 2.2 九组各自守住的不变量

#### ① 认证与授权（7 文件 / 1230 行 / 66 个测试）

| 文件 | 守住的不变量 |
| --- | --- |
| `test_auth.py` | 登录/登出/改密码/`/auth/me` 的基本行为 |
| `test_auth_crypto.py` | **bcrypt 必须在事件循环之外**（A-1/A-1b/A-8）：连跑 5 次 verify 期间 `asyncio.sleep(10ms)` 的最大漂移必须 < 80 ms（改前实测 1280 ms、改后 4 ms）；登录的用户名枚举时序差必须拉平（改前 58 倍、改后 1.0 倍）；`/oauth/token` 必须挂限流 |
| `test_username_validation.py` | **用户名白名单与保留名**（A-5）：空白/控制字符/标记语言一律 422；`admin` 大小写不敏感地保留（登录是精确匹配，`Admin` 会是独立账号却能冒充管理员）；对照组钉住中文名与大写名不被误杀 |
| `test_auth_cookie.py` | **TD-44：登录态走 HttpOnly cookie，前端不碰 `localStorage`** |
| `test_oauth.py` | 授权码签发与兑换；**code 一次性** |
| `test_oauth_consent.py` | **TD-78 + TD-175：同意页的签名校验、`approve` 语义、零内联脚本** |
| `test_docs_site.py` | **文档站构建脚本的回归测试**：`build_routes()` 提取的路由必须**等于**运行时 `app.routes`（曾静默少 4 条：32 vs 36）；`DOC_GROUPS` 登记的文档必须真实存在；生成物必须全在 `.gitignore` 里 |
| `test_token_revocation.py` | **TD-70：改密码后旧 token 必须失效** |
| `test_shop_page.py` | **S2-02-2：轮询订单状态绝不能烧掉一次性下载**（`GET /shop/orders/{no}` 必须只读）；同意页之外的页面都有登录与商城入口；二维码是内联 SVG、不引外部请求 |
| `test_shop_polling.py` | **轮询生命周期**（N-2）：用 node 跑 `shop.html` 真实内联脚本 + 假时钟，钉住「订单过期必须停表」（改前 9.5 秒内打 3 次且永不停止）与「未过期照常轮询」的对照组；`GET /shop/orders/{no}` 必须带 `Cache-Control: no-store`，否则页面永远看不到订单变 paid |

**代表用例**（`test_token_revocation.py`）：
- `test_old_token_is_rejected_after_password_change` —— 核心不变量
- `test_all_other_sessions_die_but_the_new_one_lives` —— **改密码的那个会话要能继续用**
- `test_tokens_without_pwd_claim_still_work_when_never_changed` —— **老 token 兼容**（没有 `pwd` 声明的，只要用户从没改过密码就仍然有效）
- `test_token_issued_before_change_is_rejected_even_if_pwd_claim_present` —— **边界：时间戳比较用 `<`**

**代表用例**（`test_auth_cookie.py`）：
- `test_drawio_frontend_runs_without_browser_storage`（**L210-L219，真跑 node**）
- 静态扫描那条会**先剥掉 Jinja 注释**（L204），否则会被 `oauth_consent.html:11` 的字面量 `<script>` 误判

#### ② 工具功能（4 文件 / 674 行 / 52 个测试）

| 文件 | 守住的不变量 |
| --- | --- |
| `test_sql_ddl.py`（28 个，本目录单文件最多） | DDL 解析：表/列/主键/外键/注释、边界与畸形输入 |
| `test_er_page.py`（5 个，**真跑 node**） | **后端返回的字段名与前端 `layoutEr` 读的字段名两端对得上** |
| `test_mermaid.py` | LLM → Mermaid 类图；LLM 挂了要降级 |
| `test_word_export.py` | DDL → Word 数据字典 |

> **`test_er_page.py` 的输入是项目自己的 `database init/full_init.sql`**（L18）——
> 用真实 DDL 而不是造一个玩具样例，这样解析器的回归会连带被测出来。

#### ③ 电商与支付（8 文件 / 2087 行 / 110 个测试）

| 文件 | 守住的不变量 |
| --- | --- |
| `test_order_state.py` | **状态机只允许前进**；重复通知幂等 |
| `test_wechat_pay.py` | NATIVE 下单 + 签名 |
| `test_wechat_notify.py` | **回调验签、AES-GCM 解密、幂等**（同一通知来两遍只生效一次） |
| `test_mock_pay.py` | 模拟通道；**生产模式下必须 404** |
| `test_manual_pay.py` | **人工确认收款（S5-04/TD-205）**：只有管理员能确认；非 manual 模式下端点不存在；复用 `mark_paid` 故幂等且 `CLOSED` 也能收；另钉住 `full_init.sql` 种子账号必须带 `role=1`；**必须写 `codemax.audit` 审计日志**（N-3：谁确认的这一单事后要能查到），且被 403 挡掉的尝试不留记录 |
| `test_download.py` | **预签名 URL + 一次性下载双重校验** |

**代表用例**（`test_order_state.py`）：
- `test_state_values_are_persisted_strings` —— **状态值必须是持久化字符串**（改了字面值会让老数据读不出来）
- `test_only_forward_transitions_allowed` —— **不能倒退**
- `test_mark_paid_twice_is_idempotent` / `test_late_paid_notification_after_download_is_noop` —— **幂等**
- **但 `CLOSED → PAID` 是刻意允许的**（TD-156：**收到钱就必须发货**）

#### ④ 流程图（3 文件 / 414 行 / 27 个测试）

| 文件 | 守住的不变量 |
| --- | --- |
| `test_diagrams.py` | 需鉴权，**且只能操作自己的记录** |
| `test_diagram_quota.py` | **TD-64：配额 + 软删除** |
| `test_diagram_concurrency.py` | **TD-65：乐观锁** |

**代表用例**（`test_diagram_concurrency.py`，9 个全部围绕 ETag）：
- `test_missing_if_match_is_428` / `test_garbage_if_match_is_400` —— **两种坏输入要分开**
- `test_stale_version_is_rejected_and_writes_nothing` —— **不只是拒绝，还要确认没写进去**
- `test_concurrent_saves_only_one_wins` —— 真并发
- **`test_non_owner_gets_404_not_412`** —— **越权必须返回 404 而不是 412**，否则就泄漏了「这个 id 存在」

#### ⑤ 爬虫（5 文件 / 1517 行 / 82 个测试）

| 文件 | 守住的不变量 |
| --- | --- |
| `test_crawler.py` | httpx + BeautifulSoup 抓取；**体积/节点预算** |
| `test_politeness.py` | **TD-133：robots.txt、按域间隔、全局并发上限** |
| `test_extract.py` | LLM 指认选择器 + 提取 + 入库 |
| `test_admin_ingest.py` | **TD-138：管理员抓取入库端点**（仅 role='admin'） |
| `test_dynamic_crawl.py` | **TD-191：Playwright 动态渲染** |

> **`test_politeness.py` 有一个隔离要求**：`politeness._states` 按 origin 缓存 1 小时，
> **setup 与 teardown 都要 `reset_cache()`**，否则用例之间会互相污染。

#### ⑥ 智能客服（4 文件 / 1189 行 / 79 个测试）

- `test_faq.py` —— BM25 + 向量融合召回（`BM25_WEIGHT=0.6`）
- `test_support.py` —— 意图路由、三层编排（规则/FAQ/RAG）、兜底转人工
- `test_faq_semantic.py` —— 语义 FAQ 检索（S4-02-5）：`/embeddings` 乱序返回也要还原输入顺序；预热失败只退回词袋不拖垮启动；`None`（不可用）与空列表（没内容）必须区分
- `test_intent_cascade.py` —— 三级级联：**规则有把握时绝不调 LLM**（靠调用计数守）；语义/LLM 都失败仍转人工；每次级联落一条标注样本日志

#### ⑦ 运维与横切（5 文件 / 1054 行 / 56 个测试）

| 文件 | 守住的不变量 |
| --- | --- |
| `test_ops.py`（25 个） | 安全头、健康探针、生产自检、CPU 池兜底、**CSP 白名单与前端外部源一致** |
| `test_ratelimit.py` | **TD-15：限流**（滑动窗口、按 key 独立、`X-Forwarded-For` 默认不信任） |
| `test_schema_sync.py` | **建表脚本与 ORM 必须同步**（HANDOVER 点名的坑） |
| `test_perf.py` | **S5-02：性能预算**（FAQ 80 ms、DDL 线性而非平方、重活不阻塞事件循环） |
| `test_config_validation.py` | **数值配置的范围约束**（越界必须启动即失败；含 `RATE_LIMIT_WINDOW=0` 会让限流 fail-open 的实测、以及 `.env.example` 自身必须合法） |

**`test_ops.py` 里两条设计得最巧的**：
- `test_every_external_origin_used_by_frontend_is_allowed_by_csp` —— **反向校验**：扫模板与静态资源里每个外部域，逐个断言在 CSP 白名单里。没有它，「加了新 CDN 忘了改 CSP」的故障**不是测试红，而是上线后页面白屏**
- `test_csp_allows_inline_scripts_because_templates_still_need_them` —— **故意会在好事发生时变红**：等内联脚本都外置了，这条会红，提醒收紧 CSP

**`test_schema_sync.py` 的边界要说清**：它**只比表名与列名，不比类型/默认值/索引**。
「PostgreSQL 其实不接受这句 SQL」只有 CI 的真库 job 能抓到（`ci.yml:152-153` 真执行两遍）。

#### ⑧ 页面与文档站（2 文件 / 272 行 / 17 个测试）

`test_site.py` —— `TOOLS` 清单驱动的 SSR 页面 / TDK / 导航 / sitemap / robots。

#### ⑨ 端到端（1 文件 / 530 行 / 22 个测试）

`test_e2e.py` —— **S5-01：把前面各阶段的单元测试串成完整业务旅程**。三段：

| 段 | 行 | 旅程 |
| --- | --- | --- |
| **S5-01-1 支付全链路** | L99-L219 | 注册 → 下单 → 支付 → 下载（7 个用例，含过期、迟到通知、并发重复支付） |
| **S5-01-2 下载与防盗链** | L221-L290 | 并发下载只有一个赢、压缩包真的有多个文件、预签名 URL 不能复用、篡改/过期链接被拒 |
| **S5-01-3 SSO 跨域** | L292-L427 | 两个平台间的完整 SSO 旅程、跨域头、`redirect_uri` 必须精确匹配、**一个 client 的 code 不能被另一个用** |

**代表用例**：
- `test_late_payment_on_closed_order_still_delivers`（L155）—— **TD-156：订单过期关闭后钱才到，仍然要发货**
- `test_concurrent_duplicate_payment_marks_paid_exactly_once`（L189）—— **CAS 保证只标记一次**
- `test_expired_order_can_never_be_downloaded`（L176）—— **但过期未付款的绝不能下载**
- `test_code_from_one_client_cannot_be_used_by_another`（L373）—— **跨客户端串用授权码**

### 2.3 三处 `skipif`（全部写清了怎么才能跑起来）

| 位置 | 条件 | reason 原文 |
| --- | --- | --- |
| `test_oauth.py:184-187` | `TEST_DATABASE_URL.startswith("sqlite")` | **需要真数据库：SQLite 用 StaticPool 共享单连接，两个请求排不成真正的并发** |
| `test_dynamic_crawl.py:316-320` | `RUN_BROWSER_TESTS != "1"` 或浏览器不可用 | **需要真浏览器：`pip install playwright && playwright install chromium`，再以 `RUN_BROWSER_TESTS=1` 运行。本沙箱下不到浏览器二进制（CDN 不可达）** |
| `test_auth_cookie.py:210` | `shutil.which("node") is None` | 未安装 node |

**这解释了两种后端的计数差异**：

| 后端 | passed | skipped | 差异原因 |
| --- | --- | --- | --- |
| SQLite（默认） | 561 | **4** | 见下面四条 |
| 真 PostgreSQL 16 | 563 | **2** | 两条并发用例跑起来了；另两条仍跳 |

4 条 skip 的实测原因（`pytest -rs` 可复现，别照抄本表，行号会变）：

| 位置 | 原因 | 真库上 |
| --- | --- | --- |
| `test_e2e.py` 并发下单（TD-199） | SQLite + StaticPool 共享单连接，一个请求的 `rollback` 会把别人的插入一起回滚 | **跑起来** |
| `test_oauth.py` 授权码并发（TD-85） | 同上，排不成真正的并发 | **跑起来** |
| `test_dynamic_crawl.py` 动态抓取（TD-191） | 要 `playwright install chromium`，沙箱下不到二进制 | 仍跳 |
| `test_faq_semantic.py` 语义阈值标定（TD-206） | 要真实 embedding API，设 `LLM_API_KEY` 才跑 | 仍跳 |

> ⚠️ **`test_dynamic_crawl.py:322` 在沙箱里永远跑不了** —— `cdn.playwright.dev` 与
> `playwright.azureedge.net` 都 HTTP 000。**这条需要用户在本机 Windows 上验**，期望 19 passed。

### 2.4 已知的测试隔离坑（都已在代码里处理，改动时要保持）

| 坑 | 位置 | 处理 |
| --- | --- | --- |
| **真库连接绑死事件循环** | `conftest.py:37-40` | 真库用 `NullPool` |
| **mock 类必须在 import 时捕获基类** | `test_wechat_*.py` 等 | 晚捕获会拿到已被替换的类 |
| **`politeness._states` 按 origin 缓存 1 h** | `test_politeness.py` | **setup 与 teardown 都要 `reset_cache()`** |
| **全局关限流** | `conftest.py:22` | 限流由 `test_ratelimit.py` 自己打开测 |
| **`monkeypatch.setenv` 改不了已加载的 `settings`** | 多处 | 要改配置得 `monkeypatch.setattr(settings, ...)` |
| **`assert_public_url` 不能随便删** | `test_dynamic_crawl.py` | 实测删掉 → **2 failed**（曾经「照样过」的结论已不成立） |

---

## 3. 执行逻辑流

### 3.1 一个用例从开始到结束

```text
pytest 收集 tests/（pytest.ini:3 testpaths）
  │
  ├─ import conftest.py
  │    ├─ L22   settings.RATE_LIMIT_ENABLED = False    全局关限流
  │    └─ L30-L41 建引擎（SQLite→StaticPool / 真库→NullPool）
  │
  └─ 每个用例：
       ├─ client fixture  L105-L106  create_all      ← 干净的库
       │                  L108-L110  灌两个 OAuth 客户端
       │                  L112-L116  覆盖 get_db 依赖
       ├─ 用例体：AsyncClient 发真实 HTTP 请求
       │            → 中间件 → 依赖注入 → 路由 → 业务 → 异常处理器
       ├─ L119            清 dependency_overrides
       └─ L120-L121       drop_all                    ← 拆干净
```

### 3.2 三种「不经过 HTTP」的测试

| 方式 | 用在哪 | 为什么 |
| --- | --- | --- |
| **直接调函数** | `test_crawler` `test_faq` `test_politeness` `test_schema_sync` `test_config_validation` | 测的是纯逻辑，不需要 HTTP 开销 |
| **`subprocess` 跑 node** | `test_er_page:79/127/151`、`test_auth_cookie:219` | **要真执行前端 JS**，Python 里没法跑 |
| **多进程** | `test_perf.py` | 要压真并发，单进程 asyncio 压不出来 |

### 3.3 一次改动的验证顺序

```text
改代码
  ├─ .venv/bin/ruff check .                    ← CI 的 lint job
  ├─ .venv/bin/python -m pytest -q             ← CI 的 test-sqlite job
  │    期望：650 passed, 4 skipped
  └─ （动了 SQL / models / 时间相关）起真库再跑一遍   ← CI 的 test-postgres job
       期望：626 passed, 2 skipped
       （依赖大升级后那次实测；本轮新增 24 条用例后**未重跑真库**，实跑应更高）
       配方见 HANDOVER.md §9
```

> **`AGENTS.md` 的 ALWAYS 段**：每次改完都要跑 `pytest -q` 并迭代到全绿，
> **绝不跳过、注释掉或降低标准**。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `393a113`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# ① 文件数 / 总行数 / 测试函数数（本文 1.1 的三个数字）
python -c "
import pathlib, re
fs = sorted(pathlib.Path('tests').glob('*.py'))
print('文件数:', len(fs))
print('总行数:', sum(len(p.read_text(encoding='utf-8').splitlines()) for p in fs))
print('测试函数:', sum(len(re.findall(r'^(async )?def test_', p.read_text(encoding='utf-8'), flags=re.M)) for p in fs))
"
# 预期：文件数 44，总行数 10190，测试函数 547（本机实测一致）

# ② 按行数排序的文件清单（本文 2.1 分组表的依据）
python -c "
import pathlib
for p in sorted(pathlib.Path('tests').glob('*.py'), key=lambda p: -len(p.read_text(encoding='utf-8').splitlines())):
    print(len(p.read_text(encoding='utf-8').splitlines()), p.name)
"

# ③ 每个文件测的是哪个 app 模块（本文 2.1 的「被测模块」列）
grep -rl "app\.<模块名>" tests/*.py          # 例：grep -rl "app.tools.sql_ddl" tests/*.py

# ④ 找某个具体用例在哪
grep -rn "def test_<关键字>" tests/

# ⑤ skipif（本文 2.3）
grep -rn --include="*.py" "skipif" tests/
# 预期 10 处，分布在 7 个文件：test_auth_cookie / test_dynamic_crawl / test_e2e /
# test_faq_semantic / test_oauth / test_shop_page(3) / test_shop_polling(2)
# ⚠️ 其中 6 处是 `shutil.which("node") is None` —— 装了 node 就**不会**跳过。
# 全量实际的 4 处 skip 是：2 处并发用例需真库、1 处需真浏览器、1 处语义阈值需 embedding key。

# ⑥ 全量跑一遍
python -m pytest -q
# 本机实测：650 passed, 4 skipped（654 collected，SQLite）

# ⑥b 覆盖率（N-4 修复：`.coveragerc` 以前入库却连 coverage 都没装，跑都跑不了）
python -m coverage run --rcfile=.coveragerc -m pytest -q
python -m coverage report --rcfile=.coveragerc
# 本机实测：**全量 96.0%**（2194 条语句，缺 87 行）
# ⚠️ 必须带 --rcfile：`.coveragerc` 里 `concurrency = thread,greenlet` 那行是**必需**的，
#    少了它 coverage 会把 greenlet/线程上跑过的端点代码记成未覆盖。
#    实测同一批用例（4 个文件 / 107 passed）：带 concurrency **59.3%** vs 不带 **57.0%**。
#    ⚠️ 注意 `concurrency` 只影响**采集**、不影响报告：拿同一份 .coverage 数据换个
#    rcfile 去 report 会得到完全相同的数字，那样「对比」是假的 —— 必须重跑采集。
#    文件头写的「89% vs 97%」是更早那次全量的记录；本轮实测带 concurrency 为 96.0%，
#    不带 concurrency 的**全量**差值未重测（要再花 6 分钟），只有上面的子集对照。
# ⚠️ 清数据文件写全 `rm -f .coverage .coverage.*` —— `rm -f .coverage*` 会连配置文件一起删。

# ⑦ 只跑某一组
python -m pytest tests/test_e2e.py -q
# 本机实测：21 passed, 1 skipped
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动本目录任何文件后，
> 本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **两条特别提醒**：
> ① 本文 1.1 的「passed 数 / 511 个测试函数」这类数字，**加一个用例就会变** ——
>    历史上已经因为「加 1 条测试」导致 24 处条数、11 个文件过期。改完必须重跑再写；
> ② 断言里的关键策略值**写死数值**，不要用 `MAX_BYTES + 1` 这种相对写法
>    （取任何值都能过，等于没测）。
