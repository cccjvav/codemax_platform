# HTTP 路由与业务编排

## 模块职责

路由负责认证依赖、输入/响应、HTTP 错误映射，也实际承担部分查询、所有权检查和事务编排。加长 README 不会让这些逻辑自动迁移到服务层。
请求经过 main 装配的中间件，再由 FastAPI 解析参数和调用依赖；不能假设所有依赖按一个手写固定顺序执行。精确路径清单由离线站从当前源码提取并与运行时路由测试对比。

## 文件与入口

### auth.py：注册、登录与改密

| 函数 / 接口 | 请求与响应 | 权限、副作用与错误 |
| --- | --- | --- |
| `register` POST `/auth/register` | JSON RegisterIn → 用户公开信息 | 用户名重复 400；字段错误 422；异步哈希后写库，捕获并发唯一冲突；不能自行指定管理员角色 |
| `login` POST `/auth/login` | 表单 username/password → token；设置 HttpOnly Cookie | 凭据失败 401，已禁用账号 403；不存在用户也走假哈希校验；响应 token 给非浏览器客户端使用，浏览器不另存 localStorage |
| `change_password` POST `/auth/password` | old_password/new_password → 新 token 与 Cookie | 需登录；用户写锁下重新核验旧密码、更新哈希与时间、递增凭据版本、提交；旧 JWT/授权码失效 |
| `logout` POST `/auth/logout` | 清除本浏览器 Cookie | 不建立服务器端单 token 黑名单；不能宣称注销了所有设备 |
| `me` GET `/auth/me` | 当前公开 UserOut | 401 表示会话无效；前端权限显示以此为依据，但授权仍由后端决定 |
| `_set_auth_cookie` | token → Cookie 属性 | HttpOnly、SameSite=Lax；production 下 Secure。代理/TLS 与 ENV 必须一致配置 |

### oauth.py：授权码流程

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
| `create_order` POST `/shop/orders` | 登录用户下单 → 订单与支付展示数据 | 复用有效 pending；过期关闭后重建；部分唯一索引挡并发重复；wechat 配置缺失 503、下单失败 502；金额来自服务器配置 |
| `order_status` GET `/shop/orders/{order_no}` | 自己的订单当前状态、expired | 只读 no-store；不会自动关单、领取链接或发起新订单 |
| `order_history` GET `/shop/orders` | before 游标 → `{orders,next_cursor}`，最多 50 条 | 自己的历史，按 id 倒序；next_cursor 非空不保证下一页一定还有条目 |
| `download_url` POST `/shop/download/{order_no}` | 自己的已付订单 → 短时 download_url | paid/downloaded 均可领取；先查对象并生成 URL，再记录发放；pending/closed 403。不是“只能领一次” |
| `serve_download` GET `/shop/dl` | key、expires、signature → FileResponse | 此出口不要求登录，依靠有效 bearer 链接；签名/过期 403，文件不存在 404；链接在有效期内可重用，不能宣传成防转卖系统 |
| `pay_notify` POST `/shop/pay/notify` | 原始微信回调 → 微信格式 SUCCESS/FAIL | 不依赖用户 Cookie；新鲜度、验签、解密后核对商户、订单和金额；原子确认，重复通知幂等 |
| `mock_pay_page` / `mock_pay_confirm` | 模拟收银台／自己的订单确认 | 仅 mock 模式，其他模式 404；真实 production 配置拒绝开启 mock |
| `confirm_paid_manually` POST `/shop/orders/{order_no}/confirm` | 管理员已核实的订单 → 已支付 | 仅 manual；服务器不知道个人收款码是否到账；必须本人核账。记录日志，不是独立不可篡改审计表 |
| `_payload` / `_qr_svg` / `_storage` / `_ok` / `_fail` | 展示字段、服务端二维码、存储错误映射、微信响应封装 | 不把扫码/二维码加载当付款凭证；用户侧和平台回调的响应格式不同 |
| `ping` | 登录态共享冒烟 | 只证明该受保护端点能响应，不验收支付能力 |

当前是单个配置商品。商品名称/金额在订单中保存，但文件 key 未按订单版本快照；不要更换全局 key 销售另一商品后让旧订单下载错货。定制服务通过站内会话协商，不等于已实现定制报价/里程碑订单系统。

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
| [`app/routers/auth.py`](auth.py) | `4edd21d2c8b4` | L1–L131 |
| [`app/routers/diagrams.py`](diagrams.py) | `5cfbcce46a49` | L1–L227 |
| [`app/routers/health.py`](health.py) | `c5adf1210f78` | L1–L45 |
| [`app/routers/messages.py`](messages.py) | `2a4df4fafa87` | L1–L121 |
| [`app/routers/oauth.py`](oauth.py) | `3c6513870ae7` | L1–L254 |
| [`app/routers/shop.py`](shop.py) | `336f2c0f7409` | L1–L470 |
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
