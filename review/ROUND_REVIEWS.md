# 每轮复审记录

用户 2026-09-27 要求：每轮任务之后，必要时做一次复审；当轮不做或发现不在当轮处理的，都要记录下来，之后统一回头处理。本文件只存复审证据与结论；**搁置项的待办状态只在 [ROADMAP](../ROADMAP.md) 的 R 组维护**，这里不另立第二套剩余项清单。

每轮复审固定检查：
1. 复看本轮 diff：行为是否与 TD 描述一致，有没有漏改的调用方和文档。
2. 在真 PostgreSQL（本机 pgserver 一次性库，或同一 SHA 的 CI job）上跑受影响的测试；SQLite 通过不代表 PG 通过。
3. 等最终 SHA 的六个 CI job 全部完成。被新提交取消的运行不算，汇报里写明 SHA 与结论。
4. 发现的问题：当轮能小改就修（有测试、有 TD），否则登记到 ROADMAP R 组并写明理由。

## 2026-09-27：TD-291～TD-295（用户批准的五项跟进）

范围：导读维护脚本、客服 reason 不外泄、控制字符统一、OAuth 令牌端点错误格式、客服页「先问智能助手」。复审修复登记为 TD-296。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-01 | **CI 真 PostgreSQL job 挂死到超时**。TD-292 新用例 `test_llm_failure_detail_goes_to_logs_not_to_the_public_reason` 同时用了 `db` 与 `client` 两个夹具；第 3 组参数里 `answer()` 的查询让 `db` 会话的事务一直持有表锁，`client` 先拆除时 `drop_all` 要排他锁，永远等不到（`db` 夹具更晚拆除）。SQLite 没有表级锁，本地和 SQLite job 都通过。本机 pgserver 复现：`-v` 停在该用例第 3 组。 | 已修（TD-296）：直接调用后 `await db.rollback()` 再走 HTTP。本机 PG 全量 1955 passed。断言未改。 |
| RR-02 | **流程缺口**：TD-291～294 的中间提交 CI 都被后续推送取消（工作流开启了取消进行中的运行），TD-295 汇报时最终 SHA 的 PG job 仍在运行，所以 RR-01 没被发现；违反 ROADMAP H-03 与 AGENTS 工作循环第 8 步。 | 已改流程：AGENTS 工作循环新增第 9 步，汇报前必须等最终 SHA 全部 CI 完成，并在本机 PG 上跑受影响测试。 |
| RR-03 | TD-295：回答显示后切换成管理员登录，「把这个问题留言给管理员」仍显示，点击无反应（按钮显隐只在显示回答时计算）。 | 已修（TD-296）：记住 `lastEscalated`，`onUser` 按新角色重算显隐；Node 场景新增断言，修复前源码与产物都失败。 |
| RR-04 | TD-294：为了缺参数时返回 400 `invalid_request`，令牌端点表单参数改为空串默认值，OpenAPI（/docs）里这些参数显示为可选。 | 接受，不改：RFC 语义优先；参数检查在端点内完成并有测试。仅记录。 |
| RR-05 | TD-295：转人工的固定答案「请登录站内客服页发送留言」，用户在客服页上读到时略显多余。该文案同时通过 `/support/ask` 返回给其他调用方。 | 搁置 → ROADMAP R-03。 |
| RR-06 | 导读笔记中既有、非本轮引入的生成结果不一致：`app/routers/shop.py` 6 块（手写文件）、`tests/test_manual_pay.py::test_admin_confirm_is_idempotent`、`tests/test_second_review_regressions.py::test_pg_serializes_quota_and_message_retries`、`app/tools/llm.py::LLMClient._call`（旧模板变体）。 | 搁置 → ROADMAP R-04（笔记可读性，不影响门禁）。 |

复核过、没有发现问题的点：TD-293 共享常量覆盖全部 9 个字段（自省测试守住）、已存复核事件解码不追溯；TD-294 授权页错误格式未变、未知客户端仍跑假哈希、429 仍为 `detail`；TD-292 日志不含用户问题原文以外的新敏感字段；TD-291 脚本只改指定文件的条目。

## 2026-09-27：TD-296 的 CI 结果与 TD-297（管理员资金操作共用检查）

范围：9a4edbd（TD-296）的 CI；TD-297 抽出 `app/routers/admin_common.py`，payments_admin 与 refunds_admin 改用。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-07 | **9a4edbd 的 CI 有三个 job 失败**（依赖扫描、ruff、前端产物检查通过）：新建的 `review/ROUND_REVIEWS.md` 没有登记进 `scripts/build_docs_site.py` 的 `DOC_GROUPS`。文档站 job 因 AGENTS、ROADMAP、review/README 指向它的链接无效而失败；SQLite 与 PostgreSQL 两个测试 job 都挂在 `tests/test_docs_site.py::test_doc_groups_all_exist`（每份已跟踪的 .md 都必须登记）。本地提交前的全量测试是在该文件尚未 `git add` 时跑的，这个用例只核对已跟踪文件，所以本地假通过；日志取不到，在 9a4edbd 的干净工作树里复现确认。TD-296 的其余修复不受影响，但这个 SHA 不算通过。 | 已修（随 TD-297 提交，TECH_DECISIONS TD-296 补记）：登记到「审计入口与历史阶段」组；本地整站构建无无效链接，`test_docs_site.py` 通过。流程：AGENTS 工作循环第 3 步补充「全量测试前先 `git add` 新文件；增删文档后本地跑整站构建」。 |
| RR-08 | TD-297 变异检查：让 `locked_active_admin` 跳过身份复核后，close_channel、reconcile 与退款各端点共 10 个用例失败，但 **`review_order` 没有任何用例失败**——它在写入边界的管理员复核此前没有测试（只测了外站来源与普通用户）。 | 已修：新增 `test_review_rechecks_the_admin_at_the_write_boundary`（role / status / 凭据版本 3 组），变异下 3 组均失败。 |
| RR-09 | `scripts/build_docs_site.py` 的导读笔记有 7 块与生成结果不一致，HEAD 上同样存在，不是本轮引入。 | 搁置 → 并入 ROADMAP R-04。 |

复核过、没有发现问题的点：各端点原有检查顺序不变（`review_order` 仍先比确认单号，`close_channel` 仍先复核管理员）；删掉的手写 `update(User)` + 重读与 `lock_user` 等价（都是无更新时间副作用的行锁并重读）；6 个测试文件的替身目标改到 `admin_common.lock_user` 后断言未改，若误留旧目标 `monkeypatch.setattr` 会直接报错。本机 PostgreSQL 上受影响的 420 个用例全部通过。

## 2026-09-27：TD-298（账单申请与 bill_reconcile 整理）

范围：`wechat_pay.apply_trade_bill`；`bill_reconcile` 抽出 `_scoped_rows`、`_verify_migrations`、`_payment_code`。另确认 TD-297 的 47a4421 六个 CI job 全部通过（RR-07 的修复生效）。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-10 | 变异检查：删掉对账范围里的交易类型（NATIVE）或币种（CNY）条件，`tests/test_wechat_bills.py` 全部通过。原来只有「其他应用」被排除的用例；这两个条件若在以后的修改里丢失，JSAPI 或外币付款会被当成本应用付款去匹配，并报成「渠道有付款、本地无订单」一类的差异。 | 已修：新增 `test_non_native_or_non_cny_rows_are_only_excluded`（2 组），两种变异分别被抓到。 |

复核过、没有发现问题的点：`apply_trade_bill` 生成的路径与原来逐字相同（原始字节断言 + 变异确认）；`_payment_code` 的判定顺序与结果代码未变，7 种差异都有用例；`_verify_migrations` 仍在调用方原有的会话与超时内执行，`snapshot` 的 SQL 顺序不变（PG 快照隔离用例依赖执行顺序）。

## 2026-09-27：TD-299（管理页未知请求体集中管理）

范围：`app/frontend/payments-admin.js` 与打包产物。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-11 | 变异检查：「发送成功后也删除发送尝试键」和「换单时不清空未知请求体」两种变异下，`tests/test_payments_frontend.py` 全部通过；在重构前的原代码上做等价变异结果相同，是原有缺口。前者若发生，管理员在发送成功后再点一次会生成新 request_id，成为第二次真实退款申请（服务端会以「已有观察」等条件拦截部分情形，但前端这道防线不应无测试）；后者会把上一单的请求体带到下一单（服务端会以确认单号不符 409 拦下）。 | 已修：新增 `submit-resend`、`submit-switch` 场景（源码与打包产物各一组），两种变异分别被抓到；新场景在重构前的代码上也通过，证明描述的是原有行为。 |

复核过、没有发现问题的点：八条成功提示与原三元表达式逐字比对一致；失败时的两处清除条件未变；退款组内的局部变量改名为 `prior`，避免遮蔽新的 `pending` 对象；打包产物已重建，产物漂移检查与所有 Node 场景通过。

## 2026-09-27：R-02 第一部分——关键防线变异检查（TD-300）

范围：`app/deps.py`、`app/security.py`、`app/routers/auth.py`、`app/ratelimit.py`、`app/storage.py` 与 `app/routers/shop.py` 的下载出口、微信支付回调入口、`app/payment_ledger.settle`。另记：`shop-page.js` 经审阅决定不重构——职责单一，注释对应实测 bug，并发保护集中；仅有的三处短重复抽出后反而要跨函数追读。

方法：每个变异只删掉或放宽一条判断，跑该防线相关的测试文件（鉴权组 12 个文件、限流组 7 个、下载组 12 个、收款组 12 个），`-x` 首个失败即判「被抓到」，结束后恢复源码（每次都核对 `git status` 干净）。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-12 | 54 个变异中 18 个存活，其中 7 个是真实测试缺口：登录处禁用账号检查；「恰好一个 Origin 头」；下载出口的订单状态复查；回调金额整数类型；回调流水号长度；**settle 的商户/应用快照比对**（换商户后新商户通知可把旧商户订单记为已付款）；settle 的人工收款依据非空（数据库约束只查 IS NOT NULL）。 | 已补测试（TD-300），每个都在对应变异下失败。settle 快照比对一项此前完全无测试，是本轮最重要的发现。 |
| RR-13 | 另 11 个存活变异判为等价或纵深防御（理由逐项写在 TD-300）。审查中另见 `tests/test_auth_cookie.py::test_authed_get_routes_are_read_only` 的导读生成块与源码不一致（HEAD 上同样存在）。 | 等价项不补测试；笔记不一致并入 ROADMAP R-04。 |

被直接抓到、无需处理的防线（36 个）：禁用账号令牌、凭据版本、管理员角色、跨站元数据、Bearer 豁免、登录来源、令牌声明结构、签发版本、改密递增版本、登录假哈希；限流计数、代理信任、XFF 链、IPv6 /64；下载未付款、退款（锁后/校验后）、冻结对象、链接过期；回调配置、报文大小、时间戳、平台序列号、事件类型、商户/应用/交易类型、币种、金额相等、支付时间时区；settle 的审计事件归属、渠道、流水号为空、金额、重复确认一致性、已付款历史单、paid_at 落库。

## 2026-09-27：R-02 第二部分——OAuth 变异检查（TD-301）

范围：`app/routers/oauth.py`（同意页 GET/POST、令牌端点）。测试组：test_oauth、test_oauth_consent、test_release_boundaries、test_second_review_regressions、test_review_regressions、test_audit_20260915、test_token_revocation。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-14 | 23 个变异中 12 个存活（另 1 个模式写错未参与首轮），10 个是真实缺口：停用客户端、**授权码与客户端绑定**、**签发后禁用的用户**、锁行后复查、签名绑定凭据版本、approve 精确值、response_type、**登记回调地址的格式校验**、生产拒绝 http 回调、畸形 client_id（NUL 一组仅 PG 可区分）。 | 已补测试（TD-301），逐个在变异下失败；畸形 client_id 在本机 PG 上确认。 |
| RR-15 | `used` 预检与原子消费互为双层；原子消费在 PG 上被既有并发用例抓到。授权码明文存库、不支持 PKCE。 | 不补测试；后两项按现有威胁模型接受，理由写入 TD-301。 |

## 2026-09-27：R-02 第三部分——下单、订单状态、交付快照与渠道关单（TD-302）

范围：`app/routers/shop.py`（下单、单查、订单历史、模拟/人工确认、历史订单绑定、下载领取）、`app/order_state.py`、`app/delivery.py`、`app/order_closures.py`。阅读中未发现生产代码错误。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-16 | 62 个变异中 36 个存活，20 个是真实测试缺口。最重要的是：**单查与订单历史的归属过滤**（删掉即越权读取，此前无用例失败）；用过期的内存对象关单时只有 UPDATE 的 status 条件能保护已付款；**两位不同管理员并发关同一单**——现有并发用例只用一位管理员，管理员行锁已串行化，测不到订单锁。 | 已补测试（TD-302），逐个在变异下失败；并发关单用例在本机 PG 上三次变异均被抓到，SQLite 与 PG 上正确代码均稳定通过。 |
| RR-17 | 另 16 个判为等价、双层或不可能状态（理由逐项写在 TD-302）。另见三个导读生成块与源码不一致（HEAD 上即存在）：test_mock_pay「模块装配」与 `_Req`、test_checkout_concurrency `BlockingProvider.__init__`。 | 不补测试；笔记不一致并入 ROADMAP R-04。 |

过程记录：变异运行器在首次运行时因脚本错误把一个变异留在了 `shop.py`，整体超时又把一个留在了 `delivery.py`；两次都当场从 git 恢复，并改为「写入变异也放进 try/finally」。之后每批结束都核对 `git status --short app` 为空。

## 2026-09-27：R-02 第四部分——退款服务层（TD-303）

范围：`app/refunds.py`、`app/refund_requests.py`、`app/refund_submissions.py`、`app/refund_verification.py`。阅读中未发现生产代码错误。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-18 | 94 个变异中 44 个在全部退款测试下仍存活，12 个是真实测试缺口。最重要的是：**核验领取 CAS 的 `attempts` 条件**（另一个 worker 在 SELECT 与 UPDATE 之间完成一整轮时唯一的保护，此前没有用例）；原收款凭证比对元组的流水号与商户/应用；冻结请求体的回调长度与原流水号格式；冻结字节的摘要复核；同一发送请求 ID 换内容重放；配置不完整时不查询；已核验任务不能接管。 | 已补测试（TD-303），逐个在变异下失败；领取竞争用例在 SQLite 与本机 PG 上都抓到变异。 |
| RR-19 | 另 32 个判为等价、路由层先拦、双层或不可能状态（理由逐项写在 TD-303）。附带发现：账本接口的 `refund_prepare_allowed` 不调用 `original_receipt`，账本不一致时提示「可准备」而服务端返回 409。 | 不补测试。提示项不改：服务端 fail-closed，正常流程下收款结算一致写入订单与凭证，不会出现这种状态。 |

过程记录：全量复跑用两个独立 worktree 并行，每批结束核对 `git status --short app` 为空后删除 worktree。

## 2026-09-27：R-03 与 R-05（TD-304）的本轮复审

范围：本批 diff（support-page.js、intent.py、support.py、politeness.py、word.py 及其测试、文档与导读）。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-20 | 复看 diff 时发现两处自己引入的问题：新加的寒暄词「早上好」同样会被子串命中（「早上好几次都打不开」被判为寒暄）；app/tools/README 里 `state_for` 一行仍写「按 scheme/netloc 分组」，与新的站点键不符。 | 已修：带「早」的问候全部改为整句匹配并补反例；README 改为规范化站点键。 |
| RR-21 | 顺带检查同类子串匹配：转人工关键词「人工」会命中「人工智能」（已在 TD-304 修复）；中文技术词「超时」「异常」会把「支付超时」这类平台问题判成专业问题。 | 后者不改：走站内文章检索，检索不到相关内容时照常转人工，风险可接受；理由写入 TD-304。 |

## 2026-09-27：R-04（TD-305）的本轮复审

范围：本批 diff（`scripts/code_reading_narrate.py`、其测试、`docs/code_reading_notes.json` 的 29 块）。所有 regen 的块都用脚本对照 HEAD 核对，第一段未变；全仓 `check` 退出码为 0（142 个文件，4 个登记手写块）。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-22 | 本轮起初的"全仓 check"是在 shell 里拼路径列表，`database init/db_init.py` 按空格被拆开，`check` 在 `entry("database")` 处退出，后面约 40 个文件没有检查到。按正确列表重跑后，又发现 1 处不一致（test_llm_response_bounds）。 | 测试改为在 Python 里从导读 JSON 取全部 .py 路径参数化（`NOTED_PY`），另加一个用例确认名单包含带空格的路径；test_llm_response_bounds 那块已核对并登记为手写块。 |
| RR-23 | 只比文本查不出块边界错位：已提交导读里有 5 块的 `end` 晚了 1–4 行（app/delivery.py 3 块、test_download、test_refund_verification），`add` 在新定义插到原定义之前时也会造成错位。本轮我在同一文件上按 HEAD 重复 remap，行号被平移两次；新检查在提交前就报了出来，已按 HEAD 重建这两个条目。 | `check` 增加"块范围必须包含其定义"检查，修正 5 处错位与 `add` 的拆块方向，并补测试（`add` 用例在旧工具上失败）。 |

## 2026-09-28：界面整理（TD-306）的本轮复审

范围：本批 diff，包括 base.html、5 个页面模板、support.css、3 个前端脚本及其产物、测试和文档。复审时重新截了改后的全部页面，身份包括游客、普通用户、管理员；还截了点选会话和订单之后的状态、登录浮层。三种身份下的页面都没有横向溢出。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-24 | 第一版全站控件规则沿用了站内输入框的浅灰边框（`#cbd5e1`，后来试过 `#94a3b8`）。算了一下：白底上分别只有 1.48:1 和 2.56:1，达不到 WCAG 1.4.11 对控件边界 3:1 的要求。站内已有的 textarea 和登录框一直是这个颜色，此前的对比度检查只覆盖文字色，没发现这个问题。 | 改为 `--control-border: #7d8ca3`（白底 3.41:1，`#f8fafc` 浅底 3.26:1），textarea、登录框和统一规则共用；测试按公式算比值。 |
| RR-25 | 顺带看到的问题，都不属于本批：① 登录浮层的「登录 / 注册」两个标签外观相同，看不出当前是哪一个；② 管理页订单列表直接显示状态码 `pending / manual`，而详情区有中文说明；③ 手机顶栏第二行横向滚动，「站内客服」「毕设服务」在 390px 下滚到屏幕外，没有可滚动的提示。 | 登记为 ROADMAP R-06，之后处理。 |

## 2026-09-28：R-06（TD-307）的本轮复审

范围：本批 diff，包括 auth.js、payments-admin.js、base.html 及产物、两份测试和文档。复审时重新截了登录浮层（切到「注册」）、手机导航（初始状态与滑到最右）、管理页选单后的列表与合同区。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-26 | RR-25 ② 写「详情区有中文说明」不准确：合同区的「状态：」一行同样是原值 `pending / manual`。当时只看了列表截图，没有核对详情区的代码。 | 列表和合同区一起改为「中文（原值）」，共用 `orderState`；这条更正记在这里，RR-25 原文保留。 |
| RR-27 | 查表最初写的是 `Object.hasOwn`，它要求 Chrome 93 / Safari 15.4。站内其他脚本没有用到这么新的 API，没必要因为一张标签表抬高浏览器下限。 | 改为 `Object.prototype.hasOwnProperty.call`；自有属性检查由 `constructor` 用例钉住。 |
