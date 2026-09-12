# 测试策略与运行指南

Windows + conda 的环境核对、无 `.env` 验收副本、SQLite/真实 PG 和覆盖率命令见 [本机测试指南](../docs/WINDOWS_CONDA.md)。浏览器和外部服务另按 [验收手册](../docs/ACCEPTANCE_GUIDE.md)；不要把 Node VM 当成真实浏览器。

## 模块职责

测试回答分层问题：纯函数验证算法，HTTP 集成验证依赖/权限/异常映射，真实 PostgreSQL 验证数据库语义，Node 验证浏览器脚本生命周期，文档测试验证提取/链接/结构。
并非“一律走 HTTP”，也并非“全部测试都不打真实数据库”。测试数量按具体提交记录在验收报告中，不复制固定函数数或历史耗时当当前指标。

## 文件与入口

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
| [`tests/conftest.py`](conftest.py) | `c70cb44c495d` | L1–L212 |
| [`tests/test_admin_ingest.py`](test_admin_ingest.py) | `9b6e6799819e` | L1–L345 |
| [`tests/test_auth.py`](test_auth.py) | `81d2a2d26326` | L1–L67 |
| [`tests/test_auth_cookie.py`](test_auth_cookie.py) | `173f70aa70a9` | L1–L327 |
| [`tests/test_auth_crypto.py`](test_auth_crypto.py) | `ae02f0e03c7a` | L1–L180 |
| [`tests/test_code_reading.py`](test_code_reading.py) | `8e48adefb347` | L1–L142 |
| [`tests/test_config_validation.py`](test_config_validation.py) | `13ec12dfa2ec` | L1–L180 |
| [`tests/test_crawler.py`](test_crawler.py) | `22cd77a5bd4a` | L1–L344 |
| [`tests/test_diagram_concurrency.py`](test_diagram_concurrency.py) | `2a44f9d2a7ea` | L1–L147 |
| [`tests/test_diagram_quota.py`](test_diagram_quota.py) | `6a4613493dd1` | L1–L153 |
| [`tests/test_diagrams.py`](test_diagrams.py) | `d0e3630e1695` | L1–L119 |
| [`tests/test_docs_contract.py`](test_docs_contract.py) | `a6d61f682988` | L1–L98 |
| [`tests/test_docs_site.py`](test_docs_site.py) | `b5811f75840a` | L1–L418 |
| [`tests/test_download.py`](test_download.py) | `0a05c879945a` | L1–L247 |
| [`tests/test_drawio_auth_state.py`](test_drawio_auth_state.py) | `15736019e19b` | L1–L56 |
| [`tests/test_dynamic_crawl.py`](test_dynamic_crawl.py) | `d764399a1b53` | L1–L350 |
| [`tests/test_e2e.py`](test_e2e.py) | `c31229ccb07d` | L1–L532 |
| [`tests/test_er_page.py`](test_er_page.py) | `85003015e498` | L1–L176 |
| [`tests/test_extract.py`](test_extract.py) | `550c6f7a3db7` | L1–L240 |
| [`tests/test_faq.py`](test_faq.py) | `8e9cf7ac294c` | L1–L131 |
| [`tests/test_faq_semantic.py`](test_faq_semantic.py) | `9c4111f977a0` | L1–L605 |
| [`tests/test_frontend_supply_chain.py`](test_frontend_supply_chain.py) | `5238ce5719c4` | L1–L207 |
| [`tests/test_intent_cascade.py`](test_intent_cascade.py) | `56e85b17742f` | L1–L213 |
| [`tests/test_manual_pay.py`](test_manual_pay.py) | `de4540b98674` | L1–L323 |
| [`tests/test_mermaid.py`](test_mermaid.py) | `5a961a7ab99e` | L1–L183 |
| [`tests/test_mock_pay.py`](test_mock_pay.py) | `96bc978f5dc2` | L1–L157 |
| [`tests/test_oauth.py`](test_oauth.py) | `b17f166c15be` | L1–L248 |
| [`tests/test_oauth_consent.py`](test_oauth_consent.py) | `4005b0b271f0` | L1–L199 |
| [`tests/test_ops.py`](test_ops.py) | `489ffbede4ec` | L1–L539 |
| [`tests/test_order_state.py`](test_order_state.py) | `7deb8e28ef91` | L1–L134 |
| [`tests/test_perf.py`](test_perf.py) | `75404eeca36d` | L1–L360 |
| [`tests/test_politeness.py`](test_politeness.py) | `a50a1f27f425` | L1–L284 |
| [`tests/test_proxy_headers.py`](test_proxy_headers.py) | `f99e631e1fc2` | L1–L110 |
| [`tests/test_ratelimit.py`](test_ratelimit.py) | `7103160ca474` | L1–L152 |
| [`tests/test_review_regressions.py`](test_review_regressions.py) | `344be2cad4cf` | L1–L124 |
| [`tests/test_schema_sync.py`](test_schema_sync.py) | `ea1200feb254` | L1–L74 |
| [`tests/test_second_frontend_regressions.py`](test_second_frontend_regressions.py) | `642952b432cc` | L1–L86 |
| [`tests/test_second_review_regressions.py`](test_second_review_regressions.py) | `2b2173f2571c` | L1–L340 |
| [`tests/test_shop_page.py`](test_shop_page.py) | `0a36d6860d03` | L1–L455 |
| [`tests/test_shop_polling.py`](test_shop_polling.py) | `76a98525c2db` | L1–L179 |
| [`tests/test_site.py`](test_site.py) | `cf8e184736a2` | L1–L65 |
| [`tests/test_sql_ddl.py`](test_sql_ddl.py) | `d22443181e2b` | L1–L254 |
| [`tests/test_support.py`](test_support.py) | `9cd0e6ef02c4` | L1–L240 |
| [`tests/test_support_messages.py`](test_support_messages.py) | `de7599c20fee` | L1–L113 |
| [`tests/test_support_rag_perf.py`](test_support_rag_perf.py) | `2e06151a09d2` | L1–L202 |
| [`tests/test_token_revocation.py`](test_token_revocation.py) | `4530ff5f9bd3` | L1–L237 |
| [`tests/test_username_validation.py`](test_username_validation.py) | `29a23292f4df` | L1–L109 |
| [`tests/test_wechat_notify.py`](test_wechat_notify.py) | `4bc1bbdf4524` | L1–L452 |
| [`tests/test_wechat_pay.py`](test_wechat_pay.py) | `51a720468300` | L1–L279 |
| [`tests/test_word_export.py`](test_word_export.py) | `d7c7c66103db` | L1–L92 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

模型请求与抓取通常注入本地替身；要在报告中区分“执行了本地真实代码”和“调用了真实外部服务”。pytest 参数化会把一个函数展开成多个用例，所以函数数不等于 collected 数。
模块级配置、缓存、节流与 dependency_overrides 都需要清理。不要为了通过测试删除隔离断言、恢复有风险的动态出网，或把数据库 skip 改成伪造成功。

### 精读机制回归

`test_code_reading.py` 用隔离文件验证分段连续性、旧指纹即使主清单刷新后仍失败、未知/重复文件、非法段界、漏尾、占位文字、生成/空文件不能冒充精读、未补项可见和输出转义。另验证 docs CLI 在数据模式也拒绝过期说明。
它不会给解释内容自动判真；业务含义仍需对照源码和对应业务回归。本批 `test_support_messages.py` 另有逐段造数/断言/边界说明，其他测试不会因为在清单中就标成精读完成。

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
