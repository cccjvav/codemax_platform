# 全仓审计、管理整理与独立交叉复核交接

**日期：2026-09-19 · 业务源码基线：`7f2e125dd6dc42fb7b31d8c295f176c8e3cb2f50` · 固定分支：`arena/01a08bf5-codemax-platform`**

本报告是可反驳、可复现的审计证据，不是安全认证、法律意见或真实收款上线许可。当前任务只在 [ROADMAP](../ROADMAP.md)；恢复/发布核验只在 [HANDOVER](../HANDOVER.md)。接手助手应独立审查，不能照抄本报告评级。

## 1. 结论与本轮边界

1. 阶段十七日账功能已实现并本地提交；审计开始时远端仍是阶段十六 `56f4aa7`，其 CI 35455979865 不能证明阶段十七。发布 dry-run 已恢复可用，但不等于实际推送；最终交付按本文第 8 节核验。
2. 现有资金链比旧审查报告完整得多：收款唯一凭证、订单交付冻结、全额退款撤权、显式授权发送/未知恢复、停止及发送前纠错、持久核验与独立系统登记、关单和日账均已有实现。本轮没有建立“重复扣款/任意退款/跨账号下载”等新的已证实攻击结论。
3. **六项独立合成探针在 SQLite 与非超级权限 PostgreSQL 均复现待修行为**，归为 F-01–F-05；另有 F-06–F-08 的源码观察/工程债。正常回归绿色不能覆盖这些资源/协议边界。
4. 本轮不新增支付功能、依赖、schema 或业务承诺；不执行真实商户、生产数据库或真实模型压测。新增诊断代码、修正一处测试范围注释、文档注册与说明；应用运行逻辑/历史 SQL 不改。
5. **新同伴付款阻断附件尚不可访问**：已检查当前工作树、可访问上传路径和全部 8 个远端分支的 Markdown/常见附件清单，未找到本次新材料，并已请求文件名/路径/补传。9 月 15 日两份原稿已保留、此前已交叉验证，但不是这个新附件。H-01 必须保留“待核对”。

## 2. 全仓覆盖与诚实边界

### 2.1 文件清单和静态检查

对基线 `git ls-tree -r --name-only 7f2e125` 的 **363 个已跟踪文件**逐一编目、计算 SHA/字节数并按类型检查；不包括忽略的 `.venv`、node_modules、runtime、生成文档站、Git 对象或实际用户数据。

| 类型 | 基线数量 | 本轮检查方式/局限 |
| --- | ---: | --- |
| Python | 134 | 全部 AST 解析；Ruff 与测试另列。核心调用链语义复核，不声称每个测试/生成语句逐行认证 |
| JavaScript / MJS | 109 / 2 | 全部 `node --check`；手写前端与构建产物对照、Vite 重建。压缩第三方包不是逐行人工安全审查 |
| Markdown | 68 | 全量清单/导航/注册/链接；现行入口与操作指南语义整理，历史日期/SHA 证据不改写为今天的结论 |
| SQL | 19 | 历史字节不变；维护调用链、账本/约束和 PG 测试。不是全部数据库版本/任意旧库升级证明 |
| HTML / CSS | 10 / 2 | 全部 Jinja 模板解析，模板/脚本协作与样式/构建检查；没有真实浏览器视觉签收 |
| JSON | 5 | 全部 JSON 解析；锁文件、配置/精读数据及生成门禁；数量/摘要不证明讲解语义正确 |
| YML | 3 | CI/Agnes/Compose 配置与合同测试，核对触发/权限/六 jobs；未实际运行 Docker 部署 |
| PNG / SVG | 1 / 1 | PNG 标识/尺寸 1440×1940、SVG XML 与用途核对；未通过扫码证明真实收款人/商户资格 |
| 其他配置 | 9 | 5 个无扩展名文件及 `.example/.ini/.txt/.toml` 各1；环境样例、依赖、忽略规则、Dockerfile 等 |

各行合计为 363。所有读取的文本均有效 UTF-8，未发现替换字符；这不是秘密扫描或语义正确性证明。

原始清单可用同一 Git 基线重建，避免把将来的文件总数硬写成“始终当前”。本次删除 4 份 Markdown、新增 1 份审计报告/1 份诊断源码；当前源清单由文档门禁实时生成。

### 2.2 跨模块语义复核

| 范围 | 已核对的主链路 | 仍需独立复核/验收 |
| --- | --- | --- |
| 装配、配置、中间件、限流 | main/startup_checks/security/deps、可信代理、生产门禁、日志、健康/就绪 | F-01；代理实配、容量/全局预算；Compose 不等于自动摘流 |
| 认证/OAuth | JWT/密码与账号状态、Cookie、同意/回跳、受信第一方边界 | F-04；真实浏览器；第三方 scope/audience/PKCE/撤销为另行产品范围 |
| 支付与交付 | shop → wechat_pay/payment_ledger → delivery/storage；退款前后短链、凭证/审计原子性 | F-03/F-06；真实商户、证书轮换、运营/恢复签收 |
| 退款/核验/关单/日账 | 准备/版本授权/started先提交/原号恢复、锁顺序、任务token/DB租约、独立系统权限、只读重复读/私有报告 | 不把通知/ACK/差异零当结案；实际 worker/商户/Windows权限尚未签收 |
| 工具/爬虫/模型/客服 | SQL图/Word导出、SSRF地址与固定DNS、受限抓取、LLM/FAQ/RAG、站内人工对话 | F-02/F-05/F-07；不重启动态Chromium；真实向量/中文FAQ标定、robots每跳专项 |
| 前端/模板/静态资源 | 认证切换、旧响应相关性、Drawio origin/source/epoch、商店与管理页、客服幂等/列表序号 | 真实iframe、草稿恢复、跨账号/移动端/Word；Node不是浏览器 |
| DB/SQL/测试 | 模型、维护CLI/账本/事务锁、迁移顺序、只追加约束与并发夹具 | F-08；更早存量基线/完整DDL等价、最小生产权限、备份恢复 |
| CI/构建/管理文档 | 六jobs、Agnes独立授权诊断、依赖锁、文档注册/指纹/Skill副本、Windows步骤 | 精确SHA发布；供应链加固按风险，不把旧统计当本轮测量 |

未宣称对所有压缩 vendor 代码/所有历史报告每句话/每种浏览器和硬件完成逐行语义认证。未进行生产负载、真实资金、法律合规、渗透覆盖或覆盖率百分比测量。

## 3. 已复现的问题与建议

复现入口：[tests/audit_handoff_probes.py](../tests/audit_handoff_probes.py)。仅显式执行，默认 pytest 不收集；**6 passed 表示复现了以下行为，绝不是修复完成**。使用有限的约 2 MiB 合成流，不耗尽资源；无真实外部请求。HTTPX 日志即使显示配置的提供方 URL，MockTransport 仍是在本机返回合成字节。

### F-01 / P1：字段长度校验晚于请求体读取，422 回显大输入

- 来源：`main.py`、`app/middleware.py`、`app/schemas.py::MermaidIn`、`app/routers/tools.py::mermaid`；无通用解析前增量字节预算。支付通知自己的 64 KiB 预算不能覆盖普通 JSON 路由。
- 证据：匿名向 `/tools/mermaid` 发送无 Content-Length 的分块 JSON，text 为 2 MiB，而字段上限 10000。**实际读取 2,097,163 字节，返回 422，响应 2,097,303 字节**。字段校验拒绝不等于没有消耗内存/解析/响应带宽；本探针没有调用 LLM。
- 风险：公开入口资源消耗与错误回显；不是泄露其他用户数据，也没有执行 OOM/高并发攻击。实际反向代理的独立限制可能缓解，仓库 Compose 没有这样的代理预算配置。
- 建议/验收：A-01，在解析前按路由计数原始 ASGI receive、总预算/超时，超限 413；限制验证错误中大 input；保留回调原始正文验签。测有/无/伪造 Content-Length、分块、取消和代理真实入口。

### F-02 / P1（启用模型时）：LLM 先缓冲全体响应，异常 JSON 边界不完整

- 来源：`app/tools/llm.py::LLMClient.chat/embeddings` 使用 `await client.post` 后才读 JSON/向量；`app/tools/support.py`、tools 路由依赖 LLMError 分类。
- 证据一：短合法回答 `ok` 加无关 padding，**2,097,207 字节全被读取且被接受**，没有 HTTP 响应字节上限。模型 token 限制不约束这些无关 HTTP 字段。
- 证据二：1200 层、2401 字节 JSON 列表导致 **RecursionError** 逃出，而非统一 LLMError；证明服务端协议错误规范化有缺口，不是实际供应商已经返回此输入。
- 源码观察：未见覆盖整次调用的 wall-clock deadline 或共享 in-flight/额度预算；HTTPX 单次读超时不应宣传成整体时限。**MockTransport 没有验证真实慢读行为**，需本地有界 HTTP 服务器另测。
- 建议/验收：A-02，流式读取上限/有限解压、内容/结构/深度边界、总 deadline、取消释放与并发准入；聊天/向量一致脱敏，不泄露上游原文。确认默认关闭/无 key 时的隔离，不向真实提供方压测。

### F-03 / P2：同单可并发进入两次预支付调用

- 来源：`app/routers/shop.py::create_order` 在持久订单提交后发送预支付；已有订单复用，但未见这个 POST 的限流依赖/同单在途占用控制。
- 证据：同一已登录用户并发两次 POST，在提供方桩内用事件屏障等待两个调用；SQLite/PG 均观察到 **2 个同时在途的 native_prepay 调用、1 个订单、0 个收款凭证**，两次 200。启用 RATE_LIMIT_ENABLED 且工具额度为 1 仍如此——这不是声称工具额度本来就适用于商城，而是证明不能依赖它保护此路由。
- **不是重复扣款证明**：两次使用同一个 out_trade_no，渠道幂等/收款唯一凭证已有保护；本探针没有真实渠道资金动作。风险是配额/资源消耗和未知/晚到预支付观察处理的复杂性。
- 建议/验收：A-03，用户/订单限流，跨会话持久单飞或租约、失败退避/未知恢复，仍用原单号/冻结请求；不可为串行而持 DB 锁跨网络，也不必不加判断地照搬整套退款发送协议。

### F-04 / P2：表单登录接受显式跨站来源（浏览器利用待验证）

- 来源：`app/routers/auth.py::login/_set_auth_cookie`，与管理写入口来源检查对比。
- 证据：合法合成账号凭据 + `Origin: https://untrusted.invalid`、`Sec-Fetch-Site: cross-site` 的 form POST 返回 **200 并设置 access_token Cookie**。
- 已证实的是服务端接受来源；没有完成浏览器 HTTPS 导航/SameSite/CORS 的端到端 login-CSRF 利用。不是盗取密码/JWT 的证明，也不是一般 API 客户端应该无条件禁止的结论。
- 建议/验收：A-04，先双来源浏览器 PoC 与明确 API 合同，再加来源/Fetch-Metadata/CSRF 策略；保留合法第一方、原移动/CLI 使用方式，测 Cookie 覆盖/登录混淆及失败错误行为。

### F-05 / P3：embedding 的 index 接受 bool/float

- 来源：`app/tools/llm.py::LLMClient.embeddings` 对 index 数列使用等值比较；Python 的 False 与 0、0.0 与 0 相等。
- 证据：单条结果 index 分别为 **False / 0.0** 均成功返回向量。有限数值/同维向量校验已经存在，不应误报“完全没有向量验证”或密钥泄露。
- 建议/验收：A-05 严格整数类型，然后验证唯一/完整/范围；这只是低优先级协议严谨性，不是资金或跨用户漏洞。

## 4. 源码观察与待测优化（不是已复现攻击）

| 编号 | 证据/影响 | 后续与不可退化条件 |
| --- | --- | --- |
| F-06 / P2 | `shop.serve_download → delivery.verify_snapshot/file_digest`：有效 bearer GET 每次哈希冻结文件，再复查退款；未见该 GET 限流。合法最大快照可达 512 MiB；重复/Range 可能放大磁盘/线程开销，但本轮未压测 | O-01。先测量，不删除完整性/二次退款检查；签名链接可转交是已有边界，不声称它等于用户会话 |
| F-07 / P3 | `app/tools/support.py` 检索加载全部 Article、JSON序列化/摘要后维护缓存；limiter是单进程，crawler礼貌状态的规模/淘汰值得专测 | O-02/O-07。先界定数据量、并发和SLO，再选索引/增量缓存/全局配额；管理员抓取不是任意匿名入口；未证实SSRF新绕过 |
| F-08 / P2 | `tests/test_schema_sync.py` 对表/列集合及部分时间约束有效，不等于全类型/default/索引/FK/CHECK/触发器等价。模块原注释“任何一边变都报错”过强，已修注释 | O-03。隔离PG对照 fresh-init 和迁移链、保留数据并验证行为；不能改旧SQL或用表列测试替代恢复验收 |

维护优化：资金路由/服务已较复杂，先按不可变证据与提交边界整理契约/测试，再考虑拆模块；镜像目前安装整份 requirements，runtime/dev 分离需独立依赖方案；供应链 digest/hash lock、静态类型和性能参数只按证据推进。SQL 方言限制、Drawio 草稿/恢复、第三方 OAuth、法律/来源/许可余项已保留到 ROADMAP，未随旧队列删除而丢失。

## 5. 已有保护：不要被旧报告误导

- 收款使用订单/用户锁、只追加 PaymentReceipt/PaymentEvent、唯一渠道流水及原单金额/商户/app 约束；预支付前先持久提交原订单。模块实现与真实商户验收仍不同。
- 下载签名绑定订单/对象/期限，检查冻结摘要与最新退款凭证；已付未退款用户可以重领短链，不应恢复“一次链接等于一次终身权益”。已接纳传输/已下载副本无法召回。
- 退款准备、授权、发送开始、受理/未知、成功凭证分开；started 先提交，无网络持锁；授权链/停止与首次操作者、原号恢复、worker token/DB时间/最后CAS已有明确防护。
- 系统登记另有默认关闭权限与系统审计，不冒用管理员。渠道关单/日账读取独立默认关闭；退款账单行只观察，不能凭当日静态行状态登记退款或自动结案。
- 当前生产 OAuth 限显式信任的第一方客户端；没有声称已实现最小权限第三方授权服务器。人工客服网页已实现，用户本人可以接待；没有外部开票/人工供应商承诺。

本轮尚未完成新的金融逻辑漏洞证明；这不等于形式化证明不存在问题。独立交叉复核应重点追踪原子提交、未知恢复、并发新事实/复核指纹及下载撤权，而不是只搜 `FOR UPDATE` 或数测试。

## 6. 文档与管理清理结果

### 删除而非继续堆叠

| 从工作树删除 | 原因/现行承接 |
| --- | --- |
| `CONSOLIDATED_ERROR_SUMMARY.md` | 9月11日旧基线问题清单，当前缺陷状态失效；Git保留原输入，本轮报告/ROADMAP承接当前判断 |
| `docs/REVIEW_CROSSCHECK.md` | 早期跨审/修复队列已被后续16批推进，不再作为当前计划；Git保留当时证据 |
| `DOCUMENTATION_SUMMARY.md` | 与文档政策/自动清单重复；改指 DOCUMENTATION_POLICY 与本轮审计 |
| `docs/DOCUMENTATION_QUALITY_REVIEW.md` | 旧文档重做验收被错误引用成“本轮内容审核”；Git保留旧证据，活跃入口改指本轮报告 |

需要旧原文可只读 `git show 7f2e125:docs/DOCUMENTATION_QUALITY_REVIEW.md`（其他同理），无需把失效状态页复制回当前目录。保留 9月15日两份原稿、第一批唯一逐项反驳/反例、阶段2–17及 SECOND_REPAIR_ACCEPTANCE 等独有历史证据；原稿不改写。

### 现行材料同步

- HANDOVER 压缩为恢复入口及四层发布证据；ROADMAP 改成 G0–G4 唯一当前队列，任务有优先级/验收/环境，移除已完成 S/R 计划和“下一步一定做服务生命周期”的武断顺序。
- review README 保留历史交叉判定/实测，移除竞争待办与堆叠“当前”横幅；manager/stages、manager README 同步，不新建空阶段。
- 修正 DEPLOY 中“退款全未实现”“就绪端点存在即自动摘流”的现行误导；ACCEPTANCE 分开显式退款与尚无的自动发送/外部服务；Windows旧阶段补后续 Agnes 已完成层次，不再把历史网络失败当当前阻断。
- AGENTS/根 README/总览/架构/文档政策/Windows参考等导航、站点 DOC_GROUPS、测试和构建脚本说明、精读及README指纹随删除/新增同步。
- Skill 因真实发布状态漂移与审计证据问题从 v2.4 升为 v2.5，权威源/manager副本逐字同步，经验记录说明原因；其余 Skill 不机械升级。没有修改不可访问的上游/全局 Skill。
- **不是宣称全部历史文字已获语义认证**；历史报告若出现旧路径/旧“未完成”仅在其日期/基线上理解。实际现行链接与注册由本轮门禁验证。

## 7. 本轮验证记录

所有下面“本轮”均需实际退出码；失败/未执行不拼接成通过。测试只在可丢弃数据库和合成提供方运行，无业务数据。

| 检查 | 本轮结果 |
| --- | --- |
| 基线文件静态编目 | 363文件；全部Python AST/JS-MJS语法/JSON/Jinja/SVG解析通过，UTF-8/替换字符检查无错误 |
| 独立诊断 SQLite | 6 passed，4.51秒。F-01–F-05均复现，不是保护性回归 |
| 独立诊断 PostgreSQL | 6 passed，3.21秒；一次性PG，suite_test为NOSUPERUSER/NOCREATEDB/NOCREATEROLE；已清理 |
| 本轮完整 SQLite | **1687 passed / 7 skipped / 1 warning，1034.08秒**，退出0；默认套件不收集六项诊断 |
| 本轮完整 PostgreSQL | **1693 passed / 1 skipped / 1 warning，1141.64秒**，退出0；一次性受限角色/独立库，finally已清理 |
| Ruff/pip check/依赖审计/前端 | 全部通过；npm audit 0项；pip-audit沿用唯一PYSEC-2026-1325例外，输出2 ignored；Vite零产物漂移（仍有大于500kB的chunk提示） |
| 文档/精读/Skill/站点/定向测试 | 75 passed；295源码/13 owners/0错误，190文件/2110讲解段；完整站及data-only通过，58文档页/63路由；Skill副本一致 |
| SQL/金融运行逻辑/依赖未改 | 已用git diff核对，与7f2e125一致；两份9月15日原稿也逐字未改 |
| 真实商户/模型/Windows/浏览器/Docker/生产恢复 | 本轮未执行，分别在 ROADMAP G2 排队 |

本轮两套全量都保留既有 passlib/crypt 弃用警告；没有隐藏或增加无理由 skip，真实向量标定仍未执行。文档最后整理后再跑定向门禁，最终提交仍由完整双库 CI 复验。

对照用的**历史阶段十七**结果：SQLite1687 passed/7 skipped（1071.93秒）、PG1693 passed/1 skipped（1175.79秒），均早于最后CLI隐私加固；随后定向SQLite170/1skip、PG101、docs70。不能用它们替代上表本轮全量或最终SHA CI。

可重复的 Linux 沙箱命令（Windows 使用 Conda 指南，不复制 shell）：

```bash
.venv/bin/python -m pytest -c pytest.ini tests/audit_handoff_probes.py -q -s
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/python -m pip check
.venv/bin/python scripts/check_docs_contract.py
.venv/bin/python scripts/build_docs_site.py
.venv/bin/python scripts/build_docs_site.py --data-only
npm audit --audit-level=high
npm run build
git diff --exit-code -- app/static/js
```

PG复验只把 TEST_DATABASE_URL 指向**另建的可丢弃非超级权限测试数据库**，再执行同样 pytest；fixture会重建表。建库/权限配方见验收手册，绝不复制业务 DATABASE_URL。本轮临时 pgserver 运行器创建受限角色和专用库、finally清理，临时日志不提交。pip-audit 沿用仓库明确的既有 PYSEC-2026-1325 例外，不扩大豁免；若审计通过也不得说“零已知风险”。

## 8. 最终发布与接手验收

此文档参与最终提交，不能自嵌自己的 commit hash。最终交付消息应给出 **完整 SHA、固定分支远端一致性、匹配 headSha 的 run URL 和六个 job 结果**；报告生成/本地测试成功不等于上传。

```bash
git rev-parse HEAD
git ls-remote origin refs/heads/arena/01a08bf5-codemax-platform
gh run list --workflow ci.yml --branch arena/01a08bf5-codemax-platform --limit 5 --json databaseId,headSha,status,conclusion,url
gh run view <匹配HEAD的run-id> --json headSha,status,conclusion,jobs,url
```

必须逐项 success：lint、audit、frontend、docs、test-sqlite、test-postgres；running/cancelled/其他SHA不是通过。Agnes专项是另一工作流，不替代六项。若失败/受阻，交付明确未发布或未验收并留下实际证据，不重启无依据的“请换key/网络”猜测。

交给下一助手的顺序：核SHA/CI → 获取新同伴附件 → 独立重跑诊断和调用链 → 评审/实现 G1 → 按首发类型执行 G2 → 经用户确认才扩 G3；性能任务 G4 先测后改。演示、免费工具、固定文件收费、定制服务的门槛不同；不默认要求首发部分退款/多实例/云存储/自动会计关账。可选“全额退款、单实例、本地文件、人工核账”的有界首发也必须通过适用的真实商户/部署/经营签收。
