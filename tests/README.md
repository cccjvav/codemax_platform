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
| 交付校验缓存 | test_delivery_verify_cache、test_download（出口用例） | 首次全量哈希后同一 inode 只 stat；篡改/回拨 mtime/换 inode/宽限期内都重哈希；出口重复+Range 只哈希一次而退款后仍 403；进程内、POSIX 限定 |
| 结构等价 | test_schema_sync（文本级表/列）、test_schema_equivalence（PG 目录级） | 一次性 pgserver 上 fresh-init / 0008 接入 / 重放 0002–0008 三条路径的列、约束、索引、触发器、函数、序列逐项相等；比较器自检；0001 幂等；不证明生产库状态或数据迁移正确性 |
| 算法与文本 | test_sql_ddl、test_word_export、test_er_page | SQL/Word 纯函数和 Node 布局；不等于支持完整 SQL 方言或 Word 所有版本 |
| 抓取与模型 | test_crawler、test_extract、test_admin_ingest、test_mermaid 等现有模块 | MockTransport 和假模型控制外部返回；真实解析器/事务仍执行；不访问第三方目标来复现问题 |
| FAQ 与 RAG | test_faq、test_faq_semantic、test_intent_cascade、test_support | 排序、阈值算法、回退与资料检索；真实 embedding 阈值标定需要单独密钥与语料 |
| 运维边界 | test_ops、test_config_validation、test_review_regressions | CSP、配置、任务池、错误与缓存头；不是生产负载压测或网络隔离验收 |
| 输入/上游资源边界 | test_request_body_budget、test_llm_response_bounds、test_llm_concurrency | 请求体预算（1 MiB / `/diagrams` 2 MiB / 回调自管）与 LLM 响应 1 MiB、深嵌套、index 类型、总时限；LLM 并发闸门 4 个在途、第 5 个不发往提供方、失败路径归还槽位、503/502 映射、转人工、前端重试一次；进程内 ASGI/MockTransport/事件屏障，不替代代理限额或真实供应商实测 |
| 限流身份与容量 | test_ratelimit、test_download（出口限流）、test_audit_20260915（NoScan） | 合成时钟：满桶先回收过期桶、满且全活跃仍拒绝（reason=capacity、告警限频）、IPv6 /64 归并、`GET /shop/dl` 与领取共用 `download` 桶；单进程语义，不是多副本共享配额或压测 |
| 下单并发与登录来源 | test_checkout_concurrency、test_auth_cookie（登录来源组） | 事件屏障验证同用户预支付单飞（提供方一次、等待者复用/重试、取消不泄漏）、`order` 桶限流；六种跨站标记的表单登录 403 无 Cookie、同源/无头仍 200；进程内 ASGI，不是多实例互斥或真实浏览器 |
| UI 对比度与可访问性 | test_ui_accessibility | WCAG 相对亮度公式先对照参考值，再钉六处文字色 ≥ 4.5:1、旧色不再出现、`:disabled`/`:focus-visible`、浮层 ARIA；Node 真跑源码与产物验证 Esc 关闭与焦点归还；`support.css` 不含 `:has()`。静态 + Node VM，不是浏览器渲染或读屏 |
| 站内消息和前端 | test_support_messages、test_second_frontend_regressions、test_shop_page | 数据权限/重试，Node VM 执行源码和构建脚本；无真实浏览器布局或 diagrams.net 联网验证 |
| 文档与供应链 | test_docs_contract、test_docs_site、test_frontend_supply_chain、test_ci_supply_chain | 覆盖、指纹、锚点、签名展示、渲染转义、依赖边界；工作流 action 钉 SHA、权限只在评论 job 开写、requirements 精确版本、package-lock integrity；人工解释仍需源码评审 |

文档用例另外核对 Windows 指南登记和内嵌 Python 语法，并用 stub 执行 PG 密码编码/退出码、模型标定先加载配置再收集的入口；不连接数据库/模型，不把这些检查声称为 Windows 或 conda 实机运行。

表内省略 `.py`；自动清单列出全部真实文件。测试不能只断言 HTTP 200：还要核验返回字段、库内状态、未发生的副作用以及重复/失败路径。

## 第十七批：日账与只读快照

`test_wechat_bills.py`覆盖合成已验签元数据→无签名文件哈希→严格现代ALL解析→付款双向比对→私有报告链路。独立Decimal造汇总、签名验证实际GET字节、合法内容篡改、固定URL/token脱敏、压缩/截断/预算/重复拒绝、券额/发起退款/UTC+8跨日、源/合同/流水冲突和不写财务均有反例。真实可丢弃完整PG以非超级用户执行CLI，PG轮次再验证独立连接并发下只读重复读；SQLite对应skip不是替代证明。私有文件0600/独占发布、故障清临时文件、默认关闭/实际目标覆盖和CLI错误脱敏都有执行测试，不连接真实商户。Windows符号链接权限不足可明确skip，其ACL/NTFS及商户原始账单仍需另行签收。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`tests/__init__.py`](__init__.py) | `e3b0c44298fc` | 空文件（无源码行） |
| [`tests/conftest.py`](conftest.py) | `bc1e7693175a` | L1–L259 |
| [`tests/test_admin_ingest.py`](test_admin_ingest.py) | `7aec5a7104ca` | L1–L366 |
| [`tests/test_agnes_integration.py`](test_agnes_integration.py) | `f697c9b6d36a` | L1–L179 |
| [`tests/test_audit_20260915.py`](test_audit_20260915.py) | `9e16fe49e2d0` | L1–L298 |
| [`tests/test_auth.py`](test_auth.py) | `81d2a2d26326` | L1–L67 |
| [`tests/test_auth_cookie.py`](test_auth_cookie.py) | `cb10c9c40344` | L1–L380 |
| [`tests/test_auth_crypto.py`](test_auth_crypto.py) | `44aa17391233` | L1–L263 |
| [`tests/test_checkout_concurrency.py`](test_checkout_concurrency.py) | `f17b4b3efb10` | L1–L220 |
| [`tests/test_ci_supply_chain.py`](test_ci_supply_chain.py) | `47e43b1b2ce8` | L1–L99 |
| [`tests/test_code_reading.py`](test_code_reading.py) | `76bcfd71fc58` | L1–L206 |
| [`tests/test_code_reading_narrate.py`](test_code_reading_narrate.py) | `812387e137aa` | L1–L311 |
| [`tests/test_config_validation.py`](test_config_validation.py) | `24c3aa1c80cb` | L1–L196 |
| [`tests/test_crawler.py`](test_crawler.py) | `52d674f20ecc` | L1–L373 |
| [`tests/test_db_admin.py`](test_db_admin.py) | `a2027aea7c8a` | L1–L284 |
| [`tests/test_delivery_verify_cache.py`](test_delivery_verify_cache.py) | `03d8fd22cf3d` | L1–L196 |
| [`tests/test_diagram_concurrency.py`](test_diagram_concurrency.py) | `de0122ee569b` | L1–L180 |
| [`tests/test_diagram_quota.py`](test_diagram_quota.py) | `c3c57f770a41` | L1–L171 |
| [`tests/test_diagrams.py`](test_diagrams.py) | `d0e3630e1695` | L1–L119 |
| [`tests/test_docs_contract.py`](test_docs_contract.py) | `8e1372f4d8f5` | L1–L98 |
| [`tests/test_docs_site.py`](test_docs_site.py) | `08d17b62423a` | L1–L473 |
| [`tests/test_download.py`](test_download.py) | `532509f67572` | L1–L386 |
| [`tests/test_drawio_auth_state.py`](test_drawio_auth_state.py) | `bf31fef574c7` | L1–L222 |
| [`tests/test_drawio_unsaved_changes.py`](test_drawio_unsaved_changes.py) | `18a81ee178a9` | L1–L157 |
| [`tests/test_dynamic_crawl.py`](test_dynamic_crawl.py) | `1c68e42322e2` | L1–L353 |
| [`tests/test_e2e.py`](test_e2e.py) | `3641667e742d` | L1–L532 |
| [`tests/test_er_page.py`](test_er_page.py) | `ce5bf99908f0` | L1–L485 |
| [`tests/test_extract.py`](test_extract.py) | `51ad80685f7f` | L1–L318 |
| [`tests/test_faq.py`](test_faq.py) | `8e9cf7ac294c` | L1–L131 |
| [`tests/test_faq_semantic.py`](test_faq_semantic.py) | `d92fb77c23fc` | L1–L607 |
| [`tests/test_free_text_control_chars.py`](test_free_text_control_chars.py) | `c6b6344a754f` | L1–L46 |
| [`tests/test_frontend_supply_chain.py`](test_frontend_supply_chain.py) | `71ec176ef8cc` | L1–L319 |
| [`tests/test_intent_cascade.py`](test_intent_cascade.py) | `b373f8176ef3` | L1–L215 |
| [`tests/test_llm_concurrency.py`](test_llm_concurrency.py) | `c7c6f8df0def` | L1–L270 |
| [`tests/test_llm_response_bounds.py`](test_llm_response_bounds.py) | `b994e9afa2f7` | L1–L135 |
| [`tests/test_manual_pay.py`](test_manual_pay.py) | `5a14fb833254` | L1–L331 |
| [`tests/test_mermaid.py`](test_mermaid.py) | `d836ada04f44` | L1–L344 |
| [`tests/test_mock_pay.py`](test_mock_pay.py) | `404c8385f38f` | L1–L287 |
| [`tests/test_oauth.py`](test_oauth.py) | `367d3c3c7b7d` | L1–L433 |
| [`tests/test_oauth_consent.py`](test_oauth_consent.py) | `4005b0b271f0` | L1–L199 |
| [`tests/test_ops.py`](test_ops.py) | `19dcb2d9c987` | L1–L658 |
| [`tests/test_order_closures.py`](test_order_closures.py) | `a4652243679f` | L1–L279 |
| [`tests/test_order_state.py`](test_order_state.py) | `3cc847284250` | L1–L138 |
| [`tests/test_payment_event_labels.py`](test_payment_event_labels.py) | `4f5b82922d76` | L1–L234 |
| [`tests/test_payment_ledger.py`](test_payment_ledger.py) | `120f73083b2c` | L1–L429 |
| [`tests/test_payment_queries.py`](test_payment_queries.py) | `7c2e7045beee` | L1–L114 |
| [`tests/test_payment_review.py`](test_payment_review.py) | `779c7a799754` | L1–L291 |
| [`tests/test_payments_admin.py`](test_payments_admin.py) | `9a3a3be86dd2` | L1–L277 |
| [`tests/test_payments_frontend.py`](test_payments_frontend.py) | `ab6a21622471` | L1–L643 |
| [`tests/test_perf.py`](test_perf.py) | `87b3decfd95d` | L1–L398 |
| [`tests/test_politeness.py`](test_politeness.py) | `03e7222f7d16` | L1–L537 |
| [`tests/test_probe_llm.py`](test_probe_llm.py) | `41e6491c69a5` | L1–L154 |
| [`tests/test_proxy_headers.py`](test_proxy_headers.py) | `f99e631e1fc2` | L1–L110 |
| [`tests/test_ratelimit.py`](test_ratelimit.py) | `73ae8c780609` | L1–L402 |
| [`tests/test_refund_health.py`](test_refund_health.py) | `b4a1020ba64d` | L1–L407 |
| [`tests/test_refund_notifications.py`](test_refund_notifications.py) | `968ae7c80110` | L1–L324 |
| [`tests/test_refund_reauthorization.py`](test_refund_reauthorization.py) | `c54e0c7ac6aa` | L1–L302 |
| [`tests/test_refund_requests.py`](test_refund_requests.py) | `9454b4708035` | L1–L332 |
| [`tests/test_refund_stops.py`](test_refund_stops.py) | `d3e938dc54b7` | L1–L300 |
| [`tests/test_refund_submissions.py`](test_refund_submissions.py) | `5beed0578472` | L1–L451 |
| [`tests/test_refund_verification.py`](test_refund_verification.py) | `c99136ba72af` | L1–L348 |
| [`tests/test_refunds.py`](test_refunds.py) | `835b910ba09b` | L1–L460 |
| [`tests/test_release_boundaries.py`](test_release_boundaries.py) | `c37988b5e45d` | L1–L215 |
| [`tests/test_request_body_budget.py`](test_request_body_budget.py) | `077979ed448c` | L1–L210 |
| [`tests/test_review_regressions.py`](test_review_regressions.py) | `ddb1734435d0` | L1–L127 |
| [`tests/test_schema_equivalence.py`](test_schema_equivalence.py) | `763478f8979b` | L1–L219 |
| [`tests/test_schema_sync.py`](test_schema_sync.py) | `70e23dab4d56` | L1–L75 |
| [`tests/test_second_frontend_regressions.py`](test_second_frontend_regressions.py) | `36a6e0f2fdf0` | L1–L151 |
| [`tests/test_second_review_regressions.py`](test_second_review_regressions.py) | `8e3792de0465` | L1–L341 |
| [`tests/test_shop_page.py`](test_shop_page.py) | `8ec8ac8cdc47` | L1–L737 |
| [`tests/test_shop_polling.py`](test_shop_polling.py) | `8b9bba996bce` | L1–L183 |
| [`tests/test_site.py`](test_site.py) | `cf8e184736a2` | L1–L65 |
| [`tests/test_sql_ddl.py`](test_sql_ddl.py) | `9fffc93591fb` | L1–L420 |
| [`tests/test_support.py`](test_support.py) | `f0ccc6097a4a` | L1–L342 |
| [`tests/test_support_messages.py`](test_support_messages.py) | `55ec9da62581` | L1–L126 |
| [`tests/test_support_rag_perf.py`](test_support_rag_perf.py) | `2e06151a09d2` | L1–L202 |
| [`tests/test_system_refunds.py`](test_system_refunds.py) | `43f04049ad2a` | L1–L325 |
| [`tests/test_token_revocation.py`](test_token_revocation.py) | `8d134a6b87aa` | L1–L254 |
| [`tests/test_ui_accessibility.py`](test_ui_accessibility.py) | `4305629d264b` | L1–L1042 |
| [`tests/test_username_validation.py`](test_username_validation.py) | `29a23292f4df` | L1–L109 |
| [`tests/test_verification_controls.py`](test_verification_controls.py) | `2075e6b53046` | L1–L236 |
| [`tests/test_wechat_bills.py`](test_wechat_bills.py) | `b2e08d815890` | L1–L565 |
| [`tests/test_wechat_notify.py`](test_wechat_notify.py) | `1243c4534687` | L1–L501 |
| [`tests/test_wechat_pay.py`](test_wechat_pay.py) | `c18c57773ac9` | L1–L320 |
| [`tests/test_word_export.py`](test_word_export.py) | `8bfbb5850a11` | L1–L108 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

模型请求与抓取通常注入本地替身；要在报告中区分“执行了本地真实代码”和“调用了真实外部服务”。pytest 参数化会把一个函数展开成多个用例，所以函数数不等于 collected 数。
模块级配置、缓存、节流与 dependency_overrides 都需要清理。不要为了通过测试删除隔离断言、恢复有风险的动态出网，或把数据库 skip 改成伪造成功。

### 精读机制回归

`test_code_reading_narrate.py`（TD-291）验证导读维护工具的机械部分：语句写法沿用现有模板、嵌套定义与模块级语句的块归属、在临时 git 仓库里 init/remap/regen/add/drop 之后导读仍通过完整校验，以及每个有导读的 .py 文件的 `check` 都通过（TD-305 起由 `MAINTAINED` 名单扩展到全仓；另测截断、多行字面量、U+FFFD 转义、首块为定义、`add` 插在原定义之前、块边界错位与 `confirm` 登记）。它同样不判定中文说明的语义。

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

## 2026-09-22 DDL 解析方言边界回归（TD-269）

`test_sql_ddl.py` 新增：`ALTER TABLE [ONLY] … ADD [CONSTRAINT] FOREIGN KEY` 两种写法各出一条边、ALTER 未知表或字符串里的 ALTER 忽略；列级与表级 `REFERENCES parent` 不写列都补成父表主键，复合主键/父表缺失时 to_column 留空且边保留；`USERS`/`"users"` 引用折叠到 `Users`，`mixed`/`"mixed"` 引用 `"Mixed"` 保持悬空（与 PG 一致），带引号且逐字一致的仍精确命中；十种方言类型修饰的输出类型串逐项钉住。修复前 4 项失败。

## 2026-09-22 爬虫资源边界回归（TD-268）

`test_politeness.py` 新增：Crawl-delay 超上限（61 秒）立即 RobotsDisallowed 且页面未请求、恰等于上限（60 秒）仍接受；状态表上限 3 时 LRU 淘汰顺序 c/a/d；持锁项不淘汰、全部持锁时允许暂时超上限；A 允许→302→B 全禁时读到 B 的 robots 且 B 页面未请求；同域重定向到 Disallow 路径被拦；robots 自身 302 只跟一次不递归。MockTransport + 字面量公网 IP，SSRF 校验真跑；不访问外网。

## 2026-09-22 工作流供应链守卫（TD-267）

`test_ci_supply_chain.py` 只读两份工作流 YAML、requirements.txt 与 package-lock.json：每个第三方 `uses:` 是 40 位 SHA 且带 `# vX.Y.Z` 注释；同一 action 在所有工作流里 SHA 一致；`ci.yml` 顶层只有 `contents: read`，含 `gh pr comment` 的恰好是两个 test job 且只有它们有 `pull-requests: write`；agnes 工作流只读且手动触发；requirements 每行 `==`；lock 每个包有 integrity；CI 用 `npm ci`。不访问网络，不证明上游 SHA 本身可信。

## 2026-09-22 下载出口校验缓存回归（TD-266）

`test_delivery_verify_cache.py`：autouse fixture 清缓存、强制启用、把 `_now_ns` 拨到宽限期之后；`hashing_calls` 监听 `file_digest` 调用；`tamper` 改写文件并等待 ctime 落到新的时间戳刻度（本机 ext4 粒度 4 ms）。覆盖：首次哈希后只 stat、错摘要/错大小/非快照 key 不缓存、同大小改写与 mtime 回拨都重哈希并判失败、同字节换 inode 重哈希一次后继续缓存、上限满清空、缺文件不缓存、宽限期内每次都哈希、平台开关关闭时每次哈希、`file_digest` 本身无状态。`test_download.py` 新增出口用例：领取一次哈希，随后 200/206/206/200 四次请求不再读文件；直接写入付款+全额退款凭证后缓存命中的请求仍 403 且未重哈希。修复前该用例 `AttributeError`。

## 2026-09-20 数据库结构等价回归（TD-265）

`test_schema_equivalence.py` 复用 `test_db_admin.isolated_pg`（module 级一次性 pgserver + 非超级用户）建三个库：`initialize` 直建；`initialize` 后 `legacy_0008` 拆回 0008 形状再 `adopt_legacy`；再用 `_STRIP_TO_PRE_0002` 逆序剥掉 0002–0008 的结构、以 autocommit 原样执行七份历史 SQL（0007/0008 自带 BEGIN/COMMIT）后 `adopt_legacy`。`catalog()` 只读系统目录，`differences()` 递归点名每一处不同；前提用例先确认快照真的含 ≥16 表、≥8 函数、≥13 触发器、≥10 CHECK 与部分索引，避免空对空。首轮即发现 `sys_user.role` 缺 NOT NULL 与账本表默认值拼法两处漂移，修在源头后三路径零差异；另验 0001 在现行库上为空操作、`ledger_ddl()` 与 full_init 逐字相同。修复前该文件 6 项中 4 项失败。

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

## 2026-09-23 排版/导航回归与测试成本（TD-272）

`test_ui_accessibility.py` 增加排版与导航组：跳过链接与 `#main` 目标、表单控件 `font: inherit`、窄屏 16px 输入框（iOS 缩放）、窄屏导航/页脚 24px 命中区、favicon 零外链、管理员入口按角色显隐（Node 真跑 `auth.js` 与产物：匿名保留、普通用户隐藏、管理员显示）、客服时间色在两种气泡底色上 ≥4.5:1、工具页有可见页面标题（真实渲染断言整站每页只有一个 `h1`）、订单管理页只读分区与危险操作红框、长订单号换行规则。`test_drawio_auth_state.py` 增加「首屏零次重建 iframe、换账号必须重建」。`test_llm_concurrency.py` 增加未配置模型时的访客文案与其反例（其它 502 仍原样显示）。

测试成本：`tests/conftest.py` 现在把**测试进程**的 bcrypt 成本降到 4（实测全量 1276 s → 约 220 s，占原时长 81% 的热点），并给 `default_llm` 一个明显的占位 key，让依赖注入之外的用例拿到可断言的上游行为而不是「未配置 key」的本地错误。`test_auth_crypto.py` 用 `production_cost` fixture 把两条时序/侧信道断言恢复到生产轮数：它同时重置那枚缓存的假哈希 —— 假哈希轮数在生成时固定，只改 CryptContext 会让两侧成本不同（实测跑出 35.9 倍假差异）。新增 `product_file` fixture 覆盖「相对根 `storage/`」这条默认路径。

TD-273（会话到期保住用户内容）：`test_drawio_auth_state.py` 新增 Node 场景，真实驱动「编辑器握手 → autosave → 保存拿 401」，断言到期时**零次**重建 iframe、浮层弹出、同一账号重登仍不重建、换账号必须重建（旧代码上必红）；`test_ui_accessibility.py` 的到期场景新增草稿断言（到期后 `#support-body` 仍是用户写的内容，旧代码上必红）。首屏 iframe 用例扩成三种情形（访客、启动即登录、登录态迟到）都断言零次重建，之后换账号仍必须重建。

## 2026-09-24 已购用户与返回恢复（TD-274 / V-05）

`test_shop_page.py` 新增一节用 Node 真跑 `auth.js` + `shop-page.js`（`_OWNED_EXECUTOR` + `_run_owned_scenario`，`SHOP_CASE` 决定订单列表与单张订单，`?order=` 形态在脚本执行前写进 `location.search`）：最新一张就是已购单时打开 `/shop` 直接显示该单状态、点它不下单（旧代码红）；有已购权益但最新一张是未付款单时只给「已购买，去下载」+ 说明、点它回到已购那一单且订单数不变（旧代码红）；全额退款的订单不算权益、按钮仍是「立即购买」（防过修的对照）；`?order=` 直接显示该单的支付成功/已全额退款；未过期的待支付单恢复后重新起轮询，过期的不恢复也不起轮询。`test_mock_pay.py` 增加返回链接必须带订单号、无单号时不拼空参数。这些是**源码 + Node 桩**证据；真实点击链路由沙箱内 Chromium 走完（付款 → 返回 → 已购按钮 → 手机视图），仍未做 Windows/真实商户验收。

## 2026-09-24 限流补齐（TD-275 / 复核 N-02）

`test_ratelimit.py` 新增一组：把 `RATE_LIMIT_DIAGRAM_WRITES` 调到 2，断言 `POST /diagrams`、`PUT /diagrams/{id}` **共用**同一个写入桶（第三次写入 429、Retry-After 是数字、文案含「频繁」）且读取（列表/打开）不受影响；把 `RATE_LIMIT_REGISTER_DAILY` 调到 2，断言第三次注册 429、文案说明是每日注册上限、Retry-After 大于 60 秒；另有两条按对端地址换身份的用例（同一 IP 用尽后另一个 IP 仍可注册/写入，IPv6 同 /64 共用配额）。这些用例在旧路由上先红。每日桶表独立于主桶表，所以另有一条断言：注册每日配额用尽后工具接口仍 200。

## 2026-09-25 真实浏览器复核回归（TD-278）

- `test_ui_accessibility.py`：`test_mobile_inputs_avoid_ios_zoom` 重写 —— 原来只查规则字符串存在，而规则实际被覆盖；现在钉住「16px 规则是 base 样式表最后一条、点名 `.modal input`」与「其他模板的 `<style>` 不给输入控件写 font」两个层叠前提。命中区用例的选择器随顶栏结构去掉 `.actions a`（顶栏右侧已无链接）。新增窄屏两行顶栏结构、shop 状态区先于「我的订单」、drawio 登录提示可整段切换；单 h1 渲染用例加入客服页、商店页与模拟收银台（`mock_mode` fixture）。
- `test_payments_frontend.py`：HARNESS 增 `empty-list` 与 `detail-visibility` 场景。
- `test_drawio_auth_state.py`：认证顺序用例同时断言 `#drawio-login-prompt` 在首屏/登录后/退出后的显隐。
- `test_auth_cookie.py`：Node 登录用例断言登录后密码框被清空、用户名保留。
- `test_second_frontend_regressions.py`：`support-scroll` 场景验证新消息的自动滚动与不打断阅读。

所有新增/改写断言都在改前的模板或脚本上失败（先红后绿），没有删除或放宽既有断言：被替换的两条字符串断言由更强的层叠/结构断言接替。

## 2026-09-25 ER 布局几何契约（TD-279）

- `test_er_page.py::test_layout_consumes_backend_payload`：原来钉死「x1 = 子表右边、y1 = 子表竖直中点、x2 = 父表左边」与「每行 3 张」—— 那正是被修掉的缺陷。改为 `_assert_geometry`：端点在外键列/被引用列那一行、折线只有水平/竖直段且每段都不进入任何表、最后一段水平进入父表、`path` 与 `points` 一致、表两两不重叠、宽度 160–340、截断文字以「…」结尾且是原文前缀、画布装得下；另断言父表在子表左边。节点顺序、连线集合与关键外键来源这几条原断言保留。
- 新增 `test_layout_edge_cases_keep_the_geometry_contract`（超长名字截断、自引用、a↔b 环、14 叶子星型图拆列并走车道）与 `test_initial_view_is_readable_instead_of_squeezing_the_whole_diagram`。

前两条在旧 `er-layout.js` 上失败（先红后绿）；被替换的坐标断言由更强的几何断言接替，没有跳过或删除用例。

## 2026-09-25 修改密码与 Mermaid 按需加载（TD-280）

- `test_ui_accessibility.py`：`test_password_dialog_changes_password_and_handles_every_outcome`（Node 真跑 auth.js 源码与产物：前端拦截、400 文案、成功请求体与清空、Esc 焦点归还、再次打开不残留、401 走到期）与 `test_password_dialog_markup_matches_the_backend_contract`。
- `test_frontend_supply_chain.py`：`test_mermaid_loads_on_first_generate_and_retries_a_failed_download`（页面加载 0 次 import、失败不缓存、成功复用、strict 不变）与 `test_mermaid_page_bundle_defers_the_renderer_and_preloads_from_static_js`（首屏闭包 < 20 KiB、预加载前缀只能是 /static/js/）；`test_mermaid_is_local_and_locked` 的字面量从静态 import 换成 `import("mermaid")`，并新增「不得动态 import 远程地址」。
- `test_llm_concurrency.py` 执行器把 `import("mermaid")` 换成桩；`test_ops.py` 的 gzip 用例改取产物里最大的文件（mermaid-page.js 已只剩几 KiB）。

新增用例均在改前的源码/产物上失败；替换的两处断言保持同等或更强的约束，没有跳过或删除用例。

## 2026-09-26 商城关闭页、历史二维码与下载 404（TD-281）

- `test_download.py::test_missing_product_file_404` 加强：404 detail 不得含对象 key、须含「购买权益未删除」，`codemax.shop` 日志里必须有 key。
- 新增 `test_download.py::test_order_history_draws_qr_only_for_pending_rows`：closed/paid/pending 三张单都带 weixin:// 码，历史接口只画 pending 那张，单查接口照旧有码。
- 新增 `test_ui_accessibility.py::test_closed_order_page_shows_its_order_number`；`test_shop_order_history_comes_after_every_status_section` 与 `test_shop_page.py` 的 Node 桩随模板去掉 `st-downloaded`（区段已删，不是放宽断言）。

## 2026-09-28 截图复核后的界面整理（TD-306）

`test_ui_accessibility.py` 末尾新增六条：textarea 默认正文字体且只有 `class="code"` 等宽（DDL 输入框点名、自然语言输入不点名、`support.css` 不再改字体）；统一控件规则用 `:where()`、不写字体、边框色 `--control-border` 在白底与 `#f8fafc` 上 ≥3:1；ER/类图按钮行在 `.form-actions` 里；商城订单号标题可换行；三个列表都用 `button.item` 并设/清 `aria-current`；Node 真跑客服页源码与产物（管理员未选会话时的标题、只有当前会话带 `aria-current` 且刷新后保留、消息时间「2026/09/28 09:49」、退出后游客按钮打开登录浮层）。改前的源码下这组用例会失败（标题、列表 class、时间格式都不同）；另把「中文时间」「刷新后保留标记」两处改回旧写法做过变异核对，用例随之变红。

## 2026-09-28 R-06 界面小问题（TD-307）

- `test_payments_frontend.py` 新增 `state-labels` 场景（源码与产物各一）：列表第一行全文、未知状态原样显示、合同区状态文字；渠道值 `constructor` 用来确认查表只认自有属性（把查表改成 `labels[code] !== undefined` 时用例变红）。
- `test_ui_accessibility.py` 新增 `test_login_dialog_marks_the_current_tab`（Node 真跑 auth.js 源码与产物，打开与切换后 `aria-pressed` 和标题同步）与 `test_mobile_nav_fades_at_the_right_edge_and_the_last_link_can_clear_it`（渐隐与占位只在窄屏段）。

## 2026-09-28 管理页写操作构造重构（TD-308）

- `test_payments_frontend.py` 新增 `review-change`（复核应答丢失后改说明再提交）与 `control-change-job`（核验控制结果未知后改选另一个任务）两个场景，源码与产物各一：都必须只发出一次请求并显示拒绝提示。它们补的是原有缺口：把复核比较里的「说明」、控制比较里的「任务」去掉时，此前没有用例失败。
- 控制场景的输出多了 `notice`（页面提示），复核场景多了 `message`；原有断言不变。

## 2026-09-28 模拟收银台失败提示（TD-309）

- `test_mock_pay.py` 新增 `test_mock_pay_page_failure_text_uses_the_shared_error_wording`：Node 假 DOM 跑页面脚本的源码与产物，errorText 从 auth.js 源码中取真实实现；五种应答（字符串 detail、校验错误数组、非 JSON 502、auth.js 缺席、401）断言提示全文、按钮恢复、返回链接隐藏、只发一次请求。原写法下前四种失败。

## 2026-09-28 账单解析按表头名取列（TD-311）

- `test_wechat_bills.py::test_strict_parser_rejects_bad_or_ambiguous_whole_file` 新增 `duplicate-payment-id`、`duplicate-refund-id`、`payment-coupon-refund`：只重复微信侧标识、付款行带充值券退款金额。原有的两个重复用例是整行相同，去掉任一标识的去重都不会失败。
- 新增 `test_column_numbers_come_from_unique_header_names`：表头名不重复，`TOTAL_COLUMNS` 正好指向 `AMOUNT_NAMES`。

## 2026-09-28 微信签名认证三处合一（TD-312）

- `test_wechat_notify.py`：`test_notify_rejects_duplicate_signature_header`、`test_notify_rejects_missing_signature_header`，对四个签名头各跑一遍。重复用例在改之前的代码上返回 200。
- `test_wechat_pay.py::test_signed_message_header_bounds`：四个头的长度上限边界。
- `test_audit_20260915.py::callback_stub` 改为替换 `shop.verify_signed_message` 一个入口，原来替换三个函数。

## 2026-09-28 可信代理地址段只解析一处（TD-313）

- `test_ratelimit.py::test_malformed_proxy_cidrs_trust_nobody_consistently`：畸形地址段下 HSTS/链接判定与限流身份一致地不信任代理；合法配置下跳过可信中间代理；XFF 链有畸形项时退回直接对端。
- `test_ops.py::test_malformed_trusted_proxy_cidrs_in_production_is_rejected`：四种写错方式在生产环境各报一条；`_clean_prod` 显式设置默认地址段。

## 2026-09-28 标识符里的 `$`（TD-314）

- `test_sql_ddl.py::test_dollar_sign_inside_identifiers_is_not_a_dollar_quote`：带 `$` 的列、外键引用、表名全部解析；隔空格的 `$q$…$q$` 仍是字符串。
- `test_payment_ledger.py::test_transaction_guard_rejects_real_wrappers` 新增 `a$x$; COMMIT; b$x$` 用例。
- `test_perf.py`：`$x$` 表名相关的两处说明更新，断言不变。

## 2026-09-28 前端错误文字（TD-315）

- `test_ui_accessibility.py::test_network_failures_are_shown_in_chinese`：Node 真跑 `auth.js` + `support-page.js`（源码与产物），覆盖三种浏览器的网络失败文字、脚本错误原样、登录表单、客服页读取与助手 422。
- `test_ui_accessibility.py::test_frontend_pages_never_show_raw_exception_text`：源码扫描护栏。
- `test_er_page.py::test_word_export_button_is_disabled_while_exporting`：源码断言导出期间禁用按钮。

## 2026-09-28 流程图页未保存改动（TD-316）

- `test_drawio_unsaved_changes.py`：Node 真跑 `drawio-page.js`（源码与产物），检查打开、新建、导入、移到回收站前的询问与取消，保存、下载、保存期间又改、打开失败、换账号时「未保存」的变化，以及 `beforeunload`。

## 2026-09-28 第二轮截图复核（TD-317）

- `test_ui_accessibility.py` 末尾五条：待支付态标题、标题字号阶梯（h2/h3 元素规则、模拟收银台与授权页不再写死字号）、空框隐藏、窄屏页脚行距规则的顺序、授权页回调地址可换行。`test_shop_order_number_in_headings_can_wrap` 改为钉住订单号在可换行的 `.muted` 行里。

## 2026-09-28 类图与 ER 页的错误提示（TD-318）

- `test_mermaid.py`：Node 桩 `_STAGE_HARNESS` 与四条用例，分清请求失败和渲染失败，渲染失败时只显示第一行提示、保留源码、清空预览；产物与源码一致；提示里的方位词与模板顺序一致。
- `test_er_page.py`：一条源码检查（页面脚本依赖 d3，不能用 Node 桩跑）。`test_frontend_supply_chain.py` 的 `initialize` 配置期望值加上 `suppressErrorRendering: true`，仍整体比较。

## 2026-09-28 删除流程图的加锁顺序与额度判定（TD-319）

- `test_diagram_concurrency.py`：保存确认所有权之后再发删除，删除排在保存之后（真 PostgreSQL 上改前失败）。
- `test_diagram_quota.py`：超额时别人的、已删除的、不存在的 id 都是 404，自己的图仍是 409。

## 2026-09-28 ER 图截断（TD-320）

- `test_er_page.py`：按定义核对 3000 个随机串的截断位置，并给 19900 字符的表名、列类型计时，上限 1.5 秒（旧实现 21.6 秒）。

## 2026-09-28 类图提示词与正文提取（TD-321 / TD-322）

- `test_mermaid.py` 的 `test_prompt_marks_keys_in_a_form_mermaid_displays`：提示词规则 4 必须给出 `+int id PK`、`+int userId FK`、`PK FK`，`<<` 只能出现在「不要用」之后。渲染效果是真 Chromium 实测的结论，CI 里只钉提示词。
- `test_extract.py` 的 `test_extract_content_keeps_every_text_exactly_once`：嵌套列表、pre、引用、h4、表格、`<br>`、注释、脚本、样式、noscript 混排的正文逐行核对；`test_extract_content_survives_nesting_deeper_than_recursion_limit`：嵌套深度为递归上限两倍时仍能取出文字。

## 2026-09-28 事件历史中文名称（TD-323）

- `test_payment_event_labels.py`（新）：从 `app/` 的语法树找出后端写入的全部事件种类（字面量、常量、导入常量、条件表达式、局部变量、辅助函数参数、`query_` / `refund_query_` 加 `wechat_pay` 允许的状态），与 `payments-admin.js` 的 `EVENT_LABELS` 双向比较；扫描器自检（六种写法、共 35 种）；措辞规则（非成功凭证不写「成功」，四种已验签非成功观察写明「不是成功」，名称不重复）；事件行 CSS 有 `pre-line`。
- `test_payments_frontend.py` 新场景 `event-labels`（源码与产物）：已登记种类显示「中文（原值）」，`constructor` 与未登记的新种类原样显示。
- 改前代码上：名称表两条、CSS 一条、两个 `event-labels` 场景都失败；另外人为改后端一个种类、删一个名称、把非成功观察写成「成功」，各自失败。

## 2026-09-28 事件种类读取守卫（TD-324）

- `test_payment_event_labels.py` 的 `test_every_kind_the_backend_reads_is_one_it_writes`：后端按字符串读取的事件种类（`x.kind == / !=`、`x.kind.in_(...)`，含导入的 `ISSUES` 与 `*` 展开）必须都是后端会写入的种类。人为把 `overdue_start` 的 `prepay_unknown` 拼错时现有相关用例全部通过，本条失败并指出文件和行。

## 2026-09-28 第三轮截图复核（TD-325）

- `test_ui_accessibility.py` 的 `test_visible_text_has_no_emoji_only_characters`：模板与前端脚本里用户看得见的部分（去掉注释）不许出现默认以 emoji 显示的字符。`test_empty_support_thread_leaves_no_gap_but_stays_a_live_region`：空留言列表只收外边距、不隐藏，模板里 `<ol>` 内部没有空白。两条在改前的文件上都失败。

## 2026-09-28 第四轮截图复核（TD-326）

- `test_mermaid.py`：执行器支持逗号分隔的多步提交（ok / parse-error / offline / 502 / html200），假 DOM 按模板初始 hidden、记录属性。新增成功后失败调暗并说明（三种失败）、下次成功恢复、首次失败不说过时（含 200 非 JSON 不打开空白源码框）、画不出来时不标过时；产物检查加上新文案与 `data-stale`。
- `test_er_page.py`：新增页面装配执行器，把 `er-page.js` 的 import 换成 d3 链式桩与布局桩后在 node 里执行；覆盖成功后失败调暗并说明（DDL 有误、断网）、下次成功恢复、首次失败不说过时、绘制失败清空画布回到空状态；另有产物检查与 `data-stale` 样式覆盖三个元素的检查。
- `test_ui_accessibility.py`：管理员空会话（执行真实 `auth.js` + `support-page.js`）、空状态元素属性与位置、`button.item` 的 keep-all 与 overflow-wrap、`select` 的 max-width、Drawio 行内按钮为描边。
- 改前代码上 15 条失败；「首次失败不说过时」是守卫，改前也通过。

## 2026-09-29 重构复核（TD-327）

- `test_config_validation.py`：`ILLEGAL` 加 `ALGORITHM` 的 `hs256` / `RS256` / `none`；新增 `test_every_allowed_jwt_algorithm_signs_and_verifies_with_the_secret`，白名单每个取值都用字符串密钥实际签发并校验令牌（取值来自 `get_args`）。
- `test_dynamic_crawl.py`：`test_launch_failure_is_translated_not_leaked` 改名 `test_installed_playwright_still_reports_rendering_disabled`，假 `async_playwright` 被调用即报错，钉住停用时不启动浏览器；缺包用例另断言提示写明已停用、安装后也不能使用。
- `test_db_admin.py`：新增 `test_bootstrap_admin_cli_rejects_bad_credentials_before_connecting_and_names_the_field`（三种不合规凭据，连库替换成直接判失败，报错点名字段、不含口令）与 `test_validate_admin_credentials_keeps_the_12_character_minimum`。
- 改前代码上 8 条失败；「不启动浏览器」是守卫，改前也通过。

## 2026-09-29 DDL 列名折叠（TD-328）

- `test_sql_ddl.py`：`test_table_level_primary_key_matches_columns_by_folded_name`（表级 `PRIMARY KEY (ID)` / `(id ASC)` 对上列 `id`，带引号的 `"ID"` 与 `id` 仍是两列）、`test_foreign_key_columns_are_reported_as_defined`（表内、列级、ALTER 三条外键路径的列名都是定义时的写法；带引号父列与父表缺失时保留原写法）、`test_comment_on_targets_fold_case_and_schema_like_references`（COMMENT ON 的表与列按折叠和 schema 对上，带引号的定义只认原样）。
- 改前代码上 3 条全部失败。

## 2026-09-29 退款通知标识格式（TD-329）

- `test_refund_notifications.py::test_summary_maximum_and_malformed_display`：另用含全部允许特殊字符的通知 ID 与退款单号（`|*@`）做一次入库→读回。守卫性质：改前改后都通过；只把读回那份格式改窄时，只有这条新断言失败。
