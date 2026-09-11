# CodeMax Platform：合并审计错误摘要

> 可直接交给另一名助手继续复核或实现。  
> 审计代码基准：`b1859184d8b0fdf31984166ab9862af0fe5238e5`（`arena/01a0599b-codemax-platform`）。  
> 当前会话分支：`arena/01a0852c-codemax-platform`，当前 `b02f771e7676480bb115a0fd8242e380c7d55a9e` 只额外加入了用户提供的 `review-plan-2.txt`，应用代码与审计基准相同。  
> 汇总日期：2026-09-11。当前阶段只审计、复现、分类；**尚未实施仓库修复**。

## 1. 已独立复核的基线

| 项目 | 真实结果 / 口径 |
| --- | --- |
| SQLite 全量测试 | `654 collected = 650 passed + 4 skipped` |
| PostgreSQL 全量测试 | `654 collected = 652 passed + 2 skipped`；不是 `626 + 2` |
| 测试源码 | 42 个 `tests/test_*.py`；549 个静态/唯一测试函数；参数化后 654 个节点 |
| 覆盖率 | 配置口径下恰为 `96.0%`：2194 statements，87 missed |
| CI | 6 个 job：`lint`、`audit`、`docs`、`frontend`、`test-sqlite`、`test-postgres` |
| 路由 | 39 条业务路由 |
| Python 依赖 | `requirements.txt` 有 26 个非注释依赖，全部固定版本 |
| 模板 | 8 个 Jinja2 模板、367 行；真实内联 `<script>` 为 0，内联 `on*=...` 为 0 |
| 前端 | 7 个 `app/frontend/*.js` 源文件（6 个构建入口 + 1 个纯布局模块 `er-layout.js`），以及 6 个 `app/static/js/*.js` 构建产物 |
| 数据库迁移 | 6 个迁移：`migrate_0001` 至 `migrate_0006` |
| 文档 | 29 个 tracked Markdown：21 个主项目文档（排除 `.claude`、`.github`）+ 1 个 workflow README + 7 个 Agent Skill |
| 文档站清单 | 当前错误地生成 21 页：它包含 workflow README，却漏掉 `app/frontend/README.md`；修后应为 22 页（若未新增文档） |
| 模块规模 | `app/tools`: 11 个 `.py` / 2136 行；`app/routers`: 10 / 1355；`app/` 根：16 / 1715；模板：8 / 367 |
| 根代码/构建配置 | 当前说明书拟纳入的 10 个文件合计 1587 行（含 1217 行 lockfile） |
| 当前 fresh-checkout 警告 | 仓库自身可修 6 条：2 个同步测试被 module-level asyncio marker 波及、2 个 invalid-escape docstring、Starlette 422 常量弃用产生的调用期警告；另有 1 条 Passlib `crypt` 上游警告 |

计数必须基于 tracked 文件。工作区里 `anime-bar-css.html` 与 `pelican-cyclist.html` 是无关的 untracked 文件，不能纳入项目统计或提交。

## 2. 合并后的确认缺陷

### P1-01　生产启动检查分支串错

- **位置**：`app/startup_checks.py:39-63`。
- **现象**：检查短 `SECRET_KEY` 的 `elif` 实际绑定在 `DB_PASSWORD` 检查上，而不是默认密钥检查上。
- **后果**：
  - DB 密码缺失时，短密钥诊断被压掉；
  - DB 密码存在时，默认密钥同时收到“默认值”和“过短”两条重复诊断。
- **安全修法**：把密钥逻辑写成相邻的 `if default ... elif too_short ...`；DB 检查保持独立 `if`。**不能只把现有 `elif` 改成 `if`**，否则默认密钥仍重复报告。
- **回归测试**：覆盖“默认/短/合格密钥 × DB 密码缺失/存在/DATABASE_URL 存在”的组合。

### P1-02　RAG 缓存不会因同 URL 更新而失效

- **位置**：`app/tools/support.py:45-55,172-212` 与 `app/tools/extract.py:152-165`。
- **现象**：缓存指纹只有 `(count, max(id))`；`save_article()` 对已有 URL 做原行更新，二者均不变，后续检索继续使用旧正文/旧索引。
- **已复现**：同 URL 更新后检索仍命中旧内容。
- **最低安全修法**：成功写入文章后显式失效本进程缓存，并写“先建缓存→更新同 URL→只能搜到新内容”的测试。若要支持多实例，需数据库修订号、可靠 `update_time` 指纹或跨进程失效机制；只清本进程不能宣称解决多实例一致性。
- **文档同步**：修正 TD-214 当前“文章只插入、没有更新路径”的错误前提；实际 `save_article()` 明确支持更新。

### P1-03　Drawio 初始登录态有竞态

- **位置**：`app/frontend/auth.js:108-132`、`app/frontend/drawio-page.js:158-170`。
- **现象**：`auth.js` 加载时立即 `refresh()`；Drawio 稍后才注册 `onChange`。若 `/auth/me` 很快完成，通知发生在监听器注册前，Drawio 会一直显示未登录，直到下一次登录态变化。
- **安全修法**：订阅 API 注册后立即回放当前快照，且保留之后的通知；或提供可等待的 ready/refresh Promise。不要仅靠再发一个重复 `/auth/me` 请求掩盖时序问题。
- **回归测试**：用 Node 真跑源码，分别模拟“refresh 先完成”和“listener 先注册”两种顺序。

### P1-04　CSP 仍无必要地允许脚本内联，且两道哨兵均有盲区

- **位置**：`app/middleware.py:16-21,53`、`tests/test_ops.py:70-97`、`tests/test_frontend_supply_chain.py:63-104`。
- **事实**：8 个模板里没有真实内联脚本或事件处理器；当前唯一“命中”来自 `oauth_consent.html` 的 Jinja 注释文本 `<script>`。
- **修法**：只从 `script-src` 删除 `'unsafe-inline'`；`style-src 'unsafe-inline'` 仍有真实内联样式需求，不能一起删。
- **测试必须同时修**：
  1. 去掉 Jinja/HTML 注释后，反向断言模板没有无 `src` 的脚本、没有 `on*=`，必要时也禁 `javascript:`；
  2. CSP 外部来源扫描必须覆盖 `app/frontend/**/*.js` 与 `app/static/js/**/*.js`。当前两个测试都漏掉真实的 Mermaid ESM import，因此即使误删 jsDelivr 白名单也可能全绿；
  3. 保留 Mermaid 的 jsDelivr 白名单和 Drawio frame origin，除非依赖策略另行改变。
- **同步**：更新 middleware 注释、TD-163、app/templates/static/root 说明书及相关测试文案。

### P1-05　前端把所有成功响应都无条件当 JSON，代理/网关异常时崩成解析错误

- **位置**：至少 `app/frontend/{mermaid-page,er-page,mock-pay-page,shop-page}.js`；同类成功路径也存在于 `auth.js`、`drawio-page.js`。
- **已复现**：API 被预览代理返回 `200 text/html` 时，页面抛 `Unexpected token '<'` 或未处理 Promise rejection，用户看不到可操作错误。
- **修法**：建立小型统一响应解析策略：检查状态和 `Content-Type`，安全读取错误体；2xx 非 JSON/畸形 JSON也应作为协议错误，而不是继续渲染空对象。不要把代理返回的整页 HTML暴露给用户。
- **测试**：每类页面至少覆盖 4xx JSON、5xx/2xx HTML、畸形 JSON、网络错误；继续用 Node 执行真实源码，再重建并核对 6 个产物。

### P2-01　PostgreSQL `COMMENT ON` 标识符关联不完整

- **位置**：`app/tools/sql_ddl.py:242-265`。
- **现象**：`COMMENT ON COLUMN public.t.c ...` 的三段限定名匹配失败；合法未加引号标识符的大小写折叠也可能导致注释找不到表/列。
- **修法约束**：不能只用无条件 `.lower()`，否则会破坏 PostgreSQL 双引号标识符的大小写语义。解析时应保留“是否加引号”，未加引号按方言规则规范化，加引号则精确匹配；表注释、列注释、外键应复用同一标识符规则。
- **测试**：schema-qualified table/column、未引号混合大小写、双引号精确大小写、反引号既有行为、错误大小写不得误配。

### P2-02　恢复流程图响应缺少 ETag

- **位置**：`app/routers/diagrams.py:173-192`。
- **现象**：create/get/update 都返回版本 ETag；restore 返回同一个版本化资源却不返回，客户端必须额外 GET 才能安全 PUT。
- **修法**：给 restore 注入 `Response`，refresh 后设置 `_etag(diagram)`；测试恢复响应头，并用该值完成下一次 `If-Match` 更新。

### P2-03　仓库自身测试警告

1. `tests/test_shop_polling.py:25` 的 module-level `pytestmark = pytest.mark.asyncio(...)` 同时标记了两条同步 Node 测试。  
   **正确修法是删除第 25 行 module marker**（仓库已配置 `asyncio_mode=auto`）；`114`、`143` 是必要的 Node `skipif`，不得删除。
2. `app/routers/admin.py:87` 使用已弃用的 `HTTP_422_UNPROCESSABLE_ENTITY`；改为 `HTTP_422_UNPROCESSABLE_CONTENT`。
3. `tests/test_docs_site.py:129` 与 `tests/test_dynamic_crawl.py:345` 的 docstring 含非法转义；改 raw docstring 或双写反斜杠。
4. Passlib 的 `crypt` 弃用警告来自上游，单独记录，不要伪称仓库警告“全部清零”。

### P3-01　`.env.example` 对默认收款码的说明与实现相反

- **位置**：`.env.example:54-56`。
- **现象**：默认值 `/static/pay_qr.png` 被称为占位图；实际它是用户提供并由 `app/config.py`、TD-225 设为默认的真实收款码。可选占位图是 `/static/pay_qr.svg`。
- **修法**：仅修文案并保留合规/人工确认提醒；不要替换实际配置值。

### P3-02　首页 CTA 使用无效的交互元素嵌套

- **位置**：`app/templates/base.html:65`。
- **现象**：`<a><button>...</button></a>` 把两个交互元素嵌套，违反 HTML 内容模型，也会造成重复的键盘/辅助技术语义。
- **修法**：保留一个 `<a>` 并直接把它样式化成按钮；不要用 JavaScript 模拟链接。增加一个模板结构断言，避免再次出现 anchor/button 双重交互。

### P3-03　文档站构建脚本存在确认的死代码与清点口径分叉

- **位置**：`scripts/build_docs_site.py:28,478-489,668`。
- **现象**：
  - `_render_home()` 的 href 含恒为 `False` 的条件表达式，行为等价于直接读取 `doc_href[d['path']]`；
  - `CODE_EXT` 未被使用，`_source_page_list()` 另写了一份更窄的后缀元组，导致“代码文件”和“源码页”口径分叉；
  - `_source_page_list()` 用裸 `rglob`，本工作区会把两个 untracked HTML 算进去：函数当前返回 120，而相同窄后缀下 tracked 基线是 118。
- **修法**：简化死表达式；对扩展名只保留一个事实来源；清单必须来自 tracked 文件。若源码页继续使用窄后缀，预期 118；若统一使用 `CODE_EXT` 并纳入三个 tracked JSON，预期 121。两种都可，但文档和测试必须明确同一口径，不能靠删除 untracked 文件得到目标数。

## 3. 文档与文档工具的确认缺陷

这不是“搜索替换几个旧数字”即可完成；应先修清点方法，再在最终代码落定后统一重写/复算。

### D-01　根 README

- 模块规模应更新为：tools `11/2136`、routers `10/1355`、app root `16/1715`、templates `8/367`。
- API 标题写 39 条，但表内漏 3 条：`GET /shop`、`GET /shop/orders/{order_no}`、`POST /shop/orders/{order_no}/confirm`。
- Agent Skills 只列 3/7，漏 `deploy-check`、`dependency-audit`、`finish-subitem`、`frontend-build`。
- 模块说明书漏 `app/frontend/README.md`。
- 文档总数必须写清口径：29 tracked Markdown = 21 主项目 + 1 workflow + 7 skills；不能只写“仓库共 21 份”。
- SQLite 当前基线正确；PostgreSQL 应为 `652 + 2`，不是第二方案提出的 `626 + 2`。

### D-02　前端说明书

- `app/static/README.md` 仍按旧 `er.js`/少量静态文件结构写，建议按 6 个构建产物、图片资产、Vite 生成边界整体重写。
- `app/templates/README.md` 仍描述 7 个模板、约 822 行、内联脚本与旧行号；实际 8 个/367 行/0 内联脚本，需整体重写。
- `app/frontend/README.md` 必须纳入根 README 和 `scripts/build_docs_site.py::DOC_GROUPS`。

### D-03　模块说明书

- `app/tools/README.md`、`app/routers/README.md`、`app/README.md` 的模块行数、函数行为与行号基准已漂移，不能只改顶部 SHA。
- `app/routers/README.md` 的路由总表已经列出轮询和人工确认端点；真正过期的是 `shop.py` 详细章节（仍写旧 324 行实现且缺行为说明），所以“新增两条路由”会重复，应该补详细章节。
- `app/README.md` 还缺 `SHOP_MANUAL_QR`、`assert_notify_fresh` 防重放、`pay_qr.png` 静态资产等当前行为。

### D-04　数据库、根文件、架构表

- 数据库现有 6 个迁移。不能机械把“其余 4 个 DO 块”改为“其余 5 个”：`migrate_0006` 是直接的 `CREATE UNIQUE INDEX IF NOT EXISTS`，不是 DO 块。正确描述应区分 0001 显式事务、0002-0005 DO、0006 幂等建索引。
- `docs/ROOT_FILES.md` 当前仍写 6 个根代码/构建文件与 25 个依赖；拟定口径为 10 个文件/1587 行、26 个依赖。需明确 `.env.example` 等为何属于“另述”而不计入 10。
- `docs/ARCHITECTURE_GUIDE.md` 6.9 当前真实结果：crawler 35；politeness 21；extract 16；admin_ingest 18；dynamic_crawl 20 passed + 1 skipped；合计 **110 passed + 1 skipped**，不是只把 28 改成 35 后保留旧总计。

### D-05　HANDOVER / ROADMAP / TECH_DECISIONS

- HANDOVER 技术栈的 FastAPI `0.104` 应更新为 `0.141.1`。
- §8 序号重复需修。
- ROADMAP 未勾选项实为 10：4 个父标题 + 6 个叶子，不是 11 = 5 + 6。
- “上线阻塞项统一为 3 条”不是纯事实修正：TECH_DECISIONS 的正式摘要目前只有 TD-113、TD-124；TD-206（语义阈值未标定）只在启用该能力的生产口径下才可视为阻塞。应先决定口径，再同步摘要表和 HANDOVER，不能强行写 3。
- 不得写“警告三类全部清零”：即使处理 asyncio marker 与 422 常量，两个 invalid-escape 仍需单独修，Passlib 上游警告仍保留。

### D-06　DOCUMENTATION_SUMMARY 与文档校验器

- `DOCUMENTATION_SUMMARY.md` 的文件数、行数、模板/JS/SQL 分类、交付表与“0 漏项”结论均已失真；只补“统计日期”会把错误快照包装得更像可信结论。
- 文档清点应使用 `git ls-files -z`，而不是裸 `rglob`，否则会吸入 untracked 文件和生成物。
- 目录说明书归属应按最长路径前缀匹配；`app/frontend/` 不能被父级 `app/README.md` 假装覆盖。
- `test_doc_groups_all_exist` 只验证“登记项存在”，没有验证“所有应登记 Markdown 都已登记”，所以漏掉 frontend README 仍然全绿；应做集合双向一致性检查并明确排除 7 个 skills。
- 文档站修复当前清单后，应报告 22 文档/39 路由（若实现阶段未新增路由或文档）。当前“21 文档”恰好是“多含 workflow、少含 frontend”的数字巧合。

### D-07　工程约束与构建注释也已漂移

- `AGENTS.md:29,106,111` 把 Node 限定为“仅测试期”，但 TD-221 之后 Node/Vite 也是**构建期**工具；正确边界是“不得引入生产运行时，允许构建与测试”。
- `scripts/build_docs_site.py:191-220` 的当前职责还包括动态注册的 `/shop`，不只是 `/` 与三个 `/tools/*`。`site.py` 的实际注册位置是 41-42，不是 39-40；`TD-141` 只讲多实例内存限流，不足以解释公开页面为何不鉴权，应换成真正相关的决策或删掉误引。
- `vite.config.mjs:22` 声称固定文件名搭配“中间件的 Cache-Control”，但 middleware 并未给静态 JS 设置该响应头。文档同步轮应先删除虚假断言；若要新增缓存策略，则属于需要行为测试的独立设计决定，不能为兑现注释而随手实现。
- `er.js`、当前内联脚本等旧称谓确有多处，但“原先是内联脚本”一类历史说明仍然正确。只能改错误的**当前时态**引用，不能要求字面量全仓清零。
- `.claude/skills/pre-commit-review/SKILL.md` 的提交命令硬编码旧会话分支，也应改成当前分支占位/动态取值，避免后续助手向错误分支推送。

### D-08　历史数字不得全局抹除

- `624`、旧 CI job 数、旧版本号等有些出现在带日期的升级记录、实验记录和 TD 决策历史中，属于历史证据，必须保留。
- 只更新声称“当前”“现有”“预期”的断言；历史段落必要时追加“当前基线见……”交叉引用。
- 因此第二方案的验收条件“grep 不再出现 617/619/624/…/FastAPI 0.104”等是**危险且不可接受**的。

## 4. 第二份方案 `review-plan-2.txt` 的逐项结论

| 项 | 结论 | 订正 |
| --- | --- | --- |
| A1 | 部分正确 | 警告真实，但错误定位成 114/143 的 asyncio decorator；那两行是必须保留的 Node `skipif`。删第 25 行 module marker。 |
| A2 | 确认 | 改为 `HTTP_422_UNPROCESSABLE_CONTENT`。 |
| A3 | 原则确认、方案不完整 | 可删 script-src 的 inline；还要修 Jinja 注释假阳性和两个漏扫 frontend/static-js 的来源哨兵。style-src 不能删。 |
| A4 | 确认 | 新发现的真实文档缺陷。 |
| B1 | 混合 | SQLite、文件/行数正确；PostgreSQL `626 + 2` 错，真实 `652 + 2`；禁止篡改历史记录。 |
| B2 | 大体确认 | 三条 shop API、4 个 skills、frontend README 与文档口径均确有遗漏。 |
| B3 | 确认 | static README 应整体重写。 |
| B4 | 确认但低估范围 | templates README 不只是标题、行数、表格过期，流程与安全说明也需重写。 |
| B5 | 确认但不完整 | tools README 需更新；不能只换顶部 SHA 而保留错误行号/行为。 |
| B6 | 部分正确 | 路由总表已有两端点；详细 shop.py 章节才缺失。 |
| B7 | 确认但不完整 | 三项确实缺；app README 还有大量行号/行为漂移。 |
| B8 | 部分正确且机械替换危险 | 迁移数是 6，但“其余 5 个 DO 块”错误，0006 不是 DO。其他文档也有同一旧数。 |
| B9 | 确认 | 26 依赖正确；10 文件口径可用，但需解释边界并复算 1587 行。 |
| B10 | 部分正确 | crawler=35；另一个旧值 dynamic=20+1；五文件合计=110+1。 |
| B11 | 混合 | FastAPI、序号、ROADMAP 算术正确；警告清零和无条件“3 个 blocker”不成立。 |
| B12 | 不充分/不安全 | 加日期不能修复虚假“全覆盖”；必须修统计方法和正文。 |
| C1/C3 | 可保留 | Ruff 与前端 drift gate 合理。 |
| C2 | 需改写 | 当前数量可复核，但修复新增测试后应重新计数；Passlib warning 需单列。 |
| C4 | 错误期望 | 纳入 frontend README 后应为 22 docs / 39 routes，而不是 21 / 39。 |
| C5 | 拒绝 | “旧字符串全部消失”会破坏历史记录，只能语义审查当前断言。 |
| C6 | 不足 | 最终验收必须补 full PostgreSQL、fresh compile warnings、pip-audit、文档集合/链接检查及针对缺陷的回归测试。 |

## 5. 第三份“文档同步与小修轮”方案的比较

### 5.1 基线数字

| 声称 | 结论 | 说明 |
| --- | --- | --- |
| SQLite `650 + 4 = 654`、42 个测试模块 | 确认 | 与独立全量运行一致。 |
| 路由 44 总条目 / 39 业务 method-path / 34 业务唯一路径 | 确认 | 39 业务条目中 `/diagrams`、`/diagrams/{id}`、`/oauth/authorize`、`/tools/mermaid` 复用路径；另有 4 个 FastAPI 文档路由和 1 个 static mount。 |
| 配置 41 | 确认 | `Settings` 与 `.env.example` 均为 41，集合一致。 |
| 依赖 26 = runtime 19 / test 5 / docs 1 / lint 1 | 确认 | 分类与 `requirements.txt` 的章节一致。 |
| CI 6 | 确认 | 与工作流一致。 |
| 文档站 84/218/39/281/21、core 40/93 | 数值复现，但 21 不是正确目标 | 当前生成器确实输出这些值；21 页漏 `app/frontend/README.md`，修后应为 22。 |
| 源码页 118 | 有条件正确 | 这是当前窄后缀集合下的 **tracked** 数。当前实现裸扫工作区会因两个 untracked HTML 实际返回 120；使用完整 `CODE_EXT` 后 tracked 目标则是 121。 |
| PostgreSQL | 方案缺失 | 已独立全量运行：`652 passed + 2 skipped`，最终修复后仍须重跑。 |

### 5.2 代码小修

- **与本审计一致**：收紧 CSP、422 常量、两个 raw docstring、删除 `test_shop_polling.py:25` 的 module marker。
- `test_proxy_headers.py` 与 `test_username_validation.py` 的测试函数全部是 async，没有当前警告；只需确认，不应因“同模式”而顺手删除。它们的 session loop scope 是有意配置。
- `_render_home` 死表达式、未使用 `CODE_EXT`、`base.html` 的 `<a><button>`、route 行号/TD 误引、Vite Cache-Control 虚假注释，均是新方案补充的真实小问题。
- “清掉 8 处 stale 注释”只能作为语义审查清单，不能按 `er.js`/“内联脚本”字面全删；多处“原先……”是应保留的历史说明。
- CSP 方案仍漏了本审计确认的两项：Jinja 注释假阳性，以及 CSP/CDN 测试没有扫描 `app/frontend` 与 `app/static/js`。

### 5.3 文档与校验

- `DOCUMENTATION_SUMMARY` 的 100/91/99 确需统一；正确的 broad tracked-code 口径是排除 `.claude` 和整个 `docs/site` 后 **121 个**。当前最近前缀归属检查还会发现 6 个未被所属说明书提及的 tracked 文件，必须随文档补齐后才可能得到 121/0。
- `总览.md` §8① 当前 `str.startswith()` 参数写错，执行会抛 `TypeError`；修为前缀 tuple 是确认项，但还必须改成 tracked inventory，否则两个临时 HTML 会污染结果。
- AGENTS 的 Node 约束、8 课而非 7 课、core 93 edges、39 routes、脚本 950 行、CI 304 行、6 migrations 等当前断言确需同步。
- 旧 PostgreSQL `563 + 2` 是当前门禁处的过期值；已有独立结果是 `652 + 2`。带日期的真正历史实验仍应保留，不接受“所有旧数字零残留”。
- “warnings 回到 1 条”在修掉 6 条仓库自身警告后是合理目标；剩余 1 条为明确列账的 Passlib 上游 warning。
- `SUMMARY §6① = 121/0` 只有在使用 tracked 文件、最长前缀归属并补齐 6 个说明书遗漏后才成立；不能通过删除 untracked 作品文件达成。
- `npm run build` 与产物 drift 是合理门禁，但还应有 `npm ci`、full PostgreSQL、pip-audit 和针对行为缺陷的回归测试。

### 5.4 流程与范围

- 给 `pre-commit-review` 增加子 README、总览锚点、前端产物检查是有效的流程补洞；检查应调用可执行脚本，而不是再硬编码一批易腐数字。
- “每个子项都做变异验证”只适用于关键行为逻辑；纯文档、注释和死表达式清理应使用确定性 before/after 校验，不应伪造变异结果。
- 该方案要求在 `arena/01a0599b-codemax-platform` 修改并逐项 push，**与当前固定会话分支及用户当前的只比较约束冲突**。本会话只能在 `arena/01a0852c-codemax-platform` 工作，且未获授权前不推送修复代码。
- 该方案自称“小修轮”，因此没有覆盖已经复现的 startup 分支串错、RAG 同 URL 缓存失效、Drawio 登录竞态、schema-qualified SQL comments、restore ETag、全前端非 JSON 响应等问题。它可并入统一计划，但不能替代完整修复计划。

## 6. 其他建议的分类（避免误修）

| 建议 | 分类 | 处理 |
| --- | --- | --- |
| `PGPORT=55432` | 已证伪 | 当前 `pgserver==0.1.4` Linux 环境走 Unix socket/default 5432；不要写死 55432。 |
| shell/数据库 URL 写 `&amp;` | 已证伪 | HTML 转义不能写入 shell URL。 |
| 把 OAuth2 写成 OIDC | 已证伪 | 本项目是 OAuth2 authorization-code flow，没有 OIDC identity layer。 |
| politeness 加随机 jitter | 可选增强/非缺陷 | 当前同域请求已在锁中排成严格间隔；jitter 需明确跨实例威胁模型和上下界，不能只给 `sleep(wait)` 随手加随机数。 |
| 全仓递归扫描每个 URL | 不安全方案 | 会把注释、文案、非网络字面量误报为 fetch 来源；应按 HTML tag/ESM import/fetch 等实际语法定向扫描。 |
| OAuth fixture 与 SQL seeds | 当前一致、测试缺口 | 可加合同测试验证 client ID、redirect URI、name 与 bcrypt secret，但不能报告为当前数据漂移。 |
| CSS 中文注释乱码 | 误报 | 文件 UTF-8 正常，不改。 |
| Passlib `crypt` warning | 外部/上游债 | 单列允许，不要用全局忽略吞掉仓库其他弃用警告。 |
| `ecdsa` PYSEC-2026-1325 | 外部 blocker | 上游无修复版本；保留精确的 pip-audit ignore 与 TD 记录。 |
| 真实微信支付、OSS/COS、embedding 阈值、真浏览器 | 外部资源/已记录债 | 不得伪造验证结果；有凭据/浏览器时再完成相应验收。 |

## 7. 建议实施顺序

1. 先为 P1/P2 行为缺陷写会失败的最小回归测试，再修 startup、RAG、Drawio、SQL、restore ETag。
2. 统一前端响应解析；修 CSP 和两道安全哨兵；`npm run build` 同步 6 个产物。
3. 清理 6 条仓库自身警告；保留并注明 Passlib 上游警告。
4. 修文档清点/manifest 的集合验证，再以 tracked 文件为源重新生成全部数字。
5. 最后统一重写 README/HANDOVER/TD/架构表；仅更新当前断言，保留日期化历史。
6. 在最终测试数稳定后才写新基线和行号 SHA；不要在中途硬编码预期总数。

## 8. 最终验收门槛

```text
ruff check .
python -Wall -m compileall -f -q app tests scripts
python -m pytest -q                         # SQLite，全量；以修后 collect 数为准
TEST_DATABASE_URL=... python -m pytest -q  # PostgreSQL，全量
python -m pytest --cov=app ...             # 维持配置门槛，当前基线 96.0%
npm ci
npm run build
git diff --exit-code -- app/static/js
pip-audit --strict -r requirements.txt --ignore-vuln PYSEC-2026-1325
python scripts/build_docs_site.py --data-only
python scripts/build_docs_site.py
```

另外必须验证：

- startup 组合诊断、RAG 同 URL 更新、Drawio 两种时序、schema-qualified/case-sensitive SQL comments、restore→PUT ETag；
- 2xx/4xx/5xx 非 JSON 前端响应不会崩溃；
- 模板无真实 inline script/handler，CSP 仍覆盖 Mermaid 与 Drawio 的实际外部来源；
- `DOC_GROUPS` 与应收录的 tracked Markdown 双向一致，修后预期 22 docs / 39 routes（若无新增）；
- 文档本地链接与锚点有效；所有“当前”数字由最终 HEAD 复算；
- `git status` 不纳入或提交两个无关 untracked HTML 文件。
