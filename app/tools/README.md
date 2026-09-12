# 工具算法、模型调用与检索

## 模块职责

本目录负责 SQL/Word、HTTP 抓取与提取、FAQ/意图/检索编排。不是所有业务都在这里：订单、认证和站内会话的事务分别位于公共层或 routers。
这些链共享 `llm.py`；`crawler` 与 `politeness` 有延迟导入关系，不能称为“完全独立”。本层主要抛领域异常，HTTP 状态码由路由映射。

## 文件与入口

### SQL 与 Word

| 函数 | 输入 → 输出 | 算法与边界 |
| --- | --- | --- |
| `parse_ddl(sql)` | 建表 SQL → `{tables, edges}` | 不执行 SQL；常见 MySQL/PostgreSQL 子集，不是完整语法验证器；没有可识别表时返回空图，由 HTTP 层报 400 |
| `_scan` / `_strip_comments` / `_in_string_positions` | 原始 SQL → 词法状态、去注释文本或字符串位置 | 区分引号、转义与注释；先屏蔽字面量再判断约束，不能把 DEFAULT 字符串中的 PRIMARY KEY 当约束 |
| `_iter_tables` / `_read_balanced` / `_split_top_level` | SQL 或表体 → 表块或顶层字段片段 | 按括号层级与引号分割，不按所有逗号直接 split；类型参数和字符串内逗号必须保留 |
| `_parse_table` / `_parse_column` / `_parse_constraint` | 字段片段 → 列、主键标记、外键边 | 同时处理列内与表级约束；复合外键展开为字段配对边，不是完整关系约束模型 |
| `_identifier_parts` / `_qualified` / `_short` | SQL 标识符 → 组成部分 / 展示标签 | 表的内部映射使用 tuple，区分带字面点号的引号名称与 schema 分隔；同名表需要消歧 |
| `_apply_comments` / `_unquote` / `_unescape` / `_paren_list` | SQL 注释与标识符片段 → 对应表列说明 | COMMENT ON 要匹配正确 schema；无法找到的显式引用不冒认成另一张短名相同的表 |
| `build_data_dictionary(graph)` | parse_ddl 形状的图 → DOCX bytes | 以表/字段生成数据字典；只在导出表示中移除 XML 禁止的控制字符；不在这里做完整图 schema 验证 |

`word_export` 路由先做 20,000 字符输入限制、100 表/合计 400 字段限制，再提交 CPU 任务。直接调用 `build_data_dictionary` 的脚本不自动获得 HTTP 限流或任务预算。

### LLM 协议

| 入口 | 契约 | 失败与注意事项 |
| --- | --- | --- |
| `LLMClient.chat(system, user)` | OpenAI 兼容 chat/completions → 非空字符串，最多 100,000 字符 | 缺 key、网络/HTTP 异常、响应结构错误抛 LLMError；不证明模型内容事实正确 |
| `LLMClient.embeddings(texts)` | 文本列表 → 与输入顺序对应的向量列表；空输入返回 [] | 检查条数、index 完整唯一、维度一致、非空及有限数值；不允许错位向量进入检索 |
| `get_llm()` | 返回默认客户端，供 FastAPI 注入 | 默认客户端在导入时从 settings 构造；运行中改 settings 不会自动重建它；测试用 dependency_overrides |
| `generate_mermaid` / `_strip_fence` | 用户描述 → 去围栏的 Mermaid 文本 | 首关键字检查不是完整 Mermaid 解析；实际渲染仍可能报语法错。前端 strict 模式也不能替代后端响应结构校验 |

兼容 API 需要分别确认对话与 embedding 模型，不能承诺任意供应商只改 URL 就能工作。密钥不得进入日志或前端。

### 静态抓取、礼貌策略和骨架

| 入口 | 输入 → 输出 | 边界 |
| --- | --- | --- |
| `assert_public_url(url)` | URL → 校验通过，无返回值 | 检查 scheme、端口、主机及解析地址；违规抛 CrawlError。一次预检查不能取代实际连接目标控制 |
| `PublicTransport.handle_async_request` | HTTPX Request → Response | 连接到检查过的数值 IP，保留原 Host 与 TLS SNI；不使用环境代理，不用跨主机 keep-alive；可注入的测试 transport 不代表生产网络实测 |
| `_request` | URL、transport、字节上限 → HTTPX Response | 手动逐跳验证重定向、按解压后字节限量；重建响应时移除已消费的压缩/长度头，避免二次解码 |
| `fetch` | URL → Page（最终 URL、HTML 等） | robots → 域节流/进程内并发闸 → 请求；非成功页与不支持的内容拒绝；不执行 JavaScript |
| `to_skeleton` | HTML → 有界文本 DOM 骨架 | 迭代遍历；默认最多 1000 节点、深度 32、输出 32,000 字符，并限制单段文本/属性；给模型结构提示，不等于已提取正文 |
| `check_allowed` / `_load_robots` | URL、UA、注入抓取函数 → 判定或 RobotsDisallowed | 404/410 视为没有 robots 限制；401/403、暂时不可知等保守拒绝。复用同一受约束请求路径读取 robots |
| `state_for` / `min_interval_for` / `throttle` | 域状态 → 最小间隔或等待 | 状态按 scheme/netloc 分组；锁内更新上次请求时间；优先站方有效 Crawl-delay，否则默认间隔 |
| `_get_semaphore` / `reset_cache` | 进程内并发闸／清理测试状态 | 不是跨服务全局限速；域缓存无硬性 LRU 容量上限，不应描述成无限规模抓取系统 |

**动态浏览器当前停用。** `browser_available()` 只检查可否 import Playwright，不代表功能可用；`render()` 保留预检查和返回 Page 的接口，但生产 `_goto()` 会抛 BrowserUnavailable。安装浏览器不能解除这一限制。`_abort_non_public` 是保留的防御 helper，不能据此宣称浏览器已有网络隔离。恢复前需要专门的隔离出口设计与真实浏览器验证。

### 文章提取与入库

| 入口 | 输入 → 输出 | 失败、写入与约束 |
| --- | --- | --- |
| `identify_selectors` / `_parse_selectors` | DOM 骨架或模型回复 → title/author/published_at/content 选择器字典 | 解析 JSON/围栏并验证键和值；模型调用错包为 ExtractError，保留 LLMError 原因以便路由映射 502 |
| `extract_fields` | 原始 HTML + 选择器 → 字段文本 | BeautifulSoup 选择器提取真实页面文本，不让模型直接编正文；选择器无效或必需字段为空抛 ExtractError |
| `parse_page` | 最终 URL、HTML → ParsedArticle | 骨架 → 选择器 → 文本；补 source_site，保留来源日期字符串；不写库 |
| `parse_article` | URL → ParsedArticle | 先 fetch 再 parse_page，使用重定向后 URL；有网络/模型副作用但不提交事务 |
| `save_article` | 会话、ParsedArticle → Article | URL 原子 upsert；同 URL 更新原行，保留原创建时间；自己 commit 并重新查询，不适合直接嵌入调用者更大原子事务 |

保存前拒绝空字符以及超长 URL(500)、标题(300)、作者(100)、日期(50)、域名(200)；单位为 Python 字符，不悄悄截断原文。生产 PostgreSQL 与 SQLite 测试都执行同一校验；HTTP 路由把这类 ExtractError 映射为 422。其他数据库故障不伪装成正常提取失败。

### FAQ、意图与回答编排

| 入口 | 返回与排序语义 | 限制与回退 |
| --- | --- | --- |
| `tokenize` / `_corpus_tokens` | jieba 分词；把问题、关键词、答案组成索引语料 | 词袋索引在导入时建立，改 FAQ 源码需重新加载/部署，不是数据库在线编辑器 |
| `_Index.bm25` / `cosine` | 原始 BM25／TF-IDF 余弦分量 | BM25 无固定上界；余弦使用相同加权空间的模长；不能把排序分当概率 |
| `search(query, k)` | Top-k FaqHit，含 score 和 confidence | score 是当前候选集的融合排序分，不可跨查询比较；confidence 是绝对启发式置信分，也不是统计校准概率 |
| `_normalize` / `_saturate` | 相对归一化／有界饱和值 | 分别服务排序与置信度，不能混用阈值 |
| `semantic_threshold` / `calibrate_threshold` | 当前配置阈值／正负样本分数的分隔值 | 空样本、分布重叠或安全间隔不足拒绝计算；默认经验值未代替真实模型标定 |
| `_semantic_key` / `warm_semantic_index` | 提供方、凭据摘要、模型、transport、语料绑定的向量缓存；成功 bool | 预热客户端超时最多 5 秒；模型失败回退词袋；先验证完整向量再发布，不把半个索引提供给查询 |
| `semantic_ready` / `semantic_search` | 是否有索引／语义 Top-k；不可用 None | ready 不证明任意客户端/语料都兼容；查询持有一致快照，等待期间缓存换代则回退；None 与“有结果但不相关”不同 |
| `reset_semantic_index` | 清空语义向量、模长、键 | 用于测试/显式失效，不负责重建词袋索引 |
| `RuleIntentRouter.classify` / `llm_classify` | IntentResult（FAQ/闲聊/专业、置信度、理由） | 确定性规则先行；LLM 只做分类，低信心或协议错误不能变成高置信硬答 |
| `_second_opinion` / `_log_labeling_sample` | 低置信规则的语义/模型复核，及标定样本日志 | 只在需要时调用模型；样本日志不是人工客服会话，运营应控制访问与保留期限 |
| `_retrieve_articles` / `_build_index` | 相关标题与正文片段；无相关内容 []，查询失败 None | 读取文章内容并计算指纹，跨进程修改可见；不是增量向量数据库，全文读取/哈希仍有成本 |
| `reset_article_index` | 清除进程内文章检索缓存 | 测试隔离；文章持久数据不删除 |
| `answer` / `_escalate` | SupportReply：答案、意图、来源、置信分、引用、escalated/reason | 规则 → 必要时第二意见 → FAQ/LLM/RAG；低置信、资料缺失或模型失败给人工入口，不自动创建工单、派单或通知管理员 |

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/tools/__init__.py`](__init__.py) | `e3b0c44298fc` | 空文件（无源码行） |
| [`app/tools/browser.py`](browser.py) | `b669596ec90a` | L1–L97 |
| [`app/tools/crawler.py`](crawler.py) | `d90c8f402324` | L1–L285 |
| [`app/tools/extract.py`](extract.py) | `bf983e6a4cb0` | L1–L173 |
| [`app/tools/faq.py`](faq.py) | `92d4a215ceb2` | L1–L392 |
| [`app/tools/intent.py`](intent.py) | `0d9c64c5c6ab` | L1–L182 |
| [`app/tools/llm.py`](llm.py) | `b8c288c5b3cb` | L1–L156 |
| [`app/tools/politeness.py`](politeness.py) | `253d12854aa5` | L1–L183 |
| [`app/tools/sql_ddl.py`](sql_ddl.py) | `0919561c4417` | L1–L336 |
| [`app/tools/support.py`](support.py) | `f60ce5802d2f` | L1–L319 |
| [`app/tools/word.py`](word.py) | `3359cd1776a4` | L1–L62 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

模型回复和抓到的网页都不是可信业务指令。解析成功不等于内容准确；RAG 只说明使用了材料，不保证回答无幻觉。
确定性 FAQ 与已登录站内人工会话不依赖真实模型；无密钥时语义增强回退，Mermaid/模型解析功能不可用。静态抓取与动态渲染的上线状态必须分开写。

## 变更与验证

算法变更优先纯函数测试，协议变更用 MockTransport，异常映射另跑 HTTP 测试。重点文件：`test_sql_ddl.py`、`test_word_export.py`、`test_crawler.py`、`test_extract.py`、`test_faq_semantic.py`、`test_support.py`、`test_second_review_regressions.py`。
从仓库根执行 `python -m pytest tests/test_admin_ingest.py tests/test_second_review_regressions.py -q` 验证入口边界；真实模型、支付和浏览器联调不能用 mock 通过来替代。
