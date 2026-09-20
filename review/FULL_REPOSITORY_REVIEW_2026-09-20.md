# 全仓独立复核（接手复核 H-02）与优化清单

**日期：2026-09-20 · 复核基线：`ea11619bc5c11ce82aef82bcfe5e2411303a30b4`（远端 `arena/01a08bf5-codemax-platform` 当时的 tip） · 执行环境：Linux 沙箱、Python 3.11.2、Node 22.22.3、SQLite 默认测试库**

本报告是接手助手对 [2026-09-19 全仓审计](FULL_REPOSITORY_HANDOFF_2026-09-19.md) 的**独立复核**，加上一轮覆盖全部已跟踪文件类型的自查。它是可反驳的证据，不是安全认证、法律意见或收款上线许可。当前待办只在 [ROADMAP](../ROADMAP.md)；恢复/发布核验只在 [HANDOVER](../HANDOVER.md)。本报告本身不修改任何运行逻辑、依赖、schema 或历史 SQL；第 7 节的修复提案要经用户确认后分批实施。

## 1. 结论摘要

1. **上轮审计的六项诊断全部独立复现**（SQLite，`6 passed`，3.17 秒），F-01～F-05 评级**同意**，未发现需要反驳的结论；细节和本轮补充的调用链观察见第 3 节。
2. **新增一项 F-09 / P2**：进程内限流器 `Limiter` 满桶（16384 键）后会拒绝**所有新客户端**，而且仓库里没有任何 `prune()` 调用方。合成时钟实测：约 820 个不同来源地址 × 20 个限流 scope 就能填满桶，并以每键每 55 秒一次的极低频率长期维持；之后所有新 IP 的登录/注册/工具/下载入口持续 429。这是上轮第一批修复（"满时拒绝新 key 而不驱逐活跃配额"）的直接副作用，属于**可用性拒绝服务**，不是越权。
3. **代码与文档基本一致**。逐项对照路由表、环境变量、CLI 参数、测试文件引用、迁移编号、模块 README 计数后，只发现两处明确失真：TD-214 关于 RAG 缓存指纹的描述（上轮 F-07 已指出）与 TD-217 把 `AUTH_CODE_EXPIRE_MINUTES` 写得像环境变量（它是 `app/routers/oauth.py:25` 的模块常量）。均为文字修正，不影响运行。
4. **UI/UX 为静态审阅**（沙箱无浏览器/jsdom）：字号体系合理（正文 14px/1.6、代码 12–13px、商品价格 34px、按钮 16px），布局有断点，表单有 `label`/`maxlength`/`autocomplete`。主要改进点是**对比度**（链接/按钮蓝 `#3b82f6` 3.68:1、CTA 绿 `#16a34a` 3.30:1、提示灰 `#94a3b8` 2.56:1，均低于 WCAG AA 的 4.5:1）、登录浮层缺少 `role="dialog"`/Esc 关闭/焦点归还、`:disabled` 无视觉态、`support.css` 的 `:has()` 无回退。均为低风险小改。
5. 本轮 Ruff、pip check、文档契约、Vite 漂移、精读 SHA、全量 SQLite 套件均**通过**（第 6 节精确数字）。真实 PostgreSQL 全量由 CI job 覆盖；基线 ea11619 的六项 CI 已核实全部 success（run 35471274891）。本轮未执行真实商户、Windows、浏览器、Docker 部署或真实模型请求。

## 2. 复核范围与方法

基线 `git ls-files` 共 **362 个已跟踪文件**（.py 135、.js 109、.md 65、.sql 19、.html 10、.json 5、.yml 3、.css 2、.mjs 2、.txt 2、无扩展名 5、其他 5）。

| 类别 | 本轮做法 | 没有做的 |
| --- | --- | --- |
| Python 应用与脚本 | `app/` 顶层 30 个模块（含 `bill_reconcile`/`refund_worker`/`refund_health`/`db_admin` 四个 CLI）、`app/routers/` 14 个、`app/tools/` 11 个、`main.py`、`scripts/` 4 个 Python 脚本、`database init/db_init.py` **逐文件通读**（`faq.py`/`db_admin.py`/`refund_verification.py`/`refunds_admin.py` 为重点段落而非整文件）；额外跑了扩展 Ruff 规则集（S/B/ASYNC/DTZ/PERF/RUF/SIM/PLW/PLE/C4）做线索筛查 | 不逐行认证 ~18.5k 行测试代码；测试按文件清单、`skipif` 与关键 fixture 核对 |
| 前端 | `app/frontend/*.js` 9 个手写源码全部通读；`app/static/js/` 99 个产物重建比对零漂移 | 不逐行审计压缩第三方代码（mermaid/d3） |
| 模板与样式 | 10 个 Jinja 模板、`support.css`、`docs/site/style.css` 全部通读；字号/断点/对比度（WCAG 相对亮度公式计算）/ARIA 静态检查 | 无真实浏览器渲染、无键盘/读屏实测 |
| Markdown | 现行入口（HANDOVER/ROADMAP/AGENTS/README/总览/docs/*/模块 README/review 索引/manager）全部读；用脚本对照：文档中出现的路由路径 ↔ 运行时路由表、`Settings` 字段 ↔ `.env.example` ↔ 文档、CLI `--参数` ↔ argparse、`tests/*.py` 与 `scripts/*` 路径引用 ↔ 磁盘、活跃计数（63 路由/296 文件/191 精读/0017）| 历史报告（阶段 2–17、9 月 15 日原稿）按其日期理解，不改写 |
| SQL / 数据库 | 核对 `migration_manifest` 连续 0001–0017、`verify_ledger` 校验和逻辑、`adopt_legacy` 前置列/索引检查 | 未在 PG 上重放迁移链（CI PG job 与 `test_db_admin.py` 覆盖） |
| 配置 / CI / 容器 | `ci.yml` 六 job、`agnes-connectivity.yml`、Dockerfile（python:3.11-slim、`--no-access-log --no-proxy-headers`）、compose（`TRUST_PROXY_HEADERS=true`、端口仅 127.0.0.1）、`.gitignore`/`.dockerignore` | 未实际 `docker compose up` |
| 依赖 | `pip check` 通过；`requirements.txt` 注释中的版本理由逐条核对；`npm audit` 由 CI 覆盖 | 未做新的 pip-audit（CI audit job 覆盖同一 SHA） |

## 3. 上轮六项诊断的独立复现（H-02）

复现命令：`.venv/bin/python -m pytest -c pytest.ini tests/audit_handoff_probes.py -q -s -p no:cacheprovider`。全部 PASS 表示**复现了待修行为**，不是修复完成。

| 编号 | 本轮观测（2026-09-20，SQLite） | 判定 | 调用链复核补充 |
| --- | --- | --- | --- |
| F-01 分块大请求体 | `/tools/mermaid` 无 Content-Length 分块 2 MiB：`status=422 consumed=2097163 response=2097303` | 同意 P1 | `main.py` 无任何请求体大小中间件；只有支付/退款回调各自有 64 KiB 流式预算。前端 `auth.js::errorText` 只拼 `detail[].msg`，所以浏览器不会把大 `input` 渲染出来，但 API 客户端会收到完整回显 |
| F-02a LLM 无关字节 | `LLM accepted ignored bytes=2097207` | 同意 P1（启用模型时） | `LLMClient.chat/embeddings` 用 `client.post` 后 `resp.json()`，无响应体上限；`content` 的 100000 字符上限发生在整包解析之后 |
| F-02b 深嵌套 JSON | `nesting=1200` → `RecursionError` 逃出 | 同意 | 调用方（`routers/tools.py`、`tools/support.py`）只捕获 `LLMError`，RecursionError 会变成 500 |
| F-03 同单双预支付 | `concurrent calls=2 order_count=1 receipts=0`，`RATE_LIMIT_TOOLS=1` 仍如此 | 同意 P2 | `create_order`：`lock_user` 所在事务在 `commit()` 时释放，之后才 `await native_prepay`（顺序本身是对的，见 review 第一批"预支付早于本地持久提交"）；`POST /shop/orders` 没有限流依赖（商城只有 `download` 有） |
| F-04 跨站表单登录 | `status=200, auth-cookie-issued` | 同意 P2（浏览器 PoC 待做） | `login` 没有来源检查；Cookie `SameSite=Lax`、生产 `Secure`。财务写入口已有 `require_finance_origin`（`Origin` + `Sec-Fetch-Site`，Bearer 客户端豁免），可直接复用同一模式 |
| F-05 embedding index 类型 | `EMBED accepted index type=bool/float` | 同意 P3 | `embeddings()` 只做排序后与 `range()` 等值比较；`False == 0`、`0.0 == 0` 成立 |

上轮 F-06（下载 GET 每次全量哈希、无限流）、F-07（TD-214 指纹描述与代码不符）、F-08（schema 同步只比表/列/时区）经源码复核**均成立**，见第 5 节的处理建议。

## 4. 本轮新增发现

### F-09 / P2：限流器满桶后拒绝所有新客户端，且没有任何清理调用方

- 来源：`app/ratelimit.py::Limiter.allow`（`max_keys=16384`；`if len(self._hits) >= self.max_keys: return False, 1`）。`Limiter.prune()` 在 `app/` 内**零调用**（仅 `tests/test_ratelimit.py:93`）。`allow` 的轮转维护每次最多检查 32 个键，只回收 `expires <= now` 的键。
- 键的构成：`f"{scope}:{client_key(request)}"`，当前 20 个 scope。**未登录请求也占键**：本轮 ASGI 合成验证，5 个来源地址各打 3 条需登录/管理员的路由（结果 401/401/422）→ 限流器出现 15 个键。填桶不需要账号。
- 复现一（瞬时）：合成时钟下 16384 个不同 IPv6 地址各命中一次 `auth` scope，耗时 0.104 秒；随后新地址 `login` 请求 `allowed=False, retry_after=1`；60 秒后才恢复。
- 复现二（持续）：820 个地址 × 20 scope = 16384 键；之后每 55 秒把每个键各刷新一次（远低于每键 10 次/60 秒的配额，所以自身永不 429），连续 5 个窗口新客户端 `login allowed=False`。也就是说攻击方只需要 **≈ 5 请求/秒** 的稳定流量即可长期封锁全站新客户端。
- 放大因素：IPv6 单机可用 /64 内任意地址；`client_key` 不做 /64 归并。`TRUST_PROXY_HEADERS=true` 时以 XFF 最右侧非可信地址为键，同样可被轮换。
- 已有缓解：`Retry-After: 1` 让合法客户端很快重试，但只要攻击维持，重试仍会失败；反向代理自身的连接限制不在仓库内。
- 不是：越权、资金、跨用户数据问题；也不是"限流器有二次复杂度"（上轮已修）。
- 建议/验收（对应 ROADMAP 新增 A-07）：① 满桶时**优先淘汰已过期键**（O(1) 摊销，用 `_keys` 队列头部即可），仍不淘汰活跃键；② 对 IPv6 按 /64（可配置）归并作为限流身份；③ 桶满时对新键的响应改为可区分的原因并记 warning 日志，便于发现攻击；④ 可选：按 scope 分桶上限，避免一个 scope 拖垮 `auth`。保护性回归：满桶且存在过期键时新键必须放行；满桶且全活跃时仍拒绝（保留上轮反例）；/64 内两个地址共享配额；`NoScan` 断言继续成立。

### 文档/注释失真（低）

| 位置 | 现状 | 应改为 |
| --- | --- | --- |
| `TECH_DECISIONS.md` TD-214 | "RAG 索引按 `(count, max(id))` 指纹缓存复用"，并在"代价"栏说"将来若加了编辑文章正文的功能必须给表加 `update_time`" | 当前代码 `app/tools/support.py::_retrieve_articles` 对全部 `(id, title, content)` 做 sha256 指纹，`app/tools/README.md:78` 与代码一致；应在 TD-214 追加一句"后续改为全文内容指纹，代价是每次仍需读全表正文"，不改写原始记录 |
| `TECH_DECISIONS.md` TD-217 | "`AUTH_CODE_EXPIRE_MINUTES=10` 就作废" 读起来像 `.env` 项 | 注明它是 `app/routers/oauth.py` 常量，不是 `Settings` 字段（`.env.example` 也确实没有它） |
| `app/routers/shop.py::_payload` | 关键字参数 `pay_mode` 在函数体第一行被 `order.payment_mode or 'legacy'` 覆盖，五处调用方传的值都是死参数 | 删除参数或改名为 `_`；纯清理，行为不变 |

### 其他源码观察（不构成缺陷，供优化时参考）

- `app/tools/crawler.py:165` 用裸 `assert r is not None` 做控制流保护；`python -O` 下会消失。建议改为显式 `if ... raise CrawlError`。
- `app/wechat_bills.py:143` 使用 SHA1 比对账单摘要——这是微信账单接口自身的合同（`hash_type=SHA1`），不是弱哈希误用；保留但可加 `# noqa: S324` 说明。
- 扩展 Ruff 集显示 15 处已无效的 `# noqa`（RUF100）和 16 处模块级 `global`（PLW0603，均为进程内缓存/单例，符合 TD-141 单实例前提）。可在维护批次清理，不改行为。
- `/support/ask`（智能客服 RAG 入口）当前没有任何前端页面调用，只有 Windows 验收文档通过 `/docs` 手工调用；它不鉴权、只有 `RATE_LIMIT_LLM` 限流。若短期不接入 UI，可考虑在生产要求登录或明确保留为公开演示端点（产品决策，见 ROADMAP）。
- `app/tools/faq.py` 在导入时初始化 jieba（约 0.5 秒），发生在应用启动而非首请求；已被 `lifespan` 预热覆盖，无需改。
- CSP：`script-src 'self'`、`style-src 'self' 'unsafe-inline'`。所有页面样式都是模板内联 `<style>`，`unsafe-inline` 因此必需；若要收紧需先把样式外置（O-08 范畴）。

## 5. 上轮 F-06～F-08 的复核意见

| 编号 | 复核 | 建议 |
| --- | --- | --- |
| F-06 下载 GET | 成立：`serve_download` 每次 `run_in_threadpool(verify_snapshot)` 全量 sha256（最大 512 MiB，30 秒截止），且 `GET /shop/dl` 无限流依赖；`POST /shop/download/{no}` 有 `download` scope 限流。签名链接绑定订单/对象/期限，`refund_for` 在哈希前后各查一次 | 先给 `GET /shop/dl` 加与 POST 相同的 `download` 限流（一行依赖，零行为改变），再按 O-01 测量后决定是否用"快照目录名 = 摘要 + 文件 mtime/size 复核"替代每次全量哈希；**不缓存权益判断** |
| F-07 RAG 缓存 | 成立：全表读取 + 全文 JSON 序列化 + sha256，每个 `/support/ask` 请求都做一次；命中缓存时跳过分词/建索引 | 文档先改（见第 4 节）；性能优化按 O-02，先有语料规模/并发目标 |
| F-08 schema 同步 | 成立：`tests/test_schema_sync.py` 比对表/列集合与 timestamptz；不比 default/nullability/索引/FK/CHECK。注释已在上轮修正 | 按 O-03 在可丢弃 PG 上比较 fresh-init 与迁移链的 `information_schema`/`pg_constraint`/`pg_indexes` 快照；不改历史 SQL |

## 6. 本轮验证记录

| 检查 | 结果（2026-09-20，基线 ea11619，工作树未改动运行代码） |
| --- | --- |
| 分支/远端 | 本地 HEAD `ea11619` = `git ls-remote origin refs/heads/arena/01a08bf5-codemax-platform`；工作树干净 |
| 上轮最终 CI | `gh run view 35471274891`：headSha `ea11619`，六 job（ruff / pip-audit / npm build / docs / SQLite / PostgreSQL 16）全部 success |
| Ruff / pip check | `All checks passed!` / `No broken requirements found.` |
| 文档契约 | `README contract: 296 files, 13 owners, 0 errors` |
| 精读数据 | `docs/code_reading_notes.json` 191 个文件 sha256 全部与磁盘一致 |
| 文档站 | `build_docs_site.py --data-only`：模块 135 · 依赖边 620 · 路由 63 · 符号 1928 · 文档 58 份 · 精读 191 文件 / 2117 段 |
| 前端 | `npm ci` + `npm run build` 后 `git diff --exit-code -- app/static/js` 零漂移（仍有 >500 kB chunk 提示） |
| 六项诊断 | `6 passed, 1 warning in 3.17s`，输出见第 3 节 |
| 全量 SQLite | 见本节末尾实际结果 |
| 7 个 skip 的原因 | 5 项需真实 PostgreSQL（`test_e2e`、`test_oauth`、`test_second_review_regressions`×3 中的 PG 专用、`test_wechat_bills` 并发快照）、1 项需真实向量 key（`test_faq_semantic` 标定）、其余为 Node/POSIX 条件在本沙箱均满足；无无理由 skip |
| F-09 复现 | 合成时钟脚本（本报告第 4 节描述），未写入仓库；修复批次会转成 `tests/test_ratelimit.py` 保护性回归 |

全量 SQLite 套件：**1687 passed / 7 skipped / 1 warning（passlib `crypt` 弃用），退出码 0**，两次运行分别 1096.72 秒与 1106.10 秒（第二次是沙箱快照恢复、依赖按 `requirements.txt`/`package-lock.json` 重装之后，用于确认环境重建不改变结果）。与上轮报告的 1687/7 一致。

## 7. 建议的修复批次（待用户确认后实施）

每批：保护性测试先红后绿 → 最小实现 → 模块 README/精读 SHA 与段界 → TECH_DECISIONS 新 TD → HANDOVER/ROADMAP 同步 → `git commit -F` → 推送固定分支 → 核对精确 SHA 六项 CI。不新增依赖、不改 schema、不改历史 SQL。

| 批次 | 内容 | 涉及文件 | 风险/兼容 |
| --- | --- | --- | --- |
| B1（P1，A-01） | 纯 ASGI 请求体预算中间件：Content-Length 超限直接 413；无长度分块累计超限即 413 并停止读取；支付/退款回调路径保留其自身 64 KiB 预算与原始字节；422 错误体不回显 `input` | `app/middleware.py`、`main.py`、`app/routers/shop.py`/`refund_notify.py` 只核对不改 | 默认上限需定（建议 1 MiB 通用、Drawio 保存 `content ≤ 500000` 字符需 ≥ 2 MiB 路由级例外）；不影响 GET |
| B2（P1，A-02） | `LLMClient`：`client.stream` 累计字节上限（建议 1 MiB）、`json.loads` 前深度/大小检查、`RecursionError/ValueError` 统一归一为 `LLMError`、整次调用 wall-clock deadline | `app/tools/llm.py`、`tests/test_llm*.py` | 默认无 key 时行为不变；不对真实提供方压测 |
| B3（P2，F-09/A-07） | 限流器过期键淘汰 + IPv6 /64 归并 + 满桶日志 | `app/ratelimit.py`、`tests/test_ratelimit.py`、`tests/test_audit_20260915.py`（保留反例）| 单进程语义不变；配置项 `RATE_LIMIT_IPV6_PREFIX`（可选） |
| B4（P2，F-06 前半） | `GET /shop/dl` 加 `download` scope 限流 | `app/routers/shop.py`、`tests/test_download.py` | 一行依赖；合法重试受 30 次/60 秒约束，与 POST 一致 |
| B5（P2，A-03） | `POST /shop/orders` 加 per-user 在途单飞（进程内 `asyncio.Lock` 按 user_id，加 `RATE_LIMIT_TOOLS` 限流） | `app/routers/shop.py` | 不持 DB 锁跨网络；多实例仍各自单飞（TD-141 前提） |
| B6（P2，A-04） | 表单登录复用 `require_finance_origin` 的 Origin/Fetch-Metadata 校验（Bearer/无头客户端豁免） | `app/routers/auth.py`、`app/deps.py` | 需要先确认是否存在跨域第一方登录客户端；否则会被拒 |
| B7（P3，A-05 + 文档） | embedding index `type is int` 严格校验；TD-214/TD-217 文字修正；`_payload` 死参数清理；`crawler.py` 裸 assert | `app/tools/llm.py`、`TECH_DECISIONS.md`、`app/routers/shop.py`、`app/tools/crawler.py` | 无行为变化（除拒绝非整数 index） |
| B8（P3，UI） | 对比度：链接/按钮 `#3b82f6→#2563eb`（5.17:1）、CTA/价格 `#16a34a→#15803d`（5.02:1）、提示 `#94a3b8→#64748b`（4.76:1）；登录浮层 `role="dialog" aria-modal aria-labelledby`、Esc 关闭、关闭后焦点回到触发按钮；`button:disabled` 灰化 + `cursor:not-allowed`；`:focus-visible` 轮廓；`support.css` 用 JS 切换 class 替代 `:has()`；`payments-admin` 长提示允许换行 | `app/templates/base.html` 等模板、`app/frontend/auth.js`、`support-page.js`、`app/static/support.css`，重建 `app/static/js` | 纯样式/可访问性；测试 `test_mock_pay.py` 等对 HTML 片段有断言，需同步 |

第 8 节以后不再有内容；上述提案的取舍、代价与回看条件在实施时各自记 TD，不在本报告重复维护状态。
