# `app/templates/` 模块说明书

> **行号基准 commit：`3652005`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/README.md`（`site.py` 提供这里的上下文变量）、`app/routers/README.md`（谁渲染这些模板）、
> `app/tools/README.md`、`database init/README.md`、`tests/README.md`、`scripts/README.md`。
>、`app/static/README.md`、`.github/workflows/README.md`、`docs/ROOT_FILES.md`。

---

## 1. 模块概述

### 1.1 定位

7 个 Jinja2 模板、491 行。**这是本项目的前端**（技术选型已定：Jinja2 SSR，不引入 Node / Nuxt / Next，见 `AGENTS.md`）。

分工是**「SSR 出外壳，图表客户端渲染」**：

| 谁负责 | 内容 |
| --- | --- |
| **服务端（Jinja2）** | HTML 骨架、TDK（`title` / `description` / `keywords`）、`canonical`、导航、表单初值 |
| **客户端（浏览器）** | ER 图（D3.js）、类图（Mermaid）、流程图（Drawio iframe）、所有 `fetch` 调用 |

这么分的原因写在 `app/routers/site.py` 的模块 docstring 里：图表本来就是客户端渲染，SSR 只负责 HTML 外壳与 TDK（给 SEO 用）。

### 1.2 实测结构

```text
app/templates/                  810 行
├── base.html           122 行   唯一的父模板：TDK + 全站 CSS + header/nav + 页脚
│                                + 登录/注册浮层 + {% block content %}（S2-02-2 起 42 → 118 → 122 行）
├── index.html           11 行   首页：遍历 tools 出卡片
├── er.html              90 行   SQL DDL → ER 图 + Word 导出（d3 打进 /static/js/er-page.js）
├── mermaid.html         63 行   自然语言 → UML 类图（Mermaid ESM）
├── drawio.html         200 行   Drawio iframe + 云端保存（乐观锁）★ 最复杂
│                                （S2-02-2 起 221 → 200：常驻登录框换成全站浮层）
├── shop.html           260 行   商城落地页 + 下单/支付/下载三态（S2-02-2 新增）
├── mock_pay.html        42 行   模拟收银台（仅 SHOP_PAY_MODE=mock）
└── oauth_consent.html   22 行   OAuth 授权同意页（纯表单，零脚本）
```

实测统计：

```text
7 个模板 {% extends "base.html" %}，只有 base.html 自己不继承
8 对 {% block content %} / {% endblock %}
内联 <script>：5 个模板（er / mermaid / drawio / mock_pay / shop）
外部源：cdn.jsdelivr.net（**仅 mermaid@11**，d3 已打包 TD-222）、embed.diagrams.net
本地脚本：/static/js/er-page.js（构建产物）
```

> ⚠️ **数「内联脚本」时要先剥掉 Jinja 注释。** `oauth_consent.html:11` 的注释里写着字面量
> `<script>`（原文是「纯表单，不含任何内联 `<script>`」），正则一扫就会误判成 5 个。
> `tests/test_auth_cookie.py:204` 专门先 `re.sub(r"\{#.*?#\}", "", ...)` 再扫，就是为了避开这个。

### 1.3 谁在渲染它们（与 `app/site.py` 的对应）

| 模板 | 渲染方 | 上下文变量 |
| --- | --- | --- |
| `base.html` | 不直接渲染，被继承 | — |
| `index.html` `er.html` `mermaid.html` `drawio.html` `shop.html` | `app/routers/site.py` 的 `_page_view()` 闭包 | 见下 |
| `mock_pay.html` | `app/routers/shop.py` 的 `mock_pay_page()` | 公共字段 + `order_no` |
| `oauth_consent.html` | `app/routers/oauth.py` 的 `authorize_form()` | 公共字段 + `client_name` `client_id` `redirect_uri` `state` `sig` `username`，且 `auth_ui=False` |

**所有渲染 base.html 的地方都必须走 `app/site.py` 的 `page_context()`** —— 它统一提供
`request` `title` `description` `keywords` `canonical` `site_name` `tools` `shop_path`
`product_name` `product_amount` `auth_ui` 这 11 个公共字段，页面自己的业务字段用 `**extra` 传。

这条不是洁癖：`mock_pay_page()` 曾经自己拼上下文、只传了 `title` 和 `order_no`，
结果页面渲染出 `<h1><a href="/"></a></h1>` 空品牌、`<nav></nav>` 空导航、CTA 的 `href=""`
—— 它照样返回 200，而当时的测试只断言「页面能开 + 有订单号」，所以一直没被发现。
现在 `tests/test_mock_pay.py::test_page_context_is_the_single_source_of_base_template_fields`
会把 base.html 实际用到的变量与 `page_context()` 提供的做集合比对，漏一个就红。

**「加一个工具页」只需往 `app/site.py` 的 `TOOLS` 加一条** —— 路由、导航、sitemap、TDK 自动跟上，**不用碰这个目录**（除非要新建模板文件）。

### 1.4 全目录统一的四条约定

**① 一律 `{% extends "base.html" %}` + `{% block content %}`。** 7 个子模板无一例外（`base.html` 自己除外）。

**② 登录态只走 HttpOnly cookie，脚本不碰 token。** 这是 TD-44 的直接后果，模板里有三处显式说明：
- `drawio.html:49-50`：**登录态由 HttpOnly cookie 携带，脚本读不到也不需要读 token。因此「是否已登录」只能问后端**
- `drawio.html:194-195`：响应体里的 `access_token` 是给 API 客户端用的，**页面直接忽略**
- `drawio.html:212`：进页面时问一次后端有没有有效 cookie —— **不能像以前那样靠读 localStorage 判断**

**③ 新页面不许再添内联脚本。** `oauth_consent.html:11-12` 的注释：「CSP 仍允许 `unsafe-inline`，新页面就别再添一个理由」。由 `tests/test_oauth_consent.py:139-144` 强制（断言同意页 HTML 里 `"<script" not in html`）。

**④ 所有 `fetch` 都带 `credentials: "same-origin"`**（`drawio.html:60`、`mock_pay.html:30`）—— 让浏览器带上登录 cookie。

---

## 2. 文件级详细说明书

### 📄 文件名：`base.html`（42 行）

- **文件职责**：**唯一的父模板**。全站共用的 `<head>`（TDK + CSS）与 `<header>`（站名 + 导航）。

#### 结构

**L1-L9 `<head>` 的元信息**
- **L6 `<title>{{ title }}</title>`**
- **SEO 三件套按「有值才输出」** —— `{% if description %}` / `{% if keywords %}` / `{% if canonical %}`。
  空值输出出去比不输出更糟：空 `canonical` 会让搜索引擎把当前 URL 当成规范地址的替身，
  空 `description` 会让摘要被搜索引擎自己瞎猜。开发用的收银台页没有 SEO 诉求，正好走空值分支。
  `canonical` 由 `app/routers/site.py` 用 `SITE_BASE_URL + tool.path` 拼出

**L10-L31 全站 CSS（内联 `<style>`）** —— 21 条规则。**关键几条**：
- **L18 `.split`** —— `grid-template-columns: 380px 1fr`，左输入右预览的两栏布局（`er.html` 与 `mermaid.html` 都用它）
- **L28 `#er-canvas`** —— ER 图画布，`height: 70vh`
- **L29 `#mermaid-preview`** —— 类图预览区，`min-height: 300px`
- **L30 `@media (max-width: 900px)`** —— 窄屏把两栏塌成一栏
- **L22 `.error`** —— 错误文案样式，`white-space: pre-wrap`（保留换行）

> **为什么 CSS 内联而不是外部文件**：CSS 只有 21 条规则，多一个 HTTP 请求不划算。
> 注意 **CSP 的 `style-src` 也允许 `'unsafe-inline'`**（`app/middleware.py:45`），
> `drawio.html:3-11` 与 `mock_pay.html:3-11` 同样有页面级内联 `<style>`。

**L34-L37 `<header>`**
- **L35 站名链回首页** `{{ site_name }}`
- **L36 导航是一行 Jinja 循环** —— `{% for t in tools %}<a href="{{ t.path }}">{{ t.title }}</a>{% endfor %}`。**`tools` 来自 `app/site.py:34` 的 `TOOLS` 常量**，所以加工具页导航自动跟上

**L38-L40 `<main>{% block content %}{% endblock %}</main>`** —— 子模板的插入点。

---

### 📄 文件名：`index.html`（11 行）

- **文件职责**：首页。遍历 `tools` 出卡片，**零脚本**。

#### 结构

**L4-L9 一个 Jinja 循环**
- **L5 `<a class="card" href="{{ t.path }}">`** —— 整张卡片是个链接
- **L6-L7 `{{ t.title }}` / `{{ t.description }}`** —— 都取自 `app/site.py` 的 `Tool` dataclass

**没有 `{% if %}`、没有脚本、没有 `fetch`** —— 纯 SSR。这正是「引流页要能被搜索引擎收录」的做法。

---

### 📄 文件名：`oauth_consent.html`（22 行）

- **文件职责**：OAuth 授权同意页。**纯表单，零脚本** —— 这是刻意的（见 L11-L12 注释）。

#### 结构

**L3-L10 说明区**
- **L4 `{{ client_name }} 请求访问你的账号`**
- **L5 `当前登录：{{ username }}`** —— 让用户确认自己是谁
- **L7-L8 明确告知边界**：同意后可代表你访问受保护资源（单点登录），**但它拿不到你的密码**
- **L10 回调地址用 `<code>` 显示** —— 让用户能看清要跳到哪

**L11-L12 Jinja 注释** —— 「纯表单，不含任何内联 `<script>` —— 见 TD-163（CSP 仍允许 `unsafe-inline`，新页面就别再添一个理由）」

**L13-L20 表单（本页的核心）**
- **L13 `method="post" action="/oauth/authorize"`**
- **L14-L17 四个隐藏字段**：`client_id` / `redirect_uri` / `state` / **`sig`**
  - **`sig` 是 `app/routers/oauth.py:48-60` 的 `_sign()` 算出来的 HMAC 签名**，作用是把这次 POST 绑定到「本站渲染过的那张同意页」—— 攻击者没有 `SECRET_KEY` 就签不出合法签名
- **L18-L19 两个提交按钮共用 `name="approve"`，值分别是 `"1"` 和 `"0"`**
  - **L18 同意 → `value="1"`**；**L19 拒绝 → `value="0"`**，带 `class="ghost"`（次要样式）
  - **与后端 `app/routers/oauth.py:111` 的 `approve: str = Form("0")` 对齐** —— 默认值是 `"0"`（拒绝），表单里少传字段时必须落到**更安全**的那一侧

> **由两条测试守着**：
> - `tests/test_oauth_consent.py:139-144` —— 断言 `"<script" not in html`（本页不得含内联脚本）
> - `tests/test_oauth_consent.py:147-150` —— 断言表单的 `action` 与隐藏字段与 POST 端点期望的一致，**否则用户点了同意会 422**

---

### 📄 文件名：`mock_pay.html`（42 行）

- **文件职责**：模拟收银台（答辩演示用）。**只在 `SHOP_PAY_MODE=mock` 时才会被渲染**（`app/routers/shop.py:123-124` 否则直接 404）。

#### 结构

**L3-L11 页面级 `<style>`** —— 5 条规则，全部以 `mock-` 或 `#pay` / `#out` 开头，不与全站 CSS 冲突。

**L13 醒目的黄色警告条** —— 「⚠️ 模拟支付通道 —— 不会产生真实扣款，仅用于本地开发与答辩演示」。**这是防「演示页面被误当成真支付」的第一道防线**（第二道是生产环境下这个页面根本不存在）。

**L15 订单号 `<code id="no">{{ order_no }}</code>`** —— 脚本从 DOM 读它（L22）。

**L16-L17 说明本页的开关与生产行为** —— 「本页由 `SHOP_PAY_MODE=mock` 开启；生产环境必须设为 `wechat`，**此时本页与模拟支付接口都会返回 404**」。

**L18-L19 按钮与输出区**

**L21-L41 内联 `<script>`**
- **L22-L23 从 DOM 取订单号与输出区**
- **L25-L40 点击处理器**：
  - **L26-L27 注释说明鉴权方式**：登录态由 HttpOnly cookie 携带（TD-44），**脚本读不到**；没登录就让后端回 401，下面分支负责把话说清楚
  - **L28-L33 `POST /shop/mock-pay/confirm`**，`credentials: "same-origin"`（L30），JSON body 只有 `order_no`
  - **L35-L39 三档分支**：
    - **`200`** → 显示「支付成功（模拟）」+ 状态 + 模拟流水号
    - **`401`** → **「未登录：请先在工具页登录后再来。」**（人话，不是干巴巴的状态码）
    - **其它** → 显示 `失败 {status}：{detail}`

---

### 📄 文件名：`mermaid.html`（63 行）

- **文件职责**：自然语言 → UML 类图。

#### 结构

**L3-L16 两栏布局（`.split`）**
- **L6 `<textarea id="text-input" rows="12">`** —— placeholder 直接给了示例句子
- **L8-L9 两个按钮**：`type="submit"` 的「生成类图」与 `type="button"` 的「填入示例」
- **L12 错误区** —— `role="alert"` + `hidden`（无障碍）
- **L13 `<pre id="mermaid-source" hidden>`** —— 显示生成的 Mermaid 源码，便于复制
- **L15 `<div id="mermaid-preview">`** —— 渲染区

**L18-L62 内联 `<script type="module">`**
- **L19 从 CDN 导入 Mermaid ESM** —— `https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs`。**这个域必须在 CSP 白名单里**（`app/middleware.py:38` 的 `_CDN`）
- **L20 `mermaid.initialize({ startOnLoad: false, securityLevel: "loose" })`** —— `startOnLoad: false` 是因为要手动控制渲染时机
- **L30-L32 「填入示例」** —— 把 L22 的 `SAMPLE` 填进输入框
- **L34-L37 `fail(msg)`** —— 显示错误
- **L39-L61 提交处理器**：
  - **L40 `ev.preventDefault()`** —— 阻止表单真的提交
  - **L42 `submit.disabled = true`** / **L58-L60 `finally` 里恢复** —— 防连点
  - **L44-L48 `POST /tools/mermaid`**
  - **L50 非 2xx → 显示 `data.detail`**（后端给的人话）
  - **L51-L52 显示 Mermaid 源码**
  - **L53-L55 渲染**：**L53 `preview.removeAttribute("data-processed")` 是关键** —— Mermaid 渲染过会给节点打这个标记，不清掉的话第二次渲染会被跳过
  - **L56-L57 `catch` → 「渲染失败」**

---

### 📄 文件名：`er.html`（90 行）

- **文件职责**：SQL DDL → ER 图，外加 Word 数据字典导出。

#### 结构

**L3-L16 两栏布局**
- **L6 `<textarea id="ddl-input" rows="16">`**
- **L8-L10 三个按钮**：生成 ER 图（submit）、导出 Word、填入示例
- **L13 错误区**（`role="alert"` + `hidden`）
- **L15 `<svg id="er-canvas">`** —— 注意是 **`<svg>`** 不是 `<div>`（D3 直接往里画）

**L18-L19 两个外部脚本**
- **d3 已不走 CDN**（TD-222）：由 npm 打进 `/static/js/er-page.js`
- **`/static/js/er-page.js`** —— **项目自己的渲染代码**（源码 `app/frontend/er-page.js`），提供全局函数 `renderEr()`

**L20-L89 内联 `<script>`**
- **L21-L31 `SAMPLE`** —— 用数组 `join("\n")` 拼的示例 DDL（两张表 + 一个外键），**比写多行字符串更好维护**
- **L38-L44 `post(url, payload)`** —— 抽出的 fetch 封装，两个处理器共用
- **L46-L49 `fail(msg)`**
- **L55-L72 「导出 Word」处理器**：
  - **L58 `POST /tools/word-export`**
  - **L59-L62 非 2xx → 显示 `data.detail`**
  - **L63-L68 下载 blob**：`URL.createObjectURL` → 造一个 `<a download>` → `.click()` → **L68 `URL.revokeObjectURL(url)` 释放**（不释放会泄漏内存）
  - **L66 文件名写死 `data_dictionary.docx`** —— 与后端 `app/tools/word.py` 的 `FILENAME` 一致
- **L74-L88 提交处理器**：
  - **L79 `POST /tools/er-diagram`**
  - **L81 非 2xx → 显示 `data.detail`**
  - **`renderEr("#er-canvas", data)`** —— 调 `/static/js/er-page.js` 里的全局函数
  - **L77 / L85-L87 `submit.disabled` 与 `finally` 恢复**

---

### 📄 文件名：`shop.html`（260 行）

- **文件职责**：商城落地页（S2-02-2）。**这是全站第一个能真正下单的页面** ——
  在此之前 `POST /shop/orders` 是个裸接口，前端零调用者，用户只能翻 `/docs` 手敲。

#### 四个状态区（同一页面就地切换，不跳新页）

| 区块 id | 何时显示 | 关键控件 |
| --- | --- | --- |
| `st-landing` | 默认 | 商品名、价格、`btn-buy` |
| `st-pending` | 下单后 | 二维码（`qr_svg`）或收银台链接；每 3 秒轮询 |
| `st-paid` | 支付成功 | `btn-download` |
| `st-downloaded` | 已下载 / 已关闭 | 说明文案 |

#### ⚠️ 一条 UI 铁律：**绝不能自动调 `POST /shop/download/{order_no}`**

那个接口会把订单一次性烧成 `downloaded`（后端 CAS 只让一个请求拿到链接，
见 `app/routers/shop.py` 的 `won`）。所以：

- **查状态只能走 `GET /shop/orders/{order_no}`**（只读，专为轮询而加）
- **下载只能由用户主动点按钮触发**

违反的后果是「付了钱下载不了」，而且极难排查。
`tests/test_shop_page.py::test_status_polling_does_not_burn_the_one_time_download` 钉住这条。

#### 其它两处细节

- 价格由 `product_amount / 100` 换算（金额以「分」存储），**不在模板里写死数字**
- 未登录点「立即购买」会唤起全站登录浮层，登录成功后自动继续下单 ——
  即 "value first, ask later"：先让用户决定要买，再要求身份

---

### 📄 文件名：`drawio.html`（200 行）★ 最复杂

- **文件职责**：Drawio 流程图编辑器 + 云端保存。**跨域 iframe 通信 + 乐观锁 + 登录态管理三件事叠在一起**。

#### 结构

**L3-L11 页面级 `<style>`** —— 工具栏、iframe（`height: 74vh`）、登录区样式。

**L13-L20 登录区**
- **L15-L16 用户名/密码输入框** —— **登录表单直接内嵌在工具页里**，这就是 `app/routers/oauth.py:83-86` 说的「本站没有独立登录页」
- **L17-L18 登录/退出按钮** —— **L18 退出按钮初始 `hidden`**，登录后才显示（L208）
- **L19 `<span id="auth-status">`** —— 状态文案

**L22-L31 工具栏**
- **L23 图名输入框** / **L24 我的流程图下拉框**
- **L25-L28 四个按钮**：新建、保存到云端、下载 `.drawio`、导入本地文件
- **L29 `<input type="file" accept=".drawio,.xml" hidden>`** —— 藏起来的文件选择器，由「导入」按钮触发（L158）

**L33 Drawio iframe**
- `src="https://embed.diagrams.net/?embed=1&ui=atlas&spin=1&proto=json&saveAndExit=0&noExitBtn=1"`
- **`proto=json`** 是关键 —— 约定用 JSON 走 postMessage 通信
- **这个域必须在 CSP 的 `frame-src` 里**（`app/middleware.py:39` 的 `_DRAWIO`）

**L35-L220 内联 `<script>`**

**① 模块级状态（L36-L51）**

| 变量 | 行 | 作用 |
| --- | --- | --- |
| `ORIGIN` | L36 | `https://embed.diagrams.net`，**收发 postMessage 都要校验它** |
| `BLANK` | L37 | 空白 drawio XML 模板 |
| `currentId` | L45 | **`null` = 尚未保存到云端的新图**（决定 POST 还是 PUT） |
| `currentXml` | L46 | 当前图的 XML |
| `etag` | L47 | **手上这一版的版本号，保存时带回去做乐观锁（TD-65）** |
| `loggedIn` | L51 | 登录状态。**L49-L50 注释**：cookie 是 HttpOnly，脚本读不到，**所以只能问后端** |

**② 两个辅助函数（L53-L64）**
- **`send(msg)`　L53-L55** —— `frame.contentWindow.postMessage(JSON.stringify(msg), ORIGIN)`。**第二个参数指定目标源**，不写就是 `"*"`（会把数据发给任何人）
- **`api(url, method, body, extraHeaders)`　L57-L64** —— fetch 封装，**L60 `credentials: "same-origin"`** 带 cookie，L61 允许追加头（`If-Match` 就靠它）

**③ postMessage 协议（L67-L81）**
- **L68 是关键安全校验**：`if (evt.origin !== ORIGIN || evt.source !== frame.contentWindow) return;`
  —— **同时校验来源域与来源窗口**，只认 drawio 的消息。**没有这行，任何页面都能往这里 postMessage**
- **L70-L74 `try { JSON.parse } catch { return }`** —— 非法 JSON 静默丢弃
- **L75 `init` 事件 → 回一个 `load`**（把当前 XML 推进 iframe）
- **L76-L79 `save` 事件 → 更新 `currentXml` 并调 `saveToCloud()`**
- **L80 `autosave` 事件 → 只更新 `currentXml`**，不触发保存

**④ `saveToCloud()`　L84-L116 —— 乐观锁的客户端一侧**
- **L85-L88 未登录 → 显示「未登录，未保存」并返回**
- **L89-L94 请求构造**：
  - **`currentId ? PUT /diagrams/{id} : POST /diagrams`**（L90-L91）
  - **L92 名称为空则用「未命名流程图」**
  - **L93 `currentId ? { "If-Match": etag } : undefined`** —— **只有改已有的图才需要带版本**（新建没有版本可比）
- **L95-L98 `401` → 「登录已失效，请重新登录」**
- **L99-L104 `412` → 版本冲突。L100-L101 的注释是这个函数最重要的一句**：
  > 另一个标签页/设备已经改过了。**绝不能自动重试** —— 那正好会把对方的改动盖掉，**乐观锁就白加了**。让用户自己决定。
- **L105-L110 其它非 2xx** —— **L106 注释**：409（配额用满）后端给了人话，**直接显示比一个光秃秃的状态码有用**
- **L111-L115 成功**：**L113 `etag = res.headers.get("ETag")`** —— **服务端已经把版本 +1，必须换掉手上的旧版本**，否则下一次保存必然 412

**⑤ `refreshList()`　L118-L131** —— 重新拉列表填下拉框。**L123 先重置成占位项**再逐个 append；**L130 若当前有打开的图则选中它**。

**⑥ `openFromCloud(id)`　L133-L146** —— 从云端打开一张图。**L142 同样要取新的 `ETag`**；L144 把 XML 推进 iframe。

**⑦ 本地保存/导入（L149-L168）**
- **L149-L156 下载 `.drawio`** —— `Blob` → `createObjectURL` → `<a download>` → `click()` → **L155 `revokeObjectURL`**
- **L158 「导入」按钮 → 触发隐藏的文件选择器**
- **L159-L168 文件选中处理**：
  - **L162-L163 读文本、`currentId = null`**（导入的图算新图，保存时会 POST 而不是覆盖别人的）
  - **L164 文件名去掉扩展名当图名**
  - **L167 `ev.target.value = ""`** —— **L167 注释**：不清空的话，**连续导入同一个文件不会再触发 `change`**

**⑧ 按钮绑定（L170-L179）**
- **L171-L178 「新建」** —— 重置 `currentId` / `currentXml` / 名称 / 下拉框，并把空白 XML 推进 iframe
- **L179 `list.onchange`** —— `list.value && openFromCloud(list.value)`（选中占位项时不动作）

**⑨ 登录/登出（L181-L210）**
- **L181-L198 登录**：
  - **L182-L189 `POST /auth/login`**，**`Content-Type: application/x-www-form-urlencoded`** + `URLSearchParams` —— 因为后端用的是 `OAuth2PasswordRequestForm`（表单，不是 JSON）
  - **L190-L193 失败 → 显示状态码**
  - **L194-L195 注释**：响应体里的 `access_token` 是给 API 客户端用的，**页面直接忽略 —— 登录态已经由 `Set-Cookie` 落到 HttpOnly cookie 里了**
  - **L196-L197 `setAuthState(true)` + 刷新列表**
- **L200-L204 登出** —— `POST /auth/logout`，然后 `setAuthState(false)`
- **L206-L210 `setAuthState(on)`** —— 切 `loggedIn`、显隐退出按钮、写状态文案

**⑩ `checkAuth()`　L213-L219，L219 立即调用**
- **`GET /auth/me`** —— 成功就认为已登录
- **L212 注释说明了为什么需要它**：进页面时问一次后端有没有有效 cookie —— **不能像以前那样靠读 localStorage 判断**（TD-44 之后脚本根本读不到 token）

---

## 3. 执行逻辑流

### 3.1 一次页面请求

```text
GET /tools/er
  │
  ├─ app/routers/site.py:20-36  _page_view(tool) 闭包
  │     上下文 = { title, description, keywords, canonical, site_name, tools, request }
  │     ↑ 全部来自 app/site.py 的 Tool dataclass（TOOLS 常量）
  │
  ├─ Jinja2 渲染 er.html
  │     {% extends "base.html" %} → 套上 TDK + 全站 CSS + header/nav
  │     {% block content %}       → 填入 er.html 自己的 L3-L89
  │
  ├─ SecurityHeadersMiddleware 加上 CSP 等 5 个安全头
  │
  └─ 浏览器收到 HTML
        ├─ d3 已打进产物，不再走 CDN（TD-222）
        ├─ 加载 /static/js/er-page.js                  ← CSP 'self'
        └─ 执行内联 <script>                            ← CSP 'unsafe-inline'（TD-163 的妥协）
```

### 3.2 四条「前端 ↔ 后端」链

```text
【ER 图】  er.html:79  POST /tools/er-diagram   → data → renderEr() (来自 /static/js/er-page.js)
【Word】   er.html:58  POST /tools/word-export  → blob → createObjectURL → <a download> → revoke
【类图】   mermaid.html:44  POST /tools/mermaid → data.mermaid → mermaid.run()
【流程图】 drawio.html      见下表（5 个端点）
【客服】   （无专属模板，接口在 /support/ask）
【支付】   mock_pay.html:28  POST /shop/mock-pay/confirm
【登录】   drawio.html:182   POST /auth/login（表单）
           drawio.html:201   POST /auth/logout
           drawio.html:214   GET  /auth/me
```

`drawio.html` 用到的 5 个 `/diagrams` 端点：

| 前端位置 | 方法 + 路径 | 特殊头 | 特殊处理 |
| --- | --- | --- | --- |
| L90-L91 | `POST /diagrams` | — | 新建（`currentId` 为 null） |
| L90-L91 | `PUT /diagrams/{id}` | **`If-Match: <etag>`** | 保存；**412 绝不自动重试**（L100-L101） |
| L120 | `GET /diagrams` | — | 刷新下拉框 |
| L134 | `GET /diagrams/{id}` | — | 打开；**L142 取新 ETag** |
| — | `DELETE` / `restore` | — | **前端未接入**（回收站只有 API，没有 UI） |

### 3.3 三处「乐观锁」在前后端的对应

| 环节 | 后端 | 前端 |
| --- | --- | --- |
| 版本从哪来 | `app/routers/diagrams.py:97` / `:110` 写 `ETag` 响应头 | `drawio.html:113` / `:142` `res.headers.get("ETag")` |
| 版本怎么带回去 | `app/routers/diagrams.py:118` 读 `If-Match` 头 | `drawio.html:93` `{ "If-Match": etag }` |
| 冲突怎么办 | `app/routers/diagrams.py:147-153` 返回 412 + 人话 | `drawio.html:99-104` 显示提示，**绝不自动重试** |
| 版本也在响应体里 | `app/schemas.py:70-72` `DiagramSummary.version` | （前端目前只读头，没用响应体那份） |

**为什么响应体里也要放一份 `version`**：`app/schemas.py:70-71` 说明 —— 客户端不总能方便地读响应头，而**列表页也需要知道每张图的当前版本**。

### 3.4 谁在守着这些模板（实测 4 条测试）

| 测试 | 位置 | 守什么 |
| --- | --- | --- |
| **外部源反向校验** | `tests/test_ops.py:70-82` | 扫 `app/templates/*.html` 与 `app/static/*` 里出现的**每个外部域**，逐个断言它在 CSP 白名单里。**L80 还断言「必须扫到至少一个」**，防止 glob 路径写错导致测试空转 |
| **`unsafe-inline` 自我提醒** | `tests/test_ops.py:85-94` | 断言「还有内联脚本」**且**「CSP 里有 `unsafe-inline`」。**L93 的断言消息是「已经没有内联脚本了？那就可以收紧 CSP，请删掉这条测试」** —— 这是一条**故意会在好事发生时变红**的测试 |
| **同意页零脚本** | `tests/test_oauth_consent.py:139-144` | `"<script" not in html` |
| **前端不碰浏览器存储** | `tests/test_auth_cookie.py:202-207` + `:212` | 两层：① 静态扫模板里的 `localStorage` / `sessionStorage`（**L204 先剥掉 Jinja 注释**，否则会被 `oauth_consent.html:11` 的字面量误判）；② **用 node 真执行内联脚本** —— 桩把 `localStorage` 的每个方法都换成抛错，**并真的点一次登录按钮**（`byId("btn-login").onclick()`），碰一下就当场失败 |

> **第 4 条值得单独说**：它不是静态检查，而是**真的在 node 里跑前端代码**（`tests/test_auth_cookie.py:34` 的 `_NODE_HARNESS`）。
> 桩里连 `fetch` 的返回形状都对齐了后端（L54-L57：**列表接口返回数组、其余返回对象**），
> 注释写明原因：**否则 `refreshList()` 的 `for...of` 会先炸在桩上而不是炸在前端代码上**。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `3652005`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) 每个模板的行数（本文 1.2 的清单）
python -c "import pathlib; [print(len(p.read_text(encoding='utf-8').splitlines()), p.name) for p in sorted(pathlib.Path('app/templates').glob('*.html'))]"
# 预期 7 行，共 491 行

# 2) 继承关系、内联脚本数、外部源（本文 1.2 的那组数字）
#    ⚠️ 数内联脚本要先剥掉 Jinja 注释，否则 oauth_consent.html:11 的字面量 <script> 会被算进去
python -c "
import re, pathlib
for p in sorted(pathlib.Path('app/templates').glob('*.html')):
    t = p.read_text(encoding='utf-8')
    body = re.sub(r'\{#.*?#\}', '', t, flags=re.S)          # 先剥 Jinja 注释
    inline = len(re.findall(r'<script(?![^>]*\bsrc=)[^>]*>', body))
    ext = re.findall(r'https?://[A-Za-z0-9.-]+', t)
    print(f'{p.name:<20} extends={\"extends\" in t!s:<5} 内联script={inline} 外部源={sorted(set(ext))}')
"
# 预期：只有 base.html extends=False；内联 script 为 5 个模板（er / mermaid / drawio / mock_pay / shop）

# 3) 守住这些模板的 4 条测试（本文 3.4）
python -m pytest tests/test_ops.py tests/test_oauth_consent.py tests/test_auth_cookie.py -q
# 本机实测：48 passed

# 4) CSP 白名单与模板用到的外部源是否一致
python -c "
import re, pathlib
from app.middleware import CONTENT_SECURITY_POLICY as csp
used = set()
for pat in ('app/templates/*.html', 'app/static/*.js'):
    for f in pathlib.Path('.').glob(pat):
        used |= set(re.findall(r'https?://[A-Za-z0-9.-]+', f.read_text(encoding='utf-8')))
print('用到的外部源:', sorted(used))
print('不在白名单的:', sorted(o for o in used if o not in csp))
"
# 预期：不在白名单的 → []（空列表）
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动本目录任何文件后，
> 本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **改模板时还要留意三处联动**：
> ① 加外部 CDN → 必须同时改 `app/middleware.py` 的 CSP 白名单（`test_ops.py:70-82` 会红）；
> ② 把内联脚本外置完 → `test_ops.py:85-94` 会红，那时应当**收紧 CSP 去掉 `unsafe-inline`** 并删掉那条测试；
> ③ 碰 `localStorage` / `sessionStorage` → `test_auth_cookie.py` 会红（它会用 node 真跑一遍）。
>
> 背景（为什么文档里的数字比代码更容易腐烂、以及一次真实的漏改事故）见
> `docs/ARCHITECTURE_GUIDE.md` 第 7 课 7.10。
