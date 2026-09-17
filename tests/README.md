# 测试策略与运行指南

Windows + conda 的环境核对、无 `.env` 验收副本、SQLite/真实 PG 和覆盖率命令见 [本机测试指南](../docs/WINDOWS_CONDA.md)。浏览器和外部服务另按 [验收手册](../docs/ACCEPTANCE_GUIDE.md)；不要把 Node VM 当成真实浏览器。

## 模块职责

测试回答分层问题：纯函数验证算法，HTTP 集成验证依赖/权限/异常映射，真实 PostgreSQL 验证数据库语义，Node 验证浏览器脚本生命周期，文档测试验证提取/链接/结构。
并非“一律走 HTTP”，也并非“全部测试都不打真实数据库”。测试数量按具体提交记录在验收报告中，不复制固定函数数或历史耗时当当前指标。

## 文件与入口

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
| 站内消息和前端 | test_support_messages、test_second_frontend_regressions、test_shop_page | 数据权限/重试，Node VM 执行源码和构建脚本；无真实浏览器布局或 diagrams.net 联网验证 |
| 文档与供应链 | test_docs_contract、test_docs_site、test_frontend_supply_chain | 覆盖、指纹、锚点、签名展示、渲染转义、依赖边界；人工解释仍需源码评审 |

文档用例另外核对 Windows 指南登记和内嵌 Python 语法，并用 stub 执行 PG 密码编码/退出码、模型标定先加载配置再收集的入口；不连接数据库/模型，不把这些检查声称为 Windows 或 conda 实机运行。

表内省略 `.py`；自动清单列出全部真实文件。测试不能只断言 HTTP 200：还要核验返回字段、库内状态、未发生的副作用以及重复/失败路径。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`tests/__init__.py`](__init__.py) | `e3b0c44298fc` | 空文件（无源码行） |
| [`tests/conftest.py`](conftest.py) | `64632ddbb161` | L1–L204 |
| [`tests/test_admin_ingest.py`](test_admin_ingest.py) | `9b6e6799819e` | L1–L345 |
| [`tests/test_agnes_integration.py`](test_agnes_integration.py) | `f51ea27a435f` | L1–L176 |
| [`tests/test_audit_20260915.py`](test_audit_20260915.py) | `5bd0fc5338bd` | L1–L300 |
| [`tests/test_auth.py`](test_auth.py) | `81d2a2d26326` | L1–L67 |
| [`tests/test_auth_cookie.py`](test_auth_cookie.py) | `9da604358174` | L1–L329 |
| [`tests/test_auth_crypto.py`](test_auth_crypto.py) | `ae02f0e03c7a` | L1–L180 |
| [`tests/test_code_reading.py`](test_code_reading.py) | `9c2df1b4d535` | L1–L206 |
| [`tests/test_config_validation.py`](test_config_validation.py) | `13ec12dfa2ec` | L1–L180 |
| [`tests/test_crawler.py`](test_crawler.py) | `22cd77a5bd4a` | L1–L344 |
| [`tests/test_db_admin.py`](test_db_admin.py) | `3c7f2eaf8cc3` | L1–L222 |
| [`tests/test_diagram_concurrency.py`](test_diagram_concurrency.py) | `2a44f9d2a7ea` | L1–L147 |
| [`tests/test_diagram_quota.py`](test_diagram_quota.py) | `6a4613493dd1` | L1–L153 |
| [`tests/test_diagrams.py`](test_diagrams.py) | `d0e3630e1695` | L1–L119 |
| [`tests/test_docs_contract.py`](test_docs_contract.py) | `a6d61f682988` | L1–L98 |
| [`tests/test_docs_site.py`](test_docs_site.py) | `504f56e7add8` | L1–L457 |
| [`tests/test_download.py`](test_download.py) | `14c5f509db26` | L1–L263 |
| [`tests/test_drawio_auth_state.py`](test_drawio_auth_state.py) | `15736019e19b` | L1–L56 |
| [`tests/test_dynamic_crawl.py`](test_dynamic_crawl.py) | `d764399a1b53` | L1–L350 |
| [`tests/test_e2e.py`](test_e2e.py) | `2cc3fa166b77` | L1–L532 |
| [`tests/test_er_page.py`](test_er_page.py) | `85003015e498` | L1–L176 |
| [`tests/test_extract.py`](test_extract.py) | `550c6f7a3db7` | L1–L240 |
| [`tests/test_faq.py`](test_faq.py) | `8e9cf7ac294c` | L1–L131 |
| [`tests/test_faq_semantic.py`](test_faq_semantic.py) | `d92fb77c23fc` | L1–L607 |
| [`tests/test_frontend_supply_chain.py`](test_frontend_supply_chain.py) | `6fbd35d188c3` | L1–L207 |
| [`tests/test_intent_cascade.py`](test_intent_cascade.py) | `b373f8176ef3` | L1–L215 |
| [`tests/test_manual_pay.py`](test_manual_pay.py) | `9c5a8cddbb02` | L1–L318 |
| [`tests/test_mermaid.py`](test_mermaid.py) | `5a961a7ab99e` | L1–L183 |
| [`tests/test_mock_pay.py`](test_mock_pay.py) | `96bc978f5dc2` | L1–L157 |
| [`tests/test_oauth.py`](test_oauth.py) | `7100b76e2ec8` | L1–L248 |
| [`tests/test_oauth_consent.py`](test_oauth_consent.py) | `4005b0b271f0` | L1–L199 |
| [`tests/test_ops.py`](test_ops.py) | `489ffbede4ec` | L1–L539 |
| [`tests/test_order_state.py`](test_order_state.py) | `3cc847284250` | L1–L138 |
| [`tests/test_payment_ledger.py`](test_payment_ledger.py) | `ee06e5fcbd31` | L1–L338 |
| [`tests/test_payment_queries.py`](test_payment_queries.py) | `7c2e7045beee` | L1–L114 |
| [`tests/test_payment_review.py`](test_payment_review.py) | `873ff3bbbb65` | L1–L271 |
| [`tests/test_payments_admin.py`](test_payments_admin.py) | `9a3a3be86dd2` | L1–L277 |
| [`tests/test_payments_frontend.py`](test_payments_frontend.py) | `654a6b9f4525` | L1–L205 |
| [`tests/test_perf.py`](test_perf.py) | `75404eeca36d` | L1–L360 |
| [`tests/test_politeness.py`](test_politeness.py) | `a50a1f27f425` | L1–L284 |
| [`tests/test_probe_llm.py`](test_probe_llm.py) | `9d96eee2f113` | L1–L154 |
| [`tests/test_proxy_headers.py`](test_proxy_headers.py) | `f99e631e1fc2` | L1–L110 |
| [`tests/test_ratelimit.py`](test_ratelimit.py) | `7103160ca474` | L1–L152 |
| [`tests/test_refunds.py`](test_refunds.py) | `928d79058be2` | L1–L445 |
| [`tests/test_release_boundaries.py`](test_release_boundaries.py) | `08d7e8d212ac` | L1–L194 |
| [`tests/test_review_regressions.py`](test_review_regressions.py) | `ddb1734435d0` | L1–L127 |
| [`tests/test_schema_sync.py`](test_schema_sync.py) | `ea1200feb254` | L1–L74 |
| [`tests/test_second_frontend_regressions.py`](test_second_frontend_regressions.py) | `642952b432cc` | L1–L86 |
| [`tests/test_second_review_regressions.py`](test_second_review_regressions.py) | `50aebe4f17dd` | L1–L341 |
| [`tests/test_shop_page.py`](test_shop_page.py) | `41a0a9a1815e` | L1–L455 |
| [`tests/test_shop_polling.py`](test_shop_polling.py) | `76a98525c2db` | L1–L179 |
| [`tests/test_site.py`](test_site.py) | `cf8e184736a2` | L1–L65 |
| [`tests/test_sql_ddl.py`](test_sql_ddl.py) | `d22443181e2b` | L1–L254 |
| [`tests/test_support.py`](test_support.py) | `9cd0e6ef02c4` | L1–L240 |
| [`tests/test_support_messages.py`](test_support_messages.py) | `de7599c20fee` | L1–L113 |
| [`tests/test_support_rag_perf.py`](test_support_rag_perf.py) | `2e06151a09d2` | L1–L202 |
| [`tests/test_token_revocation.py`](test_token_revocation.py) | `4530ff5f9bd3` | L1–L237 |
| [`tests/test_username_validation.py`](test_username_validation.py) | `29a23292f4df` | L1–L109 |
| [`tests/test_wechat_notify.py`](test_wechat_notify.py) | `efb4d957c00b` | L1–L454 |
| [`tests/test_wechat_pay.py`](test_wechat_pay.py) | `7bcab7a5bf99` | L1–L305 |
| [`tests/test_word_export.py`](test_word_export.py) | `d7c7c66103db` | L1–L92 |

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
