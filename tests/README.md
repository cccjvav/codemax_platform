# 测试策略与运行指南

Windows + conda 的环境核对、无 `.env` 验收副本、SQLite/真实 PG 和覆盖率命令见 [本机测试指南](../docs/WINDOWS_CONDA.md)。浏览器和外部服务另按 [验收手册](../docs/ACCEPTANCE_GUIDE.md)；不要把 Node VM 当成真实浏览器。

## 模块职责

测试回答分层问题：纯函数验证算法，HTTP 集成验证依赖/权限/异常映射，真实 PostgreSQL 验证数据库语义，Node 验证浏览器脚本生命周期，文档测试验证提取/链接/结构。
并非“一律走 HTTP”，也并非“全部测试都不打真实数据库”。测试数量按具体提交记录在验收报告中，不复制固定函数数或历史耗时当当前指标。

## 文件与入口

### 交接诊断已全部转成默认套件回归

`audit_handoff_probes.py` 曾保留基线 7f2e125 的六项有界合成复现（仅显式执行，PASS 代表观察到待修行为）。2026-09-20 三个修复批次后六项全部关闭，文件已删除：分块请求读完/422回显 → `test_request_body_budget.py`；LLM 大无关字段/深嵌套 JSON/bool-float 向量索引 → `test_llm_response_bounds.py`（以上 TD-260）；同单双预支付 → `test_checkout_concurrency.py`（同用户预支付单飞 + `POST /shop/orders` 独立限流桶）；跨站来源表单登录 → `test_auth_cookie.py` 的登录来源一组（以上 TD-262）。历史来源、评级与环境局限见[全仓交接报告](../review/FULL_REPOSITORY_HANDOFF_2026-09-19.md)与[接手复核](../review/FULL_REPOSITORY_REVIEW_2026-09-20.md)；报告里的复现命令指向的文件已不存在，是历史记录而非现行入口。新的回归和其他用例一样只用虚构内容/替身/临时商品，数据库必须可丢弃。


`test_agnes_integration.py` 验证 Agnes 默认值/模板一致、实际 LLMClient 非流式协议、HTTP分类与正文脱敏、关闭向量时零请求且不使用缓存、无密钥连通性模式以及 GitHub 真实请求只能手动启用。全部是离线断言，不伪装账号实测。语义测试的隔离 fixture 显式打开增强，真实标定的收集开关仍取实际配置，不因测试 fixture 自动解锁网络。

`test_probe_llm.py` 用虚构 key / MockTransport 核对单次探测的端点、方法、模型与固定输入、配置优先级、TLS基址/显式选择前置要求、重定向不转发、畸形响应和密钥回显脱敏；没有真实请求，不替代模型标定。`test_docs_site.py` 另检查 Skill 源/副本逐字一致和全部 Skill 提交/验证代码块不恢复旧分支、全量暂存、自动合并、固定 passed 数或旧 HANDOVER 编号。

### 全局 fixture 与辅助函数

| 入口 | 职责与风险 |
| --- | --- |
| `conftest.TestSession` / `engine` | 默认 SQLite；设置 TEST_DATABASE_URL 时用指定测试库。只准指向可丢弃数据库，测试会重建业务表 |
| `client` | httpx ASGITransport 调用真实应用、中间件与依赖；这是进程内 HTTP，不是启动浏览器或网络服务器 |
| `db` | 独立测试会话，用于造数据和验证持久结果；测试结束清理，不把 fixture 提权方式暴露给业务 API |
| `mock_mode` / `product` | 临时支付配置／本地商品数据；必须恢复配置与路径，避免测试互相污染 |
| `iter_app_routes` | 展开 FastAPI 包含路由，得到真实路由集合；不要只用 app.routes 顶层长度作为接口数 |
| `sso_authorize` | 走授权同意流程的测试辅助，不绕过用户/签名来伪造正常授权测试 |

### 按契约选择测试

| 主题 | 代表文件 | 验证范围与不能替代的事项 |
| --- | --- | --- |
| 身份与 OAuth | test_auth_cookie、test_oauth、test_token_revocation | Cookie/Bearer、用户状态、改密撤销、授权码与回调；不等于完整第三方身份平台联调 |
| 订单与下载 | test_e2e、test_download、test_order_state、test_manual_pay | 金额、状态、所有权、重复支付、流式下载、人工模式；mock 付款不证明真实到账 |
| 并发与迁移 | test_diagram_concurrency、test_second_review_regressions | 真实 PostgreSQL 的并发配额、文章 upsert、消息去重和旧结构升级；SQLite 对应 skip 要解释 |
| 算法与文本 | test_sql_ddl、test_word_export、test_er_page | SQL/Word 纯函数和 Node 布局；不等于支持完整 SQL 方言或 Word 所有版本 |
| 抓取与模型 | test_crawler、test_extract、test_admin_ingest、test_mermaid 等现有模块 | MockTransport 和假模型控制外部返回；真实解析器/事务仍执行；不访问第三方目标来复现问题 |
| FAQ 与 RAG | test_faq、test_faq_semantic、test_intent_cascade、test_support | 排序、阈值算法、回退与资料检索；真实 embedding 阈值标定需要单独密钥与语料 |
| 运维边界 | test_ops、test_config_validation、test_review_regressions | CSP、配置、任务池、错误与缓存头；不是生产负载压测或网络隔离验收 |
| 输入/上游资源边界 | test_request_body_budget、test_llm_response_bounds、test_llm_concurrency | 请求体预算（1 MiB / `/diagrams` 2 MiB / 回调自管）与 LLM 响应 1 MiB、深嵌套、index 类型、总时限；LLM 并发闸门 4 个在途、第 5 个不发往提供方、失败路径归还槽位、503/502 映射、转人工、前端重试一次；进程内 ASGI/MockTransport/事件屏障，不替代代理限额或真实供应商实测 |
| 限流身份与容量 | test_ratelimit、test_download（出口限流）、test_audit_20260915（NoScan） | 合成时钟：满桶先回收过期桶、满且全活跃仍拒绝（reason=capacity、告警限频）、IPv6 /64 归并、`GET /shop/dl` 与领取共用 `download` 桶；单进程语义，不是多副本共享配额或压测 |
| 下单并发与登录来源 | test_checkout_concurrency、test_auth_cookie（登录来源组） | 事件屏障验证同用户预支付单飞（提供方一次、等待者复用/重试、取消不泄漏）、`order` 桶限流；六种跨站标记的表单登录 403 无 Cookie、同源/无头仍 200；进程内 ASGI，不是多实例互斥或真实浏览器 |
| UI 对比度与可访问性 | test_ui_accessibility | WCAG 相对亮度公式先对照参考值，再钉六处文字色 ≥ 4.5:1、旧色不再出现、`:disabled`/`:focus-visible`、浮层 ARIA；Node 真跑源码与产物验证 Esc 关闭与焦点归还；`support.css` 不含 `:has()`。静态 + Node VM，不是浏览器渲染或读屏 |
| 站内消息和前端 | test_support_messages、test_second_frontend_regressions、test_shop_page | 数据权限/重试，Node VM 执行源码和构建脚本；无真实浏览器布局或 diagrams.net 联网验证 |
| 文档与供应链 | test_docs_contract、test_docs_site、test_frontend_supply_chain | 覆盖、指纹、锚点、签名展示、渲染转义、依赖边界；人工解释仍需源码评审 |

文档用例另外核对 Windows 指南登记和内嵌 Python 语法，并用 stub 执行 PG 密码编码/退出码、模型标定先加载配置再收集的入口；不连接数据库/模型，不把这些检查声称为 Windows 或 conda 实机运行。

表内省略 `.py`；自动清单列出全部真实文件。测试不能只断言 HTTP 200：还要核验返回字段、库内状态、未发生的副作用以及重复/失败路径。

## 第十七批：日账与只读快照

`test_wechat_bills.py`覆盖合成已验签元数据→无签名文件哈希→严格现代ALL解析→付款双向比对→私有报告链路。独立Decimal造汇总、签名验证实际GET字节、合法内容篡改、固定URL/token脱敏、压缩/截断/预算/重复拒绝、券额/发起退款/UTC+8跨日、源/合同/流水冲突和不写财务均有反例。真实可丢弃完整PG以非超级用户执行CLI，PG轮次再验证独立连接并发下只读重复读；SQLite对应skip不是替代证明。私有文件0600/独占发布、故障清临时文件、默认关闭/实际目标覆盖和CLI错误脱敏都有执行测试，不连接真实商户。Windows符号链接权限不足可明确skip，其ACL/NTFS及商户原始账单仍需另行签收。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`tests/__init__.py`](__init__.py) | `e3b0c44298fc` | 空文件（无源码行） |
| [`tests/conftest.py`](conftest.py) | `e540b21e04bd` | L1–L217 |
| [`tests/test_admin_ingest.py`](test_admin_ingest.py) | `9b6e6799819e` | L1–L345 |
| [`tests/test_agnes_integration.py`](test_agnes_integration.py) | `f51ea27a435f` | L1–L176 |
| [`tests/test_audit_20260915.py`](test_audit_20260915.py) | `7c1928a5f7ec` | L1–L300 |
| [`tests/test_auth.py`](test_auth.py) | `81d2a2d26326` | L1–L67 |
| [`tests/test_auth_cookie.py`](test_auth_cookie.py) | `dfdda98099dc` | L1–L374 |
| [`tests/test_auth_crypto.py`](test_auth_crypto.py) | `ae02f0e03c7a` | L1–L180 |
| [`tests/test_checkout_concurrency.py`](test_checkout_concurrency.py) | `f7fb58420a6c` | L1–L164 |
| [`tests/test_code_reading.py`](test_code_reading.py) | `9c2df1b4d535` | L1–L206 |
| [`tests/test_config_validation.py`](test_config_validation.py) | `13ec12dfa2ec` | L1–L180 |
| [`tests/test_crawler.py`](test_crawler.py) | `22cd77a5bd4a` | L1–L344 |
| [`tests/test_db_admin.py`](test_db_admin.py) | `7b2130d732b6` | L1–L241 |
| [`tests/test_diagram_concurrency.py`](test_diagram_concurrency.py) | `2a44f9d2a7ea` | L1–L147 |
| [`tests/test_diagram_quota.py`](test_diagram_quota.py) | `6a4613493dd1` | L1–L153 |
| [`tests/test_diagrams.py`](test_diagrams.py) | `d0e3630e1695` | L1–L119 |
| [`tests/test_docs_contract.py`](test_docs_contract.py) | `a6d61f682988` | L1–L98 |
| [`tests/test_docs_site.py`](test_docs_site.py) | `61fa2874c7ae` | L1–L471 |
| [`tests/test_download.py`](test_download.py) | `9f2f5a3dbfca` | L1–L293 |
| [`tests/test_drawio_auth_state.py`](test_drawio_auth_state.py) | `15736019e19b` | L1–L56 |
| [`tests/test_dynamic_crawl.py`](test_dynamic_crawl.py) | `d764399a1b53` | L1–L350 |
| [`tests/test_e2e.py`](test_e2e.py) | `3bd79049adf0` | L1–L532 |
| [`tests/test_er_page.py`](test_er_page.py) | `2309623905f8` | L1–L181 |
| [`tests/test_extract.py`](test_extract.py) | `550c6f7a3db7` | L1–L240 |
| [`tests/test_faq.py`](test_faq.py) | `8e9cf7ac294c` | L1–L131 |
| [`tests/test_faq_semantic.py`](test_faq_semantic.py) | `d92fb77c23fc` | L1–L607 |
| [`tests/test_frontend_supply_chain.py`](test_frontend_supply_chain.py) | `6fbd35d188c3` | L1–L207 |
| [`tests/test_intent_cascade.py`](test_intent_cascade.py) | `b373f8176ef3` | L1–L215 |
| [`tests/test_llm_concurrency.py`](test_llm_concurrency.py) | `f23e2e40e0d4` | L1–L248 |
| [`tests/test_llm_response_bounds.py`](test_llm_response_bounds.py) | `b994e9afa2f7` | L1–L135 |
| [`tests/test_manual_pay.py`](test_manual_pay.py) | `9c5a8cddbb02` | L1–L318 |
| [`tests/test_mermaid.py`](test_mermaid.py) | `5a961a7ab99e` | L1–L183 |
| [`tests/test_mock_pay.py`](test_mock_pay.py) | `96bc978f5dc2` | L1–L157 |
| [`tests/test_oauth.py`](test_oauth.py) | `7100b76e2ec8` | L1–L248 |
| [`tests/test_oauth_consent.py`](test_oauth_consent.py) | `4005b0b271f0` | L1–L199 |
| [`tests/test_ops.py`](test_ops.py) | `489ffbede4ec` | L1–L539 |
| [`tests/test_order_closures.py`](test_order_closures.py) | `7832a0b2f1c3` | L1–L249 |
| [`tests/test_order_state.py`](test_order_state.py) | `3cc847284250` | L1–L138 |
| [`tests/test_payment_ledger.py`](test_payment_ledger.py) | `9a2e75439553` | L1–L339 |
| [`tests/test_payment_queries.py`](test_payment_queries.py) | `7c2e7045beee` | L1–L114 |
| [`tests/test_payment_review.py`](test_payment_review.py) | `873ff3bbbb65` | L1–L271 |
| [`tests/test_payments_admin.py`](test_payments_admin.py) | `9a3a3be86dd2` | L1–L277 |
| [`tests/test_payments_frontend.py`](test_payments_frontend.py) | `161b0f76e53b` | L1–L569 |
| [`tests/test_perf.py`](test_perf.py) | `75404eeca36d` | L1–L360 |
| [`tests/test_politeness.py`](test_politeness.py) | `a50a1f27f425` | L1–L284 |
| [`tests/test_probe_llm.py`](test_probe_llm.py) | `9d96eee2f113` | L1–L154 |
| [`tests/test_proxy_headers.py`](test_proxy_headers.py) | `f99e631e1fc2` | L1–L110 |
| [`tests/test_ratelimit.py`](test_ratelimit.py) | `81b0cfb3ea5a` | L1–L279 |
| [`tests/test_refund_health.py`](test_refund_health.py) | `67132d41ebe0` | L1–L407 |
| [`tests/test_refund_notifications.py`](test_refund_notifications.py) | `459ff21e9840` | L1–L319 |
| [`tests/test_refund_reauthorization.py`](test_refund_reauthorization.py) | `a1894e966c2d` | L1–L302 |
| [`tests/test_refund_requests.py`](test_refund_requests.py) | `75cd3afc6d27` | L1–L320 |
| [`tests/test_refund_stops.py`](test_refund_stops.py) | `ab5526d5a85f` | L1–L300 |
| [`tests/test_refund_submissions.py`](test_refund_submissions.py) | `aae264f41e80` | L1–L407 |
| [`tests/test_refund_verification.py`](test_refund_verification.py) | `ed4ad7569a51` | L1–L306 |
| [`tests/test_refunds.py`](test_refunds.py) | `8c0b61cd7acd` | L1–L446 |
| [`tests/test_release_boundaries.py`](test_release_boundaries.py) | `08d7e8d212ac` | L1–L194 |
| [`tests/test_request_body_budget.py`](test_request_body_budget.py) | `67b2d198009d` | L1–L171 |
| [`tests/test_review_regressions.py`](test_review_regressions.py) | `ddb1734435d0` | L1–L127 |
| [`tests/test_schema_sync.py`](test_schema_sync.py) | `70e23dab4d56` | L1–L75 |
| [`tests/test_second_frontend_regressions.py`](test_second_frontend_regressions.py) | `642952b432cc` | L1–L86 |
| [`tests/test_second_review_regressions.py`](test_second_review_regressions.py) | `50aebe4f17dd` | L1–L341 |
| [`tests/test_shop_page.py`](test_shop_page.py) | `41a0a9a1815e` | L1–L455 |
| [`tests/test_shop_polling.py`](test_shop_polling.py) | `a8bf9e4527d0` | L1–L179 |
| [`tests/test_site.py`](test_site.py) | `cf8e184736a2` | L1–L65 |
| [`tests/test_sql_ddl.py`](test_sql_ddl.py) | `d22443181e2b` | L1–L254 |
| [`tests/test_support.py`](test_support.py) | `9cd0e6ef02c4` | L1–L240 |
| [`tests/test_support_messages.py`](test_support_messages.py) | `de7599c20fee` | L1–L113 |
| [`tests/test_support_rag_perf.py`](test_support_rag_perf.py) | `2e06151a09d2` | L1–L202 |
| [`tests/test_system_refunds.py`](test_system_refunds.py) | `43f04049ad2a` | L1–L325 |
| [`tests/test_token_revocation.py`](test_token_revocation.py) | `4530ff5f9bd3` | L1–L237 |
| [`tests/test_ui_accessibility.py`](test_ui_accessibility.py) | `e45866334747` | L1–L159 |
| [`tests/test_username_validation.py`](test_username_validation.py) | `29a23292f4df` | L1–L109 |
| [`tests/test_verification_controls.py`](test_verification_controls.py) | `e26999e14490` | L1–L224 |
| [`tests/test_wechat_bills.py`](test_wechat_bills.py) | `2209bd1c14bd` | L1–L539 |
| [`tests/test_wechat_notify.py`](test_wechat_notify.py) | `efb4d957c00b` | L1–L454 |
| [`tests/test_wechat_pay.py`](test_wechat_pay.py) | `7bcab7a5bf99` | L1–L305 |
| [`tests/test_word_export.py`](test_word_export.py) | `768f2e4799cf` | L1–L95 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

模型请求与抓取通常注入本地替身；要在报告中区分“执行了本地真实代码”和“调用了真实外部服务”。pytest 参数化会把一个函数展开成多个用例，所以函数数不等于 collected 数。
模块级配置、缓存、节流与 dependency_overrides 都需要清理。不要为了通过测试删除隔离断言、恢复有风险的动态出网，或把数据库 skip 改成伪造成功。

### 精读机制回归

`test_code_reading.py` 用隔离文件验证分段连续性、旧指纹即使主清单刷新后仍失败、未知/重复文件、非法段界、漏尾、占位文字、生成/空文件不能冒充精读、未补项可见和输出转义。另验证 docs CLI 在数据模式也拒绝过期/缺失说明、未知method被拒、guided不冒充认证、唯一notes自引用例外不会扩散到其他JSON。
它不会给解释内容自动判真；业务含义仍需对照源码和对应业务回归。当前全部测试文件都有分段讲解：首批消息测试为人工段落，其余为人工测试边界说明＋每个函数的AST语句/断言导读，不是只列测试名。Node脚本与FakeLLM等属于测试替身；真实模型/浏览器/商户仍独立验收。`test_docs_site` 新增两种CLI缺Mistune的执行级错误测试，不再用顶层无import推定完整数据模式零依赖。

## 变更与验证

在仓库根、已安装 requirements 且 Node 可用的环境中：

```bash
python -m pytest -q
python -m pytest tests/test_admin_ingest.py tests/test_second_review_regressions.py -q
```

真实 PostgreSQL：准备专门测试库，将 TEST_DATABASE_URL 设为对应 `postgresql+asyncpg://` 连接串，再执行同样的全量命令。不要在命令/日志中暴露真实生产凭据，也不要指向生产库。

```bash
coverage run -m pytest
coverage report
```

只有执行并读取新报告才能报告当前覆盖率；历史 96% 不自动继承。CI 使用独立数据库服务；结果必须绑定提交 SHA，不能拿上一提交绿灯验收新内容。

## 2026-09-20 LLM 并发闸门回归（TD-264）

`test_llm_concurrency.py`：autouse fixture 给每条用例换一把新 `InFlightGate`；`Barrier` 上游替身让前 4 个请求停在事件前，第 5 个 `chat` 抛 busy 且 MockTransport 只收到 4 个请求，释放后槽位归零、新请求照常；HTTP 500、连接错误、超大响应、超时四种失败都归还槽位且不计入 rejected；embeddings 与 chat 共用闸门；`/tools/mermaid` 满时 503 + `Retry-After: 5`、上游故障仍 502 无 Retry-After；`answer()` 在闸门满时转人工且原因含「繁忙」；Node 桩执行源码 `mermaid-page.js`（把 `import mermaid` 换成桩）验证 503 重试一次、等待取自 Retry-After（不可解析退回 5 秒）、第二次仍 503 停止并显示服务端文案、502 不重试；产物只做文本核对（Mermaid 全量分块在 Node 里不可执行）。

## 2026-09-20 UI 可访问性回归（TD-263）

`test_ui_accessibility.py`：对比度公式以 21:1 与复核报告的 3.68:1 自校验后，参数化检查 base.html/shop.html 六处规则的颜色值并算比值；全部模板与 `support.css` 不得再出现三种旧低对比色；`button:disabled` 与 `:focus-visible` 规则存在；浮层容器 ARIA 三属性与标题 id 对应；`_HARNESS` 用带 activeElement 的最小 DOM 桩分别 `require` 源码 `app/frontend/auth.js` 和产物 `app/static/js/auth.js`，验证打开后焦点进入输入框、非 Esc 键不关、Esc 关闭并把焦点还给触发按钮、焦点已离开浮层时关闭不抢焦点。旧 `auth.js` 没有 `onkeydown`，该用例在修复前失败。

## 2026-09-20 下单单飞与登录来源回归（TD-262）

`test_checkout_concurrency.py`：`BlockingProvider` 让第一次 `native_prepay` 停在事件屏障上，第二个并发请求必须在 `_prepay_flight` 上等待而不是发第二次预支付；放行后两者同单号同 code_url（reused False/True）、提供方一次、事件只有 started/ready、锁表回收为空。另测第一次结果未知（502）时等待者用同一单号重试并成功（事件 started/unknown/started/ready）、等待者被取消不泄漏引用计数、不同用户互不阻塞、`RATE_LIMIT_AUTH=3` 时第四次下单 429 且库里只有一张单。`test_auth_cookie.py` 新增：cross-site/same-site、外站/null/带路径 Origin 六种表单登录一律 403 且无 Set-Cookie；同源 Origin（含默认端口写法）与无来源头请求仍 200；来源检查先于凭据校验、不吞 401。修复前这些用例在旧代码上全部失败（已实测），不是同义反复。

## 2026-09-20 限流容量回归（TD-261）

`test_ratelimit.py` 新增键容量与身份一组：容量 4/3/200/2 的小限流器验证满桶但有过期桶时新键放行且活跃桶原样保留、满且全活跃时 `admit` 返回 reason=capacity 与指向最早到期桶的 Retry-After、单次调用只回收有限批量但多次后全部释放、满桶 warning 每分钟一条、reset 清空全部容器；`_key_for` 用最小 ASGI scope 直接调用 `client_key` 验证 IPv6 /64 归并、IPv4 映射还原与不可解析对端占固定键，另用 `ASGITransport(client=...)` 走全链路证明同一 /64 内换地址共用配额。`test_audit_20260915.py` 的 NoScan 反例保留，断言改为命中/到期两个容器同长同键集。`test_download.py` 新增出口限流用例：领取 + 两次出口 200、第三次 429，订单状态不变，reset 后同一链接照常可用。全部合成时钟或进程内 ASGI，不是多进程共享配额或真实压测。

## 2026-09-15 交叉审查增量

新增 test_audit_20260915.py：有界限流、NUL/ETag/摘要投影、日志CR与长ID、回调畸形输入、预支付前持久化/失败同号重试、SQL词法与类型、生产origin、真实源码与bundle异步取消/客服UUID/管理列表乱序。旧预支付测试改为持久pending和稳定单号，并新增提交失败时提供方零调用的反例，不再要求危险的“外部失败本地无单”。


## 第二批发布边界回归

`test_db_admin.py` 自建并清理一次性PG，用非超级用户验证无种子init、拒绝覆盖、跨连接锁/并发、旧0008接入保留订单、失败回滚/不重放、校验和、bootstrap和显式开发种子；不使用环境中的业务库DSN。`test_release_boundaries.py` 检验生产账本/管理员/重新哈希弱凭据拒绝、第一方SSO名单、真实签名但错商户/serial回调、证书有效期/公钥ID、预支付字节/总deadline及容器配置入口。既有微信RSA/AES断言保留，只有测试serial改为证书的实际编号；旧强制full_init种子的测试迁移到显式seed_demo，并由真库测试额外保护普通init无身份。

## 第三批证据边界

`test_payment_ledger.py`覆盖跨订单唯一流水、同单冲突/精确重复、独立会话并发、凭证与状态回滚、人工金额/证据/原始确认人、渠道隔离、预支付先持久化事件、文件换版/损坏/恢复、旧单核准及顶层事务词法。真PG专门执行0010升级和触发器，保持历史数据且拒绝改合同/删改凭证事件。

client依赖product提供隔离小文件；缺文件测试明确删源或快照，不依赖开发目录偶然是否有文件。默认SQLite改为临时文件+NullPool独立连接，避免StaticPool跨会话rollback污染；不降低5次并发精确重复均成功的断言。旧“不同流水也算幂等200”改为冲突409，未实现事件不再假SUCCESS，人工确认不再生成虚构MANUAL流水。

## 第四批新增验证

test_payment_queries验证真正合成平台签名、GET查询串/空正文签名、证书/公钥ID、篡改/探测/重复头/时效、状态和完整合同、上限与总期限；原native happy path也改为真合成应答签名，不让其他格式测试被“未签名”提前短路。

test_payments_admin用实际ASGI路由、独立session和签名HTTP替身验证管理员/来源/限流、50条键集只读查询、网络前开始可见、网络期间可独立写库、成功/幂等/冲突、UNKNOWN/退款不撤权、权限途中改变、commit故障后的三者原子回滚，以及回调先到的单凭证。test_payments_frontend同时执行源码和提交bundle，验证账号/详情乱序、普通用户零数据请求、确认校验、绑定/查单请求、丢响应后刷新凭证不重复确认。Node VM不验证真实布局、Cookie策略或商户行为。

第五批test_payment_review验证跟进/完成/重开、不变收入权益及更新时间、count挡低编号晚提交、过期资料/竞争版本、请求重放归属、孤立开始协议/时限、只读SQL、到账后重开、恶意输入/权限/来源/提交故障、坏复核格式/缺发起人、200候选空页续页与查询中途完成。新增Node场景对源码/bundle分别执行原请求重试、409刷新版本、换账号清屏及新筛选不继承游标；不代替浏览器Cookie/CSS验收。


## 第七批退款通知验证

test_refund_notifications使用自己的refund/AES-GCM事件构造器，借现有合成平台RSA签真实最终字节，不把付款transaction信封冒充退款。覆盖签名/重复头/探测/时效/加密/内外状态、严格分数/原收款归属、提交前失败回滚、提交后丢ACK原ID恢复、超时、同单/跨单并发、重发不重开复核、部分/乱序不改权益、独立签名查询才撤权、分页不改变最新摘要与坏摘要不回退。所有外部请求均替身，绝不真实转账。test_payments_frontend新增通知填号不提交/不代填确认、部分通知禁用与换账号迟到响应清屏；两份JS都执行。全量另跑独立真PG，不以SQLite替代并发/生产数据库证据。


## 第八批准备与迁移验证

test_refund_requests覆盖独立提交/首笔不变、同单与跨单/跨人竞争、严格分数与未知字段拒绝、来源隔离、既有退款活动拒绝造新号、提交前回滚/提交后丢ACK、权限版本/来源/限流、仅查询成功才撤权、通知不升级准备、缺审计仍可发现。真实维护库执行0011→0012和只追加/全额/原商户/管理员/既有退款活动触发器反例，不指向业务库。

历史0008/0010/0011测试倒回其基线时先移除新的依赖表/函数，再执行原升级断言；不改历史迁移。两个未来迁移测试改用当前版本+1，避免新增0012与写死测试编号撞名；仍必须实际DDL回滚和不重放。新增最低0012打包缺失拒绝测试。Node源码/bundle同测准备重试同body、丢ACK读回、改未知内容拦截、取消、账号迟到清屏及填号不提交。前端VM与SQLite不冒充浏览器/生产PG触发器验收。


## 第九批合成发送与恢复

新test_refund_submissions全程拦截真实发送，注入真正签名的MockTransport验证POST签名/精确正文/应答验签，独立连接证明started先可见且无用户锁跨网络。覆盖授权原子失败、发送前提交失败/丢ACK、发送后结果落库失败、相同/不同尝试竞争、开关/权限/商户/摘要/原额、字节原因、回调配置、独立授权事实、冷却与同号重试；申请即便SUCCESS也不撤权。真PG退回0012后升级0013，实际写坏合同/原因/回调及UPDATE/DELETE反例；原升级fixture先拆新依赖，不修改历史SQL。Node源码/bundle新增授权/发送丢应答同body、取消/开关、超字节、换账号迟到清屏。


## 第十批停止边界

test_refund_stops实际调用ASGI、独立会话和签名MockTransport，验证永久停止/首笔不变/跨人跨单/严格确认/权限来源限流/提交前后故障/独立复核事实及两个锁顺序。网络中途停止可以提交但不吞已发生结果，之后只能读旧attempt或独立查询。真PG升级0013→0014保留原授权字节，拒绝坏归属/管理员/key/依据/改删。历史降级fixture按依赖先拆停止表/函数/0014，不改历史SQL。

源码/bundle十个新场景执行停止重试同body、改未知内容、取消、换账号迟到及丢ACK读回隐藏发送；断言只POST stop，不能误发send。


## 第十一批核验任务测试

test_refund_verification.py用合成RSA/AES通知和已验签MockTransport GET，覆盖禁用/原子ACK/只读权限/部分退款/时序/坏商户/坏签名/有界修复/租约CAS/崩溃/耗尽/同事务审计；维护fixture另验证0014→0015与SQL不可变归属。独立连接测试HTTP阶段不持用户/订单锁。前端源码和bundle另验任务文本、已有凭证文案与换账号迟到响应，不是浏览器签收。旧迁移降级fixture先删新依赖表/函数/版本，不更改历史SQL。

## 第十二批：人工接管与剩余预算

test_verification_controls.py通过真实HTTP/独立会话验证严格字段、角色/来源/凭据版本/限流、跨单/跨人、首次key读回、快照陈旧与同秒ABA、同快照竞争、领取先后、在途GET不召回但结果被栅栏拒绝、未用次数重排、耗尽/成功/部分及已有凭证拒绝、提交前后ACK失败、审计失败原子回滚。全组还须在非超级用户可丢弃PG执行，不能拿SQLite替代行锁。test_payments_frontend新增源码/bundle的六种控制情景，检查未知重试body/key不变、改输入阻止、取消、换账号迟到、丢ACK读回与409重新确认。没有真实商户或浏览器调用。


## 第十三批系统登记回归

test_system_refunds.py用既有合成签名GET/通知及独立会话，覆盖显式双开关、终态不复活、完整ORIGINAL/CNY/日期、hold与过期/暂存途中租约到期、签名失败、配置/出站参数/actor重绑定、人机并发与首次归属、冲突不覆盖、commit故障/丢ACK与审计失败整笔回滚、逐订单下载撤权。worker开关传递测试替身只证明接线，真实维护fixture另验证0015→0016保留人类凭证、系统CHECK/活租约/唯一及只追加保护；不把ORM create_all等同生产触发器。旧迁移fixture先拆0016再构造历史基线，绝不对业务库运行。

前端system-on/off/race同时跑源码/bundle；已有通知/显式发送/管理员核验回归仍要求它们单独不构成自动登记权限。

本轮full_init新增后超公开ER接口20000字符预算；ER布局与Word接口夹具只去本仓库独立注释行，保留全套DDL，额外断言API图等于原SQL解析图。不增加生产预算、不裁表/字段/外键，原布局断言不变。


## 第十四批回归与输入预算

test_refund_reauthorization用真实ASGI、独立连接及合成签名发送替身，验证已停止/无活动限定、同号全额/只更正reason及正式回调、首笔/旧根/旧停止恢复、并发单后继、未知commit与审计失败、已started但未触网仍拒绝、角色/凭据/Origin、历史50上限和独立财务指纹；一次性非超级用户PG另验0016→0017、根/后继唯一及不能越过停止/开始/只追加。前端源码/bundle补独立确认、未知不改body、隐藏入口零POST、账号迟到清屏。

full_init维护函数继续增长，ER与Word改用conftest.project_ddl_for_api提取全部CREATE TABLE并断言与原始SQL完整解析图相等（每表/字段/FK及解析器支持的注释）；不截断表，不提高20000公开字符预算。过程触发器由真实PG验证，不拿可视化图认证迁移。旧迁移夹具先legacy_0016拆0017且恢复旧授权函数；仅可丢弃库。

## 核验监督与恢复测试

`test_refund_health.py`以临时私有文件检查原子替换/锁/坏状态/告警和脱敏；只读聚合用真实隔离会话，PG maintenance夹具验证完整DDL/账本启动与拒漂移。POSIX子进程SIGTERM/SIGKILL及重启真实执行，合成阻塞出站边界不触商户；未过期租约不偷领、过期新token拒旧结果、无退款凭证。另有真实空队列daemon和OS锁恢复。Windows信号项显式跳过，不算Windows服务签收；Compose文本回归不等于Docker部署。新组与全量均不能指向业务库。

第十六批test_order_closures验证真实合成RSA签名POST/204空体、开始/结果故障、同key/并发、旧查询拒绝、权限/来源/限流与迟到支付不覆盖；前端源码/bundle另验冻结未知请求和账号隔离。无真实关单，完整PG轮次用独立连接重跑竞态，不增schema或改历史迁移。
