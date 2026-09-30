# 页面模板

Jinja 只负责 HTML 外壳、表单、语义结构与站点上下文；真实交互在 `app/frontend/`，不再把旧内联脚本行号当作实现位置。

- `base.html`：导航、登录/注册浮层。经典 `auth.js` 在页面交互脚本之前执行；OAuth 同意页关闭这套登录控件。全站配色按 WCAG AA 取值（蓝 `#2563eb` 5.17:1、绿 `#15803d` 5.02:1、灰 `#64748b` 4.76:1，TD-263），`button:disabled` 灰化、`:focus-visible` 焦点环；浮层容器带 `role="dialog" aria-modal aria-labelledby="auth-title"`，Esc 关闭与焦点归还由 `auth.js` 实现。回归 `tests/test_ui_accessibility.py`；真实浏览器/读屏实测仍归 L-04。
- `er.html` / `mermaid.html`：工具表单和结果容器；本地构建的 ES module 包含第三方依赖。
- `drawio.html`：第三方编辑器、云端文件与回收站管理、本地导入/下载。保存前通过 export 协议请求新 XML。展开「管理云端文件 / 回收站」即加载列表，都为空时 `#diagram-manage-empty`（role=status，始终存在）写明没有文件（TD-333）。
- `shop.html`：固定数字商品、支付状态、历史订单和链接重领。定制需求引导至站内客服，不混作数字商品下单。
- `payments-admin.html`：管理员订单登录壳、筛选、合同/凭证/事件、人工确认/主动查单/历史绑定表单；hidden强制隐藏避免grid样式覆盖，所有数据另经鉴权API。
- `support-center.html`：公开的登录提示外壳；私人消息、会话列表及管理员操作都由鉴权 API 提供。登录提示之前是对访客开放的「先问智能助手」表单（TD-295，调用 `/support/ask`，最多 2000 字，与 `SupportIn` 一致）。
- `oauth_consent.html`：无脚本同意表单；签名绑定用户与凭证版本，回调 query 保留。
- `mock_pay.html`：开发用模拟支付，生产启动检查禁止启用。
- `not_found.html`：浏览器打开不存在的地址时的站内 404 页（TD-333），由 `routers/site.py` 的 `not_found_handler` 渲染；不回显请求路径。API 与页面脚本的请求仍收到 JSON。

修改 DOM ID 时同步检查页面脚本；不要用用户/模型文本拼接 HTML。客户消息由 `textContent` 渲染。

界面文字约定（TD-331，`tests/test_ui_accessibility.py` 检查）：一句中文不要为了行宽折成两行，HTML 会把换行显示成一个空格；引用按钮名、状态名用「」，不用弯引号；做成按钮样子的主操作链接写 `<a class="cta">`，样式由 `base.html` 的 `a.cta` 统一提供，页内不要另写一份。

## 模块职责

Jinja 页面外壳、表单与导航；交互实现放在 frontend。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/templates/base.html`](base.html) | `063dde61a830` | L1–L290 |
| [`app/templates/drawio.html`](drawio.html) | `006fcc083868` | L1–L53 |
| [`app/templates/er.html`](er.html) | `d74f3585085a` | L1–L45 |
| [`app/templates/index.html`](index.html) | `5fea01a727a8` | L1–L13 |
| [`app/templates/mermaid.html`](mermaid.html) | `3edfa668e206` | L1–L21 |
| [`app/templates/mock_pay.html`](mock_pay.html) | `27ca244aede9` | L1–L30 |
| [`app/templates/not_found.html`](not_found.html) | `9cb65036a155` | L1–L9 |
| [`app/templates/oauth_consent.html`](oauth_consent.html) | `76157115f7b4` | L1–L22 |
| [`app/templates/payments-admin.html`](payments-admin.html) | `61528f3c429a` | L1–L175 |
| [`app/templates/shop.html`](shop.html) | `c551a30280fe` | L1–L123 |
| [`app/templates/support-center.html`](support-center.html) | `202c15e1fc2c` | L1–L57 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

site.py 提供页面上下文；HTML 自动转义，不信任模型、消息和用户文本中的标记。

## 变更与验证

页面 ID 变更要同步脚本和模板测试；新增承诺必须与实际支付、交付和人工服务能力一致。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

payments-admin增加复核状态、三种处理进度及两个投影筛选。保留真实到账/永久绑定各自的确认文案；完成本轮复核不是退款成功，没有伪造退款按钮。

## 第六批界面合同

payments-admin.html增加成功退款凭证区、原商户退款号查询与人工已完成全额退款记录表单，复用完整单号/依据确认；不提供发起退款按钮。shop.html增加st-refunded，供refunded字段选择，不把原支付状态改成取消。字段与app/frontend脚本同步构建后再验证；浏览器与真实商户仍需单独签收。


第七批增加finance-refund-notice独立线索区和finance-refund-prefill填号按钮（type=button、默认disabled）。与成功退款凭证区分开，按钮不能触发表单submit；是否可见/可用由脚本配合原凭证渠道与当前通知决定，真正权限仍在API。


第八批增加request-view、refund-request表单、request-amount及request-prefill。准备表单明确一单一笔全额、未发送/未授权自动发送/不撤权；使用共同手输单号/依据。两个填号按钮均type=button，保存准备的submit只调用本地接口。现行控制列表和脚本同步；页面壳不嵌入私人准备数据。


## 第九批财务模板

新增submission-view、客户原因、手动原准备号/全额以及独立authorize/send表单。发送区默认hidden，文案明确可能真实转款；new-attempt按钮是type=button只准备下一尝试，不提交。正式启用须商户/恢复验收；普通核验按钮仍不转账。


## 第十批停止控件

新增stop-view和refund-stop表单，默认hidden；明确只阻止新的本站发送，不能撤回已有尝试/渠道退款，不释放原号或改变下载。submit发送到stop路径，不复用真实send动作。无需开启退款发送开关才能使用。


## 退款核验队列

payments-admin新增只读finance-verification-view，与通知线索/成功凭证分区；不增加按钮或改变十一类显式操作。后台查询成功观察不直接等于权益撤销，文本由payments-admin.js安全填充。

## 第十二批：所选核验任务的人工入口

payments-admin新增verification-control-view和verification-control表单（任务ID、hold/retry选项及独立提交按钮）。仍需页面公共原单确认和依据；说明暂停只作用当前任务，其他通知不受影响，在途GET不能召回，8次总预算不重置。控件隐藏/快照不是服务端权限，路由必须再次验证。


## 第十四批管理表单

payments-admin新增不可覆盖授权历史details与独立重新授权表单；客户原因输入置于两个授权表单之外由JS显式读取验证，共用确认单号/原退款号/全额/依据。停止说明限定原版本，不误称解除停止或召回渠道；模板只提供壳，权限/可更正条件在服务器重查。

管理页渠道关单分区与退款分开：显示本地closed不等于渠道关闭、先独立查单和手输合同金额，按钮只提交用户确认的close-channel；未知恢复另由源码保留原命令。


## 2026-09-23 排版、导航与表单契约（TD-272）

真实 Chromium 复核（桌面 1366×900 / 手机 390×844、axe-core 4）暴露的问题与本次修改：

- `base.html` 新增跳过导航链接（`<a class="skip" href="#main">` 与 `<main id="main">`）与站点图标 `<link rel="icon" href="/static/favicon.svg">`（此前每个页面都会多打一次 `/favicon.ico` 并 404）。顶栏「订单管理（管理员）」加了 `id="admin-entry"`，**默认 `hidden`**，只有 `auth.js` 确认 `role === 1` 才显示（TD-272 追加；页脚另有常驻入口，管理员登录前也找得到）。**前端隐藏不是权限**，角色仍由 `require_admin` 读库判断。
- 表单控件字体：`button, input, select, textarea { font: inherit }` —— 浏览器默认的 13.33px Arial 比正文小一号且字体不一致；窄屏另有 16px 覆盖，避免 iOS Safari 在输入框聚焦时放大整页。窄屏导航与页脚链接加内边距，命中区从 21–24px 提到 ≥24px（WCAG 2.2 AA）。
- 页面标题层级：整站每页**一个** `<h1>`（顶栏站点名）。`er.html`/`mermaid.html`/`drawio.html` 渲染 `page_heading`（值来自 `Tool.title`，由 `routers/site.py` 注入；首页为空）。`payments-admin.html` 的 `<h1>` 降为 `<h2 class="finance-heading">`，字号由 CSS 保持。
- 订单管理页：`.finance` 在窄屏去掉外层内边距与 section 内边距，`#finance-title`/`#finance-list button` 允许任意位置换行（28 位订单号此前把 390px 页面撑出 28px 横向滚动）；只读凭证并入 `<details class="finance-readonly" open>`（默认展开，折叠只是减少滚动）；`finance-refund-send`/`finance-refund-stop` 两个真实资金动作加 `.danger` 红框，与只读信息区分。

## 2026-09-24 已购状态与收银台返回（TD-274）

- `shop.html` 主按钮下新增 `#buy-note`（默认 `hidden`，脚本写已购说明）；FAQ 增加「已经买过一次，还能再买吗？」——同一商品不重复购买，但**全额退款后按钮会恢复为「立即购买」**，模板文案与 `shop-page.js` 的判定必须一致。
- `mock_pay.html` 的「返回商城查看订单」链接改为 `{{ shop_path }}?order={{ order_no | urlencode }}`。原因：`/shop` 响应带 `no-store`（全局中间件），Chromium 拒绝入 bfcache，返回时是全新加载、`currentNo` 为 null —— 不带单号就只能落回落地页，用户刚付完款却看不到自己的订单。没带单号时不拼空的 `?order=`。

## 2026-09-25 顶栏两行、输入框字号真正生效与页面结构（TD-278）

真实 Chromium（桌面 1366×900 / 手机 390×844、Noto Sans SC、axe-core 4）复核后修改：

- **顶栏**：「站内客服 / 订单管理（管理员）/ 商品」从 `.actions` 移进 `<nav aria-label="站点导航">`；桌面用 `.nav-end { margin-left: auto }` 把它们推到右侧（视觉不变），`.actions` 只剩登录/退出控件。≤900px 时 `header` 改为两列 grid：第一行站点名 + 登录态，第二行整组导航 `nowrap` 横向滚动。手机顶栏高度 189px（管理员更高）→ 88px。
- **手机 16px 输入框**：TD-272 的规则写在样式表中段，被后面的 `textarea { font: 13px … }` 和特异性更高的 `.modal input { font: inherit }` 盖掉，实测登录框、客服留言框、DDL 框全是 13px。现在它是 `base.html` 样式表的**最后一条**并点名 `.modal input`；页面模板自己的 `<style>` 不许再给输入控件写 `font`/`font-size`（`drawio.html` 工具条的 `font: inherit` 已删，测试钉住）。
- `.page-heading`（20px；TD-287 起 22px）移到 `base.html`，三个工具页标题一致（此前只有 drawio 自己定义，ER/Mermaid 落回浏览器默认 h2）。
- `shop.html`：「我的订单」移到所有状态区**之后**。付完款回到 `/shop`，此前「我的订单」排在「支付成功」上面，h3 也先于状态区 h2（axe `heading-order`）。
- `drawio.html`：「云端保存需登录：[登录 / 注册]」包进 `#drawio-login-prompt`，登录后由脚本整段隐藏（此前与「已登录，可保存到云端」同时出现）。
- `payments-admin.html`：列表空结果写进列表外的 `#finance-list-empty`（`role="status"`）——往 `<ul>` 里直接写字命中 axe `list`（serious）；选单前只显示 `#finance-detail-hint`，合同/凭证/操作全部包在默认 `hidden` 的 `#finance-detail` 里；`.finance pre:empty` 不画空框。
- `mock_pay.html`：页内标题 `h1` → `h2.mock-title`，恢复整站每页一个 h1（单 h1 测试现在也覆盖这页）。

## 2026-09-25 ER 画布工具栏（TD-279）

- `er.html`：SVG 包进 `section.er-view`，前面是生成后才显示的 `#er-tools`（「查看全图」按钮 `#er-fit` + 操作说明）；SVG 加 `role="img"`，`aria-label` 由脚本写成表数与关系数。页内 `<style>` 只排这一块，不给输入控件写字体（TD-278 的 16px 层叠约束不受影响）。`ddl-input`/`er-word`/脚本路径等测试钉住的 ID 不变。

## 2026-09-25 修改密码浮层（TD-280）

- `base.html`：`#auth-who` 从 span 改为 ghost 按钮（`aria-haspopup="dialog"`，可访问名称由 auth.js 写成「用户名（修改密码）」），点它打开 `#pw-mask`。浮层字段：隐藏只读的 `pw-user`（autocomplete=username，供密码管理器认账号）、`pw-old`（current-password）、`pw-new`/`pw-again`（new-password，minlength 6 / maxlength 64，与 `PasswordChangeIn` 一致）；`#pw-error` role=alert、`#pw-done` role=status。与登录浮层一样在 auth.js 之前、`auth_ui` 为假时不输出。
- 不另加顶栏按钮：窄屏第一行只放得下站点名 + 两个控件（390px 实测顶栏仍 88px、站点名不截断）。`.actions .who` 去掉灰字（按钮灰字像禁用），新增 `.modal .ok` / `.modal .account`；16px 规则仍是样式表最后一条。

## 2026-09-26 商城关闭页显示订单号（TD-281）

- `shop.html`：`#x-no` 从从不显示的 `#st-downloaded` 移到 `#st-closed`（关闭页让用户找客服核对，却没有订单号可报）；`#st-downloaded` 整段删除——downloaded 订单一直按设计渲染 paid 区、可重新领取短时链接，③ 注释已注明。

## 2026-09-26 字号与版心（TD-287）

真实 Chromium（1280 / 390 宽、Noto Sans SC）统计：改前各页 85–95% 的可见文字是 13–14px，手机上也一样；页面标题 20/21/24px 不一；首页三张卡片各占满整行；ER/类图画布生成前是空框。

- **字号**：`base.html` 的 `:root` 定 `--fs-body`（15px，≤700px 另一段改 16px）、`--fs-small` 14px、`--fs-xs` 13px、`--fs-title` 22px。textarea 14px（TD-306 起默认正文字体，只有 `class="code"` 等宽）、`pre` 与浮层提示 13px，样式表里没有小于 13px 的字号。输入框 16px 覆盖仍是样式表最后一条（手机正文 16px 写在它前面单独一段）。
- **版心**：`--edge = max(--gutter, (100% − 1440px) / 2)`，顶栏、`main`、页脚（含手机顶栏）共用，宽屏左右对齐，窄屏退回 `--gutter`（≤900px 为 14px）。`payments-admin.html` 去掉自带的 1180px 居中与 22px 内边距。商城（720px）与客服（1040px）的阅读宽度保留。
- **标题**：商城商品名与「站内客服」加 `page-heading`；管理页 `.finance-heading` 用 `--fs-title`，分区 h2 18px。
- **首页**：`index.html` 加 `<p class="home-lead">{{ description }}</p>`（与 meta 描述同源，不是标题），卡片放进 `.tool-grid`（`auto-fill, minmax(300px, 1fr)`），底部「打开工具 →」只是视觉指示（`aria-hidden`）。
- **空状态**：`#mermaid-preview:empty::before` 与 `er.html` 的 `.er-view:has(> #er-tools[hidden])::after` 在生成前显示提示；ER 提示 `pointer-events: none`，旧内核不支持 `:has()` 时只是没有提示。≤900px 时 DDL 输入框 13em、ER 画布 60vh（原 16 行 + 70vh）。
- 管理页按钮外边距只留右侧，左缘与输入框对齐。
- 回归：`tests/test_ui_accessibility.py` 末尾四条（字号变量与下限、共用版心、首页网格、空状态提示与对比度）。

## 2026-09-28 截图复核后的界面整理（TD-306）

真实 Chromium 截图（1280 / 390 宽；游客、普通用户、管理员三种身份，含有待付款订单与客服会话的数据）逐页复核后的修正：

- **横向溢出（缺陷）**：`shop.html` 待付款区标题「订单 CM…」里 28 位订单号不能换行，登录后 390px 宽实测整页被撑到 418px。加 `.shop h2, .shop .muted { overflow-wrap: anywhere; }`。
- **多行输入字体**：`base.html` 原来给所有 textarea 等宽字体（为 DDL 准备），Mermaid 的自然语言描述也成了 Consolas + 回落字体。现在 textarea 默认正文字体，`er.html` 的 `#ddl-input` 加 `class="code"` 才用等宽；`support.css` 里两条单独改回正文字体的规则随之删去。
- **单行输入与下拉框**：新增 `:where(input:not(复选/单选/文件/滑块/取色), select)` 统一规则（浅色圆角边框、白底）。`:where()` 特异性为 0，`.finance input`、`.modal input` 等页面规则仍可覆盖；规则不写字体，手机 16px 规则仍是样式表最后一条。`drawio.html` 工具栏自己的边框规则删去（其下拉框原来仍是浏览器灰底）。
- **控件边框对比度**：新变量 `--control-border: #7d8ca3`，textarea、登录浮层输入框、统一规则共用。原来的 `#cbd5e1` 在白底上只有 1.48:1，达不到 WCAG 1.4.11 对控件边界的 3:1；新色白底 3.41:1、`#f8fafc` 浅底 3.26:1。
- **按钮行**：新类 `.form-actions`（flex、8px 间距），`er.html` / `mermaid.html` 的按钮行使用。原来按钮之间只有行内空白（约 4px）。
- **列表行**：新类 `button.item`（白底、边框、左对齐、`pre-line`），选中项 `[aria-current="true"]` 浅蓝底加左侧色条。管理页订单列表、客服会话列表、商城「我的订单」原来都是实心主按钮，与「查询 / 刷新」一样，也看不出选中了哪条。`shop.html`、`support.css` 里对应的列表规则只留间距。
- **客服页**：游客提示 `#support-login` 旁加「登录 / 注册」按钮 `#support-login-btn`（原来只有一句话，要自己去顶栏找）。

截图时注入的 Noto Sans SC 以 "Microsoft YaHei" 名义声明，并把雅黑排在 `system-ui` 之前（这台无头 Chromium 里 `system-ui` 在前时中文整段空白，属于截图环境问题，站点字体栈未改）。回归见 `tests/test_ui_accessibility.py` 末尾 TD-306 一组。

## 2026-09-28 R-06 三项界面小问题（TD-307）

- **登录浮层当前标签**：`.modal .tabs button[aria-pressed="true"]` 浅蓝底、加粗、底部 3px 色条；`aria-pressed` 由 `auth.js` 的 `setMode` 设置。原来「登录 / 注册」两个标签外观相同。
- **手机导航可滚动提示**：900px 以下导航行加右缘 28px 渐隐（`mask-image`，含 `-webkit-` 前缀），末尾 `header nav::after` 占同宽，滑到最右时最后一个链接完整可读。原有 `header nav { grid-column … }` 规则未动；旧内核不支持 mask 时只是没有渐隐。
- 管理页状态文字见 `app/frontend/README.md` 同日一节。

## 2026-09-28 第二轮截图复核（TD-317）

- **标题字号**：`base.html` 新增 `--fs-subtitle`（17px），没写样式的 `h2` 用 `--fs-title`、`h3` 用 `--fs-subtitle`（元素选择器，页面里有样式的标题照常覆盖）。`mock_pay.html` 标题不再写死 24px，`oauth_consent.html` 标题改用 `page-heading`。
- **`shop.html` 待支付态**：标题「等待支付」，订单号放到下面的 `.muted` 行（与支付成功、已关闭两态一致）；`.pay .qr:empty` 隐藏，mock 模式不再显示空的收款码框。
- **`mock_pay.html`**：`#out:empty` 隐藏（点击之前不显示空灰框）；灰色改用站内同一组。
- **页脚**：窄屏 `footer .fnav { row-gap: 0; }`，行距只靠链接的触控内边距。这条必须写在 `footer .fnav` 规则之后。
- **`oauth_consent.html`**：回调地址行加 `overflow-wrap: anywhere`，长 URL 不再越过卡片右边框。

截图环境：完整的 Noto Sans SC TTF（npm `@expo-google-fonts/noto-sans-sc`）复制到 `/tmp/fonts`，页面里用 `local()` 以 "Microsoft YaHei" 名义引用。TD-306 的 data URI 子集做法会缺字，详见 TD-317。回归见 `tests/test_ui_accessibility.py` 末尾 TD-317 一组。

## 2026-09-28 事件历史换行（TD-323）

- `payments-admin.html`：`#finance-events li` 设 `white-space:pre-line`。脚本一直用换行把每条事件分成「时间 · 事件 · 操作人 / 尝试号 / 依据」三行，但原来 li 没有这条规则，三行挤成一段，操作人、尝试号和依据 JSON 连在一起（真 Chromium 截图核对，1280px 与 390px 各一张，改后无横向溢出）。

## 2026-09-28 第三轮截图复核（TD-325）

- `shop.html`：支付成功标题去掉 `✅`（无彩色 emoji 字体的系统显示成方框），与其余几态一致。
- `app/static/support.css`：`#support-messages:empty { margin: 0; }`，还没有留言时标题与留言框之间不再有空白；不隐藏，保留 aria-live 区域。

## 2026-09-28 第四轮截图复核（TD-326）

- `base.html`：`#er-canvas`、`#mermaid-preview`、`#mermaid-source` 带 `data-stale` 时调暗（生成失败时上一次的结果）；`button.item` 加 `word-break: keep-all`（列表行只在空格与标点处断行）；`select { max-width: 100%; }`（长选项不再撑宽页面）。
- `support-center.html`：`#support-inbox-empty` 空状态（`role="status"`，默认 hidden），管理员没有客户会话时由脚本显示。
- `drawio.html`：文件管理列表的行与行内按钮加间距。
