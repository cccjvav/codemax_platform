# `app/routers/` 模块说明书

> **行号基准 commit：`2dd4d8c`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/tools/README.md`（业务逻辑层）、`app/README.md`（根级基础设施，本层的地基）、
> `database init/README.md`（建表脚本）、`app/templates/README.md`（前端模板）、`app/static/README.md`、`.github/workflows/README.md`、`docs/ROOT_FILES.md`、`tests/README.md`。
> **本层只做 HTTP 的事，不写业务逻辑** —— 这条边界写在 `AGENTS.md` 的「目录约定」里。

---

## 1. 模块概述

### 1.1 定位

`app/routers/` 是本项目的 **HTTP 接口层**。9 个 router、10 个文件、1 132 行，全部在 `main.py:37-45` 注册。

**这一层只负责五件事**，业务逻辑一律下沉到 `app/tools/` 或 `app/` 根目录的服务模块：

1. **鉴权** —— 挂 `Depends(get_current_user)` / `require_admin`
2. **限流** —— 挂 `Depends(rate_limit(...))`
3. **入参校验** —— 靠 `app/schemas.py` 的 Pydantic 模型
4. **异常 → HTTP 状态码映射** —— 这是本层最有信息量的部分，见 1.3
5. **响应序列化**

实测路由统计（`import main` 后遍历 `main.app.routes`）：

```
路由条目总数 41  →  业务条目 36  →  唯一业务路径 31
（差值来自同一路径支持多种方法：/diagrams 等 4 个路径各有 GET/POST/PUT/DELETE）
按前缀：/tools 7  /shop 7  /diagrams 6  /auth 5  /oauth 3  其余各 1
```

### 1.2 依赖关系

**本层不被任何代码 import**（只在 `main.py` 被注册），是纯粹的叶子层。它向下依赖：

| router | 依赖的 `app/` 根模块 | 依赖的 `app/tools/` 模块 |
| --- | --- | --- |
| `admin.py` | `database` `deps` `models` `ratelimit` | `browser` `crawler` `extract` `llm` `politeness` |
| `auth.py` | `config` `database` `deps` `models` `ratelimit` `schemas` `security` | — |
| `diagrams.py` | `config` `database` `deps` `models` `schemas` | — |
| `health.py` | `database` | — |
| `oauth.py` | `config` `database` `deps` `models` `security` `site` `timeutil` | — |
| `shop.py` | `config` `database` `deps` `models` `order_state` `site` `storage` `wechat_pay` | — |
| `site.py` | `config` `site` | — |
| `support.py` | `database` `ratelimit` `schemas` | `support` |
| `tools.py` | `cpu_pool` `deps` `models` `ratelimit` `schemas` | `llm` `sql_ddl` `word` |

**没有任何 router 直接 import `sqlalchemy` 之外的第三方业务库** —— 微信支付签名、存储签名、密码哈希全在 `app/` 根模块里，路由层只调用。

### 1.3 全层统一的两条设计约定

**① 有副作用的接口一律不能是 GET。** 登录态在 TD-44 之后走 `SameSite=Lax` 的 cookie，而 Lax **挡不住跨站顶层导航的 GET**（那一跳会带 cookie）。所以任何会改状态的动作必须是 POST/PUT/DELETE。该不变式由 `tests/test_auth_cookie.py` 的路由清单钉住。

**② 状态码对应「责任方」，不是「严重程度」。**

| 状态码 | 含义 | 例子 |
| --- | --- | --- |
| **400** | **调用方**给的东西有问题 | URL 抓不了、robots 不允许、DDL 里没有 CREATE TABLE |
| **401** | 没登录 / 凭证无效 | 登录失败、token 过期 |
| **403** | 登录了但没资格 | 订单未支付、已下载过、账号被禁用 |
| **404** | 不存在**或不是你的**（故意不区分） | 流程图不存在、订单不存在 |
| **409** | 状态冲突 | 配额已满、版本冲突、重复恢复 |
| **412** | 乐观锁失败 | `If-Match` 版本对不上 |
| **422** | 语义处理不了 | 抓到页面但提不出正文 |
| **428** | 缺前置条件头 | 缺 `If-Match` |
| **502** | **上游**挂了 | 大模型不可用、微信下单失败 |
| **503** | **本站**能力缺失 | 支付未配置、没装 playwright、数据库连不上 |

> **404 与 403 的刻意混用**：`diagrams.py` L31-L33 与 `shop.py` L179 / L136 都把「不存在」和「不是你的」返回同一个 404，**避免探测他人资源是否存在**。

---

## 2. 文件级详细说明书

### 📄 文件名：`__init__.py`

- **文件职责**：空文件，标记 `app.routers` 为包。
- **核心类/函数清单**：无。

---

### 📄 文件名：`health.py`（43 行）

- **文件职责**：存活探针与就绪探针。**分两个端点是因为它们回答两个不同的问题**，容器编排器对两者的处置完全不同（模块 docstring L3-L13）。

#### 核心函数

**`async healthz()`　L27-L29**
- 装饰器 **L25-L26 挂了两个路径**：`/healthz` 与 `/health`（后者是别名，README 与既有测试在用，TD-164）
- **不碰任何外部依赖**。L5-L8 的 docstring 说明了为什么：如果存活探针去查数据库，数据库一抖编排器就会把**健康的**实例全部重启，把一次数据库故障放大成全站雪崩 —— 这是存活探针最经典的误用
- 返回 `{"status": "ok"}`

**`async readyz(db)`　L33-L43**
- L35-L36 **真的执行 `SELECT 1`**
- **L36-L42 是关键 try-except**：探针**必须吞掉异常**，否则探针自己会变成 500。失败时返回 503 + 一个手工拼的 JSON 字符串（L38-L39 带上异常类名），编排器只会把实例从负载均衡摘掉、等它恢复，**不重启**

---

### 📄 文件名：`support.py`（33 行）

- **文件职责**：智能客服的 HTTP 入口。

#### 核心函数

**`async ask(data: SupportIn, db)`　L18-L33**
- **L17 挂 LLM 档限流**（`RATE_LIMIT_LLM`）。模块 docstring L3-L5 说明：**刻意不设鉴权** —— 售前咨询不该要求先注册，这也是引流入口；代价是任何人都能刷，所以限流要比工具档更严（每次调用都花钱）
- L26 一行调用 `answer(data.text, db)`，业务全在 `app/tools/support.py`
- L27-L33 序列化 7 个字段。**`escalated` 与 `reason` 是给两类人看的**：前端据此决定是否弹「转人工」，运维据此排查为什么某类问题总是兜底

---

### 📄 文件名：`tools.py`（73 行）

- **文件职责**：工具平台（引流侧）的三个端点。

#### 核心函数

**`async ping(_)`　L17-L19** —— 受保护端点，用来验证与商业平台共享同一登录态（SSO）。

**`async er_diagram(data: ErDiagramIn)`　L23-L36**
- **L22 挂工具档限流**；**刻意不设鉴权**（L26：引流工具，便于 SEO 收录与游客直接使用）
- **L33 `run_in_threadpool(parse_ddl, ...)`**。L28-L32 的 docstring 记了实测依据：`parse_ddl` 是同步 CPU 密集代码，直接在 `async def` 里调用会独占事件循环 —— 满额 20000 字符 DDL 单次 33 ms，5 个并发时最后一个要等 169 ms（≈5×33，完全串行），期间**全站**请求都卡住，客服的 <80ms 指标就是这么被打穿的（TD-159）
- L34-L35 没解析到任何表 → 400

**`async mermaid(data: MermaidIn, llm)`　L40-L49**
- **L39 挂 LLM 档限流**；LLM 客户端**通过依赖注入**（`Depends(get_llm)`），测试不会真打网络
- **L45-L48 是关键 try-except**：`LLMError` → **502**（上游挂了，不是本站故障）

**`async word_export(data: ErDiagramIn)`　L53-L73**
- **L52 挂工具档限流**
- L60-L62 `parse_ddl` 走**线程池**；L61-L62 空结果 → 400
- **L70 `build_data_dictionary` 走进程池 `run_cpu_bound`**。L56-L58 docstring 给了实测依据：28 表时 parse 32 ms、生成 docx **454 ms**，是前者的 13 倍 —— **一个** Word 导出就能把事件循环占住半秒
- **L64-L69 的注释是一条方法论记录**：「并发 p95 更快」**不是**选进程池的依据（本机 2 核实测线程池 58~106ms、进程池 60~77ms，分布完全重叠，延迟量不出差别，TD-183/186）；真正的依据是 `test_build_data_dictionary_runs_in_a_separate_process` —— 直接查它跑在哪个**进程**里，判据确定、不受机器快慢影响（TD-193）
- L71-L72 返回 `.docx` 附件

---

### 📄 文件名：`site.py`（59 行）

- **文件职责**：页面 SSR + sitemap + robots（S2-02-1）。页面路由由 `app/site.py` 的 `PAGES` 清单**生成**，sitemap / robots 读同一份清单，避免「加了页面忘了进 sitemap」这类漂移（模块 docstring L3-L4）。

#### 核心函数

**`_base() -> str`　L16-L17** —— 取 `SITE_BASE_URL` 并去掉尾部斜杠。

**`_page_view(tool: Tool)`　L20-L36**（**闭包工厂**）
- L21-L33 内层 `async def view(request)` 渲染模板，上下文 7 个键：`title` / `description` / `keywords` / `canonical` / `site_name` / `tools` / `request`
- **L35 `view.__name__ = f"page_{tool.key}"`** —— 不改名的话所有页面路由会重名，`url_path_for` 与排错都会受影响

**L39-L40 模块级循环** —— 遍历 `PAGES`，用 `add_api_route` 把每个工具页注册成 GET 路由，`include_in_schema=False`（页面不进 OpenAPI）。

**`async sitemap()`　L44-L51** —— 拼 sitemap XML，每个 `<loc>` 是 `_base() + p.path`。

**`async robots()`　L55-L59** —— `User-agent: * / Allow: /` + 一行 `Sitemap:`。

---

### 📄 文件名：`auth.py`（120 行）

- **文件职责**：注册 / 登录 / 改密码 / 登出 / 取当前用户。

#### 核心函数

**`_set_auth_cookie(response, token) -> None`　L19-L40**
- L29-L38 `response.set_cookie(...)`，各属性的取舍全写在 L20-L28 的 docstring 里：
  - **`httponly=True`** —— 脚本读不到，XSS 拿不走 token（这正是 TD-44 的目的）
  - **`samesite="lax"`** —— 跨站的 POST/PUT/DELETE 不带 cookie，CSRF 对写操作免疫。**代价是跨站顶层导航仍会带上**，所以有副作用的接口一律不能是 GET。不用 `Strict` 是因为它连「从微信/邮件点链接进来」的第一跳都不带 cookie，用户会看到一次莫名的未登录
  - **`secure=settings.ENV == "production"`** —— 本地是 http，加了浏览器根本不会存这个 cookie
  - **`max_age`** 与 token 同寿命，免得 cookie 活得比 token 久、反复撞 401

**`async register(data: RegisterIn, db)`　L45-L52**
- **L44 挂 auth 档限流**
- L47-L48 用户名重复 → 400；L49-L52 建用户（密码走 `hash_password`）

**`async login(response, form, db)`　L57-L72**
- **L55-L56 挂 auth 档限流**；入参用 `OAuth2PasswordRequestForm`（Swagger 的登录按钮能直接用）
- L66-L67 用户不存在**或密码错**都返回同一句「用户名或密码错误」（不透露具体原因）
- L68-L69 账号被禁用 → **403**
- L69-L71 签发 token（带 `password_changed_at`）→ 写 cookie → 响应体也返回 token
- **同时给两种客户端用**（L61-L63 docstring）：浏览器吃 `Set-Cookie`，API 客户端 / Swagger 吃响应体走 Bearer 头

**`async change_password(data, response, db, user)`　L77-L104**
- **L75-L76 挂 auth 档限流 + 需要登录**
- L90-L92 原密码不对 → 400（**与登录端点一样不透露具体原因，避免变成密码枚举接口**）
- L93-L94 新旧密码相同 → 400
- L95-L97 写新哈希 + `password_changed_at = datetime.now(timezone.utc)`（**带时区**，列是 TIMESTAMPTZ，TD-146）
- **L98-L104 是 TD-70 的核心**：改密码会**吊销该用户此前签发的所有 token**（`create_access_token` 把新的 `password_changed_at` 写进 `pwd` 声明，`get_current_user` 每次比对）。L100-L103 同时**返回一个新 token 并换掉 cookie** —— 当前会话不该被自己踢下线，要踢的是**其他**会话

**`async logout(response)`　L108-L115**
- L115 `delete_cookie`
- **L110-L113 说明为什么故意不要求登录态**：拿着一个已过期 cookie 的客户端也该能清掉它，否则用户会卡在「我明明退出了，浏览器却还带着一个死 cookie」。token 不在服务端记录，所以退出只是让浏览器丢掉它

**`async me(user)`　L119-L120** —— 返回当前用户。

---

### 📄 文件名：`oauth.py`（188 行）

- **文件职责**：OAuth2 授权码流程（SSO 对第三方应用）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `AUTH_CODE_EXPIRE_MINUTES` | L23 | `10` | 授权码有效期（分钟） |

#### 核心函数

**`_utcnow()`　L26-L30** —— 带时区的 UTC 现在时刻。**L27-L29 明确警告不要再 `.replace(tzinfo=None)`** —— 抹掉时区就退化成裸值，与数据库写入的绝对时刻无法正确比较（时间列已是 TIMESTAMPTZ，TD-146）。

**`_oauth_error(error, description)`　L33-L34** —— 返回 400 + `{"error": ..., "error_description": ...}`（OAuth 规范格式，不是 FastAPI 默认的 `{"detail": ...}`）。

**`async _active_client(db, client_id, redirect_uri)`　L37-L45**
- L41-L42 客户端不存在或未启用 → `invalid_client`
- **L43-L44 回调地址必须与登记值完全一致** → 否则 `invalid_redirect_uri`
- **L38-L40 docstring 是关键**：同意页与签发码**共用这套校验**，免得两边校验强度不一致 —— **校验弱的那一边就是漏洞**

**`_sign(client_id, redirect_uri, state) -> str`　L48-L60**
- HMAC-SHA256 签三个参数，base64url 编码后去掉 `=`
- **L50-L58 docstring 说明了它的作用与边界**：把 POST 绑定到「本站渲染过的那张同意页」，攻击者没有 `SECRET_KEY` 就签不出合法签名，无法跳过用户点同意直接构造 POST。**但这不是 CSRF 的主要防线** —— 主要防线是登录 cookie 的 `SameSite=Lax`；这条是第二层，好处是零会话状态

**`async authorize(request, response_type, client_id, redirect_uri, state, user, db)`　L64-L102**
- **只渲染同意页，不签发授权码**（TD-78）。L65-L82 的 docstring 记了原来的设计与两个问题：
  1. 用户从头到尾没见过「某某应用请求访问你的账号」，不符合 OAuth 的用户同意语义
  2. TD-44 把登录态改成 cookie 之后它成了一个**新引入的 CSRF 面**（TD-175）：`SameSite=Lax` 挡不住跨站顶层导航的 GET，攻击者一个跳转就能替用户签发授权码。**现在 GET 签不出任何东西，那个面就关掉了**
- L91-L92 `response_type != "code"` → `unsupported_response_type`
- L87 校验客户端 → L88-L102 渲染 `oauth_consent.html`，上下文里带 `sig`（L100）
- **L82-L83 说明为什么未登录直接 401 而不跳转登录页**：本站没有独立登录页（登录表单内嵌在工具页里），为一个跳转新造一页不值得

**`async authorize_submit(client_id, redirect_uri, sig, state, approve, user, db)`　L106-L143**
- **L111 `approve` 默认 `"0"`（拒绝）** —— 表单里少传字段时必须落到**更安全**的那一侧
- **L119-L121 是关键校验**：`hmac.compare_digest` 比对签名。**注释明确说不能用 `==`** —— 签名比对不能短路返回，否则响应时间会泄露信息
- L122 再次校验客户端（与 GET 同一套）
- **L121-L125 用户拒绝**：302 回 `redirect_uri`，带 `error=access_denied`；有 `state` 就带上
- L127-L134 用户同意：`secrets.token_urlsafe(24)` 生成授权码，落库并设 10 分钟过期
- L141-L143 302 回 `redirect_uri`，带 `code` 与 `state`

**`async token(grant_type, code, redirect_uri, client_id, client_secret, db)`　L147-L188**
- L156-L157 `grant_type` 不是 `authorization_code` → `unsupported_grant_type`
- L158-L160 客户端校验 + **`verify_password(client_secret, ...)`** → 失败 `invalid_client`
- **L162-L169 授权码四重校验**：存在 / `client_id` 匹配 / `redirect_uri` 匹配 / **未被使用**
- L170-L171 过期检查（用 `as_utc` 把库里的时间转成带时区再比）
- **L173-L179 是关键：原子消费授权码**。把「检查未使用 + 标记已使用」合成**一条 UPDATE**（`WHERE code=? AND used IS FALSE`），并发重放同一个 code 时只有一个请求能拿到 `rowcount=1`。L174-L175 的注释指出原先「先查后改」存在重放窗口
- L181-L182 取用户并提交
- L184-L188 返回 `access_token` / `token_type` / `expires_in`

---

### 📄 文件名：`diagrams.py`（192 行）

- **文件职责**：Drawio 流程图存取（S2-01-3）。**流程图是用户私有资产**，与 ER/Mermaid 那类公开引流工具语义不同：**全部端点需鉴权**，且只能读写自己的记录（模块 docstring L1-L5）。

#### 核心函数

**`_alive()`　L21-L23** —— 「存活」条件 `deleted_at IS NULL`。**集中在一处，避免某个查询忘了过滤软删除的行**（L22 docstring）。

**`async _owned(db, user, diagram_id, *, include_deleted=False)`　L26-L35**
- L28 按 `id + user_id` 查；L29-L30 除非 `include_deleted` 否则加 `_alive()`
- **L31-L34 是关键**：「不存在」「不是你的」「已删除」返回**同一个 404**，避免探测他人资源是否存在

**`_etag(diagram)`　L38-L40** —— ETag 就是版本号，按 HTTP 规范加引号（强校验符）。

**`_parse_if_match(raw)`　L43-L56**
- **L47-L52 缺失就报 428**（RFC 6585 Precondition Required），**不默认放行** —— 默认放行等于这个接口仍然可以被静默覆盖
- L53-L55 值不是纯数字 → 400

**`async _live_count(db, user)`　L59-L62** —— 数**存活**行数（配额只算存活，所以删掉一张就腾出一个名额）。

**`async list_diagrams(deleted, user, db)`　L66-L76**
- **L70-L72 docstring 解释了一个路由陷阱**：用查询参数 `?deleted=true` 而不是 `/diagrams/trash` 子路径 —— 后者会被先注册的 `/{diagram_id}` 吃掉
- L74-L75 按 `update_time` 倒序

**`async create_diagram(data, response, user, db)`　L80-L98**
- **L85-L89 配额检查（TD-64）**：超过 `DIAGRAM_QUOTA` → **409**，并把上限写进错误信息
- L93-L96 建行、commit、refresh
- **L97 `response.headers["ETag"]`** —— 新建完就把版本给客户端，省一次 GET

**`async get_diagram(diagram_id, response, user, db)`　L102-L110** —— 取自己的图并回 ETag。

**`async update_diagram(diagram_id, data, response, if_match, user, db)`　L114-L157**（**乐观锁，TD-65**）
- L134 解析 `If-Match`（缺失 → 428）
- L135 确认存在性与归属（→ 404）
- **L136-L146 是关键：原子 CAS**。`UPDATE ... WHERE id=? AND user_id=? AND version=期望值 AND deleted_at IS NULL`，同时 `SET version = 期望值+1`；L140 `synchronize_session=False`
- **L147-L153 `rowcount == 0` → 412**，一个字都不写，错误信息里带上「云端第 N 版 / 你手上第 M 版」
- **L129-L132 docstring 说明了为什么必须是 CAS**：「读出来比一比再写」在并发下两个请求会同时读到同一版本、都通过检查、都写进去 —— 后写覆盖先写，锁等于没加。这与 TD-158（下载端点必须看 `mark_downloaded()` 返回值）是同一个道理
- L154-L155 commit + refresh（**会话是 `expire_on_commit=False`，不 refresh 会拿到旧值**）

**`async delete_diagram(diagram_id, user, db)`　L161-L170**
- 删除 = **打软删除时间戳**（TD-64），L169 `datetime.now(timezone.utc)`
- L165-L167 docstring：对调用方而言与硬删除无异（删完 GET 就是 404、列表里也没有）

**`async restore_diagram(diagram_id, user, db)`　L174-L192**
- L181 `_owned(..., include_deleted=True)`
- L182-L183 没被删除 → 409
- **L184-L188 恢复也要占配额** —— 否则「建满 → 删 → 恢复」就能绕过上限
- L189-L192 清 `deleted_at`、commit、refresh

---

### 📄 文件名：`shop.py`（324 行）

- **文件职责**：商业平台全部端点 —— 下单、模拟支付、一次性下载、微信回调。**本层最复杂的一个文件**。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `MOCK_PAY_PATH` | L39 | `"/shop/mock-pay"` | 模拟收银台路径（TD-124），**仅 `SHOP_PAY_MODE=mock` 时存在** |

#### 核心函数

**`async ping(_)`　L43-L45** —— 受保护端点，验证 SSO。

**`async create_order(request, db, user)`　L49-L108**
- **L60-L62 模式校验**：`SHOP_PAY_MODE` 只能是 `wechat` 或 `mock`，否则 **500**（配置错误）
- **L63-L66 `wechat` 模式下未配置齐 6 项 → 503**，错误信息里列出缺哪些环境变量
- **L67-L72 复用未支付订单**（S3-01-1-4）：避免连点几下刷出一堆待支付单
- **L73-L77 是关键分支：超时未支付**（S5-01-1）。旧单 `mark_closed` 后另起一单。L77-L79 注释说明后果：不这么做的话二维码过期后这个单会被无限复用，用户扫了必然失败，**且没有任何出路**（TD-109）
- L78-L79 有 `code_url` 就直接复用返回
- L82-L91 建新单并 **`await db.flush()`** —— L91 注释：先拿到 id，后面 commit 才不会因为下单失败而丢单。**订单先落库再去下单：微信那边付了、我们这边没单，比反过来难收拾得多**（L54-L55 docstring）
- **L95-L107 两条分支**：
  - `mock`（L93-L96）：`code_url` 指向本站模拟收银台，**用请求的 `base_url` 而不是 `SITE_BASE_URL`** —— 本地演示时链接要能直接点开
  - `wechat`（L97-L106）：调 `native_prepay`，**L105-L106 `WeChatPayError` → 502**

**`class MockPayIn(BaseModel)`　L116-L117** —— 只有 `order_no` 一个字段。

**`async mock_pay_page(request, order_no)`　L121-L127**
- L120 `include_in_schema=False`
- **L123-L124 非 mock 模式一律 404** —— 免得演示用的后门被带上生产环境（L111-L113 注释）

**`async mock_pay_confirm(payload, db, user)`　L131-L153**
- L139-L140 非 mock → 404
- L141-L143 订单不存在**或不是自己的** → 同一个 404（不暴露是否存在）
- **L144-L146 只在 `PENDING` 时才写支付信息**（幂等）
- **L147 `mark_paid`** —— L136-L137 docstring 是关键：**刻意复用与真实回调完全相同的状态机与幂等逻辑**，这样演示走通的路径和上生产走的是同一条，不会因为「演示专用代码」而漏测
- L147-L152 返回 4 个字段

**`async download_url(request, order_no, db, user)`　L164-L208**（**一次性下载，S3-02-4**）
- **L157-L163 的注释解释了为什么必须是 POST 而不是 GET**：这个端点**会改状态**（把 `paid` 烧成 `downloaded`）。GET 带副作用本来就是错的，而在 TD-44 之后它还是个 **CSRF 靶子** —— `SameSite=Lax` 只挡跨站的写方法，跨站顶层导航的 GET 照样带 cookie，攻击者一个跳转就能替用户把下载额度烧掉
- L176-L178 订单不存在或不是自己的 → 404
- **L179-L185 `PENDING` 或 `CLOSED` → 403「订单未支付」**。L180-L184 的注释很关键：`CLOSED` 是超时关闭（没付过钱），**不显式写这一行的话它会一路落到状态机**、由 `CLOSED→DOWNLOADED` 不在 `ALLOWED` 里而抛 409 —— 虽然也拦住了，但语义是错的（409 是状态冲突，这里是没权限），而且整个安全性都押在 `ALLOWED` 表不新增那条边上，**太脆**
- L186-L187 已下载过 → 403
- L189-L191 商品文件不存在 → 404
- L192 生成预签名 URL
- **L194-L203 是本端点的核心（TD-158）**：`mark_downloaded` 是一次 **CAS**，**必须看返回值**。L196-L200 的注释说明：上面的 `order.status == DOWNLOADED` 检查读的是**请求开始时的快照**，并发下多个请求会同时读到 `paid`；真正决定谁能拿到链接的是这次 CAS —— **忽略返回值的话，并发 4 个请求会全部拿到有效链接，「一次性下载」直接失效**。由 `test_concurrent_download_only_one_wins` 守着
- **L171-L175 docstring 说明了两重校验的分工**：状态机是**主力**（防无限倒卖），预签名 URL 的过期时间只是**第二重**（缩小转发窗口）。还有一条重要语义：**标记为已下载发生在发出链接时，不是文件真的被下载时** —— 云存储是客户端直连对象存储，应用根本看不到那次下载（TD-129）

**`async serve_download(request, key, expires, signature)`　L212-L231**
- L215-L216 **非 `local` 后端一律 404** —— 云存储由客户端直连，不经过本站
- **L220-L221 验签**（`verify_download`）→ 无效或过期 **403**
- L222-L225 读文件，**L224 捕获三种异常**（`ValueError` / `FileNotFoundError` / `OSError`）→ 404
- L223-L230 返回附件，文件名用 `filename*=UTF-8''` 编码（支持中文）

**`_storage(request)`　L234-L238** —— 构造存储后端，**`StorageError` → 503**（本站能力缺失）。

**`_ok()`　L241-L242** / **`_fail(status, message)`　L245-L247**
- **微信规定的应答体**，与 FastAPI 默认的 `{"detail": ...}` 不同（L246 docstring）

**`_payload(order, *, reused, pay_mode)`　L250-L259** —— 下单响应的 7 个字段。

**`async pay_notify(request, db)`　L263-L324**（**微信支付回调，S3-01-3**）
- **不走登录鉴权**：调用方是微信支付，身份靠平台证书验签确认（TD-115，L266 docstring）
- **L267-L272 docstring 是最重要的一段**：应答格式是微信规定的，**不能用 `HTTPException`**；验签通过 → 200/204 无包体，失败 → 4XX/5XX + `{"code":"FAIL",...}`。**4XX/5XX 会被微信重推**，所以「重试也没用」的情况（非支付成功通知、已处理过）**必须回 200**，否则会被无限重推
- L274-L276 未配置回调所需密钥 → `_fail(503, ...)`
- **L277 读原始报文主体** —— 验签必须用它，不能用解析后的对象
- **L280-L288 验签**：四个头（`Wechatpay-Timestamp` / `-Nonce` / `-Signature` + body），**`WeChatPayError` → `_fail(401, ...)`**
- L290-L293 JSON 解析失败 → `_fail(400, ...)`
- **L295-L305 解密 `resource`**（AES-GCM），失败 → `_fail(400, ...)`
- **L307-L308 是关键分支**：`event_type != TRANSACTION.SUCCESS` 或 `trade_state != SUCCESS` → **`_ok()`**。注释说明：退款/未支付等通知本阶段不处理，但**要确认收到，否则会被重推**
- L310-L312 订单不存在 → `_fail(404, ...)`
- **L313-L315 金额校验**：回调金额与订单金额不符 → `_fail(400, ...)`。**这是防篡改的关键一步**
- **L317-L320 幂等（S3-01-3-3）**：只在 `PENDING` 时写 `transaction_id` 与 `paid_at`，重复通知不覆盖首次的支付信息
- **L321-L324 `mark_paid`**，`IllegalTransition` → `_fail(409, ...)`；成功 → `_ok()`

---

### 📄 文件名：`admin.py`（100 行）

- **文件职责**：管理员抓取入库端点（TD-138，S4-01-4 的 HTTP 入口）。**只负责「鉴权 + 错误映射 + 入库」**，抓取与解析全在 `app/tools/`（模块 docstring L8-L11）。

#### 核心函数

**`async ingest_article(data, db, llm, admin)`　L34-L100**
- **L30-L33 挂了 LLM 档限流 + `require_admin`**。模块 docstring L3-L7 说明为什么必须双重防护：这个端点会让服务器去访问调用方给的**任意 URL** 并调用一次 LLM，公开出去就是「一个 SSRF 面 + 一个**烧钱面**」的组合
- **L63-L70 两条抓取分支**：`dynamic=true` 走 `browser.render` 再走**同一套** `parse_page`（保证换引擎不会换出一套不同的解析行为）；否则走 `parse_article`
- **L71-L89 是关键的五段 except 链，状态码对应五种责任方**：
  - **L70-L72 `BrowserUnavailable` → 503** —— 本站能力缺失（没装 playwright / 没下浏览器），不是调用方的错
  - **L73-L76 `RobotsDisallowed` → 400** —— **独立于 `CrawlError` 的异常类型，漏接就会变成 500**
  - **L77-L78 `CrawlError` → 400**
  - **L79-L81 `httpx.HTTPError` → 400** —— 目标站连不上/超时/TLS 失败，同样不是本站故障
  - **L82-L87 `ExtractError` → 422 或 502**
- **L83-L89 是全项目最值得看的一段异常处理**。**L56-L62 的 docstring 记了一个真实陷阱**：502 这一档**不能**写成 `except LLMError` —— `extract.identify_selectors` 会把 `LLMError` 包成 `ExtractError` 再抛，所以大模型故障到这里时**已经是 `ExtractError` 了**，写成 `except LLMError` 是**永不可达的死分支**，实测会把「未配置 LLM_API_KEY」报成「内容提取失败」（422）—— 诊断和状态码都错。所以 **L83 顺着 `__cause__` 认回去**
- L89 入库（同 URL 更新而非新增）
- L90-L100 返回 9 个字段，含 `content_length`（不回传全文）与 `ingested_by`

---

## 3. 执行逻辑流

### 3.1 一次请求在本层的完整路径

```
HTTP 请求
  │
  ├─ app/middleware.py         安全响应头 + CSP + 结构化日志 + X-Request-ID
  │
  ├─ 路由匹配（本层）
  │    ├─ dependencies=[Depends(rate_limit(...))]   ← 先限流（超额 429 + Retry-After）
  │    ├─ Depends(get_current_user) / require_admin ← 再鉴权（cookie 或 Bearer）
  │    └─ Pydantic 模型校验入参                      ← 不合法直接 422
  │
  ├─ 调用 app/tools/ 或 app/ 根模块的业务函数        ← 本层不写业务逻辑
  │
  ├─ except 链 → HTTP 状态码映射                     ← 本层最有信息量的部分
  │
  └─ response_model 序列化 / Response 直出
```

**顺序有讲究**：限流在鉴权**之前**（`dependencies=[...]` 先于函数参数里的 `Depends`），所以未登录的刷量请求也会被限流挡住，不会白跑一次数据库查询。

### 3.2 九条业务线各自的入口

```
GET  /healthz /health          health.healthz          L27   不碰依赖
GET  /readyz                   health.readyz           L33   真跑 SELECT 1

POST /auth/register            auth.register           L45   限流(auth)
POST /auth/login               auth.login              L57   限流(auth) → cookie + token
POST /auth/password            auth.change_password    L77   限流(auth) + 鉴权 → 吊销旧 token
POST /auth/logout              auth.logout            L108   故意不要鉴权
GET  /auth/me                  auth.me                L119   鉴权

GET  /oauth/authorize          oauth.authorize         L64   鉴权；只渲染同意页（不签码）
POST /oauth/authorize          oauth.authorize_submit L106   鉴权 + 签名校验 → 签发 code
POST /oauth/token              oauth.token            L147   客户端凭证 → 原子消费 code → token

POST /tools/er-diagram         tools.er_diagram        L23   限流(tools)；无鉴权；线程池
POST /tools/mermaid            tools.mermaid           L40   限流(llm)；无鉴权
POST /tools/word-export        tools.word_export       L53   限流(tools)；无鉴权；进程池

GET  /diagrams                 diagrams.list_diagrams  L66   鉴权；?deleted=true 看回收站
POST /diagrams                 diagrams.create_...     L80   鉴权 + 配额(409)
GET  /diagrams/{id}            diagrams.get_diagram   L102   鉴权 + 归属(404) → ETag
PUT  /diagrams/{id}            diagrams.update_...    L114   鉴权 + If-Match 乐观锁(428/412)
DEL  /diagrams/{id}            diagrams.delete_...    L161   鉴权 → 软删除
POST /diagrams/{id}/restore    diagrams.restore_...   L174   鉴权 + 恢复也占配额

POST /shop/orders              shop.create_order       L49   鉴权 → 复用/超时关单/新建
GET  /shop/mock-pay            shop.mock_pay_page     L121   仅 mock 模式，否则 404
POST /shop/mock-pay/confirm    shop.mock_pay_confirm  L131   仅 mock 模式；复用真状态机
POST /shop/download/{no}       shop.download_url      L164   鉴权 + CAS 一次性(403)
GET  /shop/dl                  shop.serve_download    L212   仅 local 后端；验签
POST /shop/pay/notify          shop.pay_notify        L263   无鉴权；验签 → 幂等 mark_paid

GET  /<工具页>×N               site._page_view         L20   SSR 出 TDK
GET  /sitemap.xml              site.sitemap            L44   读同一份 PAGES 清单
GET  /robots.txt               site.robots             L55

POST /support/ask              support.ask             L18   限流(llm)；无鉴权（引流）

POST /admin/articles/ingest    admin.ingest_article    L34   限流(llm) + require_admin
```

### 3.3 三处「并发正确性」的共同套路

本层有三个端点都用**同一种手法**解决并发问题，值得放在一起看：

| 端点 | 手法 | 行号 | 守着它的测试 |
| --- | --- | --- | --- |
| `PUT /diagrams/{id}` | `UPDATE ... WHERE version = 期望值` + 查 `rowcount` | diagrams L131-L147 | 乐观锁相关用例 |
| `POST /shop/download/{no}` | `mark_downloaded()` 返回 bool，**必须看返回值** | shop L194-L203 | `test_concurrent_download_only_one_wins` |
| `POST /oauth/token` | `UPDATE ... WHERE used IS FALSE` + 查 `rowcount` | oauth L173-L179 | 授权码重放用例 |

**共同点是：绝不「先读出来比一比再写」。** 那样在并发下两个请求会同时读到同一个旧值、都通过检查、都写进去 —— 后写覆盖先写，锁等于没加（`diagrams.py` L124-L128 与 `shop.py` L196-L200 的注释都写了这个后果）。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `2dd4d8c`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) 每个文件的路由函数与精确行范围（本文所有 Lxx-Lyy 的来源）
python -c "import ast,pathlib;[print(f'{p.name} {n.lineno}-{n.end_lineno} {n.name}') for p in sorted(pathlib.Path('app/routers').glob('*.py')) for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]"

# 2) 路由统计（本文 1.1 的三个数字）
python -c "import main; rs=[r for r in main.app.routes if getattr(r,'path','').startswith('/')]; biz=[r for r in rs if not getattr(r,'path','').startswith(('/docs','/redoc','/openapi.json','/static'))]; print(f'总 {len(rs)} / 业务 {len(biz)} / 唯一路径 {len({r.path for r in biz})}')"
# 预期：总 41 / 业务 36 / 唯一路径 31

# 3) 本层的测试
python -m pytest tests/test_auth.py tests/test_auth_cookie.py tests/test_oauth.py tests/test_oauth_consent.py tests/test_token_revocation.py tests/test_diagrams.py tests/test_diagram_quota.py tests/test_mock_pay.py tests/test_download.py tests/test_wechat_pay.py tests/test_wechat_notify.py tests/test_ops.py tests/test_admin_ingest.py tests/test_site.py tests/test_er_page.py tests/test_ratelimit.py -q
# 本机实测：196 passed, 1 skipped
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动 `app/routers/` 下任何文件后，本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> 背景（为什么文档里的数字比代码更容易腐烂、以及一次真实的漏改事故）见 `docs/ARCHITECTURE_GUIDE.md` 第 7 课 7.10。
