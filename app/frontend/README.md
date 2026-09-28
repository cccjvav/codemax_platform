# 浏览器交互源码

保持原生 JS + Vite，不引入前端框架或 Node 生产服务。文件与行为对应如下：

| 文件 | 输入 / 输出与关键边界 |
| --- | --- |
| `auth.js` | `/auth/me` 与登录/退出表单，顶栏用户名打开的修改密码浮层（`POST /auth/password`）；维护共享用户快照、顺序号与可退订监听器；初始网络错误有兜底，失败退出不假装成功。浮层打开时记住触发元素、Esc 关闭、关闭后焦点只在仍留在浮层内时归还（TD-263） |
| `er-layout.js` | 图数据到布局，纯函数；Node 测试无需安装 d3。按外键深度分列、按文字估算宽度（超长截断）、正交折线只走列间通道和顶部车道，另给出初始视图（TD-279） |
| `er-page.js` | DDL 表单、D3 图与 Word 文件；第三方代码来自本地构建。只画布局算好的东西，负责箭头、悬停提示/高亮、缩放与「查看全图」 |
| `mermaid-page.js` | 自然语言表单到 Mermaid 展示；Mermaid 点「生成类图」才按需 import（与模型请求并行，失败不缓存）；strict 模式，不允许模型放宽为 loose；503（模型并发闸门满）按 `Retry-After`（1–30 秒，默认 5）自动重试一次，再次繁忙原样显示服务端文案，502 等上游错误不重试 |
| `drawio-page.js` | 检查消息 origin/source，以关联的 export 请求读取实时 XML；文档或账号切换替换 iframe 上下文、拒绝旧响应；串行保存并保留 ETag 冲突 |
| `shop-page.js` | 主动下单、无重叠状态轮询、历史订单、短时链接重领；取消/账号切换清理状态，不自动再次下单 |
| `support-page.js` | 客户自己的消息或管理员选中的会话；分页、轮询、UUID 重试去重、账号/会话 epoch、纯文本渲染；按角色切 `.with-inbox` 两栏 class 替代 CSS `:has()`；顶部「先问智能助手」调用公开 `/support/ask`（TD-295），不走会话 epoch，登录变化不清回答；需要人工时把问题填进留言框，未登录先弹登录、登录后再填；固定转人工答案（source=human）在本页按访客/用户/管理员换成页内文案，其他来源原样显示（R-03） |
| `mock-pay-page.js` | 开发模拟支付按钮，不代表真实商户联调 |

公开 HTML 外壳不代表私人 API 公开。权限判断始终在服务端；用户 role 只决定显示管理控件，不能授权请求。
用户在 Drawio 切换文件前应保存或导出需要的工作；换账号会清除账号所属内容。站内客服不是即时在线或邮件通知系统，管理员需要打开页面处理留言。

## 关键函数与状态约定

| 文件 / 函数 | 输入、结果与副作用 |
| --- | --- |
| auth.refresh、onChange | GET 当前用户并发布用户快照；用顺序号拒绝旧响应；订阅返回退订函数。open/close/setMode 只操作登录界面，不授权 API |
| drawio.validXml、exportXml | 导入只接收有界 mxfile/mxGraphModel；exportXml 返回 Promise，关联当前 iframe 导出事件，10 秒无响应拒绝。不能用 autosave 缓存替代显式导出 |
| drawio.resetEditor、syncAuth | 文档/账号切换作废旧上下文并替换 iframe；清除旧账号数据。loading 阻止云文件未返回时提前装载临时内容 |
| drawio.api、save、refreshList、manage | API 处理错误、无效 JSON 及 204；save 串行取得实时 XML，用 ETag 更新；412 保留编辑内容。管理列表用独立序号拒绝旧响应，提供软删/恢复/明确确认后永久删 |
| shop.render、poll、stop | 根据订单状态显示界面；pollBusy 防请求重叠，stop 清计时器，取消按钮另外递增 buySeq 并清订单/补单订阅；状态查询不能偷偷下新单或领链接 |
| shop.buy、doBuy、loadHistory | 主动下单才 POST；buySeq/登录订阅处理旧响应和退订；历史分页读取不等于新订单；失败时给用户可重试路径 |
| support.endpoint、onUser、reset | 由当前用户和管理员选择决定会话 endpoint；账号/会话切换递增 epoch、abort 请求、清除列表和输入，不靠 DOM 隐藏保护数据 |
| support.render、poll、inbox | 使用 textContent 渲染，按消息 ID 去重；维护 oldest/newest，轮询在上次完成后再排 4 秒定时；inbox 游标与消息游标分开，并用请求序号拒绝乱序结果 |
| support 表单提交 | 管理员未选客户不能发送；UUID 优先 crypto，HTTP旧环境降级仅用于幂等而非凭据；一次发送保留 client_nonce，响应丢失可重试；成功也不直接把读取游标跳到 POST ID，否则会漏掉中间管理员回复 |
| er-layout.nodeHeight、layoutEr、initialView | 图数据 → 节点坐标/宽度/截断文字与折线 points/path；initialView 给出初始缩放（能读清就全图，否则 0.75 从左上角开始）。无 DOM/网络。页面脚本负责 D3 渲染、错误提示和 Word 附件请求 |
| mermaid-page / mock-pay-page 事件处理 | 前者调用同源生成 API 并捕获渲染错误；后者只是开发模拟付款确认，不证明真实收款 |

以上函数多在模块闭包内，并非公共 window API。事件绑定、DOM ID 与模板需要共同修改；JS 当前只提供文件级自动索引，这些解释是人工核对内容。

## 模块职责

手写浏览器交互源码，按页面构建；共享登录态由 auth.js 管理。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/frontend/auth.js`](auth.js) | `4cb99a94dd67` | L1–L291 |
| [`app/frontend/drawio-page.js`](drawio-page.js) | `462e8b4acdfa` | L1–L215 |
| [`app/frontend/er-layout.js`](er-layout.js) | `a2a6d4769b38` | L1–L318 |
| [`app/frontend/er-page.js`](er-page.js) | `ff0461e26931` | L1–L221 |
| [`app/frontend/mermaid-page.js`](mermaid-page.js) | `4c9ee202d8a8` | L1–L125 |
| [`app/frontend/mock-pay-page.js`](mock-pay-page.js) | `0051085bb559` | L1–L46 |
| [`app/frontend/package.json`](package.json) | `8b4333b81f4f` | L1–L14 |
| [`app/frontend/payments-admin.js`](payments-admin.js) | `bfac35ca4ea5` | L1–L363 |
| [`app/frontend/shop-page.js`](shop-page.js) | `cab919d15ed0` | L1–L376 |
| [`app/frontend/support-page.js`](support-page.js) | `c1177cf0a5b7` | L1–L224 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

Jinja 模板加载 app/static/js 中的构建产物；浏览器请求使用同源相对路径。

## 变更与验证

修改后运行 npm run build 并提交对应产物；异步操作需要处理退出登录、换账号和过期响应。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

## payments-admin.js：管理员收款工作台

reset/clearDetail递增会话与详情序号，清私人数据/输入、abort旧请求；request携带同源Cookie/no-store，只有当前会话401/403才能清屏，旧账号错误不能清掉新账号。list用独立序号和50条替换分页，detail用详情序号和独立事件游标，textContent呈现原合同、凭证和事件；不拼HTML、不写浏览器存储。operate先校验手输单号/依据，人工分数和参考流水，或核查/原文件绑定，再确认弹窗、禁止重叠操作。结果丢失先刷新凭证，不自动重试写操作；旧账号已提交的服务端操作不保证取消，但迟到结果不会回填新账号。按钮/字段与payments-admin.html同步，构建入口在vite.config.mjs；来源和构建产物都执行Node回归，仍需真实浏览器验收。

## 第五批：复核与游标上下文

payments-admin.detail显示复核状态/操作人/说明，按新资料重新待办；operate的review分支要求共同手输单号和160字内说明，带资料摘要/版本/稳定请求ID。5xx或丢响应保留原请求，4xx或读到本请求已提交则允许重新核对，旧账号响应仍丢弃。nonce只是幂等标识，不是认证秘密，现代crypto.randomUUID优先，旧开发上下文使用随机降级并由数据库唯一约束兜底。list的游标绑定筛选参数，改变范围不复用旧游标；空页有游标显示继续提示。没有自动复核/后台轮询；保存后需刷新左侧清单。

## 第六批：退款与原付款分开展示

payments-admin把退款查询/人工已退款登记纳入原有账号、详情代次和busy保护。独立字段存原商户退款号或人工退款流水/金额/带时区成功时间，确认弹窗明确不发起退款；来源从原收款凭证决定，已退款人工表单隐藏。失败保留原字段，先刷新记录再按原流水/时间重试，不另造退款号；切换账户清空字段和退款证据。所有证据用textContent，不插HTML。shop-page先看refunded展示停止下载说明；paid/downloaded仍是历史付款/领链状态。Node VM测试源码和提交bundle，不代表浏览器实机验收。


## 第七批：通知只展示/填号，不自动提交

detail在原有会话/详情序号保护内更新currentNotice，明确SUCCESS通知不等于本地退款完成；部分通知只提示核账。refund-prefill是type=button，只填refund-no，要求当前管理员/有效全额通知/非busy/可见操作区，不填确认号或依据、不fetch。clearDetail清空通知和禁用按钮；刷新进行中隐藏操作区，旧通知不可借按钮提交。账号切换/迟到详情仍由epoch/view保护。所有内容继续textContent，Node分别测源码与bundle，不代替浏览器验收。


## 第八批：准备请求的稳定身份与确认

管理脚本新增currentRequest/pendingRequest、准备金额和独立展示区。当前十一类表单共用forms列表控制隐藏/忙碌/清屏；nonce共用原有UUID优先、随机降级逻辑，它是幂等标识，不是认证秘密。prepare分支验证手输原单/全额整数分/依据，再明确确认“不是发送授权”。首次提交保存完整pendingRequest，网络错误不换ID；改未确认内容会拒绝并提示读原记录，重试仍用原body。成功或GET读到同request_id才确认已保存；换单/账号清空本地状态，服务器一单唯一仍保护晚到提交。

详情用textContent展示原商户/app、固定号码、登记人/依据与准备/匹配成功/另号成功三种含义。准备记录不推断渠道发送状态；现有通知/成功凭证仍分区。request-prefill只填查询框，不POST、不代填确认号/依据；成功凭证存在时禁用。没有退款发送按钮，历史准备不能成为未来自动发送队列。


## 第九批管理页九类显式操作

新增退款授权与发送两个表单；输入原退款号/全额、共用手输订单号和内部依据，客户reason另外输入并按TextEncoder核80字节。展示冻结正文/摘要、首次授权人和最近尝试；只有部署开关开且有授权才显示发送，后端仍独立判定。授权确认不会紧接着自动发送。

pendingAuthorization/pendingSend各保留完整body；未知时不能改内容；同发送尝试重放只读回。新尝试按钮只清页面键，须再次确认发送，服务端执行冷却/观察保护。换账号清空所有敏感输入与pending，旧响应由epoch/view丢弃；退出登录无法取消已开始的渠道请求。内存状态不是关闭浏览器后的持久队列，应先刷新原单。


## 第十批停止表单

管理页十一类显式操作共用代次/忙碌/手输确认。refund-stop复用原号/全额/授权摘要确认，内部依据作为停办/纠错说明；pendingStop保存原body/key，未知时改内容拒绝，成功或GET读到同停止ID才确认。即使发送开关关闭也能停止；已有stop隐藏发送及停止表单，并阻断新尝试按钮。

stop-view用textContent单独展示首次人/时间/依据及“不是渠道取消”警告；换账号清除stop/pendingStop和输入，迟到响应丢弃。旧尝试可能仍在网络中，前端不把登出或停止按钮宣称为网络召回。没有恢复发送按钮。


## 核验任务展示

payments-admin详情新增verification-view，textContent展示原退款号、通知事件ID、状态、次数、结果码和下次/租约时间；不新增自动POST/轮询。开关仅说明允许worker，不是在线证明；默认仅核验时SUCCESS标待管理员，同原号已有成功凭证时不再声称待确认。账号变化清屏，迟到详情由epoch/viewSeq丢弃；Node源码/bundle回归不是浏览器验收。

## 第十二批：核验调度表单

第十一类verification-control手动填当前列表的任务ID，选hold/retry，并复用手动订单确认及依据。verificationJobs只来自当前详情；确认框显示原退款号及仅此任务/不召回GET/不清零次数边界。pendingControl保存原完整body/key/snapshot；未知失败后新详情不能偷偷替换请求，改依据/任务/动作阻止提交，409已知冲突才解除pending以重新确认。成功响应或读到相同latest_control key可解除待确认；账户切换清空任务/字段/首笔动作，丢弃旧响应。所有服务端文字经textContent。GET展示不触发重排，点击取消不POST；source/bundle都用Node VM回归，不等同真实浏览器签收。


## 第十三批：区分系统凭证与人工签字

payments-admin从ledger读取refund.recorded_by及verification_event_id，以textContent显示系统核验（非管理员代办）/真实管理员和事件ID。refund_auto_record_enabled仅影响核验提示，不触发请求/授权；页面读到Web配置不证明独立worker在线。成功凭证仍独立展示不受事件翻页影响，账号/详情切换清屏与迟到响应栅栏不变。源码与bundle的system-on/off/race Node场景检查无POST、系统文字、事件编号及清屏；不是浏览器布局验收。


## 第十四批：独立更正确认与版本历史

新增refund-reauthorize表单，仅服务端允许时显示；共用手动原号/全额/客户原因，确认前版ID/摘要后POST一次，不调用send。pendingReauthorization保留完整未知body/key，修改原因/依据/前版会拒绝盲重试；详情读到历史中该key才视作可恢复记录。新授权成功后仍需单独发送。

authorization-history用textContent显示有界历史/截断、前版/首笔人/正文/停止，账号和详情隔离沿用epoch/viewSeq，clearDetail同时清历史和未知请求。Node源码/bundle覆盖save/unknown/changed/denied/race，不冒充浏览器。

第十六批新增第十三类channel-close显式表单：本地closed+可信NOTPAY只是显示条件，后端锁内重查；输入原合同金额，弹窗警告旧码可能失效但不是退款。pendingClose冻结原query_attempt/key/body，刷新新查询不偷换未知命令，恢复不重发；改未知依据拒绝。换账号清屏/拒迟到，关单观察仅textContent展示，没有自动reconcile/POST或localStorage财务正文。


## 2026-09-23：登录态导航、编辑器首屏与访客文案（TD-272）

- `auth.js::paint()` 同步顶栏管理员入口的可见性（已登录且 `role !== 1` 时隐藏）。它只影响导航，不参与鉴权判断。
- `drawio-page.js::syncAuth()` 用 `identityReady` 区分「首次同步」与「账号切换」：首次只登记身份（访问者与已登录都一样），**不重建** iframe —— 首屏此前会把模板里刚开始加载的 `embed.diagrams.net` 换掉，登录态异步返回时再换一次（访客 2 次、已登录 3 次下载）。账号切换仍走完整重置，账号隔离不退化。
- `mermaid-page.js::serverMessage()` 把 502 且 detail 含 `LLM_API_KEY` 的服务端文案换成访客能懂的「AI 生成暂不可用（站点未开启模型服务）…」；服务端 detail 不变，其它 502（上游故障）原样显示。

## 2026-09-23：会话到期与主动退出分开（TD-273）

2026-09-23 复核的 N-01（P2 回归）：TD-270 把每个 401 都交给 `sessionExpired()`，它会清用户并通知订阅者；订阅页把这条通知当成「用户主动退出」，于是 Drawio 重建编辑器加载空白图、客服清空未发送的留言 —— 提示变好了，用户的活儿却没了。

- `auth.js::notify(reason)` 现在把第二个参数传给监听器（默认 `"sync"`，只用一个参数的旧回调不受影响）；`sessionExpired()` 用 `"expired"`。主动退出、登录、换账号仍是 `"sync"`。
- `drawio-page.js::syncAuth(user, reason)`：`reason === "expired"` 时只更新顶栏与状态文字，**不清 identity / 不丢 xml / 不重建 iframe**；同一账号重新登录后 identity 仍是原值，可以直接接着保存；换成别的账号才走完整重置，账号隔离不退化。
- `support-page.js::onUser(user, reason)`：到期时先把 `#support-body` 的草稿取出来，重置视图后再放回去；轮询照常停止。
- 到期提示文案不变（仍是「登录已过期，请重新登录后继续；刚才的操作未提交」），只是这句话现在是真的。
- 另有 `CodeMaxAuth.settled`：启动瞬间 `user` 是 null，但那是「首次 /auth/me 还没回来」而不是「访客」。`drawio-page.js` 据此判断 `settled === false && !user` 时先什么都不做，等身份真正到达再登记 —— 因此「页面打开时已登录」也只加载一次外部编辑器（真实浏览器实测 3 次 → 1 次），而账号切换仍走完整重置。

## 2026-09-24：已购用户与返回商城恢复（TD-274，复核 N-05/C8）

- `shop-page.js` 新增 `entitledNo`/`restored`/`userActed` 与 `resetPrimary()`/`offerDownload()`：登录后取一次 `GET /shop/orders`，有 `paid`/`downloaded` 且未退款的订单就把主按钮从「立即购买」换成**「已购买，去下载」**并在 `#buy-note` 说明原因。点这个按钮走 `primary()` → `fetchOrder()` → `render()`，**不会 POST /shop/orders**（用户 2026-09-24 确认：单 SKU 数字商品不设计复购）。
- 返回恢复：`bootstrap()` 只跑一次 —— 先认 `?order=CM…`（收银台返回链接带的单号，正则限定字符集），再 `restoreLatest()`。`restoreLatest()` 的顺序是**已购优先**：最新一张就是已购单就直接显示该单（刚付完款返回商城时不用等轮询），否则有已购权益就只换按钮（更晚的未付款单不再诱导付款），都没有才恢复未过期的待支付单并重新起轮询；**过期单不恢复**。`fetchOrder()` 用 `undefined`/`null` 区分「这单不存在」与「暂时读不到（未登录/网络）」，后者留给下一次身份变化。
- 账号隔离：身份变化时 `resetPrimary()` + `restored = false`，再对新身份 `bootstrap()`；`userActed` 保证用户点过购买/取消/历史订单后自动恢复不再介入，不和页面内的操作抢状态。
- 主按钮的还原值 `BUY_TEXT` 在加载时从服务端渲染的按钮文案里取（价格来自配置），退款单被排除在权益之外 —— 全额退款后按钮必须回到「立即购买」，否则退完款就再也买不了。

## 2026-09-25：真实浏览器复核的交互修正（TD-278）

- `auth.js`：登录成功后清空 `#auth-pass`（浮层只是隐藏，DOM 仍在；否则明文密码留在页面里，退出后再打开还原样填着）。用户名保留。
- `drawio-page.js`：`syncAuth` 切换 `#drawio-login-prompt` —— 已登录隐藏「云端保存需登录」整段，访客或会话到期后重新出现。
- `payments-admin.js`：空结果写 `#finance-list-empty` 而不是 `<ul>` 的 textContent；`detail()` 显示 `#finance-detail`、`clearDetail()` 隐藏它并恢复提示段落。
- `shop-page.js`：下载与历史订单的 `res.json()` 加 `.catch(() => null)`，反向代理 502/504 的 HTML 页不再显示成「Unexpected token <」。
- `support-page.js`：追加新消息时，如果用户停在底部（或首屏）就滚到最新一条；正往上翻旧消息时不打断。

验证：`tests/test_auth_cookie.py`、`test_drawio_auth_state.py`、`test_payments_frontend.py`（empty-list / detail-visibility）、`test_second_frontend_regressions.py`（support-scroll）对源码与 `app/static/js` 产物各跑一遍，改前均失败。

## 2026-09-25：ER 图布局重写（TD-279，复核 N-04 / V-04）

原布局每行固定 3 张表、节点固定 230 宽，连线是「子表右边中点 → 父表左边中点」的三次贝塞尔曲线，viewBox 永远等于整图。真实浏览器量本项目 16 张表 / 24 个外键：**20 条线穿过别的表**，长列名/注释压出方框，整图被缩到约 40%（12px 字成了 5px）。

- `er-layout.js::layoutEr`：按外键深度分列（父表在左，环用递归栈截断），同层按父表位置排序，单列超过 900px 拆子列，无关系的表单独放最右；节点宽度按文字估算（`textWidth`，CJK 按 1em，只求不低估）夹在 160–340，放不下用 `fitText` 截断并保留原文；连线是正交折线，起点/终点钉在外键列与被引用列那一行（`rowCenter`），竖线只走列间通道（指向同一父列的线共用一条，形成总线），跨列的线走表格上方的车道。返回的 `nodes`/`links` 保留原字段，另加 `points`/`path`/`header`/`rows`。
- `er-layout.js::initialView`：整图按比例放下后仍 ≥ 0.75 就居中显示全图，否则 0.75 从左上角开始。
- `er-page.js::renderEr`：viewBox = 画布像素尺寸，缩放全交给 d3.zoom；线加箭头（指向父表），关系名改为悬停提示，截断文字悬停看原文；PK 琥珀、FK 蓝；鼠标停在表上高亮它的全部关系线；`#er-fit` 在全图与可读尺寸之间切换（整图本来就放得下时不显示）；aria-label 写明表数与关系数。
- `er-page.js` 生成与 Word 两处失败分支的 `res.json()` 加 `.catch(() => null)`，同 TD-278 的商城修正。

实测（同一 16 表 DDL，Chromium + Noto Sans SC）：穿表 20/24 → 0/24，文字溢出 0，axe 无违规，手机 390px 无横向溢出。

## 2026-09-25：修改密码入口与 Mermaid 按需加载（TD-280，V-06 收尾）

- `auth.js::openPassword / closePassword / #pw-form.onsubmit`：顶栏用户名（`#auth-who`，现为按钮）打开修改密码浮层。两次新密码不一致、新旧相同在前端拦下，不白白消耗改密限流额度；原密码错（400）显示服务端文案；401 只可能是会话本身到期，先关浮层再交给 `sessionExpired`。成功后服务端已吊销旧 token、给本会话换了新 cookie，所以不 refresh；三个密码框在打开、关闭、成功时都清空。退出/到期时 `paint()` 顺手关掉浮层。焦点归还抽成 `returnFocus(box, from)`，两个浮层共用。
- `mermaid-page.js::loadMermaid`：`import("mermaid")` 按需加载并缓存 promise，`initialize`（strict）在加载完成时调用；下载失败清缓存，下一次点击重新下载。提交时与模型请求同时开始下载；渲染器下载失败时仍显示生成的源码。

## 2026-09-26：商城状态视图去掉死区（TD-281）

- `shop-page.js`：`ST` 去掉 `downloaded`（从无分支显示它，模板区段已删）；`render()` 的 paid/downloaded 合并分支不变，closed 分支写的 `#x-no` 现在就在关闭区里。

## 2026-09-27：管理页未知请求体集中管理（TD-299）

`payments-admin.js` 原先用八个变量（pendingRequest、pendingReview、pendingAuthorization、pendingSend、pendingStop、pendingReauthorization、pendingClose、pendingControl）保存「结果未知、必须原样重发」的请求体，设置、成功清除、失败清除、换单清空和「详情里看到同一 request_id 就清除」分散在五处，各自按名字逐个处理。现改为：

- `pending`：按表单类型索引的对象；`RESUMABLE` 列出可恢复的八类表单。确认后 `pending[kind] = body`，成功后除 `refund-send` 外都删除（发送保留键，再点只读回同一尝试，不会第二次转款），`clearDetail` 一句 `pending = {}` 清空。
- `settle(kind, id)`：详情里出现同一 request_id 即清除；重新授权按历史列表判断，仍单独写。
- `DONE`：成功提示查表，文案未变；未列出的类型用通用结果文案。

上文历史批次里的 pendingXxx 名称即对应 `pending["表单类型"]`。新增 Node 场景 `submit-resend`、`submit-switch` 守住「成功后保留发送键」和「换单清空」两条此前没有测试的行为。

## 2026-09-28：列表行与选中标记（TD-306）

- `payments-admin.js`：订单列表按钮 `className = "item"`，文字改为「订单号 + 换行 + 其余信息」（`button.item` 是 `pre-line`，仍只用 `textContent`，不拼 HTML）；点击时先清掉其他行的 `aria-current` 再标记本行。
- `support-page.js`：会话列表同样用 `item` 与 `aria-current`，刷新列表时按当前 `target` 重新标记；管理员未选会话时标题是「请先选择一个客户会话」（原来是没有内容的「我的留言」）；`formatTime()` 固定按 `zh-CN`、24 小时制输出消息时间（原来的无参 `toLocaleString()` 跟随浏览器语言，英文系统上是「9/28/2026, 9:49:30 AM」），无效时间返回空串；游客提示里的 `#support-login-btn` 调 `auth.open()`。
- `shop-page.js`：「我的订单」按钮用 `item`，点开的那条标 `aria-current`。
- `setAttribute` / `removeAttribute` 用可选调用：旧的 Node 测试替身元素没有这两个方法。

## 2026-09-28：状态中文标签与登录标签（TD-307）

- `payments-admin.js`：`STATE_LABELS`（pending/paid/downloaded/closed，对应 `app/order_state.py` 的 `STATES`）与 `MODE_LABELS`（wechat/manual/mock/legacy）经 `orderState(row)` 显示成「中文（原值）」，订单列表与合同区共用。原值保留在括号里，便于对照日志和数据库；查表只认自有属性，未列出的值原样显示。
- `auth.js`：`setMode` 给当前标签 `aria-pressed="true"`、另一个 `"false"`，读屏与 CSS 共用。

## 2026-09-28：写操作按表单类型构造请求（TD-308）

- `payments-admin.js`：`BUILD[kind](number, evidence)` 负责各类表单的专属校验与请求：返回字符串表示校验失败（`operate` 显示为提示），返回 `null` 表示静默放弃，否则返回 `{url, body, summary}`。授权、重新授权、发送、停止四类由 `refundAttempt(kind)` 生成。
- 结果未知的请求统一经 `resume(kind, same, fresh)`：`same` 中的字段须与保存的请求体一致，否则拒绝；没有保存的才新建。各表单比较的字段沿用原规则。
- `operate` 只做公共校验（选单、可见、busy、手填单号、3–500 字依据）、确认、保存 pending、POST 与结算。新增一类写操作时：在 `forms` 里登记、在 `BUILD` 里加构造函数；结果未知时需要原样重发的，再加进 `RESUMABLE`。

## 2026-09-28：模拟收银台失败提示（TD-309）

- `mock-pay-page.js`：失败提示改为「支付失败：原因」，原因经 `window.CodeMaxAuth?.errorText` 生成（auth.js 缺席时为「请求失败（状态码）」），与其他页面措辞一致；非 JSON 应答不再显示成「失败 502：{}」。401 与成功分支不变。

## 2026-09-28：错误文字统一（TD-315）

- `auth.js` 导出 `failureText(e)`：`fetch` 的网络层失败（`TypeError` 且文字是 Failed to fetch / NetworkError… / Load failed）显示「网络连接失败，请检查网络后重试」，其余原样。
- 各页用一行 `failure = (e) => CodeMaxAuth.failureText?.(e) ?? e.message` 显示错误，不直接拼 `e.message`，由 `test_frontend_pages_never_show_raw_exception_text` 守护。
- `support-page` 的 `errorText` 委托 `auth.errorText`（原来是逐字相同的副本）；`er-page` 的「导出 Word」请求期间禁用；`drawio-page` 的 `manage()` 检查列表是数组。

## 2026-09-28：流程图页未保存改动确认（TD-316）

- `drawio-page.js` 用 `dirty` 记「既没保存到云端、也没下载到本地的改动」：收到 `autosave` 置位；保存或下载成功且期间没再改、打开/新建/导入成功、换账号时清零。
- 打开、新建、导入、移走正在编辑的图之前经 `discardOk()` 询问；`beforeunload` 在 `dirty` 时拦截关闭页面。由 `tests/test_drawio_unsaved_changes.py` 守护。

## 2026-09-28：出错提示分阶段（TD-318）

- `mermaid-page.js`：`initialize` 带 `suppressErrorRendering: true`，Mermaid 解析失败时不再往预览区画「Syntax error in text」图。`renderFailure(e)` 只取解析器提示的第一行，拼成「无法绘制模型生成的图（Mermaid 提示：…）。下方是模型给出的源码…」。提交时记录阶段：请求阶段（含断网）显示「请求失败：…」，渲染阶段清空预览区并显示上面的说明，源码框保留。
- `er-page.js`：同样按阶段显示「请求失败：…」或「渲染失败：…」。
- 两条类图提示原来都说「上方是…源码」，源码框其实在错误提示下方，已改成「下方」。由 `tests/test_mermaid.py` 的 Node 桩和 `tests/test_er_page.py` 守护。

## 2026-09-28：ER 截断改为一次扫描（TD-320）

- `er-layout.js` 拆出 `charWidth`；`fitText` 一次扫描累加前缀宽度，不再逐字删末尾并整串重量（原来平方级，2 万字符的表名卡住页面约 8 秒）。截断位置与原实现逐位一致，由 `tests/test_er_page.py` 按定义核对并计时。
