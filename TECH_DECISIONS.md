# 技术实现取舍清单（TECH DECISIONS）

> 本项目所有**实现层面的取舍**集中在此，按编号引用（如 TD-41）。
> 模块 docstring 里只留与该文件强相关的局部说明，全局取舍以本文为准。
> 更新日期：2026-09-07 ｜ 对应代码：分支 `arena/01a0599b-codemax-platform`（本文随代码同步更新，不钉 commit）

## 阅读方式

- **取舍**＝选了什么；**放弃了什么**＝另一条路；**代价**＝现在要承受的后果；**何时回头改**＝触发条件。
- 大部分取舍是"毕设阶段够用、且符合 AGENTS.md 第 1 条（代码尽量简洁）"的结果，不是能力缺失。
- **同一个 TD 可能在两处出现**：上面「上线阻塞项」摘要表（3 列、精简口径）与后面按主题分的
  明细表（5 列、完整口径）会各列一次。实测目前是 **TD-113**（微信支付未真机联调）与
  **TD-124**（模拟支付通道）这两条。所以 `grep -oE '^\| TD-[0-9]+' | sort | uniq -d`
  **会报这两个「重复」，那是交叉列举不是编号冲突**，不要照着改编号 ——
  改编号会打断全仓的交叉引用。判断是否真冲突要看内容是否指同一个决策。
- 计数口径（实测，三个数都不同，别混）：
  - **在用的表格行**（`^| TD-n`，不含划掉的）**178** 条
  - **全部表格行**（含 `~~TD-n~~ **已解决**`）**204** 条（其中划掉 26 条）
  - **全文提及**（含交叉引用与正文里的 `TD-n`）唯一 **193** 个
  - **唯一编号**（`grep -o '^| TD-[0-9]*' | sort -u`）**176** 个，编号至 **TD-228**

  引用本文时请说清用的是哪个口径，否则会得出互相矛盾的数字。

## ⚠️ 上线阻塞项（这些必须在上线前处理，不是可选项）

| 编号 | 事项 | 为什么阻塞 |
| --- | --- | --- |
| ~~TD-15~~ | ~~公开工具端点无限流~~ **已解决** | `app/ratelimit.py` 内存滑动窗口：工具 30 次/60s、LLM 10 次/60s、注册登录 10 次/60s，超额 429 + `Retry-After`（多实例部署的代价见 TD-141） |
| ~~TD-44~~ | ~~JWT 存 localStorage~~ **已解决**：登录态改走 **HttpOnly cookie**（`SameSite=Lax`，生产加 `Secure`，`Max-Age` 与 token 同寿命），前端不再碰 `localStorage`/`sessionStorage`（模板里已一处不剩）。Bearer 头**同时保留**，Swagger 与 API 客户端照旧；两者都带时以请求头为准。新增 `POST /auth/logout` 清 cookie。配套把有副作用的 `GET /shop/download/{order_no}` 改成 POST（TD-177），并把「需要登录的 GET 路由」钉成带理由的清单（TD-176） | 改用 cookie 就引入了 CSRF（TD-176/177）；`GET /oauth/authorize` 是残留面（TD-175） |
| ~~TD-64~~ | ~~流程图无配额、无软删除~~ **已解决（配额 + 软删除）**：每用户存活流程图上限 `DIAGRAM_QUOTA`（默认 50），超额 409 并把上限写进错误信息；删除改为打 `deleted_at` 时间戳，新增 `POST /diagrams/{id}/restore` 与 `GET /diagrams?deleted=true`（回收站）。对调用方而言删除语义不变（删完 GET 就 404、列表里也没有）。**恢复也要占配额**，否则「建满 → 删 → 恢复」就是绕过上限的后门。迁移：`database init/migrate_0003_diagram_deleted_at.sql`（已在真 PG 上验过升级与重跑两条路径） | 配额只管**存活**行，回收站不会自动清空 → 表增长仍不受约束（TD-179）；版本历史仍未做（TD-180）；配额检查有 TOCTOU 窗口（TD-178） |
| ~~TD-70~~ | ~~JWT 无 `jti`、无法吊销~~ **已解决**：改用**令牌版本化** —— JWT 带 `pwd` 声明（签发时 `password_changed_at` 的 UNIX 秒），`get_current_user` 每次与库里当前值比对，早于它就拒。新增 `POST /auth/password`（限流），改密码即吊销该用户**所有**旧 token，并返回一个新 token 让当前会话不掉线。**订正原文一处不准确的说法**：「封号后旧 token 仍然有效」并不成立 —— `get_current_user` 一直都查库并检查 `status != 1`，禁用是立刻生效的（`test_disabled_user_token_is_rejected_immediately` 钉住） | 撤销粒度是「按用户」而非「按单个 token」：无法只踢掉某一个会话而保留其他会话 |
| ~~TD-80~~ | ~~集成测试跑在 SQLite 上~~ **已解决** | 现在 `TEST_DATABASE_URL` 可整套跑真 PostgreSQL 16.2（当前基线：真库 619 passed + 2 skipped / SQLite 617 + 4 skipped），见 TD-121 |
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
| TD-204 | 全站登录模块做成**外部文件** `app/frontend/auth.js`（构建产物 `app/static/js/auth.js`，TD-221） + `base.html` 的 `{% if auth_ui %}` 开关；OAuth 同意页传 `auth_ui=False` | 把登录模块写成 base.html 的内联脚本（更省事） | `base.html` 是**所有**页面的父模板，包括 OAuth 同意页 —— 那是发放授权码的安全关键页，`tests/test_oauth_consent.py` 断言它渲染出来**一个 `<script>` 都没有**。第一版我直接往 base.html 加内联脚本，那条测试当场变红。做成外部文件后由 `script-src 'self'` 覆盖，连 `'unsafe-inline'` 都不需要（TD-163 的方向），再用 `auth_ui` 开关让同意页整套 UI 与脚本都不渲染。代价是多一个静态文件与一个模板变量 | 若将来上严格 CSP（nonce 方案），所有内联脚本都要外部化，这个开关可以一并去掉 |

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

| TD-206 | 意图路由改为**三级级联**（规则 → 语义 → LLM），并新增语义 FAQ 检索（S4-02-5） | 放弃了「训练一个 BERT 多分类模型」这个 ROADMAP 原方案 | **没有标注数据就训不了模型**，而这是硬约束。级联不需要任何标注：语义检索的语料就是 FAQ 表本身（12 条，启动时向量化一次）；LLM 分类是零样本。代价是两条新增的失败模式 —— 语义索引未预热、embedding 接口不可用 —— 两者都只意味着**退回词袋**，功能不缺失（`warm_semantic_index` 刻意吞掉 `LLMError`，绝不让一个可选增强拖垮启动）。`classify()` 刻意保持**同步**：纯本地判据、可离线测试，将来接 `BertIntentRouter` 也是同步的；LLM 那一层单独做成异步函数，级联由 `support.answer()` 编排。⚠ 阈值默认 0.55 **仍未实测标定**（沙箱无 embedding key），但已从模块常量改为配置项 `LLM_SEMANTIC_THRESHOLD`，且标定流程本身已被验证过，见 TD-210。 | 攒够标注样本后训 BERT；见下条 |
| TD-207 | 每次级联判定都往 `codemax.intent.labels` 日志记一条 `(原文, 规则判定, 最终标签)` JSON | 放弃了「建一张标注表 + 运营后台」 | 这是 TD-206 的配套：**没有这条日志，级联花的钱就白花** —— 答案给了，却没沉淀下任何能拿去训模型的数据。用日志而不是建表：写入零成本、不影响主链路、导出只是 `grep` + `jq`。同时它也是排障依据（能看出某个问题是被规则判对的还是被 LLM 改判的）。顺带修了一处可观测性缺口：RAG 兜底转人工时原先会**覆盖掉路由依据**，现在把 `result.reason` 一并带上。 | 需要按标签抽样质检、或要人工修正标签时，再改成表 |

| TD-208 | `CodeMaxAuth.onChange()` **返回退订函数**，`notify()` 遍历副本 | 放弃了「监听器只增不减」的简单实现 | 复审抓到的真 bug：`shop.html` 的 `buy()` 在 401 时注册「登录后补一次下单」的监听器，但当时 `onChange` 只会 push，而代码里写的 `CodeMaxAuth.onChange(() => {})` 被当成「解绑」—— 它其实只是**再追加一个空监听器**。后果是业务动作被重放：用户下次登录（哪怕没点购买）会再下一单；未登录连点几次购买会累积多个监听器，一次登录触发多次下单。node 实测复现：只重新登录一次，下单调用 2 → 3。修法有三处：① `onChange` 返回退订函数（返回函数而不是 `off(f)`，调用方不必自己存引用、也不会退订错人）；② `notify()` 遍历 `listeners.slice()` —— 回调里退订自己时直接遍历原数组会因 splice 跳过元素，且只在有多个监听器时才出现；③ 页面侧用 `offBuy` 单意图守卫。已由 `test_login_retry_does_not_replay_buy_on_later_logins` 钉住，并做了变异验证（撤掉修复即变红）。 | — |
| TD-209 | `base.html` 的公共上下文收口到 `app/site.py:page_context()` | 放弃了「各渲染点自己拼字典」的灵活性 | 同一个复审抓到：`mock_pay_page()` 只传了 `title` 与 `order_no`，页面渲染出空品牌、空导航、CTA 的 `href=""`，却照样返回 200 —— 而当时的测试只断言「页面能开 + 有订单号」。收口后 `**extra` 传页面私有字段，公共字段漏传在结构上就不可能。配套的 `test_page_context_is_the_single_source_of_base_template_fields` 把 base.html 实际用到的变量与 `page_context()` 提供的做**集合比对**，谁往 base.html 加公共变量忘了同步就会红。顺带把 SEO 三件套改成「有值才输出」：空 `canonical` 比没有更糟（会让搜索引擎把当前 URL 当成规范地址的替身）。 | 若将来页面上下文差异大到 `**extra` 装不下，再拆成多个专用构造函数 |

| TD-210 | 语义阈值从模块常量改为配置项 `LLM_SEMANTIC_THRESHOLD`，并把标定流程抽成可注入 client 的纯函数 | 放弃了「常量 + 一条只在有 key 时才跑的标定用例」这个原设计 | **起因是一个真实耦合缺陷**：`LLM_EMBED_MODEL` 本来就是可配置的（`.env.example` 里还写着「本地 Ollama 换成 nomic-embed-text」），而阈值是硬编码常量 —— 换 embedding 模型会让余弦分布整体漂移，沿用旧阈值要么永远不命中、要么乱命中。可配置的东西旁边不该有一个必须跟着它变却变不了的常量。改成每次调用现读 `settings`，标定结果只需改 `.env`，不必改代码重新发版；测试也能 monkeypatch。**顺带发现标定用例本身有两个缺陷**：① 它只断言「同义问句分数 ≥ 阈值」，方向是反的 —— 阈值定得**太高**才会失败，而定得**太低**（会把无关问句当 FAQ 直接作答，用户拿到自信的错误答案）永远发现不了；② 它不校验命中的是哪条 FAQ，「高相似度命中错误 FAQ」能顺利通过。两个都已修：现在同时量同义与无关两组分布、要求阈值落在间隙内，并断言命中身份。**更关键的是把标定流程抽成 `_run_calibration(client)`** —— 那条用例没有 API key 就永远不跑，写在里面的断言等于从没被执行过；抽出来之后沙箱用合成向量空间整条跑了一遍，当场抓出一个真 bug：`warm_semantic_index(client)` 传了 client，但 `semantic_search()` 没传，于是查询走无 key 的 `default_llm` 返回 `None`。`calibrate_threshold()` 刻意做成纯函数（重叠或间隙过窄直接抛 `ValueError` 而不是硬算一个数），所以标定逻辑本身在沙箱里被充分测过，真正需要 key 的只剩「喂真实分数」这一步。 | 攒够标注样本后训 BERT，届时这个阈值随模型一起退休 |

| TD-211 | `shop.html` 的每次 `buy()` 领一个递增序号，**只有最新尝试的响应才算数** | 放弃了「只靠 `offBuy` 单意图守卫」 | 第 4 轮复审抓到的真 bug，是 TD-208 的漏网面：上一轮修掉了「监听器永久残留」，但没管**响应乱序**。时序是 —— 未登录点购买（请求 A 发出）→ 用户从顶栏登录 → 再点一次购买（请求 B 成功下单）→ 此时 A 才迟到返回 401 → A 照样把补单监听器挂回去 → 用户将来某次重新登录（**没点购买**）凭空多下一张 pending 单。node 实测复现：`fetchCalls` 2 → 3。`offBuy` 管的是「同一时刻只登记一个意图」，管不了「这个响应还该不该被采信」—— 两件事。守卫放在 401 分支之前，所以过期响应连登录浮层都不会弹（用户早已登录，弹窗本身就是错的）。已由 `test_a_stale_401_cannot_revive_a_purchase_intent` 钉住并做变异验证。 | 若将来下单改成可取消的请求（`AbortController`），可改为直接中止旧请求 |
| TD-212 | 全部数值配置加范围约束（`Field(ge=/gt=/le=)`） | 放弃了「配置值不做校验、坏了在运行期自然暴露」 | 复审指出 `LLM_SEMANTIC_THRESHOLD` 无范围校验；顺着查发现**同类问题有一批**，其中一条是安全级的：实测 `RATE_LIMIT_WINDOW=0`（或负数）会让 `Limiter.allow()` 把所有历史命中都弹出，`len(hits) >= limit` 永不成立 —— **限流被完全关掉且不报错**（fail-open）。另外实测 `SHOP_PRODUCT_AMOUNT=-100` 会静默生成负价订单。判据是「越界是否**静默**导致行为变形」：`RATE_LIMIT_*=0` 是全挡（fail-closed，立刻可见）风险低但仍约束；`DIAGRAM_QUOTA=0`/`HSTS_MAX_AGE=0` 表示「关闭」，是合法配置，用 `ge=0` 而非 `gt=0`。约束放配置边界让进程**启动即失败**，而不是线上悄悄跑歪。`test_config_validation.py` 覆盖 16 组非法值 + 「0 在哪些字段合法」+ **`.env.example` 自身必须合法**（模板留个越界示例，用户照抄就起不来，报错还看着像代码 bug）。 | 需要按环境分级放宽时改用 validator |
| TD-213 | 依赖漏洞：CI 加 `pip-audit`，并做一次依赖升级把 **15 条已知 CVE 清到只剩 1 条** | 放弃了「依赖版本钉死就不管了」；也放弃了「加个天生就红的 audit job」 | 复审的 P1-7 只点了 `python-jose==3.3.0` 两条 CVE。装上 `pip-audit` 实跑才发现**真实面比这大得多**：6 个包共 **15 条**唯一漏洞（`starlette` 7、`python-multipart` 4、`fastapi`/`python-dotenv`/`pytest`/`ecdsa` 各 1）。<br><br>**第一轮（已修 1 条）**：`python-jose` 3.3.0 → **3.5.0**，堵住 CVE-2024-33663（OpenSSH ECDSA 等密钥格式的**算法混淆**，可用公钥签名，GHSA-6c5p-j8vq-pqhj）与 CVE-2024-33664（JWE 高压缩比解压 DoS，3.4.0 起限 250 KiB）。本站用它签发/校验**登录令牌**，算法混淆正好打在核心用途上。<br><br>**第二轮（又清 13 条）**：一次依赖升级 —— `fastapi` 0.104.1 → **0.141.1**（带动 `starlette` 0.27.0 → **1.6.0**、`pydantic` 2.5.2 → **2.13.5**、`pydantic-settings` → **2.15.0**、`anyio` → **4.15.1**），另升 `python-multipart` → **0.0.31**、`python-dotenv` → **1.2.2**、`pytest` → **9.0.3** + `pytest-asyncio` → **1.4.0**。分三阶段做、每阶段跑测试，最终**全量 624 passed / 4 skipped 与升级前完全一致**。<br><br>升级踩到两个真实的破坏性变更（都已修，并写进代码注释）：<br>① **FastAPI 0.141 改了 `include_router`**：不再把子路由摊平进 `app.routes`，而是塞一个 `_IncludedRouter` 包装对象。实测 `{getattr(r,"path",None) for r in app.routes}` 只剩 `{'/docs','/openapi.json','/redoc','/static',None}` —— 10 处靠遍历 `app.routes` 断言的测试当场全红。修法：`tests/conftest.py` 加 `iter_app_routes()` 递归展平，**只用它公开的 `original_router`**（不碰私有类名），且 `getattr(..., None)` 在旧版恒为 None ⇒ 同一份代码新旧两版都对。**生产代码不受影响** —— `build_docs_site.py` 走 AST 解析装饰器，不碰运行期 `app.routes`。<br>② **Starlette 1.x 改了 `TemplateResponse` 签名**：第一个位置参数从 `name` 变成 `request`。旧写法 `TemplateResponse("er.html", ctx)` 会把 **context 字典当成模板名**，Jinja 模板缓存拿 dict 当 key 直接抛 `TypeError: unhashable type: 'dict'`。三处调用点（`site.py`/`shop.py`/`oauth.py`）全部改为 `(request, name, context)`。<br><br>**为什么仍然加 audit job**：`requirements.txt` 是手工钉版本的，钉死之后没有任何机制会在上游披露新 CVE 时通知我们 —— 这两条 python-jose 的 CVE 就在文件里躺了很久没人知道。`--strict` 保证「扫描器没报错」和「扫描器没扫到」能区分开。<br><br>**为什么不用「加个会红的 job」**：照原样加 audit job，CI 会**立刻全红** —— 而天生就红的检查等于没加（大家会习惯性忽略红叉）。所以带显式 `--ignore-vuln` 保持绿色，用来抓**新**漏洞。**债被挂账，没有被隐藏**；第一轮挂 15 个 ID，第二轮清到只剩 1 个。实测 `pip-audit --strict -r requirements.txt --ignore-vuln PYSEC-2026-1325` → 「No known vulnerabilities found, 1 ignored」，退出码 0。<br><br>另在 `test_ops.py` 钉了两道本地防线（CI 可能被跳过、分支保护可能没开，而这两条跟着每次 pytest 跑）：`python-jose` 低于 3.4.0 直接失败；`ci.yml` 必须有 `pip-audit` 且带 `--strict`。 | **只剩 `ecdsa` 0.19.2 的 PYSEC-2026-1325** —— 上游至今没有发布任何修复版本（它是 `python-jose` 的传递依赖，本项目代码不直接 import 它）。只能等上游，或改用别的 JOSE 实现。另：`fastapi`/`starlette`/`pydantic` 的跨大版本升级已完成，但**真机（Windows + 反向代理）尚未验证**，上线前需按 `docs/DEPLOY.md` 走一遍 |
| TD-214 | RAG 索引按 `(count, max(id))` 指纹缓存复用，且分词/建索引**整体**在线程池里跑 | 放弃了「每次请求现建索引」，也放弃了「把语料全量常驻内存并常驻索引」 | 复审 P1-2：`_retrieve_articles()` 每次都 `select(Article)` 全表捞出所有文章（含完整正文）、逐篇 jieba 分词、现建 BM25/余弦索引，而 `/support/ask` **不鉴权**（只有 `RATE_LIMIT_LLM` 限流）。实测 200 篇时检索墙钟 **242.6 ms**，同期 `asyncio.sleep(10ms)` 最大漂移 **232.3 ms** —— 那 0.23 秒内**全站所有请求**（含不需鉴权的 ER 图、Mermaid 引流页）都排不上队，匿名用户反复提问就能让整站周期性卡顿。与 A-1（bcrypt 堵事件循环）、TD-159/183/186 是同一条原则。<br><br>**两个改动**：① 先用一次极轻的 `select(count(id), max(id))` 算指纹，命中缓存就完全不必把全表正文捞进内存；② 未命中时把「分词 + 建索引」丢进 `run_in_threadpool`。<br><br>⚠️ **第一版写错了，值得记**：写成 `run_in_threadpool(RetrievalIndex, [tokenize(...) for a in rows])` 是**没用的** —— 那个列表推导式在**传参时**就已在事件循环里算完了，只有便宜的建索引进了线程，实测漂移纹丝不动（236.7 ms）。必须包成一个函数 `_build_index(rows)` 再整体丢进去。<br><br>⚠️ **写测试时也踩了一个坑**：相关性用例最初复用 `_seed()` 造的语料，而那批文章正文里**也含被测词**「反向代理」，200 篇一起竞争，第一名是谁就成了语料分布问题而不是检索质量问题。对照组语料必须与被测词无关。<br><br>实测效果：事件循环漂移 **232.3 → 10.5 ms**；第二次查询 **250.4 → 2.8 ms（89×）**。新增 `tests/test_support_rag_perf.py` 4 条（先看红），变异验证撤掉线程池与缓存 → 2 条转红。 | 指纹 `(count, max(id))` 只覆盖**插入与删除**。`sys_article` 在本项目里是抓取入库、只插入的（`url` 唯一、没有编辑端点），所以够用；**将来若加了「编辑文章正文」的功能，必须给表加 `update_time` 并把它并进指纹**，否则改完搜不到新内容。另：索引是进程内全局，多实例部署时各算各的（与限流 TD-141 同理），语料更新后各实例会在下一次请求时各自重建一次。**订正（2026-09-20，O-09）**：指纹后来已改为对全部 `(id, title, content)` 做 sha256（`app/tools/support.py::_retrieve_articles`），编辑正文也会使缓存失效，本行前文的 `(count, max(id))` 与「必须加 `update_time`」是当时的方案，不再描述现行代码；代价变成每次请求仍要全表读取正文并哈希（O(语料大小)），只省下分词/建索引 |
| TD-215 | 爬虫的响应体上限改成**流式边下边判**，并额外加一道 `Content-Length` 预检 | 放弃了「先 `client.get()` 整包下完、再 `len(r.content)` 判大小」 | 复审 P1-4：旧写法 `if len(r.content) > max_bytes` 里，`r.content` 这个**属性访问本身**就已经把整个响应体读进内存了 —— 判断发生在读完之后，等于没有内存预算。喂一个 10GB 的 URL 进来，会先全下完才说「太大了」。`MAX_BYTES=2MB` 配合 `MAX_CONCURRENCY=4` 本意是把最坏内存占用钉在 ≈8MB，但旧代码让这个预算形同虚设。<br><br>⚠️ **原来的 `test_fetch_enforces_size_limit` 抓不到这点**：它只看最终抛没抛 `CrawlError`，而「先下完 4MB 再拒」和「下到 1MB 就断」对它是同一个结果。这是「只断言结果、不断言过程」的典型盲区。<br><br>**改法**：每跳都用 `client.send(req, stream=True)`，`aiter_bytes()` 累加，一超限立刻 `CrawlError` 并 `aclose()` 断流。另外服务器自己声明 `Content-Length` 超限时，**一个字节都不下**。<br><br>**新测试怎么观测「提前中断」**：用异步生成器当响应体并记录它被消费了几块。实测 1MB 上限 + 4MB 响应体，旧代码拉走 **20/20** 块，改后只拉走 **6/20** 块。变异验证：改回整包读 → 该用例转红。<br><br>**重建 Response 用公开 API 而非 `r._content`**：`httpx.Response(status, headers=..., content=..., request=...)`。这样 `.text` 的字符集解码（含 gbk 等非 UTF-8 页面）继续由 httpx 按 Content-Type 处理，已实测 gbk 往返正确；也不必往私有属性里塞字节。 | 只影响 `app/tools/crawler.py` 内部，`_request` 的返回类型仍是 `httpx.Response`，两个调用方（robots 抓取、页面抓取）无需改动。`Content-Length` 预检对**不发该头**的服务器（如 chunked 传输）不生效，此时仍靠流式累加兜底。测试用 `httpx.MockTransport` + 异步生成器即可复现，不需要真出网 |## 维护约定
| TD-216 | 本地后端的下载出口 `/shop/dl` 用 `FileResponse` 流式发文件；`LocalStorage` 新开 `local_path()` 返回**已过穿越校验**的路径 | 放弃了 `data = storage.read(key)` 整包读进内存再包成 `Response` | 复审 P1-6：旧实现把整个压缩包 `read_bytes()` 进内存。一个 500MB 的软件包就是 500MB 常驻，几个用户同时下载就 OOM；而这是**已登录用户**的正常操作，不是攻击。<br><br>**改法**：`FileResponse` 由 starlette 分块读盘、边读边发，`Content-Length` 自动算对。<br><br>**为什么新开 `local_path()` 而不是让路由自己拼 `root / key`**：保证任何下载出口都**必须**走 `_path` 的目录穿越校验，绕过它就能读服务器上任意文件。<br><br>⚠️ **改 FileResponse 必须配套一个存在性检查**：starlette 的 `FileResponse` 遇到文件不存在是在**响应阶段**才炸的，那时已经出了 `HTTPException` 的管辖范围（会变成 500）。旧代码靠 `read()` 抛 `FileNotFoundError` 被 catch 成 404，改完这条保护就没了，必须显式补 `if not path.is_file()`。<br><br>**测试怎么盯「不许整包读」**：把 `LocalStorage.read` 换成会记账的桩，断言它一次都没被调。直接盯调用比盯内存占用可靠得多 —— `ASGITransport` 在测试里本来就会把响应体缓冲起来，内存量根本测不出区别。变异验证：把 `storage.read(key)` 加回去 → 该用例转红。 | 只影响 local 后端；云后端由客户端直连对象存储，本来就不经过这个端点。`Content-Disposition` 改由 starlette 生成（`attachment; filename="…"; filename*=utf-8''…`），与旧的手写 `filename*=UTF-8''…` 等价，现有断言只查 `"attachment"` 子串。另：`app/routers/shop.py` 的 `urllib.parse.quote` 与 `fastapi.Response` 两个 import 因此不再被使用，已一并清掉（ruff F401） |
| TD-217 | `oauth_code` 的清理放在**签发授权码时顺带做**（`DELETE WHERE used OR expires_at < now`），不引入调度器 | 放弃了「只在读时判过期、永不删行」，也放弃了「起一个后台周期任务定时清」 | 复审 P1-9：授权码是一次性、`AUTH_CODE_EXPIRE_MINUTES`（`app/routers/oauth.py` 模块常量 10，不是 `.env`/`Settings` 字段）到期就作废的凭据，旧实现只在 token 端点判过期，**从不删行** —— 每次授权流都给 `oauth_code` 留一条永久记录，表只增不减。<br><br>**为什么不起后台周期任务**：本仓库目前没有任何周期任务机制（`main.py` 的 `lifespan` 只做启动预热与关闭清理），为一张小表引入一套调度不值得。放在签发点，清理量天然与流量成正比 —— 忙站自己清干净，闲站本来也不产生垃圾。<br><br>⚠️ **条件绝不能放宽成「顺手清掉旧行」**：未过期且未使用的码可能正处于「用户点了同意、正要拿去换 token」的窗口里，删掉它就表现为**登录偶发失败** —— 最难排查的那类 bug。所以写成 `used OR expires_at < now`，并配了一条**反方向**用例 `test_valid_unused_code_survives_purge` 钉住。<br><br>**测试成对写**：`test_expired_and_used_codes_are_purged`（该清的必须清，先看红：`assert 3 == 1`）+ 上面那条（不该清的绝不能清）。只写前一条的话，把条件放宽成无条件 DELETE 也能全绿。变异验证：撤掉清理 → 前者转红。 | `oauth_code` 表很小（活跃码 = 正在登录的人数），全表扫的 DELETE 成本可忽略，因此**刻意不加 `expires_at` 索引** —— 本仓库没有 alembic，加索引要同步改生产建表 SQL，为这个规模不值得。表若涨到十万级再考虑。另：`used=True` 的行不再保留，重放审计走 logger（本仓库无审计表的既有约定） |1. 新增功能时若做了取舍，**在本文追加一行并给编号**，不要只写在模块 docstring 里。
| TD-218 | 生产自检补 3 条硬检查（`DB_PASSWORD` 空 / `SECRET_KEY` < 32 位 / `SITE_BASE_URL` 非 https），`STORAGE_BACKEND=local` 走**告警不拦** | ① `SECRET_KEY` 只查「等于默认值」不查长度；② `SITE_BASE_URL` 照 review 建议查「等于默认值」；③ `STORAGE_BACKEND=local` 照 review 建议硬拦启动 | 复审 A-12。三条硬检查的理由都是「跑着但行为是错的」：`DB_PASSWORD` 默认就是 `""`，`.env` 漏一行即空，真库开 trust 认证会**静默连上**无密码的库；`SECRET_KEY` 旧检查只拦默认值，改成一个 8 位短串就绕过去了，而 HMAC 强度取决于密钥熵，短密钥可离线暴破、JWT 照样能伪造；`SITE_BASE_URL` 是预签名下载链接、HSTS、OAuth 回跳的共同基准，配错表现为「站点起得来但链接全指向错主机」。<br><br>⚠️ **两处刻意偏离 review 建议**：<br>**① `SITE_BASE_URL` 不查「是否等于默认值」** —— 默认值 `https://codemax.top` 就是真实生产域名，照那样写会把真正的生产部署判成不合规。改查客观不安全的两种：空串、非 `https://`。<br>**② `STORAGE_BACKEND=local` 只告警** —— local 后端配挂载卷、单实例是**合法**的生产形态，而这个配置项本身看不出卷挂没挂。硬拦会逼运维去关自检，那比不检查更糟。故新增 `check_production_warnings()`，由 `enforce_production_settings()` 走 `codemax.startup` logger 输出。<br><br>⚠️ **`SECRET_KEY` 用 `elif` 接在默认值检查后** —— 默认值 `dev-secret-change-me` 只有 20 位，会同时触发两条；同一个根因报两遍只是噪音，且默认值那条消息更可操作。 | **两条钉了精确条数的旧用例被改，且是加强不是削弱**：`test_production_with_all_defaults_is_rejected` 从 4 条变 5 条（多出 `DB_PASSWORD`）；`test_mock_pay_in_production_is_the_first_thing_reported` 原先用的 25 位密钥 `"a-real-long-random-secret"` 现在会被短密钥检查抓到，`len == 1` 会失去「只有支付这一条在响」的含义。三条旧用例统一改用新的 `_clean_prod(monkeypatch, **over)` 助手把**全部**字段钉死，每条用例只测一个字段。另加反方向护栏 `test_clean_production_config_passes_every_check` —— 检查项越加越多，只要有一条在正常部署下也报，运维就会习惯性忽略整个列表。变异验证：撤掉三条新检查 → 对应 3 条用例转红 |2. 修掉某条取舍时不要删行，把「何时回头改」改成「已于 `<commit>` 处理」，保留决策痕迹。
| TD-219 | 微信回调加**时间戳新鲜度**校验（`±300 秒`），且刻意放在**路由层**而不是 `verify_notify_signature` 内 | ① 保持「只在验签串里用 timestamp、不比对时间」；② 把时间策略塞进 `verify_notify_signature` | 复审 P1-3：`verify_notify_signature` 把 timestamp 拼进验签串，却**从不比对它和当前时间**。于是攻击者只要抓到一个真实回调（HTTPS 抓包、日志泄漏、转发链路任一环），就能**永久重放** —— 签名一直是合法的，让已关闭订单重新变已支付、或反复触发发货，都不需要伪造任何东西。<br><br>**窗口两侧都挡**：过去超出 = 重放；未来超出 = 伪造或对端时钟错乱（那它其它时间字段也不可信）。<br><br>⚠️ **为什么放路由层**：`verify_notify_signature` 是纯签名验证，现有 5 处单测都用固定时间戳复算签名。把时间策略塞进去会让「验签对不对」与「时间新不新」两件事纠缠，也没法各自单测。放在路由里、验签**之前**，还能省掉一次 RSA 运算。<br><br>⚠️ **测试 fixture 的一个坑**：新鲜时间戳必须在**构造时**算，不能用模块级常量 —— 模块级常量在 pytest collection 阶段就求值了，而全量套件要跑 5 分钟，晚跑到的用例会偶发踩到 300 秒窗口边界，变成极难排查的随机失败。模块级 `TIMESTAMP` 只留给纯验签函数的单测（它不查新鲜度）。 | `NOTIFY_MAX_SKEW_SECONDS=300` 取自微信官方建议。**只防重放，不防同一窗口内的重放** —— 5 分钟内原样重发仍会被接受；真正的一次性由订单状态机的 CAS（`mark_paid` 只在 `pending` 时成功）兜底，两层缺一不可。另：`Wechatpay-Serial`（平台证书序列号）仍未校验，本项目只配一把 `WX_PLATFORM_CERT`，收到任何 serial 都用它验，证书轮换期会验签失败 —— 属可接受的显式故障，未修 |3. 上线阻塞项清单要随进度同步收缩。
| TD-220 | 把 `coverage` 加进测试依赖，让 `.coveragerc` 真的能跑；覆盖率**手动跑、不进 CI 门禁** | ① 删掉 `.coveragerc`（当死配置处理）；② 在 CI 里加覆盖率 job 并设阈值门禁 | 复审 N-4 说「`.coveragerc` 入库但无流程执行」，实测发现问题**更严重**：`coverage` 既不在 `requirements.txt` 里、也没装进 venv —— 那个配置文件连跑都跑不了，纯装饰。而它的文档写得很扎实（`concurrency = thread,greenlet` 有 `sys.settrace` 逐行对照的实测依据），删掉可惜。<br><br>**选「装起来 + 写清跑法」而不是删**：这个文件解决的是真问题 —— pytest-asyncio + httpx `ASGITransport` 下请求跑在 greenlet/线程上，coverage 默认只追踪主线程，会把**执行过的端点代码报成未覆盖**。照低报的数字去补测试，会得出「这些安全分支没测」的错误结论。<br><br>**不进 CI 门禁**：全量套件 5.5 分钟，加 coverage 后 5.7 分钟；而覆盖率阈值门禁的典型后果是「为了过线补无断言的测试」，与本仓库「不许靠降标准转绿」的铁律冲突。故保持手动。<br><br>⚠️ **验证时发现的一个陷阱**：`concurrency` 只影响**采集**、不影响报告 —— 拿同一份 `.coverage` 数据换个 rcfile 去 `report` 会得到**完全相同**的数字，那样做的「对比」是假的，必须重跑采集。 | 实测：全量 **96.0%**（2194 语句 / 缺 87 行）。`concurrency` 的作用用子集对照验证：同一批 4 文件 107 用例，带 **59.3%** vs 不带 **57.0%**。⚠️ 文件头写的「89% vs 97%」是更早那次全量的记录，**不带 concurrency 的全量差值本轮未重测**（要再花 6 分钟），只有子集对照。另：`rm -f .coverage*` 会连 `.coveragerc` 一起删（同前缀），清理必须写全 `rm -f .coverage .coverage.*` |4. **有新实现/新功能 → `docs/ARCHITECTURE_GUIDE.md` 必须同步**（TD-195）：新子系统补一课（四段式），
| TD-221 | 引入 **Vite 作为纯编译期工具链**：源码放 `app/frontend/`，产物 `app/static/js/*.js` **提交进仓库**，服务器上**不装 Node** | ① 保持纯 Jinja2 + 手写 `<script>`；② 换后端到 Node；③ 产物不入库、部署时现场构建 | 用户提出「全 Python（Jinja2）是不是不太实际」。**先把问题拆开**：后端换 Node 与前端加工具链是两件成本差一个量级的事，而真正让人不舒服的是前端 —— 实测 8 个模板里内联 JS **438 行**、内联 CSS 83 行，无法复用、无类型检查、改一处要满文件找。后端 FastAPI 没有问题，不该动。<br><br>**规模判断**：前端 JS 总共约 770 行，低于框架回本线（经验值 2000+ 行），所以**不上全站 SPA**。取中间档：加构建工具链（合并/压缩/后续可加 Vue 单页），但保留 Jinja2 SSR —— 引流页需要 SEO 与首屏，SPA 会让爬虫看到空 `<div id=app>`。<br><br>**为什么产物入库**：部署保持「pip install + 起一个 uvicorn 进程」，服务器不需要 Node、不需要 `node_modules`（实测 57 MB）。代价是必须有人看守「改了源码忘了重新构建」——由 CI 的 `frontend` job 兜：重新构建后 `git diff --exit-code -- app/static/js`，有漂移就红。<br><br>**多入口而非单 bundle**：单 bundle 会把 d3 塞进每个页面，包括用不到它的 shop / oauth 同意页。<br><br>**关掉文件名 hash**：产物要入库并做漂移比对，带 hash 的文件名每次构建都变，diff 全是噪音。<br><br>⚠️ **验证漂移检查时踩的坑**：第一次模拟「改源码不构建」用的是**注释**改动，而构建会压缩、注释被剥掉 ⇒ 产物字节不变 ⇒ 检查「看起来失效」。第二次用 `.replace(..., 1)` 又只命中了**第 1 行注释里**的同名标识符。必须改**真正的代码行**才测得出来：改赋值行后产物 2.32→2.33 kB、`git diff` 有差异。**验证一个检查是否有效，构造的输入必须真的落在它看守的那条路径上。** ⚠️ **`package.json` 里绝不能写 `"type": "module"`**：那会让 Node 把**全仓每一个 `.js`** 都当 ES 模块，于是 `app/static/er.js`（**当时还在，后已迁走，见 TD-222**）里的 `module.exports = { layoutEr }` 失效，`tests/test_er_page.py` 的 node 契约测试报 `layoutEr is not a function`（实测踩过：本地全量 3 failed）。Vite 的配置文件改用 **`vite.config.mjs`** 后缀单独声明 ESM 即可，仓库默认保持 CommonJS。<br><br>⚠️ **教训**：新增一个看似局部的配置文件，可能改变整个仓库里某种文件的语义。加完必须跑**全量**，不能只跑「我认为受影响的那几个文件」—— 这次我只跑了 5 个文件全绿就提交了，漏掉的 `test_er_page.py` 恰好是唯一被 `type: module` 打到的。<br><br>| 本轮只落地了 `auth.js` 一个入口（`er.js` 与 d3 vendoring 留作下一个提交，因为那一步要同时改写 `tests/test_frontend_supply_chain.py` 里 3 条针对 CDN + SRI 的断言口径，混在一起无法独立验证）。`minify` 不能写 `"esbuild"`：Vite 8 改用 rolldown，那条已废弃且要求单独装 esbuild 包，否则构建直接报 `Cannot find package 'esbuild'`。全仓引用同步：`base.html`、`tests/test_auth_cookie.py`、`test_diagrams.py`、`test_shop_page.py`（4 处，含 2 处从磁盘读文件）、`test_shop_polling.py`、`app/README.md`、`HANDOVER.md`、`ROADMAP.md`、`总览.md`、`docs/ARCHITECTURE_GUIDE.md`、TD-204 |   已有行为变了改对应小节。同时**被文档记过的数字要全仓同步** —— 扫描用 `--exclude-dir=.venv`，
| TD-222 | d3 由 npm 打进 `app/static/js/er-page.js`，模板不再引 CDN；`er.js` 拆成 `er-layout.js`（纯布局）+ `er-page.js`（d3 渲染） | ① 保持 CDN + 钉版本 + SRI；② 把 mermaid 也一起打包 | 两条理由：**供应链** —— 原来即使钉死 `d3@7.9.0` 并加 SRI，代码仍是**运行时**从 jsdelivr 取的，本站任何一次部署都管不到那台服务器；打进产物后代码随部署走，版本与完整性由 `package-lock.json` 的 integrity 在**安装时**校验。**可达性** —— ER 图是引流页，CDN 在国内不保证可达，d3 加载失败等于整页白屏。实测产物 49.01 kB（gzip 16.73 kB），d3 被 tree-shaking 到只剩用到的部分。<br><br>**为什么必须拆成两个文件**：契约测试 `tests/test_er_page.py` 用 node 直接执行布局函数、把接口真实返回喂进去核对前后端字段。CI 上**没有 node_modules**，所以纯布局那一半绝不能 import d3。<br><br>⚠️ **`mermaid` 刻意没一起打包**：压缩后近 2 MB，会让仓库与首屏明显变重。所以 `app/middleware.py` 的 `_CDN`（jsdelivr）**还不能从 CSP 移除** —— 我一度移除了，那会让 mermaid 页被 CSP 直接拦死。<br><br>⚠️ **反扫必须匹配两种写法**：经典 `<script src="https://…">` 与 `<script type="module">` 里的**裸 ESM `import`**。第一版只写了前者，于是 mermaid 那条 CDN 引用完全没被看见 —— 而它恰恰是当时唯一还在用 CDN 的依赖。<br><br>⚠️ **变异测试自己也会是空操作**：往 `index.html` 插 CDN 标签来验证新断言，但那个模板继承 base.html、**根本没有 `</body>`**，`.replace` 什么都没改，测试「通过」被误读成检查无效。换成 `base.html` 才真的红。**做完变异必须自证变异生效了。** | 供应链测试的 3 条断言被替换：原「d3 script 标签必须有 SRI」「SRI 必须与 npm 真实文件对得上」→ 现「模板不许有未登记的 CDN 脚本（mermaid 在允许清单里）」「d3 必须真的被打进产物（源码有 import + 产物 > 20 kB）」「lockfile 必须钉死 d3 版本与 integrity」。三条都做了变异验证。契约测试的 node harness 改为 ESM：`--input-type=module` + `pathToFileURL`（**Windows 上必需**，`await import("C:\…")` 会被当非法 URL）。`app/frontend/package.json` 局部声明 `"type": "module"`，把 ESM 范围限定在该目录，避免 TD-221 那个全仓副作用 |
| TD-223 | 模板里的页面内联 JS 一律抽成 `app/frontend/*-page.js` 走 Vite 打包，模板里只留一个 `<script src>` | 保留内联：省一次请求、没有构建步骤 | 内联脚本**没法 lint、没法 import、没法单测**，而 8 个模板里已经攒了 438 行（非空行口径）。抽出来之后可以接 eslint、可以 import 共享模块、可以用 node 真跑。**实测前提**：5 个含内联 JS 的模板里 Jinja 插值 **0 处**，所以是纯机械搬迁，不需要改数据传递方式（将来若某页真需要服务端注入变量，用 `data-*` 属性传，别把 Jinja 塞进 .js —— 那会毁掉可测试性）。**两个实测坑**：① Vite 产物可能保留 ESM 语法（mermaid 页的 CDN 裸 import 就是），用经典 `<script src>` 加载会语法错 ⇒ 必须 `type="module"`。逐产物实测 `import` / `export` 出现次数，**只有 mermaid-page.js 是 1**，所以只有它加 module；其余保持经典脚本 —— `auth.js` 刻意不加，因为 `shop.html` 的内联脚本依赖 `window.CodeMaxAuth` **在 HTML 解析到该处时就已就绪**，而 module 默认 defer。② 搬迁后原先「从 HTML 里抠 `<script>` 丢给 node 跑」的测试会**静默跑空**，必须改成取外部产物并断言其非空（TD-204 那个「只取到共享模块却仍然绿」的坑换了个形状重现）。 | `app/static/js/` 从 1 个产物变 5 个：auth 2.32 kB / er-page 50.32 kB / drawio-page 3.12 kB / mermaid-page 1.08 kB / mock-pay-page 0.57 kB。多一次 HTTP 请求，但都是同源小文件、可被浏览器缓存。 |
| TD-224 | `shop.html` 的 168 行内联脚本**按原样**抽成 `app/frontend/shop-page.js`，**暂不改写成 Vue 组件** | 直接改写成 Vue SFC（原计划）；或保留内联不动 | 改写 Vue 撞上一个**实测阻塞**：Vue 组件必须 `mount()` 到真 DOM 才会渲染，而 **CI 的两个测试 job 只 `pip install`、不装 `node_modules`**（实测 `.github/workflows/ci.yml`：只有漂移检查那个 job 有 `setup-node` + `npm ci`）。node 侧没有 DOM 实现可用 ⇒ Vue 组件在 CI 里根本没法跑起来。这与 TD-222 里「`er-layout.js` 不许 import d3」是同一个约束的另一种表现。三条出路都不便宜：① 给测试 job 加 `npm ci`（两个 job 各多几十秒）；② 让 Vue 用例在 CI 里 skip —— **等于降低标准，不可接受**；③ 改用 Playwright 端到端 —— 本沙箱下载不到浏览器，且要把两个已验证的 node 契约测试推倒重写。而按原样外置**已经拿到了全部实际收益**：可 lint、可 import、可被 node 真跑，模板内联 JS 归零。Vue 只是换一种写法，不增加任何可测性。**这页还是全站最不能出错的页面**（支付），脚本里 8 处注释各对应一个实测复现过的 bug（`buySeq` 陈旧响应守卫、`offBuy` 退订生命周期、订单过期必须停表、二维码不走 innerHTML 等），改写框架会把它们全部暴露在回归风险下。 | `shop-page.js` 187 行（含头部说明），产物 2.90 kB / gzip 1.17 kB，经典 `<script src>` 加载（产物实测 `import` / `export` 出现 0 次）。**若将来仍要上 Vue**，前置条件是先在 CI 测试 job 里装上前端依赖，并保留 Playwright 或 happy-dom 其中一条真 DOM 通路；同时注意 `shop.html` 的落地态含 `{{ product_name }}` 与价格，是 Jinja SSR 出来的 —— 把落地态搬进 Vue 会让这些内容离开首屏 HTML，与 TD-221「引流页需要 SEO」冲突，需要单独决策（Vue 岛屿只接管 pending/paid/downloaded 三态是可行的折中）。 |
| TD-225 | manual 模式的收款码用**用户本人的微信个人收款码**静态图 (`app/static/pay_qr.png`，2026-09-08 由用户从微信导出并确认使用)，默认 `SHOP_MANUAL_QR=/static/pay_qr.png` | 微信 Native/商户 APIv3（要商户号 + 证书 + 公网 https 回调，即 TD-113）；第三方「免签」聚合（需监控收款码、涉二清与《支付业务许可证》合规，实测有跑路案例，不碰） | 没有商户号就拿不到任何回调，机器不可能知道钱到没到 —— 这正是 manual 模式的设计前提：由管理员人工核对到账后调 `confirm_paid_manually` 发货。个人收款码只收不付、不含密钥，提交进仓库和把它挂在网页上暴露面相同，不构成密钥级泄露。 | ① **个人码用于经营性收款**属「个人账户收经营款」，合规上用户需自行知悉（与之前调研的 MobilePay 个人户问题同类）；② 微信不会通知到账，**仍需人工确认**，这是固有代价不是缺陷；③ 二维码若被微信轮换需重新上传；④ 图是公开的，谁都能扫码付（这正是目的）。不想用真码时在 `.env` 改回 `/static/pay_qr.svg`。 |
| TD-226 | **放弃引入 Vue/React 框架**，前端保持「原生 JS 外置文件 + Vite 纯打包」，并把预装的 `vue` / `@vitejs/plugin-vue` 从依赖与 `vite.config.mjs` 里移除 | 保留预装的 vue，将来按需启用（原 TD-224 的「暂不」路线） | 2026-09-08 用户确认：框架对当前体量（前端约 800 行、单页交互）**收益有限**。实测两个硬约束让框架更不划算：① CI 的两个测试 job 不装 `node_modules`，框架组件必须挂真 DOM 才渲染，node 侧没有 DOM 实现 ⇒ **框架代码在 CI 里测不了**（只能 skip，等于降低标准）；② 要给测试 job 补 `npm ci` 或上 Playwright，都是为一个小页面付全仓库的 CI 成本。原生 JS 已拿到全部实际收益（可 lint / import / node 真跑），且与 Jinja2 SSR + SEO 的架构（TD-221）不冲突。**若将来真的要上框架**（前端涨到数千行、出现大量共享状态），前置条件：先在 CI 测试 job 装前端依赖或引入 happy-dom/Playwright 其中一条真 DOM 通路；届时本条作废。 | `package.json` 依赖从 4 个降到 2 个（`d3` + `vite`）；`vite.config.mjs` 去掉 `plugins: [vue()]`；移除后重建产物**逐字节不变**（入口全是 .js，插件从未生效）。TD-224 保留作当时的分析记录。 |
   **不要**用 `| grep -v "\.venv"`：目标行本身含 `.venv/bin/python`，那样会把要找的行全滤掉，
   得到「已无残留」的假结论（已踩过，见 `ARCHITECTURE_GUIDE.md` 7.10）。


## 2026-09-11 合并审查第一批

| 编号 | 决策 | 放弃方案 | 理由／代价 | 何时回头改 |
|---|---|---|---|---|
| TD-227 | 支付用 `status IN (pending, closed)` 的原子条件更新，同时写入交易流水和付款时间；三条支付路径统一调用 | 先把元数据写入 ORM 再按读到的单个旧状态 CAS；对所有 CAS 失败直接回成功 | 两会话测试证明读 pending 后被关单也必须接纳付款；竞争失败者不得覆盖首笔流水。mark_paid 返回 False 仅代表已 paid/downloaded 的幂等情况，异常终态抛 IllegalTransition。本批不改下载权益规则或表结构 | 若引入退款、支付事件表或对账任务，重新定义状态集合与审计模型；真实商户联调仍未完成 |
| TD-228 | Drawio 页面先订阅共享登录态，再同步读取当前 user 快照；不改变全站 onChange 的回放语义 | 再发一次 /auth/me；让所有 onChange 注册时立即回调 | 捕获快认证／慢页面脚本的初始状态，同时避免注册即回放改变购物页补单逻辑。Node 真跑源码和构建产物的两种时序共4例 | 内容同步、账号切换和乱序响应另行修复，不能将本条当作完整 Drawio 集成验收 |


## 2026-09-12 第二批决策（优先于历史相反约定）

- 凭证版本：新增 credential_version，以整数版本撤销旧 JWT/授权码；接受必要迁移与重新登录，不依赖时间戳精度。
- 付费权益：已付款订单允许重领短时链接，不把首次领链接当永久消耗；当前单配置商品，不冒充多 SKU 目录。
- 人工支持：站内客户/管理员持久会话；外部工单、发票、退款、通知未接入。
- 部署：默认单实例、可信直接代理与回环端口绑定；动态浏览器无隔离时停用。数据库事务不等于共享限流。
- 文档：保留原目录 README，采用 Git 清单、SHA-256、AST 中间表示、实际 HTML 链接门禁；不建立第二套镜像手写说明。
- 前端：本地锁定 Mermaid；跨窗口 Drawio export 关联并隔离生命周期，VM 测试不声称外部浏览器 E2E。
- 缓存/文章：语料内容指纹、模型/提供方/凭据身份和请求快照；URL 原子 upsert，超长字段明确拒绝，不悄悄截断原文。

### TD-229：当前文档改为人工函数契约与可复算证据（2026-09-12）

重写过时模块/架构说明，保留 Git 历史而不在当前指南堆叠相反规则；Python 符号页展示签名和源码说明，缺失明确提示。放弃多处手写行号/统计及逐行转述。代价：语义仍需人工复核，JS 等仍为文件级自动覆盖；引入跨语言解析器前需明确维护成本与验证集。

### TD-230：临时 CI 日志从代码清单精确排除（2026-09-12）

未知文本必须归属的门禁捕获了构建自身 tee 日志；精确忽略 docs-build.txt 与 pytest-output.txt，保留其他未知后缀新源码检查，不关闭整个门禁。增加反例；今后新增日志需显式放入既有忽略目录或登记精确规则。

### TD-231：文章元字段不提前截断，验证失败映射 422（2026-09-12）

parse_page 不再先截短 title/author/published_at；由保存前的统一校验拒绝超长字段，入库调用纳入 ExtractError 的 HTTP 映射。放弃静默丢失元数据；代价是原来被截短后接受的页面现在需要人工调整。将来若扩字段，要同步模型/迁移和契约，不恢复无提示截断。


### TD-232：Windows 以 conda 为主，验收按环境和依赖拆分（2026-09-12）

按实际使用方式，Windows 终端启用 PYTHONUTF8，干净克隆禁用自动 CRLF 转换以匹配字节指纹（不改全局 Git 配置）；conda 管解释器，pip/npm 继续使用仓库依赖清单；保留 venv 备选，不新增平行依赖锁。自动化发布验收使用无业务 .env 的独立代码副本和可丢弃测试库，浏览器演示/实际模型在开发配置目录单独执行。PG 测试命令交互读取密码并由 SQLAlchemy 编码；模型标定在收集前加载私密 .env。代价是两个工作目录需核对同一 SHA，未提交修改不会自动同步；若以后提供独立测试配置入口，可减少目录切换。Windows/Word 兼容、跨平台浏览器、外部服务与预发布分别记录，不以 CI 或脚本检查冒充实际联调。


### TD-233：契约摘要与新手精读分层，未完成项不能隐藏（2026-09-12）

按用户复盘到函数/关键语句的要求，在现有目录 README 之上增加人工连续分段说明，与原代码并排展示；不逐句认证历史报告，也不把 AST/docstring 的存在当语义解释。notes 绑定源码 SHA 和完整连续段界，构建遇到漂移拒绝，不由 --write 自动刷新。覆盖页列全部源码、待补项与生成/空文件例外。代价：部分注释变化也需复核段界，源码/说明并排会增加页面体积；本批核心实例外的分段讲解仍需继续人工补齐。若未来改为符号锚点，仍要覆盖符号外语句和 JS/SQL/模板，不能为了降低维护成本隐去未解释代码。


### TD-234：补齐全量精读，明确语法导读来源并阻止新增漏项（2026-09-12）

承接 TD-233 的首批实例，继续补齐其余120文件，当前136个非空非生成源码有1446段连续说明；其余99生成物、4空包文件、1讲解数据单列。56文件manual，80个Python文件guided：人工功能/测试契约加AST语句事实，不把机械展开认证为业务解释。构建包括data-only都要求完整；仅底层诊断可展示待补。notes JSON不能自存自身SHA，只有精确路径note_data例外，外部README清单仍校验它；无自动批量刷新讲解摘要命令。

核对真实代码同时修正下载一次性、订单过期关单、manual生产可用、SVG CSP、source map等过时注释/界面措辞。发现文档标题提取已依赖Mistune，所以data-only“只用标准库”不再成立：改为明确依赖、CLI缺依赖非零提示并加执行级测试，而不是删真实标题解析退回有误的正则计数。代价是数据模式也需一个文档依赖，以及更大的解释JSON/生成源码页；历史报告保留，最终验证记录对应本轮实际提交，不以这些覆盖数代表真机/商户/模型签收。


### TD-235：新手单线验收、显式真实模型探测与管理元复盘（2026-09-13）

在保留原 Conda/验收深入文档的前提下，以根目录 Windows新手逐步验收.md 为首次入口，明确 VS Code CMD、Conda Python、系统 Node 与各终端的工作目录。新建演示库与可丢弃测试库隔离，任何已存在/创建失败的库不继续 full_init；自动测试副本无真实 key。增加无新依赖的显式 probe CLI，复用 Settings/LLMClient，不改应用协议或默认模型；要求提供方/模型明确配置而非悄悄用默认值，拒绝不安全基址、不跟模型列表重定向，失败正文不打印。入口脚本为直接 python scripts/... 执行先加入根路径再导入 app，保留局部 E402 理由，而不要求用户额外设置 PYTHONPATH。代价是诊断信息较克制，HTTP失败详情要去提供方控制台核对；真实支持需实际联调而非 MockTransport。

管理参考 web_agent project-manager v13，映射 CONTEXT 职责到现有 HANDOVER，按需阶段/经验，不复制全局状态与隐私收集方案。权威源在本仓库 codemax-workflow Skill，同步 manager 副本，两个配套收尾 Skill 修正失效例子；阶段关闭/已解决 P1 必须元复盘，有依据才改源/升版本/同步/留进化记录。回看条件是再次出现流程诱发返工或状态分叉，不等于自动模型训练或已安装全局技能。

本轮收尾复查再把健康检查扩至全部现行 Skill，修正 schema/新工具页中的旧配方和通用测试仅凭 docstring 的建议；本仓库工作流升 v2.1，命令门禁同步扩大，详细触发与验证记 manager/experience.md。


### TD-236：Agnes 默认、向量增强显式关闭及分环境诊断（2026-09-13）

按用户选择把 Settings、LLMClient 与 .env.example 默认改为 Agnes 2.5 Flash，保留 OpenAI Chat Completions 兼容协议并显式非流式；不自动试付费模型。无公开向量能力证明，因此增加 LLM_EMBED_ENABLED 默认 false，预热/查询真正短路，客户端拒绝空向量模型；测试显式启用合成向量而不删原断言。真实标定必须显式开启并具备 key/模型，避免聊天 key 触发未知接口。代价是升级后向量增强需主动重新启用并标定；暂不增加另一提供方的独立凭证，未来有确认的需求再拆客户端。

连接问题先区分 TCP、TLS、HTTP、鉴权、输出合同。错误分类保留 HTTP 状态与安全原因类型，不回显上游正文。独立工作流只因自身改变或手动请求触发无 key 对照；真实调用仅手动 opt-in 或明确标记提交 + GitHub Secret，避免普通 CI 外部依赖/消耗额度。源/文档相符不是实测成功，GitHub 可达也不能倒推 Arena 出口已恢复；不以关闭证书或第三方转发 key 绕过。

本轮实测 GitHub 集成可 push/read Actions，但 Secrets 元数据和 workflow_dispatch 返回权限 403。因此增加受限分支/文件路径 + 显式 [agnes-live-test] 提交标记的调度后备，不存持久开启标志，也不要求用户交出 GitHub token 或合并默认分支。


### TD-237：Agnes 能力取证与免费向量候选不越界接入（2026-09-13）

用户已确认 AGNES_API_KEY 后，授权工作流做列表、chat、Mermaid 和一次已知聊天 ID 的探索性 embeddings 请求。四项独立退出码，chat/Mermaid 决定 job 状态，不能让绿色状态覆盖 embedding 失败。日志下载通道受阻时，仅把现有 CLI 已脱敏摘要/ID 限长并转义后发布 notice，保留原退出码，不增加上游正文日志。两轮真实探测都有聊天/图前缀成功，第二轮明确向量 HTTP 500；该错误与未列出向量模型不能推出平台不存在向量能力。

保留 Agnes 聊天与默认关闭的语义增强。调研本地 BGE-M3、Qwen3-Embedding-0.6B 和带条件免费额度的 Gemini Embedding 2，不未经选择就新增运行服务、依赖或独立提供方配置。代价是 FAQ 向量增强仍未启用；收益是避免猜模型 ID、跨提供方误发 key、混用旧向量或把限额/地区条款误报为永久免费。回看条件是 Agnes 提供正式向量端点/ID证据，或用户确定候选并批准接入；随后按真实中文样本重新标定。


### TD-238：交叉审查的有界修复、持久订单边界与历史资料整合（2026-09-15）

两份独立报告以889e3eb为基线，不照搬严重性或修复指令；当前逐项结论集中review/README。修复限流二次扫描/无桶上限、日志控制字符/超长ID、NUL/If-Match、回调输入、前端晚到响应/UUID以及若干SQL子集失真；生产链接固定canonical origin。微信预支付前必须持久提交订单，提供方失败后保留pending、同号重试；替换“失败不得留单”的危险旧测试，并增加提交失败不触网的反例。代价是库里可能有待核查订单，这比真实收款却本地无单更可恢复；仍需支付尝试/事件账本和查单对账，不能宣称C-02全修。

不删除用户此前授权公开展示的收款码，不替换Agnes，不放宽未验证向量/Chromium/Draw.io时序保护，不加依赖或schema来暗中扩大业务。CLI/CI/目录说明与人工精读同步；前端audit/语法检查实际加入六job，每项有限超时。删除已整合的两份文档提示词原稿，其原文留Git；审查/验收快照保留历史证据，当前入口去掉手写易漂数字。正式金融、迁移、第三方OAuth、许可/隐私政策单独设计确认；这是回看条件而不是已完成事项。


### TD-239：拒绝覆盖的初始化、明确旧库接入及生产信任门禁（2026-09-15）

用户授权继续剩余阻断项。本批新增SchemaMigration及0009，不引入依赖。init只接预建空库，原默认账号改显式开发种子；首管理员交互新口令且不得提权既有用户。旧0008结构需显式声明/部分核对，再创建账本并应用0009停用公开凭据，保留业务数据。新迁移和校验和同事务、跨连接advisory lock，历史SQL不改；SQL强制LF。代价：旧无账本部署必须停写接入，早于0008不能盲自动升级，生产首次需bootstrap；这优于自动DROP或假装所有历史迁移可重放。主程序只读启动检查账本/演示凭据/管理员，失败也清理资源。

Settings固定根.env；维护CLI先加仓库根到sys.path再导入app，对E402作与既有诊断脚本一致的局部说明，不修改全局环境。Compose不再暗中建表，镜像须包含迁移历史。回调增加固定平台证书/公钥ID、有效期与商户/app/CNY/NATIVE绑定，下单返回有字节/总deadline限制和脱敏错误。生产SSO只允许显式受信第一方，仍发完整站点JWT，不能因此宣称开放OAuth。财务事件/不可变交付/对账退款、全部DDL属性/恢复、第三方协议及业务政策继续列为未结项。回看条件是对应专项实现和真实验收，而非本批CI绿色。

### TD-240：原子收款凭证、固定交付版本及历史合同显式核准（2026-09-16）

用户批准按资金/权益计划继续。新增0010、PaymentReceipt/Event及订单合同，不加依赖。唯一来源流水不能重复入账；settle持订单写锁并刷新，同事务记凭证/paid，精确重复不改首笔信息。人工确认必须有实际参考号、分金额和依据；unknown预支付结果不当失败。PG迁移加合同冻结和证据只追加触发器，历史收入不伪造。旧基线列集合固定，维护SQL词法只屏蔽函数体/注释，顶层事务控制仍拒绝。

本地按内容寻址复制交付文件，改配置不换已购版本；复制分块/限额/双槽，存储错误在收款前拒绝。旧单保持权益但必须显式核准原合同，不能猜当前文件；只能绑定一次。代价是存储空间和旧单人工治理，未引用快照不能随意清理。主机管理员仍可信，目录不是WORM或云备份。SQLite测试必须独立连接，单内存连接不证明事务隔离。

查单/退款/争议、运营UI、定制服务里程碑、法定账务/政策和真实商户验收仍是回看条件；不能用本批绿色替代。


## TD-241：可信应答、显式查单与同事务核查事件（2026-09-17）

选择：Native下单与普通商户单号GET共用平台应答验签/身份/时效/上限；不跟随重定向或下载陌生密钥。管理员显式POST开始核查，网络前commit开始事件，返回后锁用户重查权限，完整SUCCESS才原子提交query_success/唯一凭证/paid；精确重复保留原凭证。非成功观察不自动关单/退款/撤权。

代价：本地可信配置轮换需维护；查单保守要求整套凭据，少字段或不支持状态会保留未知；管理员仍需主动处理，历史异常筛选不是结案队列。未选择定时任务/退款API，因为没有退款订单、授权幂等与权益联动闭环，不能假装安全退款。回看：实现渠道日账、明确退款和定制服务政策、真实商户授权验收后再扩展。

## TD-242：受控管理页面与财务来源补强（2026-09-17）

公开noindex登录壳不含客户数据，管理员只读API分页；前端会话/列表/详情序号防迟到回填、textContent显示、手输单号及弹窗确认，失败先读凭证而非自动重复写。人工确认、历史绑定和查单统一验证Cookie来源与Fetch Metadata；有效Bearer API客户端保留，无来源头非浏览器Cookie兼容，不宣称完整CSRF token系统。

代价：管理员不能在任意跨源页面提交财务操作；生产canonical域名/可信代理需配准。Origin补强范围明确为三种财务写，不把它扩成全站登录架构。浏览器abort不撤销已提交操作；完整异常结案、退款/权益和历史缺证仍是后续。无新schema/依赖。实现、反例和实机边界见第四批报告。

## TD-243：只追加复核快照而不伪造财务结案（2026-09-17）

使用现有PaymentEvent的operator_review和有界v1 JSON，记录followup/close/reopen。摘要含事件count/max、订单/凭证、孤立开始时限；读投影不写任务，关闭仅覆盖当时资料，后续可见变化自动过期。count必须保留，序列号不是提交顺序。写操作用管理员/来源/限流、用户/订单锁、摘要与复核版本、绑定请求ID，重复不追加。

代价：无新schema也没有指派/到期通知/后台扫描；聚合历史有数据库开销，200候选/50结果边界允许空续页。分步读取不是全库强一致快照；流程完成不是收款/退款完成，不改财务状态。以后需要资金结案、分派和查询规模扩展时再引入经过批准的专用模型，而非把任意JSON一直堆成完整会计系统。

## TD-244：退款成功证据与支付状态分离，逐次校验订单下载授权

2026-09-17，第六批。决定：仅支持既有单文件订单一笔全额原路退款核验/人工已退款登记，不发送退款申请；RefundReceipt独立只追加，唯一归属原订单/收款，成功和同单审计一起提交，不重写原paid/downloaded。v2下载签名域隔离并绑定订单，领链/下载实时查退款，冻结文件校验后再查；拒绝旧key-only链接，未退款用户重领。复核摘要纳入退款事实与退款尝试老化，未退款旧摘要保持兼容。

代价：0011迁移与应用必须配套；不能回退不识别退款的旧应用；链接可转交，不能召回已接纳传输/副本。只支持一笔全额，不能把多个部分退款相加，不能覆盖退款纠错。金额是合同额，非净结算现金账。真实商户/窗口环境与服务退款仍待验收。回看条件：接入主动退款、部分退款、服务里程碑或云下载时，重新设计预留金额/幂等请求/通知查询/纠错及撤权策略，不沿用“点按钮即完成”。


## TD-245：退款通知先入可靠线索箱，完整金融结论仍需独立查询（2026-09-18）

官方4013071196回调缺channel/currency/appid，且须5秒应答。决定不凭SUCCESS虚构ORIGINAL，不在ACK路径等待外网，也不先ACK后内存任务。现有PaymentEvent以固定refund_notify_signal保存已验签/解密/原收款绑定的有限观察；商户+通知ID去重、事实指纹冲突拒绝、提交后204；4秒应用预算，失败原ID重试。保留RefundReceipt真实成功/活跃管理员归属合同，不造系统管理员或削弱原路条件。

代价：仅是inbox，不是退款请求台账/自动后台消费者，仍需管理员主动看待办并独立查询。部分通知可提醒，但不支持部分记账/累计/撤权；相同退款不同通知ID都是新线索，排序按本地ID非渠道终态。只保存最小摘要与字段指纹，不保存敏感账户/签名包，因此不能离线复验原RSA证据。无需新依赖/迁移；既有0011与只追加规则仍必需。接收地址不会自动配置到外部办理流程，也不能保证历史补发或生产SLA。回看条件：实现持久退款请求/授权幂等及可靠消费者后，再设计自动交叉核验、重试/告警、部分与纠错；不直接放宽当前凭证规则。


## TD-246：退款准备单独持久化，稳定号不等于发送授权（2026-09-18）

增加RefundRequest与0012，限定一原单/原收款一笔全额微信准备，客户端request_id和服务器商户退款号分开。原商户/app/金额/发起人/依据冻结；同请求同事实重放保留首笔，跨归属/改内容/同单换号拒绝。用户→订单锁，准备+事件同提交；已有任何退款通知/查询或成功凭证则先核查原号，不另造。成功退款凭证仍独立，通知不能升级准备；复核直接纳入准备行而非只靠事件。

取舍：本批解决本地保存未知，不假装解决渠道发送未知；没有发送器/自动审批/完整请求快照/取消释放/部分退款。旧准备不是未来自动发送授权；内部160字符依据不能当微信80字节的用户可见reason。代价是一单不可删改重建，错误/停止跟进先保留复核说明，正式纠错需单独流程。原本写死下一迁移编号的测试改为当前版本+1，维护清单最低版本仍明确到0012，防漏打包。回看条件是接入显式发送授权、完整商户请求冻结、可靠尝试/恢复及对账后，再拓展完整退款状态机。


## TD-247：显式授权/发送与金融成功保持分层

采用0013不可变完整请求与独立授权；WX_REFUND_SEND_ENABLED默认关闭，逐次管理员确认才可发送。started先提交，固定原号/字节，精确attempt重放只读；新显式未知重试至少60秒且无受理/独立观察。申请应答不直接记成功凭证，现有独立查询仍为下载撤权依据。reason单独按80 UTF-8字节，回调固定于授权时SITE_BASE_URL。代价是通知/查询后无重发覆盖口、错误授权无就地纠错、无自动worker；后续需受控取消/纠错和可靠查询消费者，不能自动消费历史授权。


## TD-248：停止后续本站发送不等于渠道取消

采用0014只追加停止行，一授权一条；与新发送开始共享订单锁，精确旧attempt仍可读回，在途结果及独立查询继续保留。停止记录不修改原授权正文、不释放号码、不恢复下载；无恢复发送/重新授权入口。纠错先留不可覆盖原因和后续复核说明，完整纠错/可靠核验队列另做。代价是错误停止也不能直接改库重开；后续须设计可审计的新授权协议，不能暗中绕过历史停止。


## TD-249：退款通知可靠核验不冒用管理员结算权限

通知+派工单原子提交，独立默认关闭worker补旧收件箱并做已验签原号GET；CAS/数据库时钟/90秒租约及token防迟到写回，最多8次，失败退避转人工。不新增第三方依赖或FastAPI临时任务，不自动消费发送授权。现有成功凭证要求管理员，故SUCCESS仅verified待管理员独立查询；代价是仍有人手交接，不能声称全自动撤权。回看条件：明确系统结算授权/审计归属、人工接管及多商户/部分退款政策后另行设计；不是把管理员ID硬编码给worker。

## TD-250：核验接管复用不可变事件，不清零预算

新增本地hold/retry命令，不增加schema。用PaymentEvent保存全局请求key和首次人/依据，与任务同事务；snapshot含控制序号抵抗ABA，重放先于当前快照验证。hold清租约只阻止旧结果写回，不取消GET；retry仅attention且未满8次，不绕过全额/原凭证条件，已verified/耗尽仍人工查询。代价是重排不能延长预算，订单其他任务的控制也会保守使快照失效；若需额外预算/自动结算，必须另设明确授权和不可覆盖历史，不能把管理员身份交给worker。

## TD-251：根据已观测全量耗时提高CI测试预算

PG运行35396917535由20分钟上限取消，SQLite用19分48秒；本地真PG全量17分26秒，CI包含容器/安装及较慢CPU开销。将两个全量job上限设35分钟，其他四项仍20分钟；完整pytest、失败退出与扫描范围不变。不因取消重试碰运气，也不缩减测试。代价是挂起作业最长占用增加；后续监控耗时并优先优化隔离夹具/测量热点，不能无限调大。


## TD-252：系统成功登记独立授权，不冒用管理员

2026-09-19，第十三批。默认false的WX_REFUND_AUTO_RECORD_ENABLED与VERIFY/SEND分离：只允许新可信全额ORIGINAL/CNY成功GET落本站凭证，不发资金，不复活旧终态。0016用空actor_id+固定系统名+唯一verification_event_id关联不可变开始事件，既有人类记录原样保留。共享无commit financial staging，finalizer按订单→job锁重绑查询上下文，savepoint处理预期冲突，末次再读数据库时钟；凭证/终态审计/队列单事务。SQL再核活租约/同单系统事件，但不冒充能验外部RSA。

代价：部署运维配置授权而非页面审批，需迁移和Web/独立worker一致重启；默认仍有人手交接。开关关闭不能召回在途，hold仅一任务；旧终态不自动登记，耗尽仍管理员查询。实际权限依赖可信应用与数据库角色，不是防所有者篡改账本。保留全额单笔、8次及原号规则，不支持部分/纠错/重授权/渠道取消。回看条件：多商户、服务退款、额度审批或不可否认审计需求出现时设计正式政策，而非借用户身份或修改旧凭证。


## TD-253：纠错只新增发送前授权，不重写未知请求

2026-09-19。0017把单授权扩为唯一根+不可覆盖单后继链；必须原版已停止、当前叶子、同原准备/原号/全额、全单无发送或退款观察/成功。新reason和正式callback重新冻结，用户→订单锁与新started共用；先提交started即视未知，即使实际尚未触网，也不能重授权。新授权/审计单提交，原key先核首次内容恢复，不随当前配置或新版本漂移；旧初始授权/停止按显式ID恢复，当前叶子另行投影。

代价是发送后错误仍只能原号核验/人工处理，不能改额/换号/纠正成功凭证；“本站从未开始”不证明商户平台没人办理。新版本可重新显式申请但旧版永远停止，默认SEND不变，无自动发送。历史展示有界50，旧正文完整性依赖数据库备份/只追加保护，非WORM。恢复时须全历史与新应用一致，不得混跑旧单授权假设代码。回看条件：引入发送后安全修正、部分退款、额外金额授权或外部对账时另设协议，不靠编辑旧行放宽本边界。

## TD-254：本地进度与队列告警分离，不借监督增加财务权限

2026-09-19。不为heartbeat增加数据库迁移或公开API，选择受信本机私有目录、OS单写锁与原子JSON，stdlib独立checker避免坏.env/DB阻断检查本身。worker先校验完整0017，启动15秒/每轮60秒协作预算，仅完整周期更新running；SIGTERM取消保留租约，--once结束不是在线。数据只聚合计数/固定码，不打印商户/原始异常，不改变GET、AUTO/SEND授权和8次预算。

代价：默认120秒过期有观测延迟，磁盘/事件循环死锁仍需外部监督；本机文件不是共享集群健康或持久财务审计。Compose仅opt-in、非零最多5次重启，unhealthy/人工hold不自动重启，SEND/AUTO模板强制关；未提供外部送达/集群监控/生产恢复证明。另设--alerts给本地监测，终态告警可持续，不无限发重复日志。回看条件：确认监控接收方、多机部署、负载/时延需求或需要不可丢告警历史时另设计，不把状态JSON升级成资金凭证。

## TD-255：关单与本地过期、收款及日账对账分离

2026-09-19。沿用只追加PaymentEvent，不加schema/依赖；WX_ORDER_CLOSE_ENABLED默认false，只开放管理员独立关原Native订单。必须本站closed、无流水/凭证、原商户/app相符，最新查单为5分钟内可信NOTPAY；先用户再订单锁，完整确认/查询attempt/首次人/evidence随started先提交，之后无锁POST固定API和mchid正文。仅验签204空体为acknowledged，其他错误均unknown；same-key绑定首次人/内容仅读，即使后续状态/配置变化也不重发。

未知新尝试需晚于原start的新NOTPAY、原start至少60秒且仍有授权，原单号不变；任何acknowledged挡后续新关单。NOTPAY/204均不证明不存在付款竞态，finish只记观察，迟到可信SUCCESS仍settle，不改或撤销既有权益。代价：先本地关闭且先查单，多一次人工确认；没有自动过期批处理、渠道日账下载/对账或服务取消。官方接口依据见阶段报告。回看条件：引入批量自动关单、日账对账或更细取消/付款时序时另设计，不把本地closed或HTTP错误当财务结案。

## TD-256：只读日账快照不升级为结算或退款授权

2026-09-19。默认关闭WX_BILL_READ_ENABLED，显式CLI确认实际数据库目标/商户/日期；无新依赖/schema/Web权限。选普通商户现代ALL格式，用已验签申请应答的SHA1约束无签名文件；只签两个官方主站下载路径、拒重定向/压缩/未知格式，有界4MiB/5000行。交易与汇总用整数分，原合同用订单金额而非扣券后的应结金额；日期明确UTC+8。网络外的PG只读重复读快照双向检查付款及原号跨日冲突，退款保持发起快照，不反推完成或撤权。

代价：旧格式、服务商/多商户、大批量、网页操作、资金账/费率核算/部分退款、正式会计结案均未覆盖；不自动处理差异。报告为私有敏感JSON，原子独占发布、不存原始账单或token；只有摘要可定位，不是可独立重新验签的证据包/WORM。Windows ACL与本地硬链接需实机签收，原始账单不留存会限制后续离线复验。回看条件：确认保留/归档政策、商户样例格式/时区、大批量需求或网页审批后另设计，不放宽签名/完整性门禁临时跑通。

## TD-257：交接证据分层与唯一当前队列

2026-09-19。全仓审计基线7f2e125；本轮不改金融逻辑、依赖或0017历史SQL。HANDOVER只负责恢复和四层发布证据，ROADMAP独占当前任务，review保存可复核发现/历史证据。删除四份已失效或被政策/新审计承接的文档，Git保留原内容；注册/链接/精读同步，而不是继续追加矛盾横幅。

六个独立有界诊断显式调用，PASS代表已观察到待修行为，不作为安全回归纳入默认发现；修复时须变成保护性断言。未取得的新同伴附件与真实商户/浏览器/生产验收继续明示等待。代价是不能声称“全仓安全认证”或“一切上线阻断已清”，也不把单实例/全额退款等可接受产品范围强行扩张。回看条件：独立交叉复核/新附件/实际部署或产品范围改变时更新唯一队列，而非复制新管理台账。

## TD-258：支付架构参考先核准入，网页权益不改为离线license

2026-09-19。对照19f236e上传原文与当前FastAPI/Native/凭证下载链路，保留服务端密钥、冻结订单、只追加收款/退款证据和逐次授权；不因桌面参考方案改用Worker、Ed25519离线许可或删除本站账号体系。主体/渠道产品准入作为L-00先行门槛，网页收银台/轮询为A-06，外部登录消费者与桌面许可分别P-05/P-06待产品确认。证据、官方规则差异和五问答复在全仓报告第9节。

代价：暂不提供手机H5/JSAPI、其他渠道、断网桌面功能或设备数限制；通过代码回归也不代表取得经营资格。若确认丹麦/境内主体的可用渠道或桌面产品需求，另行设计商户/币种/退款/身份迁移与兼容性，不靠修改WX_*或ALGORITHM常量完成切换。原上传TXT保留原字节，讲解明确它是待评估材料而非本站实现事实。

## TD-259：接手复核先独立复现再排修复批次，新缺陷进唯一队列而不进报告状态页

2026-09-20。接手人对基线 ea11619 重跑六项有界诊断（SQLite 全部复现）并逐文件通读应用/脚本/前端/模板/文档后，只新增一份 `review/FULL_REPOSITORY_REVIEW_2026-09-20.md` 作证据，不在报告里维护状态；新发现 F-09（`Limiter` 满桶后拒绝所有新键、`prune()` 无调用方，合成时钟下约 820 地址 × 20 scope、≈5 请求/秒即可长期封锁新客户端）直接登记为 ROADMAP A-07，下载出口限流为 A-08，UI 对比度/浮层可访问性为 A-09，文字与死代码清理为 O-09。放弃了"在报告里同时修几处小问题"：文档提交与运行逻辑改动分开，才能让每个修复批次有自己的保护性回归和精确 SHA 的 CI 证据。

代价：本次提交只改文档/注册/精读指纹，六项诊断与 F-09 仍是待修行为；UI 结论只来自静态阅读和 WCAG 亮度计算，真实浏览器/读屏仍归 L-04。回看条件：任一 A-01～A-09 完成后把对应诊断替换为默认套件保护性断言并在 ROADMAP 勾销，不再追加新的"全局当前状态"文档。

## TD-260：解析前请求体预算与 LLM 响应预算都是"读到就停"，不做可配置项

2026-09-20。修复 F-01/F-02/F-05（ROADMAP A-01/A-02/A-05）。请求体：新增纯 ASGI `RequestBodyBudgetMiddleware`，紧贴路由注册（外层仍是安全头和日志）；`body_limit_for` 按路径给上限——默认 1 MiB，`/diagrams` 2 MiB（`DiagramIn` 允许 50 万字符，中文 UTF-8 可到 ~1.5 MB，1 MiB 会让合法保存失败），`/shop/pay/notify`、`/shop/refunds/notify` 返回 None 由路由已有的 64 KiB 流式读取和渠道规定的 FAIL 报文负责，不改签名原文处理。Content-Length 超限不读一字节即 413 + `connection: close`；分块或谎报长度包装 `receive` 按实际字节计，越界抛 `BodyTooLarge`——它继承 HTTPException，因为 FastAPI 读体时会把其他异常统一包成 400 "error parsing the body"，只有 HTTPException 原样上抛才能得到 413。同时注册 `RequestValidationError` 处理器去掉 `input`/`url`：422 不再把出错字段原值整个回显，前端只读 `msg`。数值由用户确认，写成常量不做 Settings：改小会破坏 drawio 保存，改大没有业务输入需要。

LLM：`LLMClient._call` 用 `client.stream` 打开响应，`_json_within_budget` 按 Content-Length 或累计解压字节在 `RESPONSE_LIMIT`（1 MiB）处停读并归 `oversize`；非 200 只取状态码不读正文；整次调用套 `asyncio.timeout(self.timeout)`，超时归 `network`；`json.loads` 的 RecursionError（C 层递归保护，非真实栈溢出）与编码/语法错误统一归 `response` 类 LLMError，`tools.py`/`support.py`/`faq.py` 只捕获 LLMError 的调用链才接得住。embedding index 改为 `type is int`，拒绝 bool/float/字符串。四项诊断从 `tests/audit_handoff_probes.py` 删除，改为默认套件 `tests/test_request_body_budget.py`、`tests/test_llm_response_bounds.py` 的保护性回归；探针文件只剩同单双预支付与跨站登录两项。

代价：应用层预算不替代反向代理 `client_max_body_size`，也不限制响应大小或并发请求数（LLM 并发/额度策略仍待 A-02 后半）；`connection: close` 让超限连接不复用，属有意为之；慢读总时限只用 MockTransport + 合成滴流验证，真实网络表现仍待实测。回看条件：出现需要 >2 MiB 的合法上传（如文件导入）、多进程部署需要共享额度或 LLM 供应商引入流式协议时再重新设计，不靠放宽常量应付。

## TD-261：限流器满桶只回收已到期桶、IPv6 按 /64 归并，不引入 Redis 也不放宽容量

2026-09-20。修复复核报告 F-09（ROADMAP A-07）与 F-06 前半（A-08）。原实现满桶（16384 键）时对任何新身份直接 `return False, 1`，而回收只靠每次请求轮转检查 32 个键并且没有任何 `prune()` 调用方；合成时钟复现：约 820 个地址 × 20 个 scope 填满键表后，只要每个键在窗口内至少刷新一次（≈ 300 请求/秒，不是 TD-259 里估算的 5 请求/秒——那个数字只让 302 个键保持活跃，本次实测已订正），所有新客户端的登录/注册/工具/下载入口就持续 429。改法：`_expires` 换成按"最后一次放行"排序的 `OrderedDict`（所有 scope 共用同一个 `RATE_LIMIT_WINDOW`，因此队头永远最早到期），`admit` 每次最多从队头弹 32 个已到期桶，遇到第一个活跃桶就停——摊销 O(1)、不遍历 `items()`，原 `NoScan` 反例继续成立；满且全活跃仍拒绝新键（不驱逐活跃桶，否则换地址就能洗掉老配额），但结论区分 `reason=capacity` 与 `quota`：Retry-After 指向最早到期桶的剩余秒数，429 文案改为"访问来源过多"，并以每分钟一条的频率记 `codemax.ratelimit` warning，让运维从响应和日志就能看出是被刷还是自己超额。身份层面 `_identity` 把 IPv6 归并到 /64、IPv4 映射地址还原成 IPv4（XFF 与直连同一规则），一台 IPv6 主机不再是无限身份。`GET /shop/dl` 挂上与 `POST /shop/download` 相同的 `download` 桶（`RATE_LIMIT_TOOLS`），出口的全量哈希不再是免登录的 CPU 放大入口。

代价：仍是单进程内存状态（TD-141），多副本各算各的；/64 归并意味着同一 /64 内的多名真实用户共享一份配额（家庭/校园 NAT64 场景可能误伤，出现投诉再评估 `IPV6_PREFIX` 是否需要配置化）；满桶时新客户端最多仍要等最早桶到期（≤ 60 秒），键容量本身不放大，因为 16384 键约 14 MiB 常驻已是单进程可接受上限；下载出口每 60 秒 30 次的配额对合法重试足够，但把 `RATE_LIMIT_TOOLS` 调小会同时收紧下载。回看条件：多实例部署（需要共享限流）、出现合法 IPv6 多用户同 /64 的实际误伤，或对满桶需要按 scope 分桶时再改，不靠调大 `max_keys` 应付。

## TD-262：同用户预支付进程内单飞 + 下单独立限流桶；表单登录复用财务来源判定

2026-09-20。修复复核 F-03（ROADMAP A-03）与 F-04（A-04）。**下单**：数据库部分唯一索引只保证一位用户同时只有一张 pending 单，保证不了两个并发请求不会各拿这张单去调一次 `native_prepay`（合成屏障下实测 2 次在途调用、1 张单）。改法：`create_order` 的 wechat 分支在本地订单 `commit()`（同时释放 `lock_user`）之后进入按 user_id 的 `_prepay_flight`——进程内 `asyncio.Lock` + 引用计数，最后一个使用者离开就删字典项，等待被取消也归还计数；锁内用 `populate_existing` 重读订单：已非 pending 409，已有 `code_url` 直接 `reused=true` 返回，否则才写 `prepay_started` 并跨网络。等待者因此要么复用第一个请求的结果，要么在它结果未知（502）后沿用**同一单号**重试，不造孤儿单。入口另挂 `order` 桶，额度沿用 `RATE_LIMIT_AUTH`（10 次/60 秒；用户确认：每次下单都可能触发一次对外预支付，比工具桶的 30 次更合适，前端"立即购买"本身也有去重）。刻意不做持久租约/数据库锁跨网络：单进程部署下进程内锁已足够，多实例各自单飞的边界与限流 TD-141 相同。

**登录**：`require_finance_origin` 的请求头判定抽成 `check_browser_origin(request, metadata_error, origin_error)`，财务版保留 Bearer 豁免，新增无豁免的 `require_login_origin` 挂在 `POST /auth/login`（限流之后、读表单之前）。浏览器带 `Sec-Fetch-Site` 非 same-origin 或外站/畸形 `Origin` 的表单提交 403 且不下发 Cookie；不带这两个头的 Swagger/脚本/curl 行为不变。它防的是登录 CSRF（把受害者登进攻击者账号、借 Lax Cookie 观察其后续操作——Lax 挡不住，因为攻击者提交时不需要受害者的 Cookie），不是同步令牌型 CSRF 系统。六项交接诊断至此全部换成默认套件回归，`tests/audit_handoff_probes.py` 删除；新回归在旧代码上全部失败（已实测），不是同义反复。

代价：单飞只在单进程内成立，多实例仍可能各发一次预支付（渠道侧同 out_trade_no 幂等兜底，本决策不声称重复扣款已消除）；等待者最多等一次预支付的 20 秒总预算；`order` 桶与登录/注册共用同一配置项，调小 `RATE_LIMIT_AUTH` 会同时收紧下单；登录来源检查依赖浏览器发送 Fetch Metadata/Origin，老旧浏览器的跨站表单不带这两个头时与无头客户端无法区分（这是 Origin 型防御的固有边界）；真实浏览器双来源 PoC 仍归 L-04。回看条件：多实例部署（改持久租约或共享锁）、出现合法的跨域第一方登录客户端（需白名单而不是关掉检查）、或渠道侧幂等语义变化时重新评估。

## TD-263：配色按 WCAG AA 换色、浮层补 dialog 语义与键盘行为，不引入 UI 框架或设计令牌系统

2026-09-20。ROADMAP A-09（复核报告第 1 节第 4 点）。全站文字/按钮色只有三处低于 4.5:1：链接与主按钮 `#3b82f6`（3.68:1）、CTA 与价格 `#16a34a`（3.30:1）、浮层提示 `#94a3b8`（2.56:1），分别换成同色相更深一档的 `#2563eb`（5.17:1）、`#15803d`（5.02:1）、`#64748b`（4.76:1，与站内已有的 muted 灰一致），白字反过来算是同一个比值。同时补齐三件此前缺失的可访问性基础：`button:disabled` 灰化加 `cursor: not-allowed`（提交中的按钮被脚本禁用，之前与可点状态无区别）；`:focus-visible` 2px 轮廓（只在键盘导航时出现）；登录浮层容器加 `role="dialog" aria-modal="true" aria-labelledby="auth-title"`，`auth.js` 在打开时记住 `document.activeElement`、Esc 关闭、关闭后只在焦点仍留在浮层内时把焦点还给触发元素（用户已点到别处就不抢回）。`support.css` 的两栏布局从 `:has(aside:not([hidden]))` 改为脚本按管理员角色切 `.with-inbox` class，去掉对旧内核不支持特性的依赖。`app/static/js/auth.js`、`support-page.js` 重新构建并提交，其余分块无变化。放弃了引入 CSS 变量/设计令牌层或前端 UI 框架：只有三个色值需要改，抽象层的维护成本高于收益；也放弃了焦点陷阱（Tab 循环锁在浮层内），浮层只有两个输入框和三个按钮，遮罩点击/Esc 都能退出，陷阱实现的复杂度和误伤风险不值得。

代价：`tests/test_ui_accessibility.py` 是静态检查加 Node 真跑源码与产物，不是真实浏览器渲染、不测读屏播报，视觉签收仍归 L-04；对比度只按白底计算，`#eff6ff` 管理员消息底上的蓝边线是装饰元素不受 4.5:1 约束；er 图 D3 描边仍用 `#3b82f6`/`#94a3b8`（图形而非文字，未改）。回看条件：新增第四种主题色、出现深色模式需求，或浮层增加到需要 Tab 循环时再考虑令牌层与焦点陷阱。

## TD-264：LLM 并发闸门每进程 4 个在途、满则立即 503 不排队；不做站内日额度

2026-09-20。ROADMAP A-02 后半，取值经用户确认。所有对模型的请求（`/tools/mermaid`、客服闲聊/RAG/意图路由、管理员抓取入库、向量化）都用同一个 `LLM_API_KEY`，单客户端的节奏已有 `RATE_LIMIT_LLM`（10 次/60 秒/身份）限制，但多个来源同时到达时全站对同一 key 的并发烧钱速度没有封顶，供应商变慢时挂起的连接也会无上限积压。改法：`app/tools/llm.py` 新增 `InFlightGate`（普通计数，不是 `asyncio.Semaphore`——Semaphore 的语义是等待，这里要的恰恰是不等待），`LLMClient._call` 进入前 `acquire`、`finally` 里 `release`，chat 与 embeddings 共用同一把闸门；满则抛 `LLMError(category="busy")`，请求根本不发往提供方。路由层把 busy 映射成 503 + `Retry-After: 5`（`/tools/mermaid`、`/admin/articles/ingest`），与上游故障的 502 区分；客服链路沿用"任何 LLMError 都转人工"，原因里带"繁忙"可追溯；mermaid 页前端对 503 按 `Retry-After`（钳在 1–30 秒）自动重试一次，再繁忙就原样显示服务端文案，502 不重试。

为什么是 4 且不排队：站点单实例、LLM 是引流功能而非收费核心，4 个并发覆盖"几秒内四人同时点"的正常峰值；排队会让第 5 个人在页面上干等几十秒才知道结果，服务器还要替他挂着连接，直接告知稍后再试并由前端重试体验更顺。为什么不做站内日额度：默认无 key 时 LLM 本就关闭，付费封顶由供应商控制台的消费上限/告警负责，站内再做一层是重复且更不可靠（进程重启即归零）。

代价：进程内状态，多实例各有各的 4（与 TD-141 同一前提）；闸门只限在途数不限速率，每次调用越慢闸门越容易满——供应商大面积变慢时 mermaid 会频繁 503，这是有意为之的保护而不是故障；`MAX_IN_FLIGHT` 是常量不是配置项，改数字需要改代码并跑 `tests/test_llm_concurrency.py`。回看条件：多实例部署（需要共享计数或按实例分摊）、LLM 成为付费核心功能需要排队与优先级、或供应商提供流式协议时重新设计。

## TD-265：三条建库路径在真实 PostgreSQL 目录上逐项等价，漂移修在源头而不是加例外

2026-09-20。ROADMAP O-03（上轮审计 F-08）。原 `tests/test_schema_sync.py` 用项目自己的 DDL 解析器读 full_init.sql 文本，只比表名/列名和时区类型，`database init/README.md` 也明写"不是完整 catalog 等价证明"。新增 `tests/test_schema_equivalence.py`：在 `test_db_admin` 的一次性 pgserver 上建三个库——full_init 直建、fresh 拆回 0008 形状后 `adopt_legacy` 走 0009–0017、再拆回 0002 之前形状把 0002–0008 历史 SQL 原样执行后 adopt——读 information_schema/pg_catalog 得到列（类型、udt、可空、默认、长度、精度、identity）、约束（`pg_get_constraintdef`）、索引、触发器、函数（`pg_get_functiondef`）、序列、表清单，递归比较，任何差异逐条点名。第一次运行就抓到两处：`sys_user.role` 在 full_init 里是 `SMALLINT DEFAULT 0`，而 migrate_0005 加的是 `NOT NULL DEFAULT 0`，新库比老库宽松；`adopt_legacy` 用 SQLAlchemy 从 ORM 编译 `schema_migration` 建表，默认值拼成 `now()` 而 full_init 是 `CURRENT_TIMESTAMP`，语义相同但目录不同。两处都修在源头：full_init 补 NOT NULL（应用从不写 NULL role，0005 之后的老库本来就是 NOT NULL，不改数据）；`adopt_legacy` 改用 `ledger_ddl()` 从 full_init.sql 正则取出同一条语句执行，去掉对 ORM 模型与 PG 方言编译的依赖。放弃了"把差异写进允许清单"：例外清单只会越积越多，等价测试就失去意义。

代价：0001（裸 TIMESTAMP → TIMESTAMPTZ）无法完整重放——它的前置形状在现行文件里已不存在，重建等于自己再写一份旧 DDL 去验证自己，只验它在现行库上是空操作（幂等分支）；快照只看 public schema，不比权限/所有者/表空间/注释；等价证明的是仓库内三条路径互相一致，不是任何一台生产库的实际状态，也不是数据迁移正确性（那由 test_db_admin/test_payment_ledger 的保留数据用例负责）；测试依赖 pgserver，无 PG 二进制的环境按既有约定 skip。回看条件：新增迁移时这组用例会自动覆盖（adopt 路径总是走到最新版本）；若将来引入不可从现行文件重建前置形状的迁移，需要像 0001 一样单独说明验证边界，而不是删掉比较项。

## TD-266：下载出口按 inode 状态跳过重复全量哈希，权益判断一次都不缓存

2026-09-22。ROADMAP O-01（审计 F-06 后半）。先测量再改：沙箱 2 核上 `file_digest` 吞吐约 900 MiB/s，512 MiB 商品每次下载 0.53 s CPU、8 个并发把两个核吃满 2.2 s；`GET /shop/dl` 挂了 `download` 桶后（TD-261）一个合法链接持有者一分钟仍可发 30 次请求，对 256 MiB 商品就是 8 s 线程池 CPU——浏览器断点续传和下载器的多段 Range 请求正是这种模式，FileResponse 对 Range 的 206/416/multipart 支持已实测正常。改法：`verify_snapshot` 保留原有的 key 前缀与 size 检查，首次（以及 inode 状态变化后）仍全量 sha256；通过后把哈希**开始前**取的 `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` 记进进程内字典，之后同一状态只 `stat()`。ctime 由内核在任何写入或元数据变更时更新，用户态改不回去（`utime` 只能改 mtime/atime），所以字节被换过、或被换成另一个 inode，下次都会重新哈希；实测 30 次校验从 7.98 s 降到 0.27 s。两条明确的边界：文件系统时间戳有粒度（本机 ext4 4 ms，某些文件系统 1–2 s），同一刻度内两次写入 ctime 相同，因此 ctime 距今不足 3 秒的通过结果不进缓存；Windows 的 `st_ctime` 是创建时间不是变更时间，非 POSIX 平台不启用缓存，行为与改前完全一致。缓存只覆盖"磁盘文件与订单冻结的摘要/大小一致"这一件事，签名、有效期、订单状态、两次退款复查仍每次执行——出口用例直接写入全额退款凭证后，缓存命中的请求照样 403 且没有重哈希。放弃了"按快照目录名 = 摘要就免哈希"（目录名是发布时写的，管理员误操作覆盖文件后目录名不会变）和"缓存到磁盘旁路文件"（又多一个可被改写的信任点）。

代价：进程内状态，重启即空、多实例各算各的；缓存上限 256 键（一个商品一个键），满了整体清空；宽限期让刚发布 3 秒内的快照仍每次全量哈希；若某文件系统的 ctime 不可信（例如某些 FUSE/网络盘），运维需在该环境把 `_VERIFY_CACHE_ENABLED` 视为不适用——当前没有配置项，改为常量是有意的（先有这种部署再决定要不要暴露开关）。回看条件：出现 >512 MiB 商品、云端直连下载接管出口、或多实例部署需要共享校验结果时重新评估。

## TD-267：第三方 action 钉 commit SHA、PR 写权限只给评论 job；镜像 digest 与 pip 哈希锁明确列为未做

2026-09-22。ROADMAP O-08 第一部分。两份工作流里 16 处 `uses: actions/checkout@v7 / setup-python@v7 / setup-node@v7` 全部改为完整 commit SHA（`3d3c42e5…` = checkout v7.0.1、`5fda3b95…` = setup-python v7.0.0、`82076278…` = setup-node v7.0.0），SHA 用 `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` 逐个核对，注释只是给人看的。理由是可变标签会随上游 force-push 移动（tj-actions/changed-files 事件就是标签被改指向恶意提交），而 SHA 不会。`ci.yml` 顶层 `permissions` 收成只有 `contents: read`，`pull-requests: write` 下沉到真正执行 `gh pr comment` 的两个 test job：lint/audit/docs/frontend 运行的第三方工具即使被投毒也拿不到能发评论、改 PR 的令牌。新增 `tests/test_ci_supply_chain.py` 把这些规则和两个既有的锁（requirements `==` 精确版本、package-lock 全部 integrity + `npm ci`）钉成默认套件回归；PyYAML 是 `uvicorn[standard]` 的既有传递依赖，没有新增依赖。

明确未做及原因：① `postgres:16` 服务镜像未钉 digest——沙箱访问不到 Docker Hub（auth.docker.io / hub.docker.com 连接失败），拿不到可核对的 digest，钉一个未经核对的值比不钉更差，而且该容器只在 CI 内提供一次性测试库；② pip `--require-hashes`——需要为 26 个直接依赖及全部传递依赖生成并维护哈希锁文件，会改变 `pip install -r requirements.txt` 这条部署与 CI 共用的安装流程，属于"先有兼容方案再做"；③ O-08 其余项（类型检查、缓存/压缩、索引/连接池）仍按"先有基准"原则待测量，不盲加。

代价：升级 action 从改一个标签变成改 SHA + 注释两处，且要用 `gh api` 核对；Dependabot/Renovate 若日后启用需配置为按 SHA 更新。回看条件：能可靠取得镜像 digest（例如有出口访问 Docker Hub 的环境）时补钉；引入 pip-tools/uv 一类锁文件工具时一并做哈希锁。
