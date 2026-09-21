# HTTP 路由与业务编排

## 模块职责

路由负责认证依赖、输入/响应、HTTP 错误映射，也实际承担部分查询、所有权检查和事务编排。加长 README 不会让这些逻辑自动迁移到服务层。
请求经过 main 装配的中间件，再由 FastAPI 解析参数和调用依赖；不能假设所有依赖按一个手写固定顺序执行。精确路径清单由离线站从当前源码提取并与运行时路由测试对比。

## 文件与入口

### auth.py：注册、登录与改密

| 函数 / 接口 | 请求与响应 | 权限、副作用与错误 |
| --- | --- | --- |
| `register` POST `/auth/register` | JSON RegisterIn → 用户公开信息 | 用户名重复 400；字段错误 422；异步哈希后写库，捕获并发唯一冲突；不能自行指定管理员角色 |
| `login` POST `/auth/login` | 表单 username/password → token；设置 HttpOnly Cookie | `login` 限流后过 `require_login_origin`（TD-262）：浏览器跨站 Fetch Metadata/外站 Origin 的表单提交 403 且无 Cookie，无来源头的 API 客户端不变；凭据失败 401，已禁用账号 403；不存在用户也走假哈希校验；响应 token 给非浏览器客户端使用，浏览器不另存 localStorage |
| `change_password` POST `/auth/password` | old_password/new_password → 新 token 与 Cookie | 需登录；用户写锁下重新核验旧密码、更新哈希与时间、递增凭据版本、提交；旧 JWT/授权码失效 |
| `logout` POST `/auth/logout` | 清除本浏览器 Cookie | 不建立服务器端单 token 黑名单；不能宣称注销了所有设备 |
| `me` GET `/auth/me` | 当前公开 UserOut | 401 表示会话无效；前端权限显示以此为依据，但授权仍由后端决定 |
| `_set_auth_cookie` | token → Cookie 属性 | HttpOnly、SameSite=Lax；production 下 Secure。代理/TLS 与 ENV 必须一致配置 |

### oauth.py：受信自有站点授权码流程

production两个发放入口均要求OAUTH_TRUSTED_CLIENT_IDS显式允许，默认拒绝所有客户端；回跳必须HTTPS。当前JWT是完整站点凭据，不能交给不可信第三方冒充scope授权。_check_client_policy同时拒绝无效客户端标识，兑换前授权码NUL/超长受控失败。

| 入口 | 契约与边界 |
| --- | --- |
| `authorize` GET `/oauth/authorize` | 已登录用户查看同意表单；不签发授权码。校验 response_type、启用客户端、精确注册回调 |
| `authorize_submit` POST 同路径 | 表单 sig 绑定 client/redirect/state/当前用户/凭据版本；拒绝同意回错误参数，批准后写一次性短时 code 再重定向 |
| `token` POST `/oauth/token` | 客户端凭据与 code 换 JWT；检查过期、已用、回调、用户状态/版本，原子消费，不能重复兑换 |
| `_active_client` / `_sign` | 客户端与回调检查／同意表单 HMAC；不能只依赖 Referer 或一个未绑定用户的签名 |
| `_callback` / `_redirect_callback` / `_consent_headers` | 保留已有回调 query、更新本次返回参数；同意页 form-action 仅允许该回调 origin，不放宽全站 CSP |
| `_oauth_error` / `_utcnow` | 统一 OAuth 错误 detail／UTC 时钟；不等同于已实现完整 OAuth/OIDC、PKCE、刷新令牌或统一单点退出 |

### diagrams.py：私有流程图

所有接口都要求当前用户，非本人资源与不存在均报 404。`list_diagrams(deleted=true)` 返回回收站摘要，不下载 XML；`get_diagram` 返回活跃文件及 ETag。

| 函数 | 写入与并发契约 |
| --- | --- |
| `create_diagram` | 用户写锁下检查活跃条数、总条数与总 UTF-8 字节；写入 XML，返回文件与 ETag |
| `update_diagram` | 必须 If-Match；检查预算后按 version 条件 UPDATE，递增 version；缺头 428、格式错误 400、旧版本 412 |
| `delete_diagram` | 软删除活跃文件，成功 204；此操作当前不要求 If-Match。不能把编辑/永久删除的版本前置条件泛化到全部写接口 |
| `restore_diagram` | 用户写锁；只恢复回收站对象，检查活跃配额并递增 version；非删除状态/配额冲突 409 |
| `purge_diagram` | 只永久删除自己的回收站对象，必须 If-Match；成功 204；不可撤销并释放总预算 |
| `_owned` / `_alive` / `_live_count` | 统一所有权、软删除过滤、活跃计数；include_deleted 用于恢复和永久删除，不只恢复 |
| `_etag` / `_parse_if_match` / `_storage_budget` | 版本协议／总条数和字节预算。替换内容排除旧行字节，回收站仍计入总量 |

### shop.py：订单、收款与文件

| 函数 / 接口 | 请求与返回 | 状态与权限 |
| --- | --- | --- |
| `create_order` POST `/shop/orders` | 登录用户下单 → 订单与支付展示数据 | `order` 桶限流（`RATE_LIMIT_AUTH`，TD-262）；复用有效 pending；过期关闭后重建；部分唯一索引挡并发重复；wechat 配置缺失 503、下单失败 502且保留pending；建单时保存配置金额，预支付先commit再进入按 user_id 的进程内单飞 `_prepay_flight`——等待者复用已写入的 code_url（reused=true）或在未知结果后沿用同一单号重试，锁内订单已非 pending 则 409；不持数据库锁跨网络，多实例各自单飞 |
| `order_status` GET `/shop/orders/{order_no}` | 自己的订单当前状态、expired | 只读 no-store；不会自动关单、领取链接或发起新订单 |
| `order_history` GET `/shop/orders` | before 游标 → `{orders,next_cursor}`，最多 50 条 | 自己的历史，按 id 倒序；next_cursor 非空不保证下一页一定还有条目 |
| `download_url` POST `/shop/download/{order_no}` | 自己的已付订单 → 短时 download_url | paid/downloaded 均可领取；先查对象并生成 URL，再记录发放；pending/closed 403。不是“只能领一次” |
| `serve_download` GET `/shop/dl` | key、expires、signature、order_no → FileResponse | 此出口不要求登录，依靠有效 bearer 链接；与 `POST /shop/download` 共用 `download` 限流桶（`RATE_LIMIT_TOOLS`，TD-261），超额 429 先于签名/数据库检查；签名/过期 403，文件不存在 404；链接在有效期内可重用，不能宣传成防转卖系统 |
| `pay_notify` POST `/shop/pay/notify` | 原始微信回调 → 微信格式 SUCCESS/FAIL | 不依赖用户 Cookie；限制报文并检查新鲜度、验签、解密、订单和金额；匹配mchid/appid、CNY/NATIVE、资源类型及固定平台serial/公钥ID；已有唯一凭证但非完整会计账本；原子确认，重复通知幂等 |
| `mock_pay_page` / `mock_pay_confirm` | 模拟收银台／自己的订单确认 | 仅 mock 模式，其他模式 404；真实 production 配置拒绝开启 mock |
| `confirm_paid_manually` POST `/shop/orders/{order_no}/confirm` | 管理员已核实的订单 → 已支付 | 仅manual且订单渠道匹配；须提供reference、实际整数分amount和evidence。原子记录收款凭证及首次确认人，日志仅补充；不是银行自动核账 |
| `_prepay_flight` | user_id → 异步上下文管理器 | 同用户预支付段互斥；引用计数在最后一个使用者离开时删除字典项，等待被取消也归还计数；进程内状态，不是跨实例锁 |
| `_payload` / `_qr_svg` / `_storage` / `_ok` / `_fail` | 展示字段、服务端二维码、存储错误映射、微信响应封装 | 不把扫码/二维码加载当付款凭证；用户侧和平台回调的响应格式不同 |
| `ping` | 登录态共享冒烟 | 只证明该受保护端点能响应，不验收支付能力 |

当前是单个配置商品。名称/金额/渠道/商户与交付文件key、摘要、大小均冻结到订单；已购文件不跟随今天的全局key变化。定制服务通过站内会话协商，不等于已实现定制报价/里程碑订单系统。

### messages.py：持久人工会话

| 函数 | 输入与结果 | 边界 |
| --- | --- | --- |
| `center` | GET `/support/center` → 页面外壳 | 页面公开不代表消息公开 |
| `MessageIn.nonblank` | body 与 UUID client_nonce → 清理首尾空白后的有效消息 | body 最长 4000 字符，拒绝全空白与空字符；同一发送重试保留同 nonce |
| `history` | customer_id、after 或 before → 最多 50 条正序消息 | after 查后续，before 查历史；两个游标不能同时给，返回 422；helper 本身不鉴权，调用路由必须先定 customer_id |
| `send_message` | 已鉴权发送者、客户、内容、角色 → 持久消息 | 用户锁与唯一约束配合；相同 nonce/内容重试返回原消息；同 nonce 用于不同客户或内容 409；内部提交/回滚 |
| `payload` | ORM 消息 → 公开会话字段 | 不把 sender_id/nonce 等全部内部字段原样输出 |
| `my_messages` / `write_message` | GET/POST `/support/messages` | customer_id 固定为当前用户；客户不能指定别人的会话 |
| `inbox` | GET `/support/conversations` | 仅管理员；每个会话最后一条、用户名、awaiting_admin，按 last_id 倒序分页；不是完整未读计数 |
| `admin_messages` / `admin_reply` | GET/POST `/support/conversations/{customer_id}/messages` | 仅管理员；目标用户不存在 404；角色实时查库 |

消息页面轮询，不自动发邮件、推送或保证管理员在线。`/support/ask` 的人工建议不会自动把问答内容插入这张消息表。

### tools、admin、support、site、health

| 入口 | 实际契约 |
| --- | --- |
| `tools.er_diagram` / `word_export` | 公开但限流；DDL 无表 400；Word 超规模 413、CPU 忙/超时 503 + Retry-After。parse 在线程池，DOCX 在有界进程池/故障回退路径 |
| `tools.mermaid` / `ping` | Mermaid 公开且 LLM 档限流，模型错误 502；ping 需登录，不是全部 `/tools/*` 都公开 |
| `admin.ingest_article` | 仅管理员并限流；静态抓取/模型提取/原子入库；CrawlError/robots/网络失败 400，ExtractError 422，模型原因 502，浏览器停用 503 |
| `support.ask` | 公开 `/support/ask`，限流；返回答案、来源、置信度、引用和人工页面 URL；异常回退通常是业务回答而非 HTTP 502，不代表派单成功 |
| `site._page_view` / `_base` / `sitemap` / `robots` | 根据站点清单注册 SSR、构造规范地址与搜索引擎入口；HTML 外壳不承载私人数据；robots 不是访问控制 |
| `healthz` | `/healthz` 与 `/health`：不访问数据库，只报告应用可响应 |
| `readyz` | `/readyz`：对 SELECT 1 设置 3 秒等待；异常 503，仅回异常类名；不检查全部业务表、模型或支付 |

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/routers/__init__.py`](__init__.py) | `e3b0c44298fc` | 空文件（无源码行） |
| [`app/routers/admin.py`](admin.py) | `34c9d1f92085` | L1–L83 |
| [`app/routers/auth.py`](auth.py) | `aad302b94e08` | L1–L135 |
| [`app/routers/diagrams.py`](diagrams.py) | `1bd6d4225cb1` | L1–L234 |
| [`app/routers/health.py`](health.py) | `c5adf1210f78` | L1–L45 |
| [`app/routers/messages.py`](messages.py) | `2a4df4fafa87` | L1–L121 |
| [`app/routers/oauth.py`](oauth.py) | `1f749cf1956d` | L1–L268 |
| [`app/routers/payments_admin.py`](payments_admin.py) | `c710e978990d` | L1–L269 |
| [`app/routers/refund_notify.py`](refund_notify.py) | `75d984ab71c8` | L1–L49 |
| [`app/routers/refunds_admin.py`](refunds_admin.py) | `6fec9d04d631` | L1–L309 |
| [`app/routers/shop.py`](shop.py) | `e4a48ff24b12` | L1–L740 |
| [`app/routers/site.py`](site.py) | `3c1007582b64` | L1–L61 |
| [`app/routers/support.py`](support.py) | `0b55ab4e7abb` | L1–L34 |
| [`app/routers/tools.py`](tools.py) | `193a7a663b00` | L1–L77 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

不要仅凭状态码推断“谁做错了”：400 抓取失败也可能来自目标站不可用；401/403/404 的精确语义由接口决定。
只读 GET 可以带查询和限流等运行时状态，但不得写入客户业务状态。已有公开下载 GET 靠签名凭证授权，不要误写成统一 JWT 鉴权。

## 变更与验证

接口变更同时核对 schema、前端调用、鉴权、事务提交、空/失败响应。优先运行对应 HTTP 测试；数据库竞争必须在真实 PostgreSQL 下另验，不以 SQLite 顺序结果代替。
新增路由会受到文档站与运行时路由集合对比测试约束。手写说明不固定总路由数；当前数量取构建数据与对应提交测试结果。

## 2026-09-15 交叉审查增量

本次审查修订：微信预支付前提交本地订单，失败保留单号/金额/名称用于重试；不是完整支付对账。回调限制64KiB并验证UTF-8、对象形状、标识长度和金额类型。图表If-Match为有界单版本，列表只取摘要列；登录NUL用户名走统一失败而不查库。发布阻断见 [交叉台账](../../review/README.md)。

## 第三批订单事务与维护端点

create_order先固定渠道/商户并准备内容寻址文件快照，再持久化订单及prepay_started，才调用微信；ready/unknown结果各留事件。相同pending不能随着当前配置切换渠道。mock仅development且只能确认mock订单；回调只结算wechat订单，精确重复200、冲突409，未接入事件422。

ManualReceiptIn校验实际金额、参考号和有内容的依据；EvidenceIn共享边界。管理员GET `/shop/admin/orders/{order_no}/ledger`禁止缓存、最多50事件，返回原始确认人而不是最后重试者。POST `/shop/orders/{order_no}/legacy-binding`只允许历史未绑定订单，核实后复制指定原文件，写一次合同与审计；不能覆盖新订单、重记旧收入或在生产绑定mock。

download_url使用订单key/hash/size，不再查当前STORAGE_PRODUCT_KEY；历史未绑定409，缺失404、损坏409，状态/购买权益保留；serve_download再次校验快照后流式响应。操作人恢复相同字节后可重领。不是运营UI、自动退款或定制服务完整工作流。

## 第四批：payments_admin.py 与财务来源检查

`payments_page`提供公开且noindex/no-store的登录壳，不在PAGES/sitemap；`orders`要求管理员，按冻结合同/客户用户名输出最多50条，before键集游标，all/manual/wechat/legacy/issues筛选只读且不触发网络；issues指有历史异常，不是未结案队列。`order_summary`只取原单，不读当日商品配置。

`ReconcileIn`复用EvidenceIn并要求确认单号。`reconcile`要求管理员和财务来源检查、独立限流桶，校验绑定渠道/商户，先持久query_started再跨网络；回来锁住用户并重读权限/凭据。SUCCESS把query_success交给settle同事务写入；冲突回滚后单独记query_conflict。未知、权限改变及非成功观察另记事件，不自动关单/退款/撤权；已有paid遇到NOTPAY/CLOSED记冲突。嵌套observation用发起时捕获的ID/name，不在回滚后读取过期ORM对象。

shop.payment_ledger现在同时给详情页返回原合同和manual操作可用标记，仍是管理员只读API。manual/legacy-binding/reconcile三种写操作共用deps.require_finance_origin；完整页面步骤、HTTP返回和外部边界见[管理手册](../../docs/PAYMENTS_ADMIN_GUIDE.md)。

## 第五批：复核读写

payments_admin.orders新增needs_review/reviewed投影筛选，原筛选不变；每批最多50单，最多扫描200候选，按最后实际检查的id返回游标，空页仍可能继续。候选读取后若已不需要复核，不误纳入待办。shop.payment_ledger附加实时review元数据，与50条事件分页独立，GET不写。

ReviewIn限制动作、160字说明、SHA摘要、严格整数版本、32位十六进制请求ID和确认单号；review_order要求管理员/来源/独立限流，先锁用户复核权限再锁订单。相同请求只返回首次记录；内容/发起人/归属冲突409。首次写比对资料与版本、只追加operator_review；无待办不能直接完成，但可主动登记跟进。与资金结算完全分离，没有新增表。服务端提交失败不会留下完成标记；HTTP错误依赖会话退出回滚释放锁。

## 第六批：refunds_admin与逐次下载授权

`RefundIn`限定确认单号及3–160字单行依据；`RefundQueryIn`加原商户退款号；`ManualRefundIn`加真实退款流水、严格整数分和AwareDatetime（必带时区）。`active_actor`按用户→订单锁序刷新权限/凭据；`target`先比确认号再取单。两个POST均有活跃管理员、财务来源防护、限流与no-store；manual入口按原凭证渠道，而不让当前SHOP_PAY_MODE覆盖旧渠道。

`manual_refund`登记实际已经完成的人工全额退款，不代为转账；仅manual原凭证。`query_refund`只发GET：先持久化包含原退款单号的refund_query_started，再网络I/O，返回后重查管理员。成功调用record_refund原子写凭证与成功审计；未知/中止/冲突和非成功各写终态，不撤回或恢复权益。进程中断可能只剩started，60秒后进入未知待核查；该查询入口不做自动退款/轮询；退款通知另外接入下节，仅留存线索。

`download_url`在耗时快照校验前查退款，之后锁单再查，才签发绑定order_no的v2短链。`serve_download`拒绝旧格式，验签后核对订单状态、冻结key/hash/size及退款，文件校验后再查一次退款；响应no-store。链接仍是可转交的短时bearer；退款提交后新授权拒绝，但已接纳传输/已下载副本无法召回。order_status/order_history返回refunded而不篡改原支付status；ledger同时展示收款与退款，事件分页不影响独立退款凭证。


## 第七批：refund_notify与管理员摘要读取

POST `/shop/refunds/notify`是公开服务器回调，不使用用户Cookie/管理员来源鉴权，而以可信平台验签和原付款归属授权。refund_notify检查接收配置、压缩与流式64KiB/4秒预算，parse_notice后save_notice持久提交，成功204空体；failure只返回固定FAIL消息/no-store，不透传原文/密钥。400协议/签名、409业务冲突、413上限、415压缩、503配置/数据库/超时；未知提交结果必须原ID重试。应用预算不保证代理端到端5秒SLA，生产仍须网关限额/监测与实测。

shop.payment_ledger在管理员权限下另读最新本地ID的通知摘要，不受事件before分页影响；仅返回notice_view通过的显示字段，普通客户无权读取。回调与管理GET均不调用退款服务/外网，不改变权益。完整配置与不自动补发历史通知的限制见管理手册第七批。


## 第八批：准备登记与只读恢复

refunds_admin新增RefundPrepareIn（拒绝未知字段、32位小写十六进制request_id、严格整数分、单行无C0/C1控制字符的3–160字依据）及POST `/shop/admin/orders/{order_no}/refunds/requests`。活跃管理员/凭据版本、来源与限流均验证，固定单号手输确认；调用prepare_request，返回首次稳定商户退款号、changed与明确preparation_only。冲突409，数据库保存未知503，不把失败写成未保存。

shop.payment_ledger另读准备及是否有既有退款活动，返回refund_request/refund_prepare_allowed，不受事件分页影响。可用标记仅是当前页面提示，真正写入仍在锁内检查。读取不写任务、不调用渠道；两个原退款核验入口和下载授权规则不变。当前需要0015，不能让未升级业务库直接接新流量。


## 第九批独立授权与发送

refunds_admin新增authorize/send两个管理员POST，均有财务来源与独立限流。RefundAuthorizeIn继承extra=forbid/严格整数/手输确认，另验reason的UTF-8字节数；RefundSendIn要求授权ID、完整摘要和固定退款号。两个路由先active_actor重查角色/状态/凭据版本并持用户锁，再进入服务订单锁。authorize仅保存，不发网络。send先持久尝试才调用渠道；未知HTTP/验签结果保守记录，SQL失败503。返回no-store，不透出原始渠道报文。已有query仍是独立金融核验入口。

shop.ledger另读refund_submission与部署开关；它不发送，历史游标不隐藏当前授权/尝试。页面可见性不是权限/可发送保证；已有通知/查询即使404也阻止本站重新申请，须人工在原渠道继续核验，不删事件解除保护。


## 第十批stop接口

POST /shop/admin/orders/{order_no}/refunds/stop复用严格RefundSendIn确认字段，但语义不是发送。require_admin、finance origin、refund-stop独立限流和active_actor版本复查后，stop_sending拥有订单锁与提交；业务冲突409，SQL保存异常503结果未知，正常响应no-store。无发送开关/商户配置前置要求，开关关闭、已有观察/成功凭证也能记录停止；无外网调用。错误不能被当成停止已提交。


## 第十一批：入站原子派工与只读投影

refund_notify仍4秒应用预算且无外部I/O；save_notice把事件和唯一核验任务一起提交才204，重试恢复首次结果。shop.payment_ledger增加refund_verification与refund_verify_enabled；最多50条任务/has_more，不随事件游标隐藏，无token或原始失败正文。require_admin和no-store保持，GET不启动worker/补队列。默认自动GET只留观察；系统登记开关另行授权后可以落系统凭证，人工查询入口保持独立。ledger新增refund_auto_record_enabled及凭证recorded_by/verification_event_id；仅显示当前Web进程配置，不证明worker在线或配置一致。

## 第十二批：核验任务控制端点

POST /shop/admin/orders/{order_no}/refunds/verification/control接收VerificationControlIn：原订单确认、严格整数job_id、hold/retry、64位snapshot、32位request_id与3–160字单行依据；拒绝额外字段。finance origin、当前管理员凭据版本及专用限流先行，control_job持锁写本地队列/审计；不调用GET或POST渠道接口。409为归属/幂等/快照/资格冲突，503是保存结果未知；正常no-store。请求不接受actor、重置计数或改变原退款号。


## 发送前重新授权端点

RefundReauthorizeIn继承严格准备/客户原因校验，补前授权ID与摘要且拒额外字段；POST refunds/reauthorize有管理员、来源及限流，active_actor在用户锁内重查角色/凭据版本，再交服务订单锁。返回authorization为该key首笔、submission为当前叶子，503不证明未提交。初始authorize也新增首笔authorization响应，重放旧根不冒充当前新版；GET ledger仅投影，无自动操作。

第十六批payments_admin新增POST `/shop/admin/orders/{order_no}/close-channel`：严格输入/来源/限流/活跃管理员凭据复核，默认门禁关闭。显式原订单/金额/查询attempt/请求key/依据；同key仅读，首次started提交后才向渠道POST。不能由reconcile或GET自动关单，finish不改原收款，503保留未知恢复内容。
