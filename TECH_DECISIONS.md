# 技术实现取舍清单（TECH DECISIONS）

> 本项目所有**实现层面的取舍**集中在此，按编号引用（如 TD-41）。
> 模块 docstring 里只留与该文件强相关的局部说明，全局取舍以本文为准。
> 更新日期：2026-09-01 ｜ 对应代码：分支 `arena/01a0599b-codemax-platform`（本文随代码同步更新，不钉 commit）

## 阅读方式

- **取舍**＝选了什么；**放弃了什么**＝另一条路；**代价**＝现在要承受的后果；**何时回头改**＝触发条件。
- 大部分取舍是"毕设阶段够用、且符合 AGENTS.md 第 1 条（代码尽量简洁）"的结果，不是能力缺失。
- **同一个 TD 可能在两处出现**：上面「上线阻塞项」摘要表（3 列、精简口径）与后面按主题分的
  明细表（5 列、完整口径）会各列一次。实测目前是 **TD-113**（微信支付未真机联调）与
  **TD-124**（模拟支付通道）这两条。所以 `grep -oE '^\| TD-[0-9]+' | sort | uniq -d`
  **会报这两个「重复」，那是交叉列举不是编号冲突**，不要照着改编号 ——
  改编号会打断全仓的交叉引用。判断是否真冲突要看内容是否指同一个决策。
- 计数口径（实测，三个数都不同，别混）：
  - **在用的表格行**（`^| TD-n`，不含划掉的）**152** 条
  - **全部表格行**（含 `~~TD-n~~ **已解决**`）**169** 条
  - **全文提及**（含交叉引用与正文里的 `TD-n`）唯一 **169** 个

  引用本文时请说清用的是哪个口径，否则会得出互相矛盾的数字。

## ⚠️ 上线阻塞项（这些必须在上线前处理，不是可选项）

| 编号 | 事项 | 为什么阻塞 |
| --- | --- | --- |
| ~~TD-15~~ | ~~公开工具端点无限流~~ **已解决** | `app/ratelimit.py` 内存滑动窗口：工具 30 次/60s、LLM 10 次/60s、注册登录 10 次/60s，超额 429 + `Retry-After`（多实例部署的代价见 TD-141） |
| ~~TD-44~~ | ~~JWT 存 localStorage~~ **已解决**：登录态改走 **HttpOnly cookie**（`SameSite=Lax`，生产加 `Secure`，`Max-Age` 与 token 同寿命），前端不再碰 `localStorage`/`sessionStorage`（模板里已一处不剩）。Bearer 头**同时保留**，Swagger 与 API 客户端照旧；两者都带时以请求头为准。新增 `POST /auth/logout` 清 cookie。配套把有副作用的 `GET /shop/download/{order_no}` 改成 POST（TD-177），并把「需要登录的 GET 路由」钉成带理由的清单（TD-176） | 改用 cookie 就引入了 CSRF（TD-176/177）；`GET /oauth/authorize` 是残留面（TD-175） |
| ~~TD-64~~ | ~~流程图无配额、无软删除~~ **已解决（配额 + 软删除）**：每用户存活流程图上限 `DIAGRAM_QUOTA`（默认 50），超额 409 并把上限写进错误信息；删除改为打 `deleted_at` 时间戳，新增 `POST /diagrams/{id}/restore` 与 `GET /diagrams?deleted=true`（回收站）。对调用方而言删除语义不变（删完 GET 就 404、列表里也没有）。**恢复也要占配额**，否则「建满 → 删 → 恢复」就是绕过上限的后门。迁移：`database init/migrate_0003_diagram_deleted_at.sql`（已在真 PG 上验过升级与重跑两条路径） | 配额只管**存活**行，回收站不会自动清空 → 表增长仍不受约束（TD-179）；版本历史仍未做（TD-180）；配额检查有 TOCTOU 窗口（TD-178） |
| ~~TD-70~~ | ~~JWT 无 `jti`、无法吊销~~ **已解决**：改用**令牌版本化** —— JWT 带 `pwd` 声明（签发时 `password_changed_at` 的 UNIX 秒），`get_current_user` 每次与库里当前值比对，早于它就拒。新增 `POST /auth/password`（限流），改密码即吊销该用户**所有**旧 token，并返回一个新 token 让当前会话不掉线。**订正原文一处不准确的说法**：「封号后旧 token 仍然有效」并不成立 —— `get_current_user` 一直都查库并检查 `status != 1`，禁用是立刻生效的（`test_disabled_user_token_is_rejected_immediately` 钉住） | 撤销粒度是「按用户」而非「按单个 token」：无法只踢掉某一个会话而保留其他会话 |
| ~~TD-80~~ | ~~集成测试跑在 SQLite 上~~ **已解决** | 现在 `TEST_DATABASE_URL` 可整套跑真 PostgreSQL 16.2（当前基线：真库 534 passed + 2 skipped / SQLite 532 + 4 skipped），见 TD-121 |
| ~~TD-84~~ | ~~无 CI~~ **已解决** | `.github/workflows/ci.yml`：三个 job（ruff 静态检查 / SQLite / 真 PostgreSQL 16 service 容器），PG job 另建库把建表脚本连跑两遍验证幂等。已实跑：run 33512433132（push）与 33512433325（pull_request）均 `success`，两个 job 全部 step 通过。注释头也已在 `4b145b5` 修正（原先 `2f3223a` 纯重命名时把激活前那段「待激活/从未跑过」的注释一起搬了进来）。GitHub App 已于 2026-09-01 取得 Workflows 写权限，workflow 可直接改并 push |
| ~~TD-90/91~~ | ~~无日志、无监控、无安全响应头~~ **已解决（S5-03-3）**：`app/middleware.py` 出安全头 + CSP + 每请求结构化日志（带 `X-Request-ID`，上游给了就沿用）；`app/routers/health.py` 出 `/healthz` 存活探针与 `/readyz` 就绪探针（后者查库、失败 503）；`Dockerfile` / `docker-compose.yml` / `docs/DEPLOY.md` 齐备 | 仍未接集中式日志与指标采集（Prometheus/ELK），报警规则只在文档里给了建议阈值 |
| TD-113 | 微信支付未经真机联调 | 沙箱无商户号/证书/公网回调，签名与报文只能算法级验证 |
| ~~TD-109~~ | ~~无超时关单，二维码过期后订单一直挂着~~ **已解决（S5-01-1）**：超过 `ORDER_EXPIRE_MINUTES` 的待支付单会在下次下单时被关掉并另起新单，用户有出路了。回归测试 `test_expired_pending_order_is_closed_and_replaced` | 待支付单会无限堆积，且过期二维码扫码必失败 |
| TD-124 | 模拟支付通道若在生产误开＝免费发货 | **已大幅缓解（S5-03）**：`ENV=production` 时启动自检会把 `SHOP_PAY_MODE=mock` 判为不合规并**拒绝启动**（`app/startup_checks.py`，`test_mock_pay_in_production_is_the_first_thing_reported` 守着），不再依赖「上线前记得看一眼 `.env`」。残留风险：若 `ENV` 本身忘了设成 `production`，自检不会生效 —— 所以部署清单里 `ENV` 是必填项 |
| ~~TD-133~~ | ~~爬虫不读 robots.txt、不限速、无抓取间隔~~ **已解决**：新增 `app/tools/politeness.py`，抓取前读并遵守 robots.txt（结果按域缓存 1 小时，避免为了抓几十篇而反复打扰目标站）、按域遵守 `Crawl-delay`（无则用默认 2 秒）、全局并发上限 4。robots 判定按通行约定：404/410 视为无限制，**401/403 视为全站禁止**，**5xx 视为规则不可知则本次不抓**（宁可漏抓可重跑，不可被封 IP）。4 项变异测试全部被杀 | robots 与限速是进程内状态，多实例部署时每个实例各有一份缓存与节流窗口，对目标站的实际请求频率会按实例数放大 |

---

## 一、技术选型（语言与库）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-01 | 导出 Word 用 `python-docx`（`app/tools/word.py`） | Apache POI（Java 库） | 无 | 除非要复用 Java 生态 |
| TD-02 | 爬虫用 `httpx` + `BeautifulSoup4`（阶段四落地） | HttpClient + Jsoup（Java 栈） | 无 | — |
| ~~TD-03~~ | ~~动态页面抓取推迟到阶段四再定 Selenium / Playwright~~ **已选型：Playwright**（用户 2026-09-03 定），实现见 TD-191 | 现在就锁定方案 | ~~阶段四开工前要先做一次选型~~ 已定 | 已完成 |
| TD-04 | 前端 Jinja2 SSR，不引入 Node / Nuxt / Next | SSR 框架的水合、路由、构建能力 | 交互全靠手写原生 JS，无组件复用 | 页面数量或交互复杂度显著上升时 |
| TD-05 | DDL 解析用正则 + 自写字符扫描器，不引入 sqlparse / sqlglot | 现成语法树的完备性 | 需自己维护转义、注释、括号边界（已因此修掉 10 个 bug） | 要支持存储过程、触发器、分区表等复杂 DDL 时 |
| TD-06 | 依赖全部钉死版本（`requirements.txt` 实测 **25 个依赖，25 个全部带 `==`**，无一例外） | 自动获取补丁更新 | 需手工升级 | — |
| TD-07 | 运行时不引入 Node，`node` 仅用于测试期 | — | 测试环境需要 node（沙箱内置 v22） | — |

## 二、DDL 解析器（`app/tools/sql_ddl.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-10 | 多词类型只取首词 | 完整类型名 | `DOUBLE PRECISION`→`DOUBLE`、`CHARACTER VARYING(50)`→`CHARACTER`，ER 图类型显示不精确 | 需要精确类型（反向生成建表脚本、字段级校验）时 |
| TD-11 | `DEFAULT` / `COMMENT` 只认单引号字面量 | MySQL 的双引号字符串 | MySQL 下 `COMMENT "x"` 取不到值。不放开是因为 PostgreSQL 里双引号是标识符，放开会误判 | 明确只服务 MySQL 时加方言开关 |
| TD-12 | 不支持 PostgreSQL 美元引用字符串 `$$ ... $$` | — | 含 `$$` 的 DDL（少见）会解析错乱 | 需要解析函数体 / 触发器时 |
| TD-13 | 未闭合的字符串 / 块注释视为延续到结尾 | 错误恢复能力 | DDL 本身语法有错时，出错点之后的内容全部失效 | — |
| TD-14 | 悬空外键（目标表不在同一份 DDL 内）在图上直接丢弃（`er.js:51`） | 用虚线标出"未解析的引用" | 用户看不出自己漏贴了哪张表 | 有用户反馈困惑时加提示 |
| TD-15 | `POST /tools/er-diagram`、`/tools/mermaid`、`/tools/word-export` 均不鉴权（引流工具要 SEO 收录、要游客能直接用），改用**按 IP 限流**防滥用 | 鉴权这道防线 | 限流是进程内存的（TD-141），且不认登录身份，同一 IP 下的多个用户共享配额 | 需要按用户配额或分布式限流时 |
| TD-16 | DDL 上限 20000 字符（`schemas.py:26`） | 超大脚本 | 超限返回 422 | 有真实大 schema 需求时 |

## 三、LLM → Mermaid（`app/tools/llm.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-20 | 只实现 OpenAI 兼容 `/chat/completions`，不做 provider 抽象 | LangChain / 多 provider 策略模式 | 换非兼容协议的模型要改代码 | 需要接入不兼容 OpenAI 协议的模型时 |
| TD-21 | `temperature` 固定 0.2，无重试、无流式输出 | 可调参数、失败重试、打字机效果 | 偶发失败直接 502 给用户 | 上线后按失败率加重试与流式 |
| TD-22 | 只校验首关键字属于 Mermaid 图类型，不校验语法 | 语法级校验（需引入 mermaid parser） | 语法错的图会在前端渲染失败 | — |
| TD-23 | Prompt 硬编码为模块常量 `SYSTEM_PROMPT` | Prompt 版本管理 / AB 测试 | 改 Prompt 需要发版 | 需要持续调优时 |
| TD-24 | 未配 `LLM_API_KEY` 时直接 502，不做本地兜底 | 模板化兜底输出 | 演示环境未配密钥时该工具不可用 | — |
| TD-25 | 请求超时固定 60s | 按模型分级超时 | 慢模型会让请求长时间挂住 | — |
| TD-26 | 输入上限 10000 字符（`schemas.py:30`） | 长代码输入 | 超限返回 422 | — |

## 四、Word 导出（`app/tools/word.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-30 | 固定 6 列：字段 / 类型 / 主键 / 可空 / 默认值 / 注释 | 索引、唯一约束、外键明细列 | 导出的数据字典不含索引信息 | 需要交付正式数据字典时 |
| TD-31 | 文件名固定 `data_dictionary.docx` | 按表名 / 时间戳命名 | 多次导出同名覆盖 | — |
| TD-32 | 用 python-docx 默认模板 + `Table Grid` 样式 | 企业模板 / 封面页 | 样式朴素 | — |
| TD-33 | 只输出文字表格，不内嵌 ER 图图片 | 图文并茂 | 需在 Word 里自行插图 | 需要图文一体时（要引入图片渲染依赖） |

## 五、前端与渲染

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-40 | d3 / mermaid / drawio 全部走公网 CDN（jsdelivr、`embed.diagrams.net`） | 本地打包自托管 | 离线、内网或 CDN 被墙时页面不可用 | 部署到国内且 CDN 不稳时改自托管 |
| TD-41 | ER 图用固定网格布局（每行 3 个，`er.js` `COLS_PER_ROW=3`） | 力导向 / 层次布局 | 表多时外键连线交叉、观感一般 | S5-02-2 渲染性能与体验优化时 |
| TD-42 | 样式内联在 `base.html`，不抽 CSS 文件、无构建步骤 | 样式复用与压缩 | 每个页面都加载全部样式（实测 1689 字节，可忽略） | — |
| TD-43 | Drawio 的 `autosave` 事件只更新内存里的 XML，不自动写库（`drawio.html:75`） | 自动云同步 | 用户不点"保存到云端"就会丢改动 | 有用户反馈丢图时加防抖自动保存 |
| ~~TD-44~~ | ~~JWT 存 `localStorage`~~ **已解决**：HttpOnly cookie（`SameSite=Lax`，生产 `Secure`）+ 保留 Bearer 双通道；前端「是否已登录」改为问 `/auth/me`，因为脚本读不到 cookie 也就无从判断 | 见 TD-175/176/177 | — |
| TD-45 | 无 PNG / SVG 导出 | 图片导出 | 只能截图 | — |

## 六、SEO 与站点（`app/site.py`、`app/routers/site.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-50 | 页面路由由 `PAGES` 循环生成，且 `include_in_schema=False` | 显式路由声明 | `/docs` 里看不到页面路由（只有 API） | — |
| TD-51 | sitemap 只输出 `<loc>` | `<lastmod>` / `<changefreq>` / `<priority>` | 搜索引擎抓取优先级信号偏弱 | 内容更新频繁后补 `lastmod` |
| TD-52 | 无缓存头、无 ETag | CDN / 浏览器缓存 | 每次都回源 | 上量后加 |
| TD-53 | 无 i18n | 多语言 | 仅中文 | — |
| TD-54 | Drawio 页面对游客开放（能画不能存） | 所有工具都需登录 | 游客产出的图不落地 | 不改——这本来就是引流设计 |
| ~~TD-55~~ **已解决**（S2-02-2） | S2-02-2「引流→变现」转化路径本轮跳过 | 工具页的转化引导 | 目前工具页没有任何商业化入口 | 商业平台上线后 |

## 七、流程图存取（`app/routers/diagrams.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-60 | 非本人记录一律返回 **404**（不是 403） | 明确告知"无权限" | 调试时不易区分"不存在"与"没权限" | 不换（避免探测他人资源是否存在） |
| TD-61 | 列表接口不返回 `content` 大字段 | 一次请求拿全 | 打开某张图要再发一次请求 | — |
| TD-62 | 列表无分页，按 `update_time` 倒序全量返回 | 分页 / 游标 | 图多时响应变大 | 单用户图数量上百时 |
| TD-63 | `content` 上限 500000 字符（`schemas.py:35`） | 超大图 | 超限返回 422 | — |
| ~~TD-64~~ | ~~无软删除、无每用户配额~~ **已解决**：`deleted_at` 软删除 + 恢复端点 + `DIAGRAM_QUOTA` 配额。**版本历史仍没做**，拆成 TD-180 单独记 | 见 TD-178/179/180 | — |
| ~~TD-65~~ | ~~无乐观锁~~ **已解决**：`sys_diagram.version` 列 + 标准 `ETag`/`If-Match`/`412`。GET/POST/PUT 都回 `ETag`，保存必须用 `If-Match` 带回来；缺头 **428**（不放行），版本对不上 **412 且一个字都不写**。判定用**原子 CAS**（`UPDATE ... WHERE version=?`）+ 检查 rowcount，不是「先读出来比一比再写」—— 后者并发下两边都会读到同一版本、都通过检查、都写进去，锁等于没加（与 TD-158 同一个道理）。**协同编辑仍没做**，拆成 TD-181 单独记 | 见 TD-181/182 | — |

## 八、认证与 SSO

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| ~~TD-70~~ | ~~无状态 JWT，payload 只有 `sub` + `exp`~~ **已解决**，见本文开头「上线阻塞项」表里的 TD-70 条 | `jti` / 黑名单 / 吊销表 | ~~token 无法主动失效~~ **原「改密码或封号后旧 token 仍有效」的说法已不成立**：`get_current_user` 每次查库比对 `pwd` 声明与 `status`，两者都立即生效 | — |
| TD-71 | 双平台共用同一个 `SECRET_KEY` | 每平台独立密钥 | 一处泄露影响全平台 | 安全评审要求时 |
| TD-72 | 每次请求按 `username` 查库（`deps.py:20`） | 按主键 id 查 / 缓存 | 多一次非主键查询 | 压测发现瓶颈时 |
| TD-73 | 无 PKCE | 公共客户端的授权码保护 | 移动端 / 纯前端客户端场景偏弱 | 接入纯前端客户端时 |
| TD-74 | `redirect_uri` 精确匹配，不支持多回调或通配 | 灵活性 | 一个客户端只能配一个回调地址 | 客户端需要多环境回调时 |
| TD-75 | 无 `refresh_token` | 长会话 | 到期需重新走一遍授权 | — |
| TD-76 | OAuth 错误统一返回 **400**（含 `invalid_client`） | 401 语义 | 与部分 RFC 建议不一致 | 对接严格客户端时 |
| TD-77 | 授权码 10 分钟有效 + 原子 CAS 消费 | — | — | — |
| ~~TD-78~~ | ~~无授权同意页~~ **已解决**：`GET /oauth/authorize` 改为**只渲染同意页**（显示申请方名称、回调地址、当前登录账号，同意/拒绝两个按钮），签发授权码挪到 `POST /oauth/authorize`。拒绝时按 OAuth 规范 302 回 `error=access_denied` 并原样回传 `state`；`approve` 默认值是 `"0"`（拒绝），表单少传字段时落到更安全的一侧。同意页是纯表单，**不含内联 `<script>`**（不再给 TD-163 添理由）。同时关掉了 TD-175 那个 CSRF 面 | 见 TD-184/185 | — |
| TD-79 | 注册时"先查后插"判重（`auth.py`） | 依赖唯一约束 + 捕获 `IntegrityError` | 并发注册同名用户可能返回 500 而非 400。**未修**：该竞态在 SQLite 测试里无法复现，无法为其写出会红的测试 | 有真实并发注册场景时 |

## 九、测试策略

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-80 | 集成测试**默认**跑内存 SQLite（`StaticPool`），但可用 `TEST_DATABASE_URL` 整套跑真 PostgreSQL | 只用一种库 | 要真库覆盖就得跑两遍 | ~~上生产前跑一次真库集成测试~~ **本轮已做到**（起库步骤见 HANDOVER §9） |
| TD-81 | LLM 用假客户端 + `httpx.MockTransport`，绝不真打网络 | 真实模型行为验证 | Prompt 的实际效果无法自动验证 | — |
| TD-82 | ER 前端契约测试用 node 真跑 `er.js`；无 node 时退化为静态字段检查 | 纯 Python 测试 | 需要 node；退化分支覆盖较弱 | — |
| TD-83 | `scripts/check_schema_pg.mjs`（PGlite 真 PG 体检）**不接入 pytest** | 每次改动都验建表脚本 | 需手动跑，且要装约 26MB 的 npm 包 | 有 CI 之后接进去 |
| TD-84 | CI 内容一开始只能入库为 `docs/github-actions-ci.yml`，由仓库管理员 `git mv` 到 `.github/workflows/ci.yml` 才生效 | push 即生效的 CI | `arena-ai-coding-agent[bot]` 当时无 Workflows 写权限，push 被 `remote rejected ... without workflows permission` 拒绝，只能靠仓库管理员 `git mv` / `git am` 绕行。**该限制已于 2026-09-01 解除**（用户授予 Workflows: Read and write），现在 workflow 改动可直接 push。保留本条是为了记录那段历史与绕行办法 |
| TD-85 | 并发类修复（授权码 CAS）在 SQLite 上只验证原子语义；真 PG 上有 `test_code_single_use_under_real_concurrency` 真并发跑 | — | SQLite 跑时该条 skip | ~~有 PG 集成测试环境时补~~ **已补**（去掉 rowcount 检查后真 PG 上 5/5 变红） |
| TD-121 | 测试库可用 `TEST_DATABASE_URL` 切到真 PostgreSQL（`tests/conftest.py`），默认仍是内存 SQLite | 只支持一种库 | 要真库覆盖得跑两遍（SQLite 55s / PG 59s） | — |
| TD-122 | 连真库时引擎用 `NullPool` | 默认连接池（更快） | 每个用例重连；换池会报 `got Future attached to a different loop`（asyncpg 连接绑事件循环，pytest-asyncio 每用例新 loop） | — |
| TD-123 | 沙箱里的 PostgreSQL 用 PyPI 的 `pgserver`（自带 PG 16.2 二进制），数据目录 `/tmp/pgdata` | apt 安装（Debian 源在沙箱不可达） | `/tmp` 不在工作区快照里，沙箱重启后要重新建库 | 有 CI 后改用 services 容器 |
| TD-124 | 加了「模拟支付通道」：`SHOP_PAY_MODE=mock` 时下单不调微信、`code_url` 指向本站模拟收银台、`POST /shop/mock-pay/confirm` 直接置为已支付 | 无商户号时的真实支付联调 | **开着就等于免费发货**（已列入上线阻塞项）；默认 `wechat`，生产模式下这两个端点一律 404 | 拿到商户号后 `.env` 改回 `wechat` 即可，代码不用动 |
| TD-125 | 模拟支付复用与真实回调**完全相同**的状态机与幂等逻辑（`mark_paid`），只在"谁触发迁移"上不同 | 为演示单写一套逻辑 | 无 | 不换（演示跑通的路径＝生产路径，不会漏测） |
| TD-126 | 模拟流水号写成 `MOCK-<订单号>` | 伪造一个像微信的流水号 | 一眼能认出是演示数据，但也意味着上线前要清库 | 上线前清理演示订单 |
| TD-127 | 模拟收银台的 `code_url` 用请求的 `base_url`，不用 `SITE_BASE_URL` | 统一用站点域名 | 反向代理下若 `base_url` 不准，链接会指错 | 需要固定域名时 |
| TD-128 | 云存储只实现**本地后端**；阿里云 OSS / 腾讯云 COS 的适配器刻意不写 | 多云真适配 | 换云厂商要新写一个实现（`Storage` 接口已留好，业务代码不用改） | 拿到 OSS/COS 密钥时（S3-02-1） |
| TD-129 | 「已下载」标记发生在**发出链接时**，不是文件真的被下载时 | 按实际下载记账 | 领了链接没下载也算已下载；链接被转发后在 TTL 内可重复下载 | 需要精确计量时改用云端访问日志 |
| TD-130 | 预签名 URL 自己用 `HMAC-SHA256(SECRET_KEY, "key\nexpires")` 签 | 用云厂商 SDK 签 | 与 `SECRET_KEY` 耦合：换密钥会让已发出的链接立刻失效 | 换云后端时交给 SDK |
| TD-131 | 未知存储后端**直接报错**，不静默退回本地 | 容错回退 | 配置写错会导致下载不可用（503） | 不换（静默降级＝把文件从对象存储挪到应用目录，是安全性降级） |
| TD-132 | 商品只有一个对象 key（`STORAGE_PRODUCT_KEY`） | 商品↔文件映射表 | 加商品要改配置甚至改代码 | 需要多 SKU 时（与 TD-108 一起改） |
| ~~TD-133~~ | ~~爬虫不读 robots.txt、不限速、无抓取间隔~~ **已解决**，见本文开头「上线阻塞项」表里的 TD-133 条 | 不做礼貌性约束 | ~~高频抓取可能被目标站封 IP~~ 已由 `app/tools/politeness.py` 覆盖 | 多实例部署时每个实例各有一份缓存与节流窗口，实际频率按实例数放大 |
| TD-134 | BeautifulSoup 用 stdlib 的 `html.parser`，不用 `lxml` | lxml 对畸形 HTML 更宽容、更快 | 极端畸形的页面解析结果可能有差异 | 遇到解析不出来的真实页面时换 |
| TD-135 | LLM **只负责指认 CSS 选择器**，正文提取仍由 BeautifulSoup 按选择器做 | 让 LLM 直接吐正文 | LLM 选错选择器就提不到内容（可重试） | 不换：让 LLM 吐正文会改写/杜撰原文 |
| TD-136 | SSRF 防护用 `is_global` 且**逐个**检查解析出的地址 | 只查第一个地址 / 只做协议白名单 | 仍存在 DNS rebinding 的理论窗口（校验与请求之间重新解析） | 需要彻底防时改成按已校验的 IP 直连 |
| TD-137 | `published_at` 按源站**原文字符串**存，不解析成 `datetime` | 统一的日期类型 | 不能按时间排序/筛选 | 需要按发布时间排序时再加一个解析后的列 |
| ~~TD-138~~ | ~~抓取 + 解析只是服务层函数，没有 HTTP 端点~~ **已解决**：`sys_user.role` + `require_admin` 依赖 + `POST /admin/articles/ingest`（`app/routers/admin.py`）。抓取/解析仍复用原服务层函数，端点只负责鉴权 + 错误映射 + 入库。**提权只能人工改库**（`migrate_0005_user_role.sql` 刻意把现存用户全填 0）。18 条新测试，8 个变异体全部杀掉；细节见 TD-188/189/190 | 有了管理员角色再加端点 —— 公开端点等于给任何人一个「让服务器抓任意 URL + 烧 LLM token」的入口 |
| TD-139 | 文章去重只看 `url` 唯一，不做正文相似度判断 | 内容指纹去重 | 同一篇文章换个 URL（如带 utm 参数）会重复入库 | 出现明显重复时加正文哈希 |
| TD-140 | LLM 指认的选择器匹配不到就**直接报错**，不自动重试 | 自动重试 N 次 | 页面小改版就会失败，需要人工看 | 需要无人值守跑批时加重试 + 告警 |
| TD-141 | 限流用**进程内存**滑动窗口，不引入 Redis | 分布式限流 | 多进程 / 多实例部署时每个进程各算各的，实际配额变成 N 倍；进程重启配额清零 | 上多实例部署时换 Redis |
| TD-142 | 默认**不信任** `X-Forwarded-For`，只取 socket 对端地址 | 开箱即用的反代支持 | 部署在 nginx 之后所有用户会共用代理 IP 的配额，必须显式打开 `TRUST_PROXY_HEADERS` | 部署到反代之后立刻打开（否则限流过严） |
| TD-143 | 测试里**默认关闭**限流（`tests/conftest.py`） | 全量用例都在限流下跑 | 几十个用例共用同一个客户端 IP，开着会互相挤爆配额；限流本身由 `tests/test_ratelimit.py` 显式打开来测 | 换成每用例独立 IP 时可去掉 |
| ~~TD-144~~ | ~~CI 里把 actions 钉在 `actions/checkout@v4` 与 `actions/setup-python@v5`~~ **已解决** | 仓库管理员 `aa87c52` 已升到 `actions/checkout@v7` / `actions/setup-python@v7`（上游 v7.0.1 / v7.0.0）。实跑验证：run 33524519419（push）与 33524753544（pull_request）均 `success`，step 名已是 `Run actions/checkout@v7`，且两个 job 的 annotations **已为空**——原先那条 `Node.js 20 is deprecated ... forced to run on Node.js 24` 消失。代价：v7 的行为在沙箱里无法预验证，只能靠 push 后 CI 实跑兜底 |

## 十、工程与运维

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| ~~TD-90~~ | ~~无日志框架、无监控告警~~ **已解决（S5-03-3）**，见本文开头「上线阻塞项」表里的 TD-90/91 条 | 不上结构化日志 | ~~出问题只能看 uvicorn 控制台输出~~ 已有每请求结构化日志 + `X-Request-ID` + 存活/就绪探针 | 仍未接集中式日志与指标采集（Prometheus/ELK） |
| TD-91 | ~~无限流~~（限流已做，见 TD-15/141）、无 CORS 配置、无安全响应头 | 基础防护 | 跨域策略靠默认，缺 `X-Content-Type-Options` 等安全头 | **上线前**（CORS 与安全头） |
| TD-92 | `SITE_BASE_URL` 默认 `https://codemax.top`，靠 `.env` 覆盖 | 按请求 Host 自动探测 | 配错会产出错误的 canonical / sitemap 绝对地址 | 部署时确认 |
| TD-93 | `.env` 不入库，仅提供 `.env.example` | — | 新环境需手工 `cp` | — |
| TD-94 | `/static` 只放 `er.js`，HTML 全部走 SSR | 静态 HTML | 旧地址 `/static/er.html`、`/static/mermaid.html` 已 404 | 若外部已有旧链接需加 301 |
| TD-195 | **讲解文档 `docs/ARCHITECTURE_GUIDE.md` 必须跟着实现走**（写进 `AGENTS.md` ALWAYS + 「做完」定义第 7 条 + `finish-subitem` 技能第 3 步） | 「先把功能做完、文档以后再补」——事实上以后永远不会补 | 每次改动都多一步文档工作；且**文档里记的数字会随之腐烂**：加 1 条测试实测导致 **24 处测试条数**过期、散落 **11 个文件** | 若文档维护成本压过收益，退化为「只在新增子系统时补一课」，但数字同步不能退 |
| TD-196 | SSRF 校验**逐跳重做**：`crawler._request` 关掉 httpx 的 `follow_redirects`，自己按跳跟随并对每一跳重跑 `assert_public_url`；`browser._goto` 加 Playwright 路由拦截器，非公网请求直接 `abort`（不建立 TCP），`render()` 再对**最终落点 URL** 校验一次 | 交给 httpx / 浏览器自动跟随重定向，代码更少 | 原先只校验入口 URL，攻击者用一个自己控制的公网页面 302 到 `169.254.169.254` 就能读走云厂商临时凭证（已复现：`127.0.0.1`、`169.254.169.254`、`10.0.0.9`、`[::1]` 四种都能读到响应体）。代价是自己维护重定向循环与 `MAX_REDIRECTS=10`；拦截器让每个子资源多一次 DNS 解析（低频的管理端点可接受）。`_abort_non_public` 里刻意不抛 `CrawlError` —— Playwright 会吞掉拦截器里的异常，最终由 `render()` 那次校验统一表达成 400 | 若将来支持流式/大文件下载，需要重新评估逐跳校验的开销 |
| TD-197 | `/oauth/token` 签发 access token 时必须传**库里当前的** `password_changed_at`（与 `/auth/login`、`POST /auth/password` 三处口径统一） | 只在登录端点带这个声明 | 漏传的后果不是安全性下降而是**功能直接坏掉**：token 的 `pwd` 声明变成 `None`，`get_current_user` 立刻拒 ⇒ 「改过密码的用户 SSO 彻底用不了」，而旧 token 照样失效，什么安全收益都没换来。原先三处签发只有 `oauth.py:185` 漏了；顺带补了「授权码签发后用户被删」的 `None` 兜底（原来会 500）。由 `test_oauth_token_still_works_after_password_change` 钉住全链路 | 若将来签发点继续增加，应把「取 user + 签 token」收敛成一个函数，避免再漏 |
| TD-198 | 写支付元数据的条件从 `status == PENDING` 放宽到 `status in (PENDING, CLOSED)`（`mock_pay_confirm` 与 `pay_notify` 两处同步改） | 只认 `PENDING`，迟到支付不留流水 | `CLOSED → PAID` 这条边是 TD-156 刻意留的（关单后用户扫旧二维码付了钱，钱收了必须发货），但支付元数据只在 `PENDING` 时写 ⇒ 实测迟到支付后 `status='paid'` 而 `transaction_id=None`、`paid_at=None`：**钱收了、货发了、账上没有支付流水**，对账与客诉时无从查证。放宽条件**不影响幂等** —— `mark_paid` 对已支付订单直接返回 `False` 且不提交，多余的赋值会随 session 关闭被丢弃（由两条 idempotent 测试钉住）。两条入账路径各写各的元数据，所以测试也**分路径各一条**，只修一条等于漏一半 | 若将来入账路径继续增加，应把「写流水 + mark_paid」收敛进 `order_state` |
| TD-199 | 「同一用户最多一张待支付单」由**数据库部分唯一索引**兜底（`uq_sys_order_user_pending ON sys_order(user_id) WHERE status='pending'`），应用层撞 `IntegrityError` 后回滚并复用已存在的那张单 | 应用层「先查 pending 再新建」，或加进程内锁 | 原实现在并发下必然漏：`asyncio.gather` 的 5 个请求在各自 `await` 处交错，**5 个 SELECT 全都在任何一个 INSERT 提交之前跑完**，于是都判定「没有可复用的单」，实测落出 5 张 pending 单 —— 且**不需要真并行**，SQLite 单连接下同样复现。进程内锁在多实例部署时各算各的（与限流 TD-141 同一个道理），只有数据库约束跨实例有效。用**部分**索引而非普通唯一约束：paid/closed/downloaded 的历史单必须能有多张。迁移 `migrate_0006_order_single_pending.sql`，**执行前须先清存量重复**否则建索引失败 | ① `rollback()` 会无条件过期 ORM 对象（`expire_on_commit=False` 管不到它），回滚后再访问 `user.id` 会触发同步懒加载 ⇒ 真 PG 上 `MissingGreenlet`，必须提前取成局部变量；② SQLite StaticPool 单连接下一个请求的 rollback 会连带回滚别人的插入，所以并发行为只能在真库上测（与 TD-80 同一个理由） |
| TD-200 | `python-multipart` 从 0.0.6 升到 **0.0.26**（不是 0.0.7） | 只升到 0.0.7 做「最小安全升级」 | 0.0.6 的 Content-Type 头 ReDoS（CVE-2024-24762）实测可复现：反斜杠每多 4 个耗时约 ×7，24 个 0.013s → 28 个 0.089s → 32 个 0.59s → **36 个 4.04s**，卡的是**主事件循环**；一个约 60 字节的头就能让整站挂起数分钟。这条路径**真的可达** —— 本项目没有任何 `UploadFile` 端点，但给 `/auth/login` 发 multipart 头时 stderr 会打出 `multipart.multipart` 自己的日志，即 Starlette 的 `request.form()` 照样会走它。只升 0.0.7 会留下另外两个同类 DoS（0.0.18 畸形 boundary 逐字节跳过并每次记日志、0.0.26 超大 preamble/epilogue），所以一次升到全部公告都修完的 0.0.26。升到 0.0.26 后同一 payload 从 4.04s 变 **0.0000s**。兼容性实测：0.0.7~0.0.26 都仍提供旧模块名 `multipart`，starlette 0.27.0 的 `import multipart` 不受影响，**不需要动 fastapi/starlette 版本** | 两条测试钉住：`test_multipart_content_type_redos_is_patched` 测运行时行为、`test_python_multipart_pinned_above_known_cve_versions` 测 requirements 声明（互补：防止改了声明没重装环境时前一条仍是绿的） |
| TD-201 | 把 `concurrency = thread,greenlet` 固化进仓库的 `.coveragerc`（此前只存在于临时文件里）；`cryptography` 提升为**直接声明**的依赖；`mistune` 补进 requirements | 不加配置文件，靠每次手敲 `--cov-config` | **没有这个配置，覆盖率会被明显低报。** 本项目用 pytest-asyncio + httpx `ASGITransport` 驱动应用，请求处理分散在 greenlet/线程上，coverage 默认只追踪主线程，于是**端点函数体里执行过的行被记成「未覆盖」**。严格 A/B（同一个测试只换配置，用 `sys.settrace` 独立取真值对照）：空配置把 `shop.py` 实际执行过的 **L201/L203/L208** 三行误报为未覆盖，加了配置后**零误报**（全量口径 89% → 97%）。危害很具体：照低报的数字补测试会重复覆盖早已覆盖的安全分支，更糟的是得出「这些安全分支没测」的错误结论。`cryptography` 此前只作为 `python-jose[cryptography]` 的 extra 存在（`importlib.metadata.requires` 可证实），但 `app/wechat_pay.py` 顶层 import 了 x509/AESGCM/hashes/padding/serialization 五处 —— 谁把 extra 去掉，微信支付模块在 import 期就炸。`mistune` 则是 `scripts/build_docs_site.py` 默认路径硬依赖却从未声明 | ⚠️ 排查时我一度得出「`.coveragerc` 无效」的**错误结论**，真因是 **`rm -f .coverage*` 这个通配符会连 `.coveragerc` 一起删掉**（它也以 `.coverage` 开头），coverage 静默回退到无配置，看起来就像配置不生效。清理必须写全：`rm -f .coverage .coverage.*`。这条已写进 `.coveragerc` 文件头 |
| TD-202 | **不拆** `requirements-dev.txt`，保持单文件（运行时 18 / 测试 4 / 文档工具 1 / 静态检查 1） | 拆成 `requirements.txt` + `requirements-dev.txt`，让生产镜像少装约 **60 MB**（实测 `pgserver` 33 MB、`ruff` 23.3 MB、`pytest` 2.6 MB，其余合计约 1 MB） | 拆分的收益是镜像体积，代价是**文档同步面从 1 个文件变成 2 个**：实测全仓有 **14 个文件、45 处**引用 `requirements.txt`（README / AGENTS / HANDOVER 未列但 WINDOWS_LOCAL_RUN 有 8 处 / ROOT_FILES 6 处 / 总览 3 处 / workflows README 3 处 / ci.yml 3 处 / Dockerfile 2 处 …），每一处都要判断语境是「跑应用」还是「跑测试」。本仓库已经因为**文档同步失守**被指出过三次（新增 `build_docs_site.py` 没同步 `scripts/README.md` 等），再引入一个「两个文件必须一起看」的约定，是在最薄弱的环节上加压。而且 `总览.md` §6.1 已把「`requirements-dev.txt` **不存在** —— 一条命令装齐」写成**有意的设计事实**。生产镜像这 60 MB 也不影响启动时间与内存占用，只影响拉取一次 | 若将来镜像体积成为实际约束（如按流量计费的 serverless），再拆；拆的时候必须同时改上述 14 个文件的 45 处引用，并在 `AGENTS.md` 文档地图里登记新文件 |
| TD-203 | 转化路径只做「落地页 + 统一登录 + 只读状态轮询 + 内联 SVG 二维码」，**埋点统计暂缓** | 完整漏斗（案例/FAQ/订单查询页 + 第三方统计） | **为什么埋点暂缓**：① 现有 CSP 的 `connect-src 'self'`（`app/middleware.py`）会直接拦掉任何往第三方域发数据的统计脚本，要放通属于**安全策略变更**，得单独评估；② 毕设场景没有真实流量，埋点也读不出统计显著的结论，会变成「装了但没用」的配置，与 AGENTS.md 第 1 条（不引入用不上的配置）冲突。**为什么二维码选 `segno` 而不是 `qrcode[pil]`**：实测 segno 1.6.6 纯 Python、零依赖、**0.07 MB**；qrcode 画 PNG 要拖 **Pillow 6.61 MB 二进制**，差约 95 倍，且 Pillow 在 Windows 上多一层二进制轮子的麻烦。输出是纯 `<path>` 内联 SVG（实测 1493 字节，无 `<script>` 无外链），内联 SVG 不受 CSP `img-src` 约束。**为什么不引 CDN 的前端二维码库**：`script-src` 白名单里唯一的 `cdn.jsdelivr.net` 在沙箱与部分网络下实测 HTTP=000 不可达，开发时二维码画不出来且极难查 | 拿到真实流量、或产品决定做 A/B 时再加埋点；届时需同步评估 CSP 放通范围 |
| TD-204 | 全站登录模块做成**外部文件** `app/static/auth.js` + `base.html` 的 `{% if auth_ui %}` 开关；OAuth 同意页传 `auth_ui=False` | 把登录模块写成 base.html 的内联脚本（更省事） | `base.html` 是**所有**页面的父模板，包括 OAuth 同意页 —— 那是发放授权码的安全关键页，`tests/test_oauth_consent.py` 断言它渲染出来**一个 `<script>` 都没有**。第一版我直接往 base.html 加内联脚本，那条测试当场变红。做成外部文件后由 `script-src 'self'` 覆盖，连 `'unsafe-inline'` 都不需要（TD-163 的方向），再用 `auth_ui` 开关让同意页整套 UI 与脚本都不渲染。代价是多一个静态文件与一个模板变量 | 若将来上严格 CSP（nonce 方案），所有内联脚本都要外部化，这个开关可以一并去掉 |

## 十一、订单与支付（阶段三，进行中）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| ~~TD-100~~ | ~~状态机只有 3 个状态~~ **已解决（S5-01-1）** | 加了第 4 个状态 `closed`：`app/order_state.py` 的 `STATES`/`ALLOWED` 都已含它，`is_expired()` 按 `create_time + ORDER_EXPIRE_MINUTES`（默认 30）判超时，`mark_closed()` 走同一套 CAS。**没有加 `expire_at` 列** —— 能算出来的东西不必存，少一个要与配置同步的字段。`refunded` 仍未做 | 关单只发生在「用户再次下单」时，没有后台定时任务扫；从不下单的用户其过期单会一直挂着（无害，只是数据不好看） | 要主动关单/释放库存时加定时任务；要做退款时加 `refunded` |
| TD-101 | 重复支付通知按**幂等**处理：已是目标状态时返回 `False` 而不是报错 | "重复通知即异常"的严格语义 | 真正的异常重复通知不会报错，只能靠日志发现 | 接入微信回调后补告警 |
| TD-102 | 状态迁移用原子 CAS（`UPDATE ... WHERE status=<期望值>` + 校验 rowcount） | 先查后改的直观写法 | 迁移结果要靠 rowcount 判断，不能靠内存对象 | 不换（与授权码消费同一套路） |
| TD-103 | 状态机目前只是领域层，**尚无 HTTP 端点** | 可被 API 触发 | 迁移暂时只能在代码与测试里发生 | S3-01-1 下单接口 + S3-01-3 回调接口落地时 |
| TD-104 | 状态值用 `VARCHAR(20)` 字符串，不用 PG 枚举或整数 | 数据库层取值约束 | 非法状态值只能靠应用层 `STATES` 常量拦（已测） | 需要库级强约束时 |
| TD-105 | 先 `json.dumps` 成字符串 → 签这串 → 把同一串 `encode` 后 `content=` 发出（`app/wechat_pay.py`） | `httpx` 的 `json=` 自动序列化 | 报文构造代码略啰嗦 | 不换（已有变异测试：改用 `json=` 会 `InvalidSignature`） |
| TD-106 | 商户私钥以 PEM 文本放 `WX_PRIVATE_KEY`，不落盘、不进 git | 从 `apiclient_key.pem` 路径读取 | `.env` 里多行 PEM 要用双引号并写 `\n` | 需要读证书文件时再加 `WX_PRIVATE_KEY_PATH` |
| TD-107 | 只实现 NATIVE（扫码）一种支付方式 | JSAPI / APP / H5 | 换支付方式要另写一套下单 | 需要小程序内支付时 |
| TD-108 | 下单只有一个 SKU，商品名与价格取自 `SHOP_PRODUCT_NAME` / `SHOP_PRODUCT_AMOUNT` | 商品表 + 多 SKU | 加商品要改配置甚至改代码 | S3-03 商品展示或需要多 SKU 时 |
| ~~TD-109~~ | ~~已有未支付订单就复用同一单~~ **已解决（S5-01-1）** | 每次重新下单（可拿到新二维码） | 复用逻辑保留（防连点刷单），但**仅限未超时**的单；超时即关单另起，二维码过期的死路已打通（见 TD-100）。`test_unexpired_pending_order_is_still_reused` 守住没超时仍复用 | 需要主动刷新二维码而不换单号时 |
| TD-110 | 下单接口「新建」与「复用」都返回 200，靠响应体 `reused` 区分 | 用 201/200 区分 | REST 语义不如状态码精确 | 需要严格 REST 语义时 |
| TD-111 | 订单先落库再调微信下单 | 先拿到 code_url 再落库 | 微信侧成功而我们落库失败时会出现"用户付了但没单"（回调按 `out_trade_no` 兜底） | 回调实现后确认兜底路径 |
| TD-112 | `transaction_id` 列与 `code_url` 一起加（前者 S3-01-3 才写） | 分两次改表 | 暂时有一列没人写 | S3-01-3 落地即消解 |
| TD-113 | 支付部分只做算法级验证：自签 RSA 密钥验签名、`httpx.MockTransport` 断言请求、AES-GCM 用冻结向量 | 真机端到端联调 | 微信侧接口变更、证书配置错误只能在上线时暴露 | 拿到商户号后补一轮真机联调 |
| TD-114 | 平台证书 / 微信支付公钥放 `.env`（`WX_PLATFORM_CERT`），不自动下载轮换 | 调 `/v3/certificates` 自动拉取、解密并缓存 | 证书轮换后要手动换 `.env`，否则回调**一律**验签失败 | 证书轮换成为运维负担时 |
| TD-115 | 回调端点不走 JWT 鉴权，身份**完全**靠平台证书验签 | 再叠一层 IP 白名单 | 验签一旦有洞＝任何人可伪造支付成功（3 条测试守着） | 需要纵深防御时加 IP 白名单 |
| TD-116 | 回调应答体用微信规定的 `{"code","message"}`，不用 `HTTPException`（那是 `{"detail"}`） | 全局统一的错误响应格式 | `shop` 路由里错误应答有两套写法 | 引入全局异常处理时统一 |
| TD-117 | 非 `TRANSACTION.SUCCESS`、或 `trade_state != SUCCESS` 的通知一律回 200 但不处理 | 回 4XX 让微信重推 | 退款等事件被静默忽略（回 200 是为了不被无限重推） | 做退款流程时 |
| TD-118 | 回调金额与订单金额不符 → 回 400 让微信重推，不吞掉 | 回 200 忽略 | 会被持续重推（刻意的：金额不符必须暴露出来） | 接入告警后改成 200 + 告警 |
| TD-119 | 支付信息与状态迁移在**同一次提交**内完成（先写字段再 CAS），重复通知靠 `mark_paid` 提前返回不覆盖 | `SELECT ... FOR UPDATE` 行锁 / 分两次提交 | 真并发下败者理论上可覆盖，但微信重复通知带的是同一个 `transaction_id`，无害；分两次提交则会留下 `status=paid` 而 `transaction_id` 为空的窗口 | 出现部分支付 / 多笔支付时 |
| TD-120 | 平台证书与「微信支付公钥」两种 PEM 都支持（按 `BEGIN CERTIFICATE` 判断） | 只支持其中一种 | 判断依赖 PEM 头部字符串 | 微信只保留一种形态时 |
| TD-205 | 加第三种支付模式 `manual`：展示静态收款码 + **管理员**人工确认收款 | 放弃了「支付自动闭环」—— 钱到账需要人去核对、去点确认 | 没有商户号就拿不到支付回调（TD-113），机器无从知道钱到没到，只能由人告诉系统。刻意**不用**个人收款码 + 第三方「免签」聚合（PayJS 一类）：那类服务要监控收款码或客户端，社区实测有「用两个月突然被封，客服不理人」「注册交钱后接口返回 500」的情况，且涉及二清与《支付业务许可证》合规问题。MobilePay 也不是出路 —— API 收款要**丹麦 CVR 号**与 Vipps MobilePay 协议，个人账户只有一个、做生意必须用按笔收费的商业账户。与 `mock` 的关键区别是**谁能调**：mock 是登录用户自己点（等于免费发货按钮，只能开在开发环境），manual 走 `require_admin`，可以留在生产。状态机复用真实回调那条 `mark_paid`（幂等、`CLOSED` 也能收，TD-156），所以现有测试全绿。 | 拿到商户号后改回 `wechat`；若要自动对账，接银行/支付宝账单 API 做定时核对 |

---

## 十二、静态检查（ruff）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-145 | 只引入 **ruff** 一个 linter，规则集在 `ruff.toml` 里**显式列出**（F/E4/E7/E9/I/B/DTZ/ASYNC/SIM/C4） | mypy 静态类型检查；ruff 默认规则集 | 无类型检查兜底，参数/返回值类型写错只能靠测试或运行期发现 | 若出现「类型不匹配」类线上问题，再单独评估 mypy |
| ~~TD-146~~ | ~~`sys_order.paid_at` 用应用侧无时区本地时间，与 `create_time`（数据库时间）不一致~~ **已解决** | 排查后发现同一批无时区 `TIMESTAMP` 列里其实混了**三种**时钟基准：① `create_time`/`update_time` = 数据库服务器本地时间（`CURRENT_TIMESTAMP`）；② `paid_at` = 应用服务器本地时间（`datetime.now()`）；③ `oauth_code.expires_at` = UTC 但被 `.replace(tzinfo=None)` 抹成裸值。**第 ③ 种 ruff 抓不到**——它确实给 `now()` 传了时区，问题出在事后抹掉，DTZ 规则不看这一步。现已统一：10 个时间列全部改 `TIMESTAMPTZ` / `DateTime(timezone=True)`，应用侧一律写带时区 UTC。代价：SQLite 不支持时区，读回来仍是裸值，故 `app/routers/oauth.py` 加了 `_as_utc()` 归一化，否则在 SQLite 上比较会抛 `TypeError: can't compare offset-naive and offset-aware datetimes`（实测 3 个用例红过）。既有数据用 `database init/migrate_0001_timestamptz.sql` 迁移 |

**为什么刻意不引入 mypy**：实测 `mypy app main.py --ignore-missing-imports` 在 26 个源文件里报
21 个错误，逐条看过后大多属于类型摩擦而不是 bug（例如两条 `AsyncClient(transport=...)`
报的是测试注入的 transport 类型不匹配，是测试写法问题）。给一个不是按严格类型写的代码库
上 mypy，产出主要是噪音，会稀释真正有价值的告警。

**ruff 规则集为什么写死**：实测 ruff 0.16.5 的**默认**集是一批具体规则而非按前缀整族启用
——含 B008 却不含 B904/B905，含 RUF007 却不含 RUF001。所以「按前缀 select」比默认更严。
写死后升级 ruff 不会静默引入新规则让 CI 突然变红。

**刻意不选的规则**：
- `E501`（行太长）：实测超 88 列的有 516 行、最长 189 列，中文注释天生宽，强行折行只产生无信息量的 diff。
- `PLR` 整族：参数个数、分支数一类主观规则，噪音大于收益。
- `RUF` 整族：`RUF001/002/003` 会把中文标点判为「歧义 unicode 字符」，本仓库全是中文注释，开了等于全屏红。

**B008 白名单**：FastAPI 的 `Depends()` 必须写在默认参数位置，这是框架用法不是 bug。
不加 `extend-immutable-calls` 白名单会有 **28 条**误报（实测），足以淹没真正的问题。

---

## 十三、智能客服（阶段四 S4-02）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-147 | FAQ 语料放在 `app/tools/faq.py` 的模块常量里，不建数据库表 | 运营后台可自行增删 FAQ | 改 FAQ 要改代码并重新部署 | 需要非开发人员维护 FAQ 时，把 `FAQS` 换成从表里读，检索逻辑不用动 |
| TD-148 | 中文分词用 `jieba` 精确模式，**不做停用词过滤** | 更干净的词袋 | 语料里会留「怎么」「可以」这类词 | 语料规模上千条后再评估停用词表 |
| TD-149 | BM25 与余弦**各自按最大值归一化后加权相加**（BM25 0.6 / 余弦 0.4） | 学习式融合（LTR）、RRF 等更讲究的做法 | 权重是拍的，没有标注数据可调；且语料只有 12 条时两路排序常常一致，融合的实际增益有限（数值上两路确实不同，已由测试守住） | 语料变大、或有点击日志可当弱标注时，换成 RRF 或训练排序模型 |

**性能**：ROADMAP 要求 FAQ 检索 < 80ms。jieba **首次**分词要加载词典（实测约 560ms），
所以 `app/tools/faq.py` 在**模块导入时**就 `jieba.initialize()` 预热；热身后单次分词
实测约 0.02ms，`tests/test_faq.py::test_latency_under_80ms` 用 200 轮均值守住这条指标。

---
| TD-150 | **S4-02-2 没有训练 BERT**，改用「`IntentRouter` 协议 + 确定性规则实现」，把模型位留成可替换插槽 | 规则准确率没有数据背书；三类边界靠阈值和词表，遇到新说法要改代码 | 训练需要三类各上千条标注、GPU 或数小时 CPU、几百 MB 权重下载，本项目三样都没有。写一个无法运行的「BERT」比诚实的规则路由更糟 | 攒到标注数据（可用线上真实问答 + 人工回填当种子）后，加 `BertIntentRouter` 实现同一协议即可，`support.py` 与路由层不用改 |
| TD-151 | FAQ 阈值 `0.40`、BM25 饱和常数 `3.0`、兜底阈值 `0.35` 都是在**现有 12 条语料**上实测标定写死的常量 | 换成语料量自适应的阈值，或按线上误判率自动调 | 无标注数据可用来调参；不写死就没有可复现的行为，测试也无从写起 | 语料规模变化后**必须重新标定**，否则这些数字失去意义；有点击日志后可用弱标注回归 |
| TD-152 | RAG 只把检索到的**原文片段**喂给模型并要求「只依据材料作答」，同时把文章标题回传前端 | 让模型自由发挥，答案更「流畅」 | 流畅但会杜撰，用户无法核对；且 `sys_article` 为空时只能转人工，不能硬编 | 语料覆盖度上来后仍保留引用要求；引用可升级为带链接的出处 |
| TD-153 | 修复 `_Index.cosine` 的量纲错误：分子原是 tf·idf 点积、分母却是**裸 tf** 模长，实测「会给源码吗」得 **1.011 > 1**（真余弦不可能 >1） | 沿用原式（数值看着也能排序） | 不是余弦就无法当置信度标定，阈值 0.40 也就无从谈起 | 已修（分子分母同为 tf·idf）。`test_cosine_is_a_real_cosine` 钉死 [0,1] 区间，忠实复现原实现的变异会被该测试杀死 |
| TD-154 | `FaqHit.score` 与 `FaqHit.confidence` **并存且语义不同**：`score` 是「本结果集内」max 归一化，只能排序；`confidence` 由未归一化原始分算出，可跨查询比较 | 只留一个分数字段 | k=1 时 `score` 恒为 1.00，拿它当绝对置信度会让「python 部署 nginx 报错」也判成 FAQ 命中（真实踩到过）。只留归一化分就没有绝对判据，只留绝对分则丢掉了结果集内的相对强弱 | 若将来只需绝对判据，可删 `score` 直接按 `confidence` 排序 |
| TD-155 | `as_utc()` 提到 `app/timeutil.py` 共享，`oauth.py` 里的私有副本 `_as_utc` 删除 | 各模块自带一份 | 两个不同路由都要做「SQLite 裸值按 UTC 解读」，且 TD-146 已经证明漏一次就是一个只在单后端出现的崩溃 | 若出现第三处不同语义的时间归一化，再拆 |
| TD-156 | 状态机保留 `CLOSED → PAID` 这条**反向**边 | 关单即终态，晚到的支付通知拒收 | 用户可能扫了已关闭订单的旧二维码并把钱付了。拒收 = 收钱不发货，比多发货严重得多。`test_late_payment_on_closed_order_still_delivers` 守着 | 接入微信退款后，可改成「晚到支付自动发起退款」并去掉这条边 |
| TD-157 | **不配 CORS** | 加 `CORSMiddleware` 让前端跨域 XHR 直调 | SSO 授权码流程全程是 302 跳转 + 服务端到服务端换 token（`client_secret` 绝不能进浏览器），没有任何浏览器跨域 XHR，配 CORS 只是白开攻击面。`test_sso_flow_survives_cross_origin_headers` 验证带 `Origin`/`Referer` 也能走通 | 将来真做浏览器直调的跨域前端时，按具体 origin 白名单开，不要用 `*` |
| TD-158 | 下载端点**必须使用** `mark_downloaded()` 的返回值判定成败 | 只靠请求开头读到的 `order.status` 判断 | 开头那次检查读的是快照，并发下多个请求会同时读到 `paid`；真正决定谁拿到链接的是那次 CAS。**此前返回值被丢弃**，并发 4 个请求全部拿到有效链接，「一次性下载」实际失效 —— 由 S5-01-2 的 `test_concurrent_download_only_one_wins` 抓出并修复，变异验证（去掉该判断）会让该测试失败 | 云存储后端同理：发链接前必须先赢得状态迁移 |
| TD-159 | CPU 密集的 `parse_ddl` / `build_data_dictionary` 用 `run_in_threadpool` 移出事件循环 | 直接在 `async def` 里同步调用（改动最小） | 同步 CPU 代码会独占事件循环，期间**全站**请求排队。实测满额 DDL 单次 33 ms、5 并发时最后一个等 169 ms；`build_data_dictionary` 更贵，28 表要 **454 ms**，一个 Word 导出就能把客服 80ms 指标打穿（实测拖慢 11.25 倍）。回归由 `test_faq_latency_not_degraded_by_concurrent_big_ddl` / `test_word_export_does_not_block_event_loop` 守住，撤掉修复两条都会红 | 同类端点（未来的报表导出、批量解析）一律照此办理 |
| TD-160 | 线程池修复后**仍有 5/20 请求超 80ms**，如实记为未消除 | 声称「已解决」 | 线程池能让出事件循环，但 **GIL 使 CPU 代码串行**，5 个并发解析的总 CPU 时间不变（实测 5 并发总耗时 ≈5×33 ms）。这是 CPython 固有限制，不是代码能绕过的 | 要彻底解决：`ProcessPoolExecutor`（进程池绕开 GIL，但要付序列化与进程开销），或给重端点加并发信号量保护轻端点 |
| TD-161 | 性能测试的断言只用**相对量**（比值 / 增长阶），不写死绝对毫秒数 | 写「必须 < 80ms」这类绝对阈值 | CI 机器与本机性能差好几倍，绝对阈值会变成随机红的噪音，进而被人 `skip` 掉。比值在慢机器上两边一起慢，稳定得多。**唯一例外**是纯函数 `search()` 的 80ms（不含网络与调度，实测 0.049 ms，余量三个数量级） | 若将来有固定规格的压测机，可在专门的 job 里加绝对阈值断言 |
| TD-162 | 压测计时的 `t0` 必须在 **`asyncio.gather` 之前**取 | 在协程内部起表（看起来更自然） | 同步 CPU 代码阻塞事件循环时，后来的请求在**排队**；从协程开始执行才起表，量到的只是自己的服务时间，排队等待被完全隐藏 —— 于是「事件循环被阻塞」这个现象根本测不出来。本项目第一版基准就是这么错的，一度得出「没有阻塞」的错误结论 | 任何延迟基准都适用：测的是用户感知的等待，不是函数自身的耗时 |
| TD-163 | CSP 保留 `'unsafe-inline'`，目标是**收窄来源**而非消灭内联 | 严格 CSP（`script-src 'self'` + nonce） | 5 个模板含内联 `<script>`（er / mermaid / drawio / mock_pay / shop），上严格 CSP 等于先做一轮前端重构。现在这版仍挡住了从任意第三方域加载脚本、`object-src`、`base-uri` 劫持与外部嵌套 | 把内联脚本外置为文件后去掉 `'unsafe-inline'`；`test_csp_allows_inline_scripts_because_templates_still_need_them` 会在内联脚本消失时变红提醒 |
| TD-164 | `/health` 保留为 `/healthz` 的别名 | 只留一个、把旧的删掉 | README 与既有测试都在用 `/health`，为统一命名去改一圈文档不值得 | 若将来统一网关健康检查路径，再一起改 |
| TD-165 | 日志只用 `logging.basicConfig` 设级别与格式，**不接管 uvicorn 的 handler** | 自建 handler / 结构化 JSON 日志 | 两边都配 handler 会重复输出或互相覆盖。当前一行一请求、带 `rid`，够用 | 接集中式日志（ELK/Loki）时换成 JSON formatter |
| TD-166 | 运维中间件写成**纯 ASGI**，不用 `BaseHTTPMiddleware` | `BaseHTTPMiddleware`（能拿到 Request/Response，代码短） | 实测 `BaseHTTPMiddleware` 让客服 p50 从 14 ms 涨到 **21 ms**，且把「大 DDL 拖慢客服」的比值从 1.04 恶化到 2.14，两条性能回归当场变红。纯 ASGI 只包一个 `send` 回调，开销可忽略；代价是只能直接操作 `scope`/`message` | 若将来中间件需要读写请求体，再评估是否值得付这份开销 |
| TD-167 | 存活探针 `/healthz` **不查数据库** | 存活探针顺便查库，"信息更全" | 库一抖，编排器会把**健康的**应用实例全部重启，把一次数据库故障放大成全站雪崩。查库是**就绪**探针 `/readyz` 的职责（它失败只摘流量不重启）。`test_liveness_probe_does_not_touch_the_database` 直接扫源码守住这条 | 无 |
| TD-168 | Word 导出用**进程池**（`app/cpu_pool.py`），并保留线程池兜底 | 只用线程池（简单） | 线程池让得出事件循环却让不出 **GIL**。实测并发轻量请求 p95：线程池 76.6 ms vs 进程池 **35.8 ms**；事件循环停顿：线程池 41 ms vs 进程池 11.5 ms。Windows 用 spawn、容器可能限进程数，故进程池失败时退化为线程池（慢但不坏），且坏过一次就记住不再重试 | 无。`run_cpu_bound` 要求函数在模块顶层、参数与返回值可 pickle |
| TD-169 | robots.txt 解析直接用标准库 `urllib.robotparser`，不自研 | 自己写解析器以支持小数 `Crawl-delay` 等 | 标准库有两处**看不见的**行为限制（已用测试钉住）：① `Entry.applies_to` 会先把我们的 UA 在第一个 `/` 处截断成 `codemax-platform`，再看 robots 里写的 agent 是不是它的子串 —— 所以站方必须写 `User-agent: codemax-platform`，**写完整 UA 串反而匹配不上**；② `Crawl-delay` 只接受整数（`isdigit()` → `int()`），`0.5` 这类小数被静默丢弃、回落到默认间隔 | 若目标站普遍用小数 Crawl-delay，再自研解析；届时 `test_fractional_crawl_delay_is_silently_ignored_by_stdlib` 会提醒 |
| TD-170 | `check_allowed` 必须让 `CrawlError`（SSRF 拒绝）**穿透**，不能被 robots 的兜底吞掉 | 统一 `except Exception` 兜底成「不抓」 | 第一版就是这么写的，后果是「这个地址不许访问」被伪装成「robots 不让抓」：排障时看不出是 SSRF 拦截，安全告警也就丢了。由 `test_ssrf_rejection_is_not_masked_as_robots_error` 守住 | 无 |
| TD-171 | robots 与限速状态是**进程内**的，不共享 | 放 Redis 让多实例共享节流窗口 | 冷启动只涉及少数几个站、单实例跑，引 Redis 不值得 | 多实例部署前需要处理：否则每个实例各有一份缓存与窗口，对目标站的实际频率按实例数放大 |
| TD-172 | 用**令牌版本化**（`pwd` 声明 = 改密码时刻）吊销旧 token，而不是 `jti` 黑名单表 | 建 `sys_token` 表存每个 jti，注销时插一条 | 黑名单要在**每次登录时写库**（写放大），且要能枚举出某用户的全部 jti 才能一次吊销；版本化零额外表、零额外写入，一次吊销该用户全部旧 token | 需要「踢掉单个会话」或「主动注销某一个 token」时，才值得引入 jti 表 |
| TD-173 | `password_changed_at` 为 **NULL 时跳过校验** | 一律要求 token 带 `pwd` 声明 | 本次上线前签发的 token 都不带 `pwd` 声明；若一律要求，**全站已登录用户会在部署那一刻集体掉线**。NULL 语义定为「从未改过密码」，只在用户第一次改密码后才开始强制 | 若要强制所有 token 带声明，得配合一次「全体重新登录」的公告 |
| TD-174 | 时间戳比较用 `claims.pwd < current` | 用 `!=` | **实测两者是等价变异**：改密码后新签发 token 的 `pwd` 与库值相等，`a != a` 本就是 False，两种写法都放行；把 `<` 改成 `!=` 后 15 条测试**全绿**，杀不掉。差别只在 `pwd > current`（时钟回拨、手改库）时出现：`<` 放行、`!=` 拒绝。选 `<` 是因为那种情况拒绝会让用户莫名登不进去，且没放宽真正的威胁模型 | 无。这条记录的目的是避免后人误以为这里有可测的行为差异 |
| ~~TD-175~~ | ~~`GET /oauth/authorize` 是 cookie 化后新引入的残留 CSRF 面~~ **已解决**（TD-78 一并做掉）：GET 现在只渲染同意页、**签不出任何东西**，攻击者诱导用户点一个链接最多让用户看到一张页面。签发只发生在 POST，而 `SameSite=Lax` 本来就不让跨站 POST 带上 cookie；表单另有一个 HMAC 签名作为第二层。`test_cross_site_navigation_cannot_issue_a_code` 复刻「只带 cookie 的跨站顶层导航」钉住这条 | — |
| TD-176 | cookie 用 `SameSite=Lax`，并把「需要登录的 GET 路由」钉成一张带理由的白名单 | `SameSite=Strict`，一步堵死 CSRF | Lax 只挡跨站的**写方法**，跨站顶层导航的 GET 照样带上 cookie —— 所以安全性依赖「GET 无副作用」这个前提。`test_authed_get_routes_are_read_only` 用**相等**断言（不是子集），新增一个需要登录的 GET 就会失败，逼作者写清它为什么没有副作用。不用 Strict 是因为它连「从微信/邮件点链接进来」的第一跳都不带 cookie，用户会看到一次莫名的未登录，对这个平台的链接分享场景是硬伤 | 若将来加了带副作用的 GET 又改不掉，就换 Strict 并接受那次未登录 |
| TD-177 | `GET /shop/download/{order_no}` 改成 **POST** | 保留 GET | 它**会改状态**（`mark_downloaded` 把 `paid` 烧成 `downloaded`，一次性下载就没了）。GET 带副作用本来就是错的；cookie 化之后它还是个 CSRF 靶子 —— 攻击者一个跨站跳转就能替用户把下载额度烧掉。该端点没有前端调用者，只有测试，所以改动面很小 | — |
| TD-178 | 配额检查是「先数再插」，没做原子化 | 数据库层加约束 / 可串行化隔离 / 咨询锁 | 并发建图时两个事务可能都数到 49 然后都插入，实际存活数会**小幅超过**上限。选它是因为配额的用途是**防刷库的刹车**，不是精确记账 —— 超出一两张没有任何后果，而为此上可串行化隔离或咨询锁，代价与收益完全不成比例。突发刷库由限流那层挡（TD-15） | 若将来配额变成计费依据（按张数收费），就必须改成原子操作 |
| TD-179 | 回收站不会自动清理 | 定时清理任务（`deleted_at` 早于 N 天的硬删） | 软删除的行仍然占着存储，所以「配额只管存活行」意味着**表增长本身没有被约束**：一个用户可以反复「建满 → 删光 → 再建满」。当前用户量下这只是磁盘问题，不是安全问题，所以没有为它引入定时任务框架 | 用户量上来后加一个定时任务硬删 30 天前的软删除行；或让 `DIAGRAM_QUOTA` 同时约束回收站大小 |
| TD-180 | 流程图**没有版本历史**（从 TD-64 里拆出来） | 每次保存留一份快照 | 只解决了「删掉找不回来」，没解决「改坏了退不回去」—— `PUT` 是直接覆盖 `content`。做版本历史要另设一张快照表并处理存储增长，与本次「上线阻塞项」无关，故未做 | 有用户反馈改坏图找不回时，加 `sys_diagram_revision` 表 + 保留最近 N 版 |
| TD-181 | 流程图**没有协同编辑**（从 TD-65 里拆出来） | 多人/多端同时编辑一张图（OT / CRDT） | 乐观锁只做到「不静默覆盖」：后保存的人会收到 412 并被要求重新打开，他这一版的改动**要自己重做**。真正的协同需要 OT 或 CRDT 与一套冲突合并 UI，与毕设范围无关 | 有真实多人协作需求时再评估 |
| TD-182 | 乐观锁走标准的 `ETag` / `If-Match` / `412`，不在请求体里放 `version` 字段 | 在 `DiagramIn` 里加一个 `version` 字段 | 三点原因：① `DiagramIn` 同时用于 POST（新建），给它加 `version` 语义上讲不通；② 请求体里的版本字段是**可选**的话就等于默认不校验，bug 原地保留，而改成必填又污染了新建；③ `ETag`/`If-Match`/`412`/`428` 是 HTTP 现成的语义，客户端与代理都认。代价是前端要多存一个响应头（`res.headers.get("ETag")`） | 无 |
| ~~TD-183~~ | ~~`test_faq_p95_survives_concurrent_heaviest_endpoint` 用绝对阈值 `p95 < 80 ms`，随机变红~~ **已解决（把这条测试换掉了）**。查下来问题比「阈值写法」严重得多，它有两个毛病：

**① 余量只有 0~25%。** 本机 2 核，正确实现下连跑 8 次：空载 p95 **19.4~20.2 ms**（极稳，2.5% 离散，80 ms 有 4 倍余量）；但「最贵端点并发时」的 p95 是 **60.2~77.0 ms**，贴着 80 这条线，稍有负载就越线。

**② 更要紧的是它测不到自己声称要守的退化。** `word_export` 先用线程池跑 `parse_ddl`（33 ms）**让出了事件循环**，20 个客服请求在那道窗口里就跑完了，之后才发生 `build_data_dictionary` 的 ~460 ms 阻塞（实测 453~504 ms）；而断言里的 `[1:]` 又把最慢的那条丢掉了。实测把 `build_data_dictionary` 改回同步执行（**修复前的真 bug**），这条 p95 是 31~88 ms，**3 次里 2 次照样通过**。换成比值也不行：线程池 58~106 ms、进程池 60~77 ms，两者分布几乎完全重叠。

真正守得住的是 `test_event_loop_stays_responsive_during_word_export`（直接测事件循环停顿；**该用例后已换成确定性判据 `test_build_data_dictionary_runs_in_a_separate_process`，下面的数字是当时的实测**，见 TD-193），本机实测三种实现：进程池 < 20 ms **通过 3/3**；线程池 40/64/41 ms **失败 3/3**；同步执行 534/541/531 ms **失败 3/3**。所以把 p95 那条换成 `test_support_and_heaviest_endpoint_coexist` —— 只保留「两个端点能同时正常返回」这个集成事实 + 一个防数量级退化的粗界（250 ms），**不再冒充 SLA 断言**。**另外两条 HTTP 层延迟断言也一并去掉了**：`test_support_endpoint_p95_under_concurrency` 单跑 19.5 ms（8 次 19.4~20.2，2.5% 离散，看着有 4 倍余量），但放进全量套件后实测 **20.3 / 88.2 / 94.8 ms**（三次），其中一次是 `/health` 基线飙到 91 ms、客服只有 20 ms —— 噪声落在哪个 20 路突发上、哪个就超线，所以绝对阈值和「客服/基线」比值阈值**都守不住**（比值在 0.22~9.8 之间摆）。根因是沙箱只有 2 核，其余 380 个测试的余温会随机撞上某一次突发。ROADMAP S5-02-1 的 80 ms 指标改由纯函数用例 `test_faq_search_meets_80ms_budget_single_threaded` 承载（实测均值 0.049 ms，三个数量级余量）。改完 `tests/test_perf.py` 连跑 8 次全绿，全量套件连跑 3 次全绿。 | **HTTP 层的 p95 延迟断言全部取消**（不是放宽阈值）——2 核机器上无法稳定断言；回归防线改为事件循环停顿（< 20 ms，对两种退化 3/3 抓住）+ 纯函数 80 ms 用例 |
| TD-184 | 同意页的防伪用**无状态 HMAC 签名**（`HMAC-SHA256(SECRET_KEY, client_id\n redirect_uri\n state)`）盖住表单，而不是会话绑定的 CSRF token | 服务端存一个会话级 CSRF token | 本站**没有服务端会话**（登录态就是那个无状态 JWT），为一个 token 引入会话存储与过期清理不划算。HMAC 签名同样能做到「POST 必须来自本站渲染过的那张页面」，且顺带防住渲染之后有人篡改 `redirect_uri` / `state`。**要说清的是**：CSRF 的主要防线仍是 cookie 的 `SameSite=Lax`（跨站 POST 不带 cookie），签名是第二层 | 若将来引入服务端会话（比如要支持「记住我的授权」），换成会话绑定的 token |
| TD-185 | `GET /oauth/authorize` 未登录时**直接 401**，不跳转登录页 | 经典 OAuth：302 到登录页，登录后带原参数跳回 | 本站没有独立登录页（登录表单内嵌在工具页里），为一个跳转新造一页不值得；且当前接入方是自己的两个平台，都是先登录再点授权 | 面向真实第三方开放、需要「点授权链接时还没登录」的体验时补 |
| TD-186 | 「重活移出事件循环」只由**事件循环停顿**（ticker）指标守，不用并发延迟 p95 守 | 用 p95 延迟断言（更贴近用户感受） | 单线程事件循环下这个指标**结构性失效**：重活一旦阻塞循环，其他请求根本不会并发执行，而是排队等它结束；等重活干完再测延迟，量到的只是排队之后的一小段。本项目实测把 460 ms 的同步阻塞塞回去，客服 p95 反而只有 31~88 ms —— **比正确实现（60~77 ms）还低**。要量「用户感知的等待」必须从重活开始之前就起表，也就是 ticker 那套（TD-162 是同一类陷阱的另一个版本） | 无 |
| TD-187 | 事件循环停顿测试在起表前先**用同一个 payload 预热一次** | 直接测量（少一次请求开销，看起来更纯粹） | 首次调用包含一次性开销：线程池首次创建、库缓存/正则首次填充、**没有 `__pycache__` 时的字节码编译**。沙箱回收后首次运行实测停顿 **61~63 ms**，冷启动 4/4 顶穿 20 ms 阈值，而热运行 12/12 通过 —— 定位办法是只删 `__pycache__`（失败）vs 只删 jieba 缓存（通过），确认与 jieba 无关。这些开销与「重活是否阻塞事件循环」无关，且每次调用都会付的部分才是要测的。**预热不削弱判据**：同步执行变异体每次调用都阻塞，实测热 26 ms / 冷 25 ms 仍被抓；word-export 变异体 3/3 失败（353~366 ms）。正确实现 10.8~16.2 ms，阈值 20 ms 落在两者中间 | 无 |
| TD-188 | 管理员角色存在**数据库列**（`sys_user.role`），不写进 JWT | 把角色塞进 JWT，省一次判断 | JWT 里的角色改不动：降权要等 token 过期才生效，被撤掉的管理员还能继续调管理端点。从库里读则**立刻生效**，而且不增加查库次数 —— `get_current_user` 本来就要读 `status` 和 `password_changed_at`（TD-70 同一思路）。有一条测试专门钉住这点：同一个 token，把 role 改回 0 后立刻从 200 变 403 | 每请求一次查库（本来就要查，无额外成本） |
| TD-189 | 管理端点的错误分**三档**：400 抓不了 / 422 提不出正文 / 502 大模型不可用 | 一律 500，或一律 400 | 三档对应三种不同的处置：400 是调用方给的 URL 有问题、422 是该换页面或改提示词、502 是上游挂了不该重试打本站。实现时踩到两个坑，都已用变异体钉住：**① `extract.identify_selectors` 会把 `LLMError` 包成 `ExtractError` 再抛**（`raise ExtractError(str(e)) from e`），所以写成 `except LLMError` 是**永不可达的死分支**，实测会把「未配置 LLM_API_KEY」报成 422「内容提取失败」—— 必须顺着 `__cause__` 认回去；**② `RobotsDisallowed` 是独立的 `Exception` 子类，不是 `CrawlError`**，漏接就变 500（`httpx.HTTPError` 即目标站超时/连不上同理）。8 个变异体（含删掉每个 except 分支）全部被测试杀掉 | 无 |
| TD-190 | 管理端点越权返回 **403 而不是 404**，并挂 **LLM 档限流** | 用 404 藏起端点；或不限流 | 端点存在与否不是本站的秘密（`/docs` 里本来就列着），假装不存在只会让管理员自己对着 404 猜半天 —— 真正的防线是「只接受管理员」，不是「别人找不到」。限流挂 LLM 档（10 次/60s）而不是工具档（30 次）：每次调用都花 LLM token，管理员账号被盗时也不能无限刷 | 攻击者能确认端点存在（但拿不到任何数据） |
| TD-191 | 动态页面用 **Playwright**，且**不放进 requirements.txt**（可选依赖）；渲染路径与静态路径**共用同一套** `parse_page` | Selenium；或把 playwright 写成必需依赖 | **为什么不 Selenium**：用户选型。**为什么不做成必需依赖**：wheel 47 MB、还要再下 ~150 MB 浏览器，而多数站点 httpx 就够；没装时端点返回 **503 + 安装命令**，而不是启动时炸掉整个应用。

**实测澄清一个误解**：Python 版 Playwright 的 wheel **自带** `playwright/driver/node`（123.7 MB，权限 `100755`），`_driver.py` 用 `os.getenv("PLAYWRIGHT_NODEJS_PATH", driver_path/"node")` 默认就调它；空 PATH（`env -i`）下实测独立运行 v24.18.1 —— 所以**不需要另外安装 Node.js**。

**顺序设计**：`render()` 里 SSRF 校验 → robots 判定 → 限速 → **才**启动浏览器。前三步不碰浏览器，所以在沙箱里是真跑的（沙箱下不到浏览器：`cdn.playwright.dev` 与 `playwright.azureedge.net` 实测均 HTTP=000）。⚠️ 浏览器同样会去访问调用方给的地址，`assert_public_url` 省不得 —— 少了它 `dynamic=true` 就是一个能打 169.254.169.254 的 SSRF 口子。

**变异测试 9 个杀掉 8 个**，唯一存活的是 `_goto` 里的 `final_url = pg.url`（取重定向后的最终地址）：端点测试把整个 `_goto` 换成了假的，所以看不到它内部 —— 这一行**只有真浏览器才覆盖得到**，已在 `tests/test_dynamic_crawl.py` 的最后一条（默认跳过、`RUN_BROWSER_TESTS=1` 打开）里补上断言，**合并前需在本机跑一次**。

另一个实测教训：把 `render` 里的 `assert_public_url` 删掉，`test_render_blocks_bad_url_before_touching_browser` **照样通过** —— 因为 `politeness.check_allowed` 会去抓 robots.txt，而 `_request` 内部也做 SSRF 校验，拦截「碰巧」还是发生了。那是巧合性防御，所以另加了一条把 robots 层整个换成空操作的测试专门钉住它（该变异体现已被杀） | 多一个可选依赖；`_goto` 内部需本机验证 |
| ~~TD-192~~ | ~~CI 的「真 PostgreSQL 16」job 存在非确定性失败~~ **已解决** | 见 TD-193 | 真因已由 CI 日志定位，**与 PostgreSQL 无关**：唯一的失败是 `tests/test_perf.py::test_event_loop_stays_responsive_during_big_ddl`，`assert 0.0747 < 0.02`，即 `1 failed, 399 passed`。原先记在这里的 `NullPool` + `drop_all` ACCESS EXCLUSIVE 锁**假设是错的** —— 那是本地另一次 `218 passed + 182 errors` 的事件，被错当成 CI 的失败原因写了进来；CI 从头到尾没有出现过 errors，只有这 1 条 failed。而且这条用例用的 `perf_client` 是内存 SQLite，**根本不连 PG**，「只有真 PG job 红」纯粹是两条 run 撞上了不同的调度噪声。 | 已于本轮处理 |
| TD-193 | 事件循环停顿测试改用**确定性判据**（查跑在哪个线程/进程），不再断言延迟 | 失去了「延迟数值」这个直观指标 | 原先两条用例都断言 `事件循环最大停顿 < 20 ms`。CI 实测正确实现的停顿是 **74.7 ms**，顶穿阈值。同一份日志排除了「CI 机器慢」：CI 的 warmup 请求 **39.8 ms**，本机 **35.7~45.3 ms**，**CPU 速度基本一样** ⇒ 那 74.7 ms 是共享 runner 的调度抖动。所以绝对阈值救不了，「停顿 / 同步耗时」的比值也救不了（本机该比值 0.30，CI 那次 2.1，分子是纯调度噪声）。改成查 `parse_ddl` 跑在哪个**线程**、`build_data_dictionary` 跑在哪个**进程**：与机器快慢无关，且判据更精确 —— 4 个变异体（同步执行 ×2、降级线程池、绕过 spy 的空转）全部被杀。 | 若将来需要真实延迟数字，靠 CI 的失败评论（TD-194）取样，不要写回断言 |
| TD-194 | CI 失败时自动把 pytest 输出摘要发成 **PR 评论** | 需要给 workflow 开 `pull-requests: write`；失败时 PR 下会多一条机器人评论 | 沙箱**取不到 CI 日志正文**：`gh api` 能拿到 job 列表和签名 URL，但那个 URL 指向 `productionresultssa18.blob.core.windows.net`，实测 **HTTP 000**（DNS 可解析，同一时刻 `api.github.com` 是 200 ⇒ 是出口被墙，不是权限）。TD-192 卡了整整一轮就是因此。绕行：每个 test job 的 pytest 步骤 `tee` 到文件，末尾加 `if: failure()` 步骤用 `gh pr comment` 把最后 300 行发出来 —— 评论走 `api.github.com`，沙箱读得到。注意写了 `permissions:` 块就**取代**默认权限，所以 `contents: read` 必须一并写上，否则 checkout 失败。 | 若 GitHub 以后允许直接取日志，这一步可以撤掉 |

| TD-206 | 意图路由改为**三级级联**（规则 → 语义 → LLM），并新增语义 FAQ 检索（S4-02-5） | 放弃了「训练一个 BERT 多分类模型」这个 ROADMAP 原方案 | **没有标注数据就训不了模型**，而这是硬约束。级联不需要任何标注：语义检索的语料就是 FAQ 表本身（12 条，启动时向量化一次）；LLM 分类是零样本。代价是两条新增的失败模式 —— 语义索引未预热、embedding 接口不可用 —— 两者都只意味着**退回词袋**，功能不缺失（`warm_semantic_index` 刻意吞掉 `LLMError`，绝不让一个可选增强拖垮启动）。`classify()` 刻意保持**同步**：纯本地判据、可离线测试，将来接 `BertIntentRouter` 也是同步的；LLM 那一层单独做成异步函数，级联由 `support.answer()` 编排。⚠ `SEMANTIC_CONFIDENCE_THRESHOLD = 0.55` **尚未实测标定**（沙箱无 embedding key），是按 text-embedding-3-small 的经验取值，上线前必须跑 `tests/test_faq_semantic.py` 里那条带 key 才执行的标定用例重新量。 | 攒够标注样本后训 BERT；见下条 |
| TD-207 | 每次级联判定都往 `codemax.intent.labels` 日志记一条 `(原文, 规则判定, 最终标签)` JSON | 放弃了「建一张标注表 + 运营后台」 | 这是 TD-206 的配套：**没有这条日志，级联花的钱就白花** —— 答案给了，却没沉淀下任何能拿去训模型的数据。用日志而不是建表：写入零成本、不影响主链路、导出只是 `grep` + `jq`。同时它也是排障依据（能看出某个问题是被规则判对的还是被 LLM 改判的）。顺带修了一处可观测性缺口：RAG 兜底转人工时原先会**覆盖掉路由依据**，现在把 `result.reason` 一并带上。 | 需要按标签抽样质检、或要人工修正标签时，再改成表 |

| TD-208 | `CodeMaxAuth.onChange()` **返回退订函数**，`notify()` 遍历副本 | 放弃了「监听器只增不减」的简单实现 | 复审抓到的真 bug：`shop.html` 的 `buy()` 在 401 时注册「登录后补一次下单」的监听器，但当时 `onChange` 只会 push，而代码里写的 `CodeMaxAuth.onChange(() => {})` 被当成「解绑」—— 它其实只是**再追加一个空监听器**。后果是业务动作被重放：用户下次登录（哪怕没点购买）会再下一单；未登录连点几次购买会累积多个监听器，一次登录触发多次下单。node 实测复现：只重新登录一次，下单调用 2 → 3。修法有三处：① `onChange` 返回退订函数（返回函数而不是 `off(f)`，调用方不必自己存引用、也不会退订错人）；② `notify()` 遍历 `listeners.slice()` —— 回调里退订自己时直接遍历原数组会因 splice 跳过元素，且只在有多个监听器时才出现；③ 页面侧用 `offBuy` 单意图守卫。已由 `test_login_retry_does_not_replay_buy_on_later_logins` 钉住，并做了变异验证（撤掉修复即变红）。 | — |
| TD-209 | `base.html` 的公共上下文收口到 `app/site.py:page_context()` | 放弃了「各渲染点自己拼字典」的灵活性 | 同一个复审抓到：`mock_pay_page()` 只传了 `title` 与 `order_no`，页面渲染出空品牌、空导航、CTA 的 `href=""`，却照样返回 200 —— 而当时的测试只断言「页面能开 + 有订单号」。收口后 `**extra` 传页面私有字段，公共字段漏传在结构上就不可能。配套的 `test_page_context_is_the_single_source_of_base_template_fields` 把 base.html 实际用到的变量与 `page_context()` 提供的做**集合比对**，谁往 base.html 加公共变量忘了同步就会红。顺带把 SEO 三件套改成「有值才输出」：空 `canonical` 比没有更糟（会让搜索引擎把当前 URL 当成规范地址的替身）。 | 若将来页面上下文差异大到 `**extra` 装不下，再拆成多个专用构造函数 |

## 维护约定

1. 新增功能时若做了取舍，**在本文追加一行并给编号**，不要只写在模块 docstring 里。
2. 修掉某条取舍时不要删行，把「何时回头改」改成「已于 `<commit>` 处理」，保留决策痕迹。
3. 上线阻塞项清单要随进度同步收缩。
4. **有新实现/新功能 → `docs/ARCHITECTURE_GUIDE.md` 必须同步**（TD-195）：新子系统补一课（四段式），
   已有行为变了改对应小节。同时**被文档记过的数字要全仓同步** —— 扫描用 `--exclude-dir=.venv`，
   **不要**用 `| grep -v "\.venv"`：目标行本身含 `.venv/bin/python`，那样会把要找的行全滤掉，
   得到「已无残留」的假结论（已踩过，见 `ARCHITECTURE_GUIDE.md` 7.10）。
