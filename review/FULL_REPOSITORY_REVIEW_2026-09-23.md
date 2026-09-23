# 第二次接手独立复核：代码、文档同步、真实浏览器 UI 与优化清单

**日期：2026-09-23 · 复核基线：`3e408a17b057b176a2fb5da5959f29b249d35f62`（TD-270，远端 `arena/01a0bf7a-codemax-platform` tip，六项 CI success：run 35786058282） · 本会话固定分支：`arena/01a0cdc6-codemax-platform` · 环境：Linux 沙箱 2 核、Python 3.11.2、Node 22.22.3、Headless Chromium 153（npm `@sparticuz/chromium`，装在仓库外 /tmp）**

本报告是本会话接手人对 [2026-09-19 全仓审计](FULL_REPOSITORY_HANDOFF_2026-09-19.md)、[2026-09-20 接手复核](FULL_REPOSITORY_REVIEW_2026-09-20.md) 及其后 TD-260～TD-270 十一批修复的**独立复核**。它是可反驳的证据，不是安全认证、法律意见或收款上线许可。当前待办只在 [ROADMAP](../ROADMAP.md)；恢复/发布核验只在 [HANDOVER](../HANDOVER.md)。**本报告不修改任何运行逻辑、依赖、schema 或历史 SQL**；第 8 节的修复提案经用户确认后分批实施。

与前两份报告的最大区别：前两轮都写明"沙箱无浏览器，UI 为静态审阅"。本轮找到了能在沙箱运行的 Chromium，**用真实浏览器渲染了全部 7 个页面（桌面 1366×900 与手机 390×844）、跑了 axe-core 4 自动可访问性扫描，并在浏览器里走完注册→登录→下单→模拟支付→下载→留言→管理员工作台全流程**。第 6 节的 UI 结论都来自实测截图与计算样式，不是读 CSS 推断。

## 1. 结论摘要

1. **前两轮的 F-01～F-09 与 A-01～A-09 修复全部成立**：逐项读了修复代码与回归测试，未发现回退或"测试同义反复"。本地全量 SQLite **1822 passed / 7 skipped，退出 0**（两次独立运行：1272.83 s、1276.30 s），Ruff、文档契约（303 文件/13 owners/0 错误）、完整文档站构建、Vite 零产物漂移均通过。7 个 skip 逐条核对原因（5 项需真 PG、1 项需真向量 key、1 项 PG 并发快照），无无理由 skip。
2. **新发现 1 项回归（P2）——TD-270 的"会话过期"处理会静默丢掉用户正在编辑的内容**：Drawio 页保存时撞上 401，`sessionExpired` 触发登出通知，`syncAuth(null)` 把编辑器重置为空白并重建 iframe；客服页 401 会清空未发送的留言草稿。用同一 Node 桩对比 TD-270 前后源码：**改前**编辑器保留用户图并显示"未保存"，**改后**重新加载的是空白图，重新登录后仍是空白（第 3 节 N-01，有可复算脚本）。这是 TD-270 本想改善的体验的反面，且 `tests/test_ui_accessibility.py` 只断言"浮层弹出"，没有覆盖"内容保留"。
3. **新发现 1 项可达的资源滥用（P2）——流程图存储没有"每 IP / 全站"上限**：注册只受 `RATE_LIMIT_AUTH`（10 次/60 秒/IP）限制，每个账号 20 MB 字节配额，`POST /diagrams` 没有限流依赖。一次性 SQLite 实测：**单个 IP 15 秒内注册 10 个账号、写入 490 张图、约 196 MB**；按窗口外推约 1.2 GB/小时/IP（第 3 节 N-02）。不是越权或资金问题，是磁盘/数据库容量拒绝服务面。
4. **真实浏览器发现 3 项此前静态审阅看不到的 UI 问题**：① 手机端（390px）订单管理页整页横向溢出 28px（长订单号标题撑破 grid）；② ER 图节点宽度固定 230px，长表名/注释/类型文字溢出方框，且布局为网格排布，关系曲线穿过无关表的方框（7 表链状示例中 6 条边有 2 条被其他表遮挡）；③ 单标签页里"模拟支付成功 → 返回商城查看订单"或浏览器后退，回到的是**商品落地页**（又出现"立即购买"），不是支付成功态——Chrome 报告 bfcache 被 `Cache-Control: no-store` 拒绝，页面重载后 `currentNo` 丢失，TD-270 为此加的 `pageshow` 分支实际不会触发（第 6.3 节）。
5. **文档与代码同步基本良好，但"当前入口"再次漂移**：本会话分支是 `arena/01a0cdc6-codemax-platform`，而 HANDOVER/AGENTS/finish-subitem Skill/Windows 指南/Conda 指南/Agnes 工作流仍写上一会话的 `arena/01a0bf7a`（TD-270 刚把 `01a08bf5` 改成它，同一问题每个会话都会重演）；ROADMAP 页眉"更新：2026-09-20"、`review/README.md` 与 ROADMAP 都没登记 TD-270；HANDOVER「本次交接范围」「接手顺序」仍在指挥接手人"执行六项探针、处理 G1"——探针已删、G1 已全部关闭（第 5 节）。
6. **性能与工程**：CI 全量套件 18 分钟（PG）/ 24 分钟（SQLite）中，**测量到 81% 的时间花在 bcrypt**（3491 次、1030.9 秒，生产级 cost 12）。只在测试进程把 bcrypt cost 降到 4，全量从 1276 s 降到 **220 s（5.8×）且 1822 项全部通过**（第 7 节 O-11）。这是本轮收益最大、风险最小的工程优化。

## 2. 复核范围与方法

基线 `git ls-files` **370 个已跟踪文件**（.py 142、.js 109、.md 66、.sql 19、.html 10、.json 5、.yml 3、.css 2、.mjs 2、.txt 2、其他 10）。

| 类别 | 本轮做法 | 没有做的 |
| --- | --- | --- |
| Python 应用 | `main.py`、`app/` 顶层装配/配置/中间件/鉴权/限流/启动检查/数据库/交付/存储/账本/订单状态/退款凭证、`app/routers/` 全部 14 个、`app/tools/` 的 llm/support/faq/intent/politeness/sql_ddl/word **逐文件通读**；资金模块（refund_submissions/refund_verification/order_closures/bill_reconcile/refunds_admin）按"网络 I/O 与 commit/锁的先后顺序"抽查调用链；扩展 Ruff（S/PERF/RUF/PLW/BLE/TRY/UP）做线索筛查 | 不逐行认证 ~19k 行测试代码；资金模块未做形式化证明 |
| 前端 | `app/frontend/` 9 个手写源码全部通读；`npm run build` 零漂移；**真实浏览器执行** + Node 桩复现（N-01/N-06） | 不逐行审计压缩的 mermaid/d3；Drawio 外部编辑器在沙箱不可达，只能用桩 |
| 模板与样式 | 10 个模板、`support.css`、`docs/site/style.css`、`site.js` 通读；**Chromium 实渲染**（桌面/手机）、计算样式取字号、axe-core 4 扫描 9 个页面状态、WCAG 相对亮度公式复核 | 未做读屏软件实测、未测 Safari/Firefox |
| 文档 | 当前入口（HANDOVER/ROADMAP/AGENTS/README/总览/docs/*/模块 README/review 索引/manager/Skill）通读；脚本对照：文档路径 ↔ 磁盘、文档路由 ↔ 运行时 67 条路由、环境变量名 ↔ `Settings`（含 `.env.example` 双向）、分支名 ↔ 实际分支、计数 ↔ 本次生成 | 历史报告（阶段 2–17、09-15 原稿）按其日期理解，不改写 |
| 数据库 / SQL | 读 `full_init.sql` 约束与索引清单、`db_admin` 账本/锁/事务控制扫描、`db_init.py` CLI | 未在 PG 上重放迁移链（CI PG job 与 `test_schema_equivalence.py` 覆盖） |
| 配置 / CI / 容器 | `ci.yml` 六 job 与各步骤实际耗时（GitHub API）、Agnes 工作流、Dockerfile、compose、`.gitignore`/`.dockerignore`、生产启动检查（以 production 配置实跑 `check_production_settings`） | 未 `docker compose up`；未取 postgres digest（Docker Hub 仍不可达） |
| 滥用 / 边界探针 | 一次性 SQLite + 进程内 ASGI：存储滥用（N-02）、下单行为（N-05）；DDL 解析器 11 组对抗输入（均 ≤ 72 ms，无超线性）；静态资源缓存/gzip 头；跨源登录在 TLS 代理后的行为（N-07） | 未做真实网络压测、未连真实商户/模型 |

## 3. 新发现（带复现证据）

### N-01 / P2（回归）：会话过期时 Drawio 未保存的图与客服草稿被静默丢弃

- **来源**：TD-270 在 `app/frontend/auth.js` 新增 `sessionExpired(status)`：`user = null; paint(); notify(); open("login")`。`notify()` 会调用所有监听器。
  - `app/frontend/drawio-page.js::syncAuth`：身份从 `alice` 变 `null` 时执行 `if (identity) xml = BLANK;` 与 `resetEditor()`（重建 iframe）。随后新 iframe 发 `init`，`load()` 把 `BLANK` 发给编辑器。
  - `app/frontend/support-page.js::onUser(null)` → `reset()` → `el("body").value = ""`：清空未发送的留言。
- **证据一（Drawio，Node 桩跑真实源码与产物）**：编辑器先 `autosave` 出一张含"30 minutes of work"的图 → 点保存 → 服务端 401 → 编辑器应答导出。结果：

  | 源码 | 浮层弹出 | 状态栏 | 编辑器重载内容 | 用同一账号重新登录后 |
  | --- | --- | --- | --- | --- |
  | TD-270 之前（`c3d428e`） | 否 | `未保存：…401` | **用户的图** | **用户的图** |
  | 当前源码 / 当前产物 | 是 | 空 | **空白图** | **空白图** |

  "改前"的体验不理想（没提示重新登录），但**不丢数据**；"改后"提示了重新登录，却把图丢了。脚本：见附录 A-1（不入库）。
- **证据二（客服，真实 Chromium）**：已登录打开客服页、在输入框写草稿，把 HttpOnly Cookie 换成过期令牌，等下一次 4 秒轮询。结果：`{"draft":"","modalOpen":true,"modalMsg":"登录已过期，请重新登录后继续；刚才的操作未提交。"}`——草稿为空，而提示语恰好写着"刚才的操作未提交"。
- **为什么测试没抓到**：`tests/test_ui_accessibility.py` 的会话过期用例只断言浮层打开、提示文案、工作区隐藏；没有断言编辑内容保留。`tests/test_drawio_auth_state.py` 覆盖的是正常登录/退出，不含"同一账号过期后重登"。
- **建议（修复批次 C1）**：区分"用户主动退出/换账号"与"会话过期"。`sessionExpired` 走独立事件（或给监听器传 `{reason: "expired"}`），Drawio 页在过期时**不**清空 `xml`、不重建 iframe，重新登录为同一用户名时直接恢复（换成别的账号才清空，保持 TD-54 账号隔离）；客服页过期时保留 `#support-body`，只停轮询。保护性回归：用现有 Node 桩断言"过期 → 同账号重登 → 编辑器内容与草稿仍在""过期 → 换账号 → 清空"，且新测试在当前代码上必须先红。

### N-02 / P2：流程图存储没有每 IP / 全站上限，单 IP 约 13 MB/秒可写入

- **来源**：`app/routers/diagrams.py` 的 `create_diagram`/`update_diagram` 没有 `rate_limit` 依赖；配额只按账号计（`DIAGRAM_QUOTA=50` 存活、`DIAGRAM_TOTAL_QUOTA=200` 总数、`DIAGRAM_BYTE_QUOTA=20000000` 字节）；注册只有 `RATE_LIMIT_AUTH`（10 次/60 秒/IP）。请求体预算 2 MiB（TD-260）只限单次。
- **证据**：进程内 ASGI、`RATE_LIMIT_ENABLED=True`、一次性 SQLite。单个客户端地址：注册 10 个账号（第 11、12 个 429），每个账号写入 ~400 KB 的图直到配额 409。**15.0 秒内 490 次保存、约 196 MB 落库**；下一窗口可重复。IPv6 按 /64 归并（TD-261）只减缓不消除（换 /64 即新身份）。
- **不是**：越权、跨账号读写、资金问题；也不是现有配额计算错误（配额按设计生效）。
- **建议（C2）**：① `POST/PUT /diagrams` 挂写入限流桶（例如复用 `RATE_LIMIT_TOOLS`，或新增 `RATE_LIMIT_DIAGRAM_WRITES`，需用户确认取值）；② 注册再加一个"每 IP 每日"上限或全站新账号速率告警；③ 可选：全站流程图总字节软上限 + 运维告警。需确认：是否接受新增配置项（AGENTS：新配置/业务范围先确认）。

### N-03 / P3：手机端订单管理页横向溢出

- **证据（Chromium 390×844）**：`document.documentElement.scrollWidth = 418 > 390`。溢出链：`MAIN sw=418` ← `.finance sw=394` ← `#finance-workspace.finance-grid sw=372` ← `SECTION sw=371` ← `H2#finance-title sw=353`（"订单 CM2026…"28 位无空格订单号）与 `#finance-list li`（按钮文字同样不换行）。`.finance { padding: 22px }` 叠加 `main { padding: 16px 24px }` 后内容宽只剩 298px。
- **建议（C3，纯 CSS）**：`.finance h2, #finance-list button { overflow-wrap: anywhere }`；`@media (max-width:760px) { .finance { padding: 12px 0 } .finance section { padding: 12px } }`。回归：用计算宽度的 Node/浏览器断言，或至少静态断言规则存在。

### N-04 / P3：ER 图文字溢出方框、连线被其他表遮挡

- **证据一（Chromium 实测 `getComputedTextLength`）**：表名+注释 `sys_refund_verification_job（退款通知后续核验任务…）` 528px、列 `last_observed_provider_status: VARCHAR(32)` 260px、`lease_until: TIMESTAMP WITH TIME ZONE` 240px，均超出固定 `NODE_W = 230`，文字画到方框外并与右侧表重叠。
- **证据二（真实 `layoutEr` + 同一三次贝塞尔公式采样）**：7 表链（每表引用前一张）6 条边中 4 条"从子表右边缘画到父表左边缘"——父表在左时曲线横穿两个端点方框；2 条被无关表遮挡。6 张表引用同一 `user` 表的星形结构：6 条边全部反向穿越端点，3 条被遮挡。连线标签 `parent_id → id` 常压在方框上。
- **建议（C4，纯前端，不加依赖）**：① 节点宽度按最长文字估算（`max(230, 7.5px × 最长字符数)`，封顶 380），超长注释截断并用 `<title>` 显示全文；② 连线按两端相对位置选边（父在左取父右边/子左边，同列取上下边）；③ 按外键拓扑排序排布（被引用多的表放第一列）。`tests/test_er_page.py` 已用 Node 跑真实布局，可直接加"文字宽度 ≤ 节点宽""同列不穿越"的断言。

### N-05 / P3：已付款用户再点"立即购买"会静默新建订单

- **证据**：已付款账号在 API 层 `POST /shop/orders` 返回新的 pending 订单（`reused=false`）；落地页没有"你已购买过此商品"的提示。结合 6.3 的"返回后落回落地页"，普通用户很可能付第二次。
- **判定**：单 SKU 数字商品重复购买对用户没有价值；但这属于业务规则（是否允许复购），不擅自改。**建议（C5，需确认）**：落地页登录后若已有 paid/downloaded 订单，把主按钮改为"已购买，去下载"，复购放二级链接并二次确认；后端保持现状。

### N-06 / P3：Drawio 页加载时重复重建 iframe（外部编辑器被下载 2–3 次）

- **证据（Node 桩跑真实源码）**：页面 HTML 已开始加载 `embed.diagrams.net`，随后 `syncAuth` 首次调用 `resetEditor()` 替换 iframe；登录态异步返回时再替换一次。访客加载 1 次重建、已登录 2 次重建（即编辑器共下载 2～3 次）。原因是 `identity` 初值 `undefined`，与 `null` 不相等，首轮 `syncAuth(null)` 也会走重置分支；`auth.js` 的 `refresh()` 无论结果都会 `notify()`。
- **建议（C6）**：`identity` 初值与首轮判断改成"首次同步只记录身份、不重置"，或模板里 iframe 先不写 `src`、由脚本在首次身份确定后设置。回归：桩断言首次加载最多创建 1 个 iframe。

### N-07 / 观察：在 TLS 终止代理后且未开启 `TRUST_PROXY_HEADERS` 时，浏览器登录被拒

- **证据**：以 `Host: 8000-demo.e2b.app`、`Origin: https://8000-demo.e2b.app`、`Sec-Fetch-Site: same-origin` 访问本地 http 服务，`POST /auth/login` 返回 **403「登录来源不匹配」**；同样请求若 Origin 是 `http://` 则 200。原因：开发模式 `public_base_url` 取 `request.base_url`（http），与浏览器 https Origin 的 scheme/端口不一致。
- **影响面**：生产（`ENV=production`）固定用 `SITE_BASE_URL`，且启动检查强制 `TRUST_PROXY_HEADERS=true`，不受影响；Windows 指南用 `http://127.0.0.1:8000` 直连，不受影响。受影响的是"开发模式 + 前面有 https 代理/隧道（ngrok、云 IDE 预览、nginx 未配转发头）"——这时连财务写操作也会 403。
- **建议（C7，仅文档）**：DEPLOY 与 WINDOWS_LOCAL_RUN 增加一句："开发环境若经 https 代理/隧道访问，需设置 `TRUST_PROXY_HEADERS=true` 与正确的 `TRUSTED_PROXY_CIDRS`，否则登录与财务操作按跨源拒绝（403 登录来源不匹配）"。不改代码。

### N-08 / 观察：`/support/ask`（匿名 RAG 客服）仍无任何页面调用

- 2026-09-20 报告已指出；本轮复核仍成立。它不鉴权、只受 `RATE_LIMIT_LLM` 与 LLM 并发闸门约束，且每次请求全表读取文章做 sha256（O-02）。**建议**：要么在客服页加"先问智能助手"入口（产品决定），要么在不需要时由配置关闭该路由，降低无人使用的公开面。

## 4. 前两轮修复的独立复核（抽样结论）

| 批次 | 复核方式 | 结论 |
| --- | --- | --- |
| TD-260 请求体预算 / LLM 响应预算 | 读中间件与 `_json_within_budget`；确认 413 经 HTTPException 路径、回调路径自管；`RecursionError` 归 LLMError | 成立。附带观察：`validation_error_without_input` 中文化覆盖了常见类型，前端 `errorText` 只读 `msg`，浏览器实测注册"ab"显示「用户名至少 3 个字符」 |
| TD-261 限流器 | 读 `admit` 队头回收、`_identity` /64、`client_key` XFF 逆序取首个非可信地址 | 成立。注意 `prune()` 仍保留为全表扫描的维护接口，请求路径不调用（与注释一致） |
| TD-262 预支付单飞 / 登录来源 | 读 `_prepay_flight` 引用计数与取消路径、锁内 `populate_existing` 重读 | 成立；N-07 是该来源检查在"开发+https 代理"下的部署注意事项，不是缺陷 |
| TD-263 配色/浮层 | Chromium 计算样式 + axe-core：7 个页面匿名态 0 违规 | 基本成立；遗漏两处见 6.2（客服消息时间 4.34:1、Drawio 下拉框无可访问名称） |
| TD-264 LLM 并发闸门 | 读 `InFlightGate` 与各路由映射 | 成立 |
| TD-265 schema 三路等价 | 读测试与 `ledger_ddl` | 成立（CI SQLite job 因装有 pgserver 同样执行） |
| TD-266 下载校验缓存 | 读 `verify_snapshot`：stat 先于哈希、ctime 宽限、非 POSIX 关闭 | 成立 |
| TD-267 CI 供应链 | 读两份工作流与 `test_ci_supply_chain.py` | 成立；postgres digest / pip 哈希锁仍未做（环境所限，已如实记录） |
| TD-268 爬虫礼貌 | 读 `on_hop`、LRU、Crawl-delay 上限 | 成立 |
| TD-269 DDL 解析 | 11 组 ≤20000 字符对抗输入（深括号、3000 列、9000 转义引号、300 表链、250 条 ALTER、1700 次 REFERENCES 等）均 ≤ 72 ms、无异常 | 成立，未见超线性 |
| TD-270 验收前第一批 | 浏览器复走第 12 步；Node 桩对比会话过期前后 | 422 中文化、静态缓存（第二次导航 `auth.js` fromCache=true）、gzip（Mermaid 首屏 626 KiB → 152 KiB）、ER 表头对比度成立；**会话过期处理引入 N-01**；**"返回商城"后状态恢复未达成**（6.3） |

资金链（收款凭证、退款准备/授权/发送/停止/重授权、核验队列、关单、日账）按"外部 I/O 前先提交 started、I/O 期间不持锁、I/O 后重锁并复核操作者"的模式抽查 `refund_submissions`/`refund_verification.run_once`/`order_closures`/`routers/payments_admin.reconcile`/`routers/refunds_admin.query_refund`：顺序与注释一致。本轮**没有**建立新的资金类攻击结论，这不等于形式化证明不存在问题。

## 5. 文档与代码同步

### 5.1 明确漂移（建议本轮直接修正的文字）

| 位置 | 现状 | 应为 |
| --- | --- | --- |
| HANDOVER.md（第 3、28、29、33 行）、AGENTS.md 第 3 行、`.claude/skills/finish-subitem/SKILL.md`（32/34/42 行）、`Windows新手逐步验收.md`（121/127/134/567 行）、`docs/WINDOWS_CONDA.md`（60/63/69/201 行）、`docs/AGNES_AI.md` 第 121 行、`.github/workflows/agnes-connectivity.yml` 第 14 行（及其断言 `tests/test_agnes_integration.py:160`） | 固定分支 `arena/01a0bf7a-codemax-platform`（上一会话） | 本会话 `arena/01a0cdc6-codemax-platform`；**根治建议**见 5.3 |
| HANDOVER「本次交接范围」 | 基线 `7f2e125`、"新增六项诊断探针" | 探针已于 TD-262 删除；当前基线与状态应指向 TD-270 之后 |
| HANDOVER「接手顺序」第 2–3 步 | "独立执行报告中的六项有界合成探针""按 ROADMAP 的 G1 处理…" | G1 已全部关闭；应改为"复核最近批次、按 ROADMAP 余项与验收前复核继续" |
| HANDOVER「已核实的上轮发布」 | `2a60134` / CI 35469332541 | 已是第十七批时代的记录；应改为最近一次已核实发布或删除该节（四层状态表已说明核验方法） |
| ROADMAP 第 3 行 | "更新：2026-09-20" | 已含 2026-09-22 条目；且未登记 TD-270 与"验收前复核"阶段 |
| review/README.md「最新独立交接」 | 截止 TD-269 | 补 TD-270 与本报告 |
| AGENTS.md 第 3 行、总览.md 第 8 行、docs/README.md 第 28 行 | "内容审核见 review/FULL_REPOSITORY_HANDOFF_2026-09-19.md" | 同时指向最新复核报告（或改为"见 review/README.md 最新条目"，避免每轮改三处） |
| manager/README.md 末行、manager/stages/README.md「当前」 | "当前任务：全仓交接审计…不是第十八批支付开发" | 阶段已进入"验收前复核"；按 SKILL 的"不堆叠当前横幅"原则改为一句指向 ROADMAP |
| `docs/code_reading_notes.json` 中 `scripts/build_docs_site.py` L272–L308 段 | 讲解写"2026-09-20 再登记接手独立复核报告" | 登记本报告后同步一句（精读 SHA 会随源码变化而强制更新） |
| `package.json` 与 `app/frontend/package.json` 的 `//` 注释 | 仍以"app/static/er.js 里的 module.exports"为理由 | 该文件已不存在（ER 布局在 `app/frontend/er-layout.js`，测试用 ESM import）；理由应改写为现状（不写 type:module 是为了不影响仓库内其他 CommonJS/脚本），或说明是历史原因 |
| `.env.example` 的 `APP_BIND_HOST` | 不是 `Settings` 字段 | 实为 Compose 变量，已有注释说明；可接受（仅记录，不改） |

### 5.2 核对通过的项

- `Settings` 全部 53 个字段都出现在 `.env.example`；文档中的环境变量名（WX_/LLM_/DB_/SHOP_/RATE_LIMIT_ 等前缀）除 TD-106 的历史"将来再加 `WX_PRIVATE_KEY_PATH`"外全部真实存在。
- 文档中出现的 API 路由均能匹配运行时 67 条（含 HEAD 等）路由；`/shop/download`、`/tools/*` 是前缀/简写引用，非失效路由。
- 现行文档引用的 `app/`、`tests/`、`scripts/` 路径全部存在（扫描器报出的 `docs/…*.js` 是 `.json` 的前缀误报，已人工排除）。
- `db_init.py` 子命令（init / adopt-legacy-0008 / migrate / status / seed-demo / bootstrap-admin）与数据库 README、Windows 指南一致；迁移 0001–0017 连续。
- 精读数据 198 文件 / 2313 段全部与磁盘 SHA 一致；文档站 59 份文档、63 条业务路由、2114 个符号。

### 5.3 根治建议：分支名不要再硬编码在 7 个文件里

TD-270 刚把 `01a08bf5` 统一换成 `01a0bf7a`，本会话又变成 `01a0cdc6`——每个 Arena 会话都会换分支，每次都要改 7 个文件、1 个工作流触发条件和 1 条测试断言。建议：
1. **Windows/Conda 指南与 AGNES_AI**：改为"克隆/拉取交付消息中给出的分支（当前为 X）"，只在 HANDOVER 顶部保留**唯一一处**当前分支名；指南引用 HANDOVER。
2. **agnes-connectivity.yml**：push 触发改为 `branches: ['arena/**']`（仍受 `paths` 与提交信息标记双重限制，不会让普通提交自动消耗额度），测试断言同步改为模式；或只保留 `workflow_dispatch`。
3. **finish-subitem Skill / AGENTS**：命令里用 `$(git branch --show-current)`，不写死分支名。
这属于文档/工作流合同变更，工作流改动需用户确认后执行（AGENTS：ci/工作流是全局门禁）。

## 6. UI / 可用性（真实浏览器）

截图（桌面与手机，共 14 张）保存在工作区 `/home/user/codemax_review_2026-09-23/`，不入库（二进制证据，避免膨胀仓库）。

### 6.1 字号与排版：整体合适，三处建议

实测计算样式：正文 `14px/1.6`、顶栏标题 18px、卡片标题 16px、商品价格 34px、购买按钮 16px、页脚 13px、订单管理页正文 14px 与表单控件 13.33px 混排、文档站正文 15px/1.75。结论：

- **合适**：14px 正文 + 1.6 行高对中文可读；标题层级 18/16/34 清晰；文档站 15px/1.75 适合长文。
- **建议 1（按钮/输入框字号）**：`button` 没有 `font: inherit`，浏览器默认 **13.33px Arial**（中文回退系统字体），比正文小一号且字体不统一；手机端登录浮层输入框是 13px。**iOS Safari 在输入框字号 < 16px 时聚焦会自动放大页面**。建议 `button, input, select, textarea { font: inherit }`，并在 `@media (max-width: 700px)` 下把 `.modal input` 与 `textarea` 设为 16px。
- **建议 2（手机顶栏过高）**：390px 下顶栏 **208px**（约占首屏 25%），三行导航 + 独立一行"登录/注册"。建议窄屏把工具导航收成一行横向滚动或"菜单"按钮，把"订单管理（管理员）"只对管理员显示（它现在对所有访客显示，点进去是"请使用管理员账号登录"，对普通用户是噪音）。
- **建议 3（触控目标）**：顶栏/页脚链接高度 21–24px，低于 WCAG 2.2 AA 的 24×24 下限边缘、明显低于移动端常用的 44px。建议窄屏为导航链接加 `padding: 8px 4px`。

### 6.2 可访问性：axe-core 结果

9 个页面状态扫描，匿名态 7 个页面 **0 违规**（TD-263 的修复有效）。登录/交互后发现 2 项：

| 规则 | 影响 | 位置 | 修复 |
| --- | --- | --- | --- |
| `color-contrast`（serious） | 客服消息时间 `#64748b` 在 `#f1f5f9` 气泡上 **4.34:1**（管理员气泡 `#eff6ff` 上 4.37:1），字号 11.7px | `support.css` `#support-messages small` | 换 `#475569`（6.92:1 / 6.96:1），与站内正文次级色一致 |
| `select-name`（critical） | Drawio 页"我的流程图"下拉框没有可访问名称（读屏只读出"组合框"） | `drawio.html` `#diagram-list` | 加 `aria-label="我的流程图"`；同时给 `#diagram-name` 加 `aria-label`（目前只有 placeholder） |

另外（非 axe 规则，但影响读屏导航）：ER/Mermaid/Drawio 三个工具页**没有页面级标题**（只有站点名 h1），订单管理页出现**两个 h1**。建议工具页加视觉可隐藏或可见的 `<h2>{{ title }}</h2>`，管理页标题降为 h2。

### 6.3 交互流程

- **单标签页"模拟支付 → 返回商城"落回落地页**（N-05 的放大器）：Chromium 报告 `/shop` 的 bfcache 被拒原因 `response-cache-control-no-store`、`response-cache-control-no-store-with-js-network-request`。页面是全新加载，`currentNo` 为 null，`refreshOnReturn()` 直接 return，页面显示"立即购买"。Windows 指南第 12 步第 3 条写的"页面会立即重查状态，应从待支付变为支付成功"只有在**新标签页**打开收银台时成立。**建议（C8）**：`/shop` 页面 HTML 本身不含私有数据（订单全靠登录后的 API），可以对该页去掉 `no-store`（API 仍 no-store），或脚本在页面加载时若已登录就自动取最近一张 pending/paid 订单并渲染对应状态；收银台"返回"链接改为 `/shop?order=<订单号>`，商城页据此直接查询该单。同时修正指南第 12 步文字。
- **订单管理页信息密度过高**：选单后整页 2718px（手机 4040px），13 个表单与 12 个 `<pre>` 叠在一列；危险操作（向微信发送退款）与只读信息同一视觉层级。建议按"合同/凭证（只读）→ 常用操作 → 退款全流程 → 历史"分成可折叠 `<details>` 分区，危险操作区加红色边框。纯模板/CSS，不改接口。
- **没有修改密码的界面**：`POST /auth/password` 存在，但只能在开发模式的 `/docs`（Swagger）调用；生产关闭 `/docs` 后，**管理员与用户都无法在网页上改密码**，Windows 指南第 11.2 步也只能用 Swagger 完成。建议（C9）顶栏用户名处加"修改密码"小浮层（复用登录浮层样式与 `errorText`）。
- **Mermaid 未配置 key 时的提示**：显示"未配置 LLM_API_KEY，无法调用大模型"——对游客是开发者术语。建议前端对 502 且文案含该标记时改为"AI 生成暂不可用（站点未开启）"，服务端文案不变。
- **商城落地页文案**：「适合谁」「答辩不慌」等营销语与「本页所售固定数字商品，内容以实际商品说明和文件清单为准」「不默认包含…」的免责语同屏，读起来互相矛盾；且页面没有"文件清单/样例截图"。这是经营内容（L-03），只作提示。

## 7. 性能、工程与安全加固

| 编号 | 观察（有测量的写数字） | 建议 | 风险 |
| --- | --- | --- | --- |
| O-11 / P2 | **测试 81% 时间在 bcrypt**：全量 3491 次 hash/verify 共 1030.9 s（总 1276 s）。测试进程把 cost 降到 4：全量 **220.37 s，1822 passed / 7 skipped**。CI SQLite job 的 pytest 步骤 18 分钟、PG 24 分钟，都逼近 35 分钟上限（TD-251 已上调过一次） | `tests/conftest.py` 在导入应用后 `security.pwd_context.update(bcrypt__rounds=4)`；生产 `CryptContext` 不变。`test_auth_crypto.py` 的时序/事件循环用例用 cost 4 仍能区分（dummy verify 与真 verify 同 cost），但建议该文件单独恢复生产 cost 以保持原测量含义 | 低：只影响测试进程；需确认生产哈希格式不受影响（`$2b$12$` 不变） |
| O-12 / P3 | Mermaid 页首屏 JS 16 个文件 626 KiB（gzip 152 KiB），首次渲染再拉 `chunk-FOHPRMQF.js` 647 KiB（gzip 138 KiB）；Vite 仍有 >500 kB 提示 | `mermaid-page.js` 改为点"生成"时 `await import("mermaid")`（首屏只剩几 KiB）；静态缓存与 gzip 已由 TD-270 解决 | 低；需重建产物并补 Node 测试 |
| O-13 / P3 | gzip 用 starlette 默认 `compresslevel=9`：实测 647 KiB 文件 level 9 需 30.7 ms、level 6 需 22.8 ms，体积只差 0.5% | `GZipMiddleware(..., compresslevel=6)` | 极低 |
| O-14 / P3 | `/static/README.md` 可公开访问（200，14 KB，含全部产物文件名与 SHA 前缀）；无 favicon（每个页面一次 404） | `StaticFiles` 挂载前排除 `*.md`（或把 README 移到 `app/static/` 之外、由文档门禁指向）；加一个极小的 `favicon.svg` 并在 base 模板引用 | 低；文档契约要求每目录有 README，需同步调整 owner 规则 |
| O-15 / P3 | 生产启动检查：`TRUST_PROXY_HEADERS=false` 是**硬拒绝启动**（实跑确认），但文案写"确实不在代理之后才可忽略此项"——硬错误无法忽略 | 二选一：改为警告（`check_production_warnings`），或把文案改成"必须开启；若确实直连公网，请…"并提供显式开关 | 需用户确定部署形态 |
| O-16 / P3 | 注册接口先查后插（`select` → `insert`），并发同名依赖 IntegrityError 兜底，正确；但 `register` 在 bcrypt（约 300 ms）之前不做任何"用户名已存在"的速率保护之外的检查，匿名 10 次/分钟/IP 即可让单核持续忙于 bcrypt | 已有 `RATE_LIMIT_AUTH`；多 IP 情况下可考虑全站注册速率上限（与 N-02 同批） | 低 |
| O-17 / P3 | 扩展 Ruff 线索：1 处无效 `# noqa`（`app/tools/browser.py:25`）；`wechat_bills.py:143` 的 SHA1 是微信账单接口合同（非误用），可加注释；16 处模块级 `global` 均为单实例缓存，符合 TD-141 | 维护批次顺手清理 | 无 |

**安全面补充核对（无新问题）**：JWT 固定 HS256 且 python-jose 拒绝 `alg=none`；Cookie HttpOnly/Lax/生产 Secure；CSP `script-src 'self'`，所有模板零内联脚本（`style-src 'unsafe-inline'` 仍因模板内联 `<style>` 而需要）；`git grep` 未发现私钥/令牌（`.env.example` 只有占位）；个人收款码图片是用户确认公开的素材（TD-225）；仓库为公开仓库，这一点与收款码公开的用途一致。

## 8. 建议的修复批次（待用户确认后实施）

每批沿用 AGENTS 工作循环：保护性测试先红后绿 → 最小实现 → 模块 README/精读 SHA → 新 TD → HANDOVER/ROADMAP → `git commit -F` → 推送本会话分支 → 核对精确 SHA 六项 CI。

| 批次 | 内容 | 需要确认的点 | 规模 |
| --- | --- | --- | --- |
| **D0（文档，建议本轮做）** | 第 5.1 节全部文字修正；本报告登记；分支名改为本会话分支 | 无（纯文档、不改运行逻辑） | 小 |
| **C1（P2，回归）** | 会话过期保留 Drawio 编辑内容与客服草稿（N-01） | 无新依赖/配置 | 小–中（前端 3 个文件 + 产物 + Node 测试） |
| **C2（P2）** | 流程图写入限流 + 注册每日/全站上限（N-02） | 是否新增配置项及取值 | 小 |
| **O-11（P2，工程）** | 测试进程 bcrypt cost 4（CI 18–24 分钟 → 预计 4–6 分钟） | 无（不影响生产） | 很小 |
| **C3 + C4 + 6.1/6.2（P3，UI）** | 手机管理页溢出、ER 节点宽度/连线、按钮字号继承与 iOS 16px、客服时间对比度、Drawio 下拉框名称、工具页标题 | 无 | 中（CSS + er-layout + 产物） |
| **C8 + C5（P3，流程）** | 返回商城后恢复订单状态；已购用户主按钮改"去下载" | C5 属业务规则（是否允许复购） | 小–中 |
| **C6 + C9 + O-12（P3）** | Drawio 首屏只建一次 iframe；网页改密码入口；Mermaid 按需加载 | C9 是新增 UI（非新业务） | 中 |
| **C7 + O-13/14/15（P3）** | 代理部署文档、gzip 级别、静态 README/favicon、启动检查文案 | O-15 需确定部署形态 | 小 |
| **5.3（P3）** | 分支名去硬编码（指南/Skill/Agnes 工作流） | 工作流触发条件变更 | 小 |

## 9. 本轮验证记录

| 检查 | 结果（2026-09-23，基线 3e408a1，运行代码未改动） |
| --- | --- |
| 分支/远端 | 本地 HEAD `3e408a1` = 远端 `arena/01a0bf7a-codemax-platform` tip；本会话分支 `arena/01a0cdc6-codemax-platform` 在开始时尚未推送 |
| 基线 CI | run 35786058282（headSha 3e408a1）：ruff / pip-audit / npm build / docs / SQLite / PostgreSQL 16 六项 success；Agnes 专项 35786058259 success（live-chat skipped） |
| Ruff / pip check | `All checks passed!` / `No broken requirements found.` |
| 文档契约 | `README contract: 303 files, 13 owners, 0 errors` |
| 文档站完整构建 | 退出 0：模块 142 · 依赖边 633 · 路由 63 · 符号 2114 · 文档 59 份 · 精读 198 文件 / 2313 段，待补 0 |
| 前端 | `npm ci` + `npm run build` 后 `git diff --quiet -- app/static/js` 退出 0（零漂移；>500 kB chunk 提示仍在） |
| 全量 SQLite ×2 | **1822 passed / 7 skipped / 1 warning（passlib crypt 弃用），退出 0**；1272.83 s 与 1276.30 s（后者带 bcrypt 计时插件：3491 次、1030.9 s） |
| 全量 SQLite（测量用，bcrypt cost 4） | **1822 passed / 7 skipped，220.37 s，退出 0**（插件在仓库外，仅用于 O-11 测量） |
| 7 个 skip | `-rs` 逐条：test_e2e:506、test_oauth:184、test_second_review_regressions:252/267/334、test_wechat_bills:427 需真 PG；test_faq_semantic:517 需真向量 key |
| 浏览器 | Chromium 153 headless；7 页 × 2 视口截图；axe-core 4.x 9 个状态；完整购买/留言/管理员流程；bfcache 原因经 `notRestoredReasons` 读取 |
| 真实 PostgreSQL | 本轮未在本地跑（CI PG job 在同一 SHA 已 success）；N-01～N-08 均不涉及 PG 特有行为 |
| 未执行 | 真实商户、真实模型、Windows 本机、Docker 部署、读屏软件、Safari/Firefox、Drawio 外部编辑器（沙箱不可达） |

## 附录 A：复现脚本（不入库，按描述可重建）

- **A-1 Drawio 会话过期**：Node `vm` 执行 `app/frontend/drawio-page.js`（与 `app/static/js/drawio-page.js`），桩出 `document`/iframe（`cloneNode` 生成新 iframe）、`CodeMaxAuth.sessionExpired` 与 `auth.js` 同语义、`fetch` 对 POST/PUT 返回 401。步骤：`init`→`load`→`autosave(用户图)`→点保存→应答 `export`→新 iframe `init`→记录 `load` 下发的 XML→同账号 `listener(user)`→再次 `init`。对照组用 `git show c3d428e:app/frontend/drawio-page.js` 与不含 `sessionExpired` 的桩。
- **A-2 客服草稿**：Chromium 登录后打开 `/support/center`，写入草稿，`page.setCookie({name:'access_token', value:'expired.jwt.token'})`，等待 5.5 秒，读 `#support-body.value` 与浮层状态。
- **A-3 存储滥用**：`DATABASE_URL=sqlite+aiosqlite:////tmp/…`，`Base.metadata.create_all`，`settings.RATE_LIMIT_ENABLED=True`，`httpx.AsyncClient(transport=ASGITransport(app, client=("203.0.113.7", 1234)))` 循环注册 12 个账号并各写入 400 KB 的图直到 409。
- **A-4 ER 遮挡**：从 `app/frontend/er-layout.js` 导入 `layoutEr`，对每条边按 `er-page.js` 的 `M x1,y1 C mx,y1 mx,y2 x2,y2` 采样 200 点，判断是否落入非端点节点矩形。
- **A-5 bcrypt 计时**：pytest 插件包裹 `passlib.context.CryptContext.hash/verify` 累计耗时；cost 4 测量插件在 `pytest_configure` 中 `app.security.pwd_context.update(bcrypt__rounds=4)`。
