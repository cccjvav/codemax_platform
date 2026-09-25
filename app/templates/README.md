# 页面模板

Jinja 只负责 HTML 外壳、表单、语义结构与站点上下文；真实交互在 `app/frontend/`，不再把旧内联脚本行号当作实现位置。

- `base.html`：导航、登录/注册浮层。经典 `auth.js` 在页面交互脚本之前执行；OAuth 同意页关闭这套登录控件。全站配色按 WCAG AA 取值（蓝 `#2563eb` 5.17:1、绿 `#15803d` 5.02:1、灰 `#64748b` 4.76:1，TD-263），`button:disabled` 灰化、`:focus-visible` 焦点环；浮层容器带 `role="dialog" aria-modal aria-labelledby="auth-title"`，Esc 关闭与焦点归还由 `auth.js` 实现。回归 `tests/test_ui_accessibility.py`；真实浏览器/读屏实测仍归 L-04。
- `er.html` / `mermaid.html`：工具表单和结果容器；本地构建的 ES module 包含第三方依赖。
- `drawio.html`：第三方编辑器、云端文件与回收站管理、本地导入/下载。保存前通过 export 协议请求新 XML。
- `shop.html`：固定数字商品、支付状态、历史订单和链接重领。定制需求引导至站内客服，不混作数字商品下单。
- `payments-admin.html`：管理员订单登录壳、筛选、合同/凭证/事件、人工确认/主动查单/历史绑定表单；hidden强制隐藏避免grid样式覆盖，所有数据另经鉴权API。
- `support-center.html`：公开的登录提示外壳；私人消息、会话列表及管理员操作都由鉴权 API 提供。
- `oauth_consent.html`：无脚本同意表单；签名绑定用户与凭证版本，回调 query 保留。
- `mock_pay.html`：开发用模拟支付，生产启动检查禁止启用。

修改 DOM ID 时同步检查页面脚本；不要用用户/模型文本拼接 HTML。客户消息由 `textContent` 渲染。

## 模块职责

Jinja 页面外壳、表单与导航；交互实现放在 frontend。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/templates/base.html`](base.html) | `3104c491e94b` | L1–L187 |
| [`app/templates/drawio.html`](drawio.html) | `1f33348a0b0f` | L1–L50 |
| [`app/templates/er.html`](er.html) | `2ba44cf7c2c1` | L1–L38 |
| [`app/templates/index.html`](index.html) | `992d913b0f43` | L1–L11 |
| [`app/templates/mermaid.html`](mermaid.html) | `529281507e00` | L1–L21 |
| [`app/templates/mock_pay.html`](mock_pay.html) | `dd6aaa5ba764` | L1–L30 |
| [`app/templates/oauth_consent.html`](oauth_consent.html) | `2a8858b00ebd` | L1–L22 |
| [`app/templates/payments-admin.html`](payments-admin.html) | `503e7d3cdec3` | L1–L165 |
| [`app/templates/shop.html`](shop.html) | `1561b62290e7` | L1–L120 |
| [`app/templates/support-center.html`](support-center.html) | `19912173fdfb` | L1–L30 |

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
- `.page-heading`（20px）移到 `base.html`，三个工具页标题一致（此前只有 drawio 自己定义，ER/Mermaid 落回浏览器默认 h2）。
- `shop.html`：「我的订单」移到所有状态区**之后**。付完款回到 `/shop`，此前「我的订单」排在「支付成功」上面，h3 也先于状态区 h2（axe `heading-order`）。
- `drawio.html`：「云端保存需登录：[登录 / 注册]」包进 `#drawio-login-prompt`，登录后由脚本整段隐藏（此前与「已登录，可保存到云端」同时出现）。
- `payments-admin.html`：列表空结果写进列表外的 `#finance-list-empty`（`role="status"`）——往 `<ul>` 里直接写字命中 axe `list`（serious）；选单前只显示 `#finance-detail-hint`，合同/凭证/操作全部包在默认 `hidden` 的 `#finance-detail` 里；`.finance pre:empty` 不画空框。
- `mock_pay.html`：页内标题 `h1` → `h2.mock-title`，恢复整站每页一个 h1（单 h1 测试现在也覆盖这页）。

## 2026-09-25 ER 画布工具栏（TD-279）

- `er.html`：SVG 包进 `section.er-view`，前面是生成后才显示的 `#er-tools`（「查看全图」按钮 `#er-fit` + 操作说明）；SVG 加 `role="img"`，`aria-label` 由脚本写成表数与关系数。页内 `<style>` 只排这一块，不给输入控件写字体（TD-278 的 16px 层叠约束不受影响）。`ddl-input`/`er-word`/脚本路径等测试钉住的 ID 不变。

