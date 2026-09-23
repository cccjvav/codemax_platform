# 当前工作队列与阶段目标

更新：2026-09-23。本页替代已完成的 S1–S5/R-01 等旧执行计划；历史可从 Git 和 [阶段索引](manager/stages/README.md)追溯。恢复入口仅 [HANDOVER](HANDOVER.md)，证据见[全仓审计](review/FULL_REPOSITORY_HANDOFF_2026-09-19.md)、[接手独立复核](review/FULL_REPOSITORY_REVIEW_2026-09-20.md)与[第二次接手复核](review/FULL_REPOSITORY_REVIEW_2026-09-23.md)。

## 优先级与范围

- **P1**：对应功能公开/收费前必须消除或在部署上明确关闭并验证隔离；不是对所有演示功能一刀切。
- **P2**：紧接的可靠性/安全加固；优先级可由独立复核的实际入口和部署证据调整。
- **P3**：协议严谨性、维护和按需性能优化。
- 本轮只审计、复现、文档整理；以下未勾选项没有偷偷实现。新依赖/schema/产品承诺仍需确认。原 0001–0017 SQL 不可改写。

## G0：独立交叉复核与交付接收（先做）

| ID | 状态/目标 | 完成标准 | 环境/依赖 |
| --- | --- | --- | --- |
| H-01 | 已取得并初评：《支付架构提示词-纯净版.txt》（19f236e） | 原文/摘要完整性已核对；逐项适配、五个问题与官方准入资料见审计第9节 | 不认证外项目源码；H-02独立复核与L-00实际准入仍待办 |
| H-02 | 已完成（2026-09-20）：接手人独立复核 | 六项诊断在 SQLite 全部复现并同意评级，调用链复核与新增 F-09 见[复核报告](review/FULL_REPOSITORY_REVIEW_2026-09-20.md)第 3–4 节；修复按 G1 分批 | 本机可丢弃 DB、MockTransport；真实 PG 全量由 CI job 覆盖 |
| H-03 | 每次交付核实：发布 | 本地 HEAD=远端分支=全部六项 CI 的 headSha；不使用 dry-run/历史绿色 | 固定分支仓库访问；最终交付消息关联 |
| H-04 | 已完成（2026-09-23）：第二次接手独立复核 | 前两轮修复与 TD-260～TD-270 逐项复核成立；全量 SQLite 1822 passed / 7 skipped；首次真实 Chromium 渲染 + axe-core + 完整流程；新发现 N-01～N-08 见[第二次复核](review/FULL_REPOSITORY_REVIEW_2026-09-23.md)第 3、6、7 节，修复按下方 V 组分批 | 本机可丢弃 SQLite、仓库外 Headless Chromium；真实 PG 由同一 SHA 的 CI 覆盖；Windows/商户/模型仍归 G2 |

## G1：公开接口资源与边界（建议下一实现批次）

| ID / 优先级 | 工作与依据 | 验收标准 | 环境/约束 |
| --- | --- | --- | --- |
| A-01 / P1 | 已完成（2026-09-20，TD-260）：`RequestBodyBudgetMiddleware` 默认 1 MiB、`/diagrams` 2 MiB、回调路径自管 64 KiB；422 不回显 `input` | Content-Length/分块/谎报长度均 413 且停读；回归 `tests/test_request_body_budget.py`；支付回调原始字节验签不变 | 应用层预算，不替代代理 `client_max_body_size`（DEPLOY 已注明）；实际代理层限额仍归部署签收 |
| A-02 / P1（启用 LLM 时） | 已完成：字节/深嵌套/总时限（2026-09-20，TD-260）；并发闸门（2026-09-20，TD-264，用户确认取值）：每进程 `MAX_IN_FLIGHT=4`，chat/embeddings 共用，满则立即 LLMError(busy) 不排队，路由 503 + `Retry-After: 5`，客服链路转人工，mermaid 页自动重试一次；不做站内日额度 | 回归 `tests/test_llm_response_bounds.py`、`tests/test_llm_concurrency.py`（第 5 个并发未发往提供方、各失败路径归还槽位、embeddings 共用、503/502 映射、转人工、前端重试） | MockTransport/事件屏障/Node 桩；单进程语义（TD-141）；消费封顶仍以供应商控制台为准 |
| A-03 / P2 | 已完成（2026-09-20，TD-262）：`POST /shop/orders` 挂 `order` 桶（`RATE_LIMIT_AUTH`）；wechat 分支在本地订单提交后进入按 user_id 的进程内 `_prepay_flight`，锁内重读订单、已有 code_url 直接复用，未知结果后等待者沿用同一单号重试 | 回归 `tests/test_checkout_concurrency.py`：并发两次 → 提供方一次/同单同 code_url/事件 started+ready；unknown 后同号重试；取消不泄漏；`order` 桶 429 | 进程内单飞（TD-141，多实例各自单飞）；不持数据库锁跨网络；不是重复扣款修复声明；持久租约未做，出现多实例需求再议 |
| A-04 / P2 | 已完成（2026-09-20，TD-262，用户确认沿用财务来源模式）：`require_login_origin` 挂 `POST /auth/login`，`check_browser_origin` 与财务写共用同一判定；跨站 Fetch Metadata/外站 Origin 的表单登录 403 且不下发 Cookie，无来源头客户端不变 | 回归 `tests/test_auth_cookie.py` 登录来源组（六种跨站标记 403、同源/无头 200、来源先于凭据） | 服务端 ASGI 证据；真实浏览器双来源 PoC 仍归 L-04 签收；不是同步令牌型 CSRF 系统 |
| A-05 / P3 | 已完成（2026-09-20，TD-260）：`type(index) is int` | 拒绝 bool/float/字符串 index，重复/缺失/越界原有校验保留；`tests/test_llm_response_bounds.py` 含正常向量排序不退化 | 本地合成；不猜供应商模型能力 |
| A-06 / P2（扩展收银台前） | 网页支付展示/轮询；审计9.4 | 保留无重叠/终态停止/换账号隔离；测后台暂停、退避/抖动、等待上限与恢复；新增HTTPS收银台前建立按渠道URL白名单，回跳不直接授予权益 | 当前Native扫码，不伪装H5/JSAPI已接入；SSE/WebSocket先有SLO依据 |
| A-07 / P2 | 已完成（2026-09-20，TD-261）：`Limiter.admit` 从按到期排序的队头回收过期桶（每次 ≤ 32，摊销 O(1)）；满且全活跃仍拒绝且 `reason=capacity`、Retry-After 指向最早到期桶、warning 每分钟一条；`_identity` 把 IPv6 归并到 /64 | 回归 `tests/test_ratelimit.py`（容量/回收/告警/IPv6 单元 + ASGI 全链路），`NoScan` 与"全活跃仍拒绝"反例保留 | 单进程语义不变（TD-141）；未引入 Redis；维持攻击门槛经复测订正为 ≈ 300 请求/秒（见 TD-261） |
| A-08 / P2 | 已完成（2026-09-20，TD-261）：`GET /shop/dl` 挂 `download` scope（`RATE_LIMIT_TOOLS`），与 `POST /shop/download` 共用桶 | 回归 `tests/test_download.py::test_download_exit_shares_the_download_rate_limit`：领取 + 两次出口 200、第三次 429、状态不变、窗口后同链接可用；全量哈希优化仍按 O-01 测量后做 | SQLite 回归；权益判断未缓存 |
| A-09 / P3 | 已完成（2026-09-20，TD-263）：`#3b82f6→#2563eb`、`#16a34a→#15803d`、`#94a3b8→#64748b`；浮层 `role="dialog" aria-modal aria-labelledby`、Esc 关闭、焦点归还；`button:disabled` 灰化 + `not-allowed`、`:focus-visible` 轮廓；`support.css` 改 `.with-inbox` class；bundle 已重建提交 | 回归 `tests/test_ui_accessibility.py`（对比度公式对照值、六处颜色 ≥ 4.5:1、旧色不再出现、ARIA 属性、源码与产物 Node 真跑 Esc/焦点）| 静态 + Node VM；真实浏览器视觉/读屏签收仍归 L-04 |

退出条件：保护性回归进入默认套件，诊断旧行为断言被替换/注明已失效；模块 README、精读、配置/部署指南同步；精确 SHA 全 CI。可以拆小批次，不能为保持诊断绿色保留缺陷。

## V：验收前复核（2026-09-22 起，按页面/文档/CLI 分组）

第一批 TD-270 已完成（Windows 指南第 7～13 步真 PG 回放：422 中文化、收银台返回入口、ER 表头对比度、静态缓存/gzip、CLI 连接失败提示、会话过期提示、Windows 测试兼容）。以下为 [2026-09-23 第二次复核](review/FULL_REPOSITORY_REVIEW_2026-09-23.md) 提出的批次，**均未实施**，每批经用户确认。

| ID / 优先级 | 工作与依据 | 验收标准 | 环境/约束 |
| --- | --- | --- | --- |
| V-01 / P2（回归） | N-01：会话过期时保留 Drawio 未保存的图与客服草稿（TD-270 的 `sessionExpired` 触发登出通知，`syncAuth(null)` 重置编辑器、`onUser(null)` 清空草稿） | Node 桩：过期→同账号重登，编辑器内容与草稿仍在；过期→换账号仍清空（账号隔离不退化）；新断言在当前代码上先红 | 前端 3 文件 + 产物；无依赖/配置 |
| V-02 / P2 | N-02：流程图写入无每 IP/全站上限（单 IP 15 秒约 196 MB） | `POST/PUT /diagrams` 限流桶；注册每日或全站速率上限；ASGI 回归复现后变 429 | 是否新增配置项及取值需确认 |
| V-03 / P2（工程）——**已完成（2026-09-23，TD-272）**：测试进程 bcrypt 成本 4（全量 1276 s → 约 220 s），生产成本由 `production_cost` fixture 保留复验 | O-11：测试 81% 时间在 bcrypt；测试进程 cost 4 全量 1276 s→220 s（1822 passed） | conftest 降 cost、生产哈希格式不变；`test_auth_crypto` 时序用例保持含义；CI 两个测试 job 时长显著下降 | 只影响测试进程 |
| V-04 / P3（UI）——**部分完成（2026-09-23，TD-272）**：跳过导航、`font: inherit`、窄屏 16px 输入框、≥24px 命中区、窄屏顶栏收窄、管理员入口按角色显隐、工具页可见页面标题、管理页单 h1/长单号换行/只读分区/危险红框、客服时间色 6.92:1 | 手机订单管理页横向溢出 28px；ER 节点文字溢出/连线穿越与遮挡；按钮不继承字体（13.33px Arial）、iOS 输入框 <16px 缩放；客服时间 4.34:1；Drawio 下拉框无可访问名称；工具页无页面标题、管理页双 h1；手机顶栏 208px | 浏览器/Node 断言：390px 无横向滚动、ER 文字宽 ≤ 节点宽、axe-core 0 违规、按钮/输入框字号继承 | CSS + er-layout + 产物；真实浏览器验收仍归 L-04 |
| V-05 / P3（流程） | 单标签页“返回商城”落回落地页（`/shop` no-store 使 bfcache 失效，`currentNo` 丢失）；已付用户再点购买静默新建订单 | 返回后显示该单状态（`/shop?order=` 或登录后自动恢复最近订单）；已购用户主按钮为“去下载”；Windows 指南第 12 步文字同步 | 复购规则属业务决定 |
| V-06 / P3——**首屏重复重建部分已完成（TD-272）**（桩断言首屏 0 次重建）；改密码入口与 Mermaid 按需加载仍待做 | Drawio 首屏重复重建 iframe（外部编辑器下载 2～3 次）；无网页改密码入口（生产关闭 /docs 后无法改密）；Mermaid 首屏 626 KiB 可按需加载 | 桩断言首次加载 ≤1 次创建；改密浮层回归；Mermaid 点生成时才加载 | 前端 + 产物 |
| V-07 / P3 | 开发模式经 https 代理访问时登录/财务操作 403（N-07，文档）；gzip level 9→6；`/static/README.md` 公开与缺 favicon；生产检查 `TRUST_PROXY_HEADERS` 硬拒绝但文案称“可忽略” | DEPLOY/WINDOWS_LOCAL_RUN 说明；响应头/静态路由回归；启动检查文案或级别与部署形态一致 | O-15 需确定部署形态 |
| V-08 / P3（文档/工作流） | 分支名硬编码在指南/Skill/Agnes 工作流，每个会话都要改 7 处（TD-270 与本轮各改一次） | 指南引用 HANDOVER 唯一一处分支名；Skill 用 `git branch --show-current`；Agnes push 触发改模式或仅手动（工作流变更需确认） | 本轮已把纯文档中的分支名改为本会话分支；工作流触发与其测试断言仍为 `01a0bf7a`，待确认 |

## G2：有边界的发布验收（和代码修复分开签收）

| ID / 优先级 | 目标 | 完成标准 | 执行环境 |
| --- | --- | --- | --- |
| L-00 / P1（真实收费前） | 经营主体与具体渠道产品准入，审计9.3 | 确认注册地/主体、数字文件或服务类目、结算账户/币种、PC/微信内外场景；取得所选渠道审核/书面说明及持续使用条件。境内个体户不能推定可开H5，支付宝个人账号/基础版不等于永久免照授权；丹麦经营可另评估合格PSP | 经营者+渠道审核；先于L-02商户联调；不收集身份证/银行卡明细到仓库，不自动迁移渠道 |
| L-01 / P1（公开部署） | 可信入口、资源与可恢复运维 | TLS/代理头、请求限制、单进程限流边界；应用非超级权限；数据库+storage快照+账本/任务的联合备份恢复；日志/私有报告访问与报警接收人有实证 | 隔离部署/容器；不是普通 pytest；参考 DEPLOY、REFUND_OPERATIONS |
| L-02 / P1（固定文件收费） | 真实商户闭环与运营 | 真实配置/证书轮换方案、支付/查单/通知、短链/重领/退款撤权、原号未知恢复和默认开关策略；商户/渠道操作获授权；退款及对账人工职责明确 | 专用商户与 HTTPS；不得直接在客户/业务 DB 试验 |
| L-03 / P1（对外经营） | 用户与经营说明 | 商品/交付、退款与客服时限、隐私/数据保留、条款/许可证及适用司法辖区由经营者/专业人士确认；不虚构外部开票/客服 | 用户/运营签收；不是丹麦/EU 合规法律结论 |
| L-04 / P2 | Windows/浏览器/桌面验收 | 按 Windows新手逐步验收.md 填真实证据：CMD+Conda+系统 Node，下载/Drawio/Mermaid/多账户对话/Word 等；记录失败及未执行 | 用户 Windows 与真实浏览器，不拿 Linux DOM/Node 代替 |
| L-05 / P2（向量功能） | 真实 embedding 与 FAQ 标定 | 用户选择候选后独立配置与协议适配，实测中文 FAQ 阈值/费用/条款；未通过时保留词袋回退 | 授权提供方或本地模型；Agnes聊天已成功≠向量已成功 |

演示可保持 mock、资金开关关闭并清晰标识；免费工具需 G1 与 L-01 的适用项；固定文件收费需 L-00/L-02/L-03；不应强制先做部分退款/多实例/云存储/自动全账结算。允许首发“全额退款、单实例、本地存储、人工核账”，但必须公开范围并满足实际经营要求。

## G3：业务扩展，先确认再设计

| ID | 待确认事项 | 决策/验收门槛 |
| --- | --- | --- |
| P-01 | 定制开发/服务产品生命周期 | 用户确认是否首发：报价/范围/付款节点/交付/客户验收/争议处理；当前固定文件商品不得包装成已完成服务订单系统；有 schema 变更先设计审批 |
| P-02 | 部分退款、发送后纠错、完整会计结算 | 仅在业务需要时独立方案；保持只追加证据及历史权益，不以日账零差异自动关账 |
| P-03 | 多实例/云存储/外部通知 | 需要时增加共享限流、文件一致性与等价逐次授权；不绕开退款门禁或假设已有供应商 |
| P-05 | 第三方登录消费者（区别于现有第一方OAuth服务器） | 用户确认后设计provider+subject稳定绑定、state/PKCE/恢复/解绑及旧客户权益迁移；不按同名邮箱自动合并，不删除现有账号体系 |
| P-06 | 桌面/IDE离线许可 | 仅确定销售可离线运行软件后设计签名算法/库兼容、明确撤权延迟、可信密钥轮换、时间回拨/设备恢复/计费与隐私；网页下载保持服务端实时授权，不因缓存有效而绕过退款 |

## G4：性能与可维护性，测量后优化

| ID / 优先级 | 依据 | 目标与防倒退条件 |
| --- | --- | --- |
| O-01 / P2 | 已完成（2026-09-22，TD-266）：测得 sha256 ≈ 900 MiB/s、512 MiB 每次下载 0.53 s CPU、30 次 Range 请求对 256 MiB 商品 8 s 线程池 CPU；`verify_snapshot` 改为首次全量哈希、此后同一 inode 状态（dev/ino/size/mtime_ns/ctime_ns）只 stat，ctime 距今不足 3 s 不缓存（时间戳粒度），非 POSIX 不启用；Range/重复/HEAD 由 FileResponse 原生支持（206/416/multipart 已实测） | 回归 `tests/test_delivery_verify_cache.py`（首哈希后只 stat、篡改/回拨 mtime/换 inode 重哈希、宽限期、上限、平台开关）与 `tests/test_download.py` 出口用例（重复+Range 只哈希一次、退款后缓存命中仍 403）| 每次请求仍实时查退款/订单/签名，不缓存权益；进程内状态；Windows 每次全量哈希不变 |
| O-02 / P3 | F-07 RAG 全文章加载和序列化、进程级限流/礼貌状态 | 先界定数据量/并发/SLO，再评估索引、增量缓存、队列与状态淘汰；不得仅为“优化”新增服务 |
| O-03 / P2 | 已完成（2026-09-20，TD-265）：一次性 PG 上比较 fresh-init / 0008 接入到 0017 / 重放 0002–0008 历史 SQL 三条路径的系统目录（列类型/可空/默认/长度/identity、约束、索引、触发器、函数、序列、表），发现并修正两处漂移：`sys_user.role` 新库缺 NOT NULL（0005 有）、接入路径的 `schema_migration` 由 ORM 编译致默认值拼法不同 | 回归 `tests/test_schema_equivalence.py`（三路径零差异、比较器自检、0001 幂等、账本 DDL 同源）；原 `test_schema_sync` 文本对照保留 | pgserver 一次性库（CI 的 SQLite job 因装了 requirements 也会跑）；不证明生产库状态或数据迁移正确性；0001 的前置裸 TIMESTAMP 形状无法从现行文件重建，只验其幂等分支 |
| O-04 / P3 | 生产镜像安装全部锁定依赖、历史资金模块复杂 | 先测镜像/攻击面及职责耦合，再设计 runtime/dev 分离或模块重构；保留完整维护 SQL/校验和与所有资金反例，不为拆分破坏恢复链 |

阶段十七实现已经完成，不再排成“待开发日账下载”。各旧批次结果在 [review 索引](review/README.md)；当前所有待办只在本页维护。

### 保留的专项余项（不因清理旧队列而丢失）

- **P-04 / 按需**：若开放不受信第三方 OAuth，再设计 scope/audience、PKCE、同意事务和撤销/会话治理；当前保持受信第一方，不把可选第三方协议强塞进首发。
- **O-05 / P3** 已完成（2026-09-22，TD-269）：四组正反语料先探针后实现——`ALTER TABLE … ADD FOREIGN KEY`（pg_dump/mysqldump 写法）改前零边，现计入；`REFERENCES parent` 不写列按父表单列主键补全，复合主键/父表不在 DDL 内留空列名；不带引号的表名引用按小写折叠、带引号精确（与 PG 一致，`"Mixed"` 定义、`mixed` 引用仍悬空）；方言类型修饰（UNSIGNED、CHARACTER SET、GENERATED、SRID）不进类型串——这一项复核后为既有行为，只补钉住用例。回归 `tests/test_sql_ddl.py` +5（4 项在旧解析器上失败）；模块 docstring 与 README 限制清单同步。仍不是完整 parser：表级 MySQL COMMENT、CHECK 内容、分区/继承不解析。
- **O-06 / P2**：Drawio 脏稿提示/恢复及 XML 子集/删除恢复前置条件先做协议设计和浏览器验收，保证账号切换隔离与 origin/source/epoch 相关性不退化。
- **O-07 / P3** 已完成（2026-09-22，TD-268）：合成探针复现三项——A 站允许、302 到 B 站时 B 的 robots 一次都没读就抓走页面；20000 个不同 origin 得到 20000 个永久状态项；`Crawl-delay: 3600`/`1e9` 被原样当作间隔（一次抓取占一个并发槽睡那么久）。修复：`_request` 的 `on_hop` 让 `fetch` 对每一跳目标做 robots 检查（robots 自身抓取不传，不递归）；状态表改 OrderedDict LRU、上限 512、持锁项不淘汰；`Crawl-delay` > 60 秒的站按不欢迎处理立即拒绝。SSRF 逐跳校验与动态 Chromium 停用未动。回归 `tests/test_politeness.py` 新增 6 项（5 项在旧代码上失败）。未复现、因此未改：robots 512 KB 上限已生效（1.3 MB robots 被 413 类拒绝）；5xx/超时的否定结果缓存整个 TTL 是既有的保守选择，保留。
- **O-08 / P3** 部分完成（2026-09-22，TD-267）：两份工作流的 16 处 `uses:` 全部钉到 commit SHA（经 `gh api` 与标签核对）并带版本注释；`pull-requests: write` 从顶层收到两个 test job；`tests/test_ci_supply_chain.py` 守住 SHA 钉住、同 action 同 SHA、权限范围、requirements 精确版本、package-lock integrity、`npm ci`。**未做**：`postgres:16` 镜像 digest（沙箱取不到可核对值）、pip `--require-hashes`（需要为 26 个直接依赖及其全部传递依赖生成哈希锁文件，改变安装流程，先有兼容方案再做）、类型检查/缓存/索引等其余项仍按"先有基准"原则待测量。
- **O-09 / P3** 已完成（2026-09-20，随 TD-262 批次后的清理提交）：TD-214 行内订正（指纹已是全文 sha256）、TD-217 注明常量；`_payload` 去掉从未读取的 `pay_mode` 形参、`crawler.py` 裸 `assert` 改为显式 `CrawlError`、`ruff --extend-select RUF100` 报告的 7 处无效 `# noqa` 删除（复核报告写的 15 处包含用 `--select RUF100` 单独运行时的 8 条误报，那样运行会丢掉 `ruff.toml` 的规则集）。无行为变化，历史 TD 顺序不动。
