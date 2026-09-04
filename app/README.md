# `app/` 模块说明书（根级基础设施）

> **行号基准 commit：`4c9868d`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/tools/README.md`（业务逻辑层）、`app/routers/README.md`（HTTP 接口层）、
> `database init/README.md`（建表脚本，`models.py` 的对偶）。
> 本文只讲 `app/` **根目录**的 16 个文件，不含子目录。

---

## 1. 模块概述

### 1.1 定位

`app/` 根目录是**整个后端的地基**：16 个文件、1 328 行。上面两层（`app/routers/` 与 `app/tools/`）都建立在它之上，而它自己**不依赖任何业务代码**。

按职责分六组：

| 组 | 文件 | 职责 |
| --- | --- | --- |
| **配置** | `config.py` `startup_checks.py` | 38 项配置 + 生产环境启动自检 |
| **数据** | `database.py` `models.py` `timeutil.py` | 引擎/会话、7 张表、跨后端时间归一化 |
| **安全** | `security.py` `deps.py` | 密码哈希、JWT 签发/解析、鉴权依赖 |
| **协议** | `schemas.py` `wechat_pay.py` `storage.py` | 入参出参模型、微信支付签名、存储策略 |
| **运行时** | `cpu_pool.py` `ratelimit.py` `middleware.py` | 进程池、限流、安全头与日志 |
| **站点** | `site.py` | 工具清单（同时驱动路由/导航/sitemap/TDK） |

`order_state.py` 单独一类：它是**订单状态机**，属于业务规则但被三处共用，所以放在根级。

### 1.2 内部依赖图

实测 `grep -nE "^from \." app/*.py`，共 14 条内部 import：

```
                       config.py  ← 被 6 个模块依赖（最底层）
                    ↙    ↓    ↓    ↘        ↘
        database.py  ratelimit  security  startup_checks  storage  wechat_pay
             ↓  ↓                  ↓  ↓
        models.py  deps.py ←───────┘  timeutil.py ←── security / deps / order_state
             ↓         ↑
      order_state.py ──┘

   site.py        ← 独立（只依赖 fastapi 模板）
   cpu_pool.py    ← 独立（只依赖 starlette 线程池）
   middleware.py  ← 独立（只在运行期从 .config 延迟 import settings）
   schemas.py     ← 独立（纯 pydantic）
```

**依赖是严格分层的，没有任何循环**（`app/tools/` 里有一处 `crawler ⇄ politeness` 循环，本层没有）。

三个值得注意的设计：

- **`config.py` 被 6 个模块依赖**（`database` `ratelimit` `security` `startup_checks` `storage` `wechat_pay`），是唯一的最底层
- **`middleware.py` 不在顶部 import `settings`**（`app/middleware.py:90` 在函数内部延迟导入）—— 中间件在 `main.py` 很早就被装配，此时配置已加载完毕，延迟导入纯粹是为了避免模块级副作用
- **`site.py` 完全独立**：它只依赖 `fastapi.templating`，不碰数据库也不碰配置（`SITE_BASE_URL` 由调用方 `app/routers/site.py` 读）

### 1.3 全层统一的四条设计约定

**① 「现取配置」而不是模块级缓存。** `pay_config()`（`wechat_pay.py:84`）、`build_storage()`（`storage.py:98`）、`rate_limit()`（`ratelimit.py:90` 的 `getattr`）都在**每次调用时**读 `settings`，而不是在模块加载时取一次值。原因写在 `wechat_pay.py:85`：方便测试改 settings 后立刻生效。

**② 跨后端时间一律在读的时候归一化。** PostgreSQL 的 `TIMESTAMPTZ` 读回来带时区，SQLite 读回来是**裸值**。所有比较前都过 `as_utc()`（`timeutil.py:17`）。不做这层，SQLite 上一比较就抛 `TypeError`，而**这个错只在 SQLite 上出现，真库测试反而看不见**（TD-146 就是这么漏的）。

**③ 并发正确性一律用原子 CAS，绝不「先读后写」。** 本层三处：`order_state._cas`（L92）、`SysDiagram.version` 乐观锁（`models.py:94`）、`OAuthCode.used` 原子消费（`app/routers/oauth.py`）。

**④ 配置错误在启动时暴露，不在运行时。** `startup_checks.enforce_production_settings()` 在 `main.py:22` 执行，不合规直接拒绝启动（`app/startup_checks.py:3-4`：**在启动时失败，而不是在用户下单时失败**）。

---

## 2. 文件级详细说明书

### 📄 文件名：`__init__.py`

- **文件职责**：空文件，标记 `app` 为包。
- **核心类/函数清单**：无。

---

### 📄 文件名：`config.py`（84 行）

- **文件职责**：全部 38 项配置，从 `.env` 读取。

#### 核心类/常量

**`class Settings(BaseSettings)`　L4-L81** —— 38 个字段，按功能分 8 组（每组前有注释说明）：

| 组 | 行 | 字段 |
| --- | --- | --- |
| 数据库 | L7-L12 | `DB_HOST` `DB_PORT` `DB_NAME` `DB_USER` `DB_PASSWORD` `DATABASE_URL` |
| 安全 | L13-L15 | `SECRET_KEY` `ALGORITHM` `ACCESS_TOKEN_EXPIRE_MINUTES` |
| LLM | L18-L20 | `LLM_API_KEY` `LLM_BASE_URL` `LLM_MODEL` |
| 站点 | L23 | `SITE_BASE_URL` |
| 微信支付 | L26-L32 | `WX_APPID` `WX_MCHID` `WX_SERIAL_NO` `WX_PRIVATE_KEY` `WX_API_V3_KEY` `WX_NOTIFY_URL` `WX_PLATFORM_CERT` |
| 商品与支付 | L35-L43 | `SHOP_PRODUCT_NAME` `SHOP_PRODUCT_AMOUNT` `ORDER_EXPIRE_MINUTES` `SHOP_PAY_MODE` |
| 存储 | L46-L49 | `STORAGE_BACKEND` `STORAGE_LOCAL_ROOT` `STORAGE_PRODUCT_KEY` `DOWNLOAD_URL_TTL` |
| 配额/限流/运维 | L54-L70 | `DIAGRAM_QUOTA` `RATE_LIMIT_*`(5 项) `TRUST_PROXY_HEADERS` `ENV` `LOG_LEVEL` `HSTS_MAX_AGE` |

几个字段注释里写明了**为什么是这个值**：

- **`SHOP_PAY_MODE`（L41-L43）** —— `wechat` = 微信支付；`mock` = 模拟收银台，**开着就等于免费发货**（TD-124）
- **`ORDER_EXPIRE_MINUTES`（L37-L39）** —— 解决 TD-109：过期二维码的订单被无限复用、扫了必失败
- **`DIAGRAM_QUOTA`（L51-L54）** —— 只约束**存活**行数，回收站本身不会自动清空（TD-179）
- **`TRUST_PROXY_HEADERS`（L62）** —— **只在可信反向代理之后才打开**（TD-142）
- **`HSTS_MAX_AGE`（L68-L70）** —— 只在请求确实是 https 时才下发，否则会把仍在用 http 的本地环境锁死一年

**`model_config`（L72）** —— `env_file=".env"` + **`extra="ignore"`**（`.env` 里多出来的键不报错）。

**`sqlalchemy_url`（property，L74-L81）**
- **L76-L77 关键分支**：`DATABASE_URL` 非空就直接用它（测试注入内存 SQLite 靠这个），否则拼 PostgreSQL URL

**`settings`　L84** —— 模块级单例，全项目都从这里读配置。

---

### 📄 文件名：`database.py`（18 行）

- **文件职责**：SQLAlchemy 异步引擎与会话。

#### 核心类/函数

**`class Base(DeclarativeBase)`　L7-L8** —— 所有 ORM 模型的基类。

**`engine`　L11** / **`SessionLocal`　L12**
- **L12 `expire_on_commit=False`** 是刻意选的：commit 之后对象属性不会失效，可以直接读。**代价**是 update 之后必须显式 `refresh()` 才能拿到新值 —— `app/routers/diagrams.py:155` 的注释专门提醒了这一点

**`async get_db()`　L15-L18** —— FastAPI 依赖，`async with` 保证会话一定被关闭。

---

### 📄 文件名：`timeutil.py`（19 行）

- **文件职责**：跨后端时间归一化（TD-146 / TD-155）。

#### 核心函数

**`as_utc(dt)`　L17-L19**
- **L19 一行**：已带时区原样返回，裸值按 UTC 解读
- **模块 docstring L3-L10 是这个文件的全部价值**：只有两处逻辑，但两处都必须在**读**的时候做。**不做这层归一化，SQLite 上一比较就抛 `TypeError: can't compare offset-naive and offset-aware datetimes` —— 而这个错只在 SQLite 上出现，真库测试反而看不见（TD-146 就是这么漏的）**

---

### 📄 文件名：`models.py`（128 行）

- **文件职责**：7 张表的 ORM 定义。

#### 核心类

**`class User(Base)`　L9-L30**（表 `sys_user`，10 字段）
- **L17 `status`** —— 1 正常 / 0 禁用
- **L18-L24 `role`** —— **L21-L23 的注释是关键设计**：角色**不进 JWT**。`get_current_user` 每个请求都从库里读用户，所以**改角色立刻生效**，不必等 token 过期，也不必像 TD-70 那样再造一个失效时间戳。代价是每请求一次查库 —— 而这个查本来就要做（要读 `status` 和 `password_changed_at`）
- **L25-L28 `password_changed_at`** —— TD-70 的核心：JWT 里带这个时间戳的副本，校验时对不上就拒。**这样改密码能一次吊销该用户所有旧 token，不必维护 jti 黑名单表**。为 None 表示从未改过密码

**`class Order(Base)`　L33-L48**（表 `sys_order`，11 字段）
- **L42 `amount`** —— 单位是**分**（整数，不用浮点）
- **L44 `code_url`** —— NATIVE 下单返回的二维码链接
- **L45 `transaction_id`** —— 微信支付订单号（回调解出）

**`class Article(Base)`　L51-L67**（表 `sys_article`，8 字段）
- **L61 `url` 唯一** —— 同一篇不重复入库
- **L64 `published_at` 存字符串** —— **L54-L55 docstring 说明**：各家日期格式差异太大，强行解析成 datetime 反而会丢信息（TD-137）

**`class SysConfig(Base)`　L70-L78**（表 `sys_config`，4 字段）—— 系统配置键值表。

**`class SysDiagram(Base)`　L81-L100**（表 `sys_diagram`，8 字段）
- **L90-L93 `deleted_at`** —— 软删除（TD-64）。**L91-L92 的注释是一条硬约束**：**每一处读取都必须带 `deleted_at IS NULL` 过滤** —— 漏一处就等于「删了还能看见」，所以 `_owned()` 与列表查询都走同一个 `_alive()` 条件，**不散写**
- **L94-L98 `version`** —— 乐观锁版本号（TD-65）。**L95-L97 说明为什么必须是原子 CAS**：「先读出来比一比再写」在并发下两边都会读到同一个版本、都通过检查、都写进去，**丢更新照旧**（与 TD-158 下载端点是同一个坑）

**`class OAuthClient(Base)`　L103-L113**（表 `oauth_client`，6 字段）
- **L110 `client_secret_hash`** —— 存哈希，不存明文

**`class OAuthCode(Base)`　L116-L128**（表 `oauth_code`，8 字段）
- **L127 `used`** —— 授权码一次性标记，配合 `app/routers/oauth.py:173` 的原子消费

---

### 📄 文件名：`security.py`（71 行）

- **文件职责**：密码哈希与 JWT。

#### 核心常量/类

**`pwd_context`　L10** —— bcrypt。

**`AUTH_COOKIE = "access_token"`　L15**
- **L12-L14 注释说明为什么必须是 HttpOnly**：脚本读不到它，XSS 就拿不走 token；存在 localStorage 里的话任何一段注入脚本都能直接读走（TD-44）

**`@dataclass(frozen=True) class TokenClaims`　L27-L35**
- **L30-L31 说明 `pwd` 的含义**：签发时该用户 `password_changed_at` 的 UNIX 秒；None 表示签发时用户从未改过密码。**校验逻辑在 `app/deps.py`（要查库，所以不放这里）**

#### 核心函数

**`hash_password(password)`　L18-L19** / **`verify_password(plain, hashed)`　L22-L23**

**`_pwd_stamp(moment)`　L38-L43**
- **L41-L42 None 直接返回 None**
- **L43 用 UNIX 秒**。L39-L40 说明理由：JWT 声明里放数字比放 ISO 串小得多，而且不受时区/格式歧义影响

**`create_access_token(subject, password_changed_at=None)`　L46-L54**
- **L49-L50 docstring 是一条使用约束**：`password_changed_at` 必须传**当前库里的值**，否则改过密码的用户拿到的 token 会立刻被判为失效
- L52-L54 三个声明 `sub` / `exp` / `pwd`

**`decode_token(token)`　L57-L71**
- **L63-L66 `try-except JWTError` → 返回 None**（不抛异常，让调用方决定怎么报错）
- **L67-L69 缺 `sub` 也返回 None**
- **L60-L61 docstring 划清了职责边界**：这里**只**验签与 exp，**不判断 pwd 是否过期** —— 那需要查库拿用户当前值，属于 `get_current_user` 的职责

---

### 📄 文件名：`deps.py`（65 行）

- **文件职责**：鉴权依赖。**全项目最容易被改坏的文件之一**。

#### 核心函数

**`oauth2_scheme`　L13**
- **L11-L12 说明为什么 `auto_error=False`**：缺 Authorization 头时返回 None 而不是直接 401 —— **还要给 cookie 一次机会**（TD-44：浏览器走 cookie，API 客户端/Swagger 走 Bearer）

**`async get_current_user(request, token, db)`　L16-L51**
- **L22 双通道取 token**：`token or request.cookies.get(AUTH_COOKIE)`。**L21 说明优先级**：两者都给时**以请求头为准**，那是调用方显式表达的意图
- **L23-L27 未登录 → 401**，并**手工补 `WWW-Authenticate: Bearer` 头**。**L24-L25 说明为什么**：`auto_error=False` 之后这个头要自己补，否则 Swagger 的「Authorize」按钮不再弹出登录框
- **L28-L30 token 无效 → 401**
- **L31-L33 用户不存在或已禁用 → 401**
- **L34-L50 是 TD-70 的核心，注释写了整整 17 行**：
  - **L47 只在 `password_changed_at` 非 NULL 时才检查** —— 这样上线本次改动不会让全站已登录用户瞬间掉线
  - **L49 比较用 `<` 而不是 `!=`**。**L38-L43 记录了一次变异测试的结论**：`<` 与 `!=` 在所有**可达**路径上行为一致（改密码后新签发的 token 其 pwd 与库里当前值相等，两种写法都放行，`a != a` 本就是 False）。**实测把这里改成 `!=` 是杀不掉的等价变异，15 条测试全绿**。两者只在 `pwd > current`（机器时钟回拨、或有人手改了库里的值）时才有区别：`<` 放行、`!=` 拒绝。**选 `<` 是因为那种情况下拒绝会让用户莫名其妙登不进去，而放行并没有放宽真正的威胁模型**

**`async require_admin(user)`　L54-L65**
- **L63-L64 `role != 1` → 403**
- **L57-L59 docstring 解释了一个反直觉的选择**：返回 **403 而不是 404** —— 端点存在与否不是本站的秘密（`/docs` 里本来就列着），假装不存在只会让管理员自己调试时对着 404 猜半天。**真正的防线是「端点只接受管理员」，而不是「别人找不到」**
- **L61 角色从库里读、不从 JWT 读，所以降权立刻生效**，不用等 token 过期

---

### 📄 文件名：`startup_checks.py`（50 行）

- **文件职责**：生产环境启动自检（S5-03）。

#### 核心常量/类

**`DEFAULT_SECRET = "dev-secret-change-me"`　L12** —— 与 `config.py:13` 的默认值一致。

**`class ProductionConfigError(RuntimeError)`　L15-L16**

#### 核心函数

**`check_production_settings() -> list[str]`　L19-L42**
- **L20-L21 非 production 直接返回空列表**
- **L20 docstring 说明为什么单独成函数**：能直接单测，不必真启动应用
- **四项检查，每项都写明后果**：
  - **L26-L29 `SHOP_PAY_MODE == "mock"`** —— **等于免费发货**（TD-124）。默认值虽然是 wechat，但**一次复制粘贴 .env 就可能带上去**，后果是白送商品
  - **L31-L32 `SECRET_KEY` 仍是默认值** —— 任何人都能伪造 JWT
  - **L34-L35 `RATE_LIMIT_ENABLED=false`** —— 公开工具端点可被无限刷（TD-15）
  - **L37-L41 `TRUST_PROXY_HEADERS=false`** —— 若部署在反向代理之后，限流会把所有用户当成同一个 IP（TD-142）。**这一项的措辞刻意留了余地**：「确实不在代理之后才可忽略此项」

**`enforce_production_settings()`　L45-L50**
- **L47-L49 有问题就抛 `ProductionConfigError`**，错误信息把所有问题**逐条列出**（不是一条条报、改一条再看下一条）

---

### 📄 文件名：`order_state.py`（98 行）

- **文件职责**：订单状态机（S3-01-2）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `PENDING` | L22 | `"pending"` | 待支付 |
| `PAID` | L23 | `"paid"` | 已支付 |
| `DOWNLOADED` | L24 | `"downloaded"` | 已下载 |
| `CLOSED` | L25 | `"closed"` | 超时关闭（S5-01-1） |
| `STATES` | L27 | 四元组 | 全部状态 |
| `ALLOWED` | L30-L37 | dict | **唯一允许的迁移边（实测 4 条）** |

**`ALLOWED` 的四条边**：

```
PENDING    → PAID, CLOSED
CLOSED     → PAID          ← 这条是故意的，见下
PAID       → DOWNLOADED
DOWNLOADED → （终态）
```

> **`CLOSED → PAID` 是全文件最值得看的一条边**（L32-L33 注释）：关单只是我们这边不再等它，但用户完全可能已经扫了旧二维码把钱付了。**钱收了就必须发货，否则是收钱不发货（TD-156）**。这条边漏掉的后果由 `test_late_payment_on_closed_order_still_delivers` 守着。

#### 核心函数

**`class IllegalTransition(Exception)`　L40-L41**

**`check_transition(current, target)`　L44-L47**
- **L46-L47 不在 `ALLOWED` 里就抛**。`ALLOWED.get(current, ())` 用 `.get` 而不是下标 —— 未知状态也不会 KeyError

**`async mark_paid(db, order) -> bool`　L50-L61**
- **L57-L58 `PENDING` 或 `CLOSED` → CAS 到 PAID**（返回是否真的迁移了）
- **L59-L60 `PAID` 或 `DOWNLOADED` → 返回 False**。L60 注释：**重复通知：幂等，不报错**
- **L61 其它状态 → 抛 `IllegalTransition`**
- **返回值语义很重要**：调用方必须看它才知道自己是不是「抢到了」这次迁移（`app/routers/shop.py:194-203` 的一次性下载就栽过这个坑，TD-158）

**`is_expired(order, ttl_minutes, now=None)`　L64-L73**
- **L70-L71 只对待支付单有意义**，其它状态一律 False
- **L72 `now` 可注入** —— 测试不必真的等 30 分钟
- **L67-L68 docstring 说明为什么不加 `expire_at` 字段**：过期时间 = `create_time` + 配置值就能算出来，**多存一列只会多一个要与配置保持同步的东西**

**`async mark_closed(db, order) -> bool`　L76-L81**
- **L78-L79 已关闭 → False**（幂等）
- **L80 先 `check_transition`** 再 CAS

**`async mark_downloaded(db, order) -> bool`　L84-L89**
- **L86-L87 已下载 → False**（幂等）
- **L88-L89 `PAID → DOWNLOADED`**，未支付会抛 `IllegalTransition`

**`async _cas(db, order, expected, target) -> bool`　L92-L98**
- **L93-L95 是整个状态机的核心**：`UPDATE Order SET status=target WHERE id=? AND status=expected`
- **L98 `return result.rowcount == 1`** —— **这就是「原子」二字的落点**。并发回调时只有一个请求能拿到 `rowcount=1`，其余拿到 0（L9-L10 docstring 说明这与 `/oauth/token` 消费授权码是同一个套路）

---

### 📄 文件名：`ratelimit.py`（101 行）

- **文件职责**：接口限流（TD-15，上线阻塞项之一）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `_PRUNE_THRESHOLD` | L26 | `1024` | key 多到这个数就顺手清一次 |

#### 核心类/函数

**`@dataclass class Limiter`　L30-L66**
- **L37 `clock` 可注入**（默认 `time.monotonic`）—— L33 说明：**测试就不必真的等窗口过期**
- **L38 `_hits: dict[str, deque]`** —— 每个 key 一个双端队列，存命中时刻

**`Limiter.allow(key, *, limit, window) -> tuple[bool, int]`　L40-L53**
- **L46-L47 滑动窗口的核心**：把队首所有「已经滑出窗口」的命中弹掉
- **L48-L49 超额 → 返回 `(False, 建议重试秒数)`**。`max(1, ...)` 保证至少 1 秒（避免 `Retry-After: 0`）
- **L50 未超额 → 记录本次命中**
- **L51-L52 key 太多就顺手 prune** —— L26 注释：避免字典随 IP 无限增长

**`Limiter.prune(window)`　L55-L63**
- **L58-L59 的注释记了一个真实的坑**：**不能只删空 deque** —— `allow` 弹出旧命中后紧接着就会 append，**deque 在实践中永远非空，那样删等于什么都没删**。所以要按「**最后一次命中的时间**」判断（L62 用 `dq[-1]`）

**`Limiter.reset()`　L65-L66** —— 清空（测试用）。

**`limiter`　L69** —— 模块级单例。

**`client_key(request) -> str`　L72-L78**
- **L74-L77 只有 `TRUST_PROXY_HEADERS` 打开时才读 `X-Forwarded-For`**，取第一个 IP
- **L78 默认取 socket 对端地址**
- **模块 docstring L11-L13 说明为什么**：**不能无条件信任 `X-Forwarded-For`** —— 那是客户端可以随便填的头，无条件信任等于把限流交给攻击者控制（**每次换一个 XFF 就是无限配额**）

**`rate_limit(scope, limit_attr)`　L81-L101**（**依赖工厂**）
- **L84 说明为什么 `limit_attr` 是属性名而不是值**：运行时才读，方便测试改配额
- **L88-L89 总开关关闭 → 直接放行**
- **L90-L93 键是 `f"{scope}:{client_key(request)}"`** —— 按「作用域 + 客户端 IP」计数，所以同一个 IP 刷 `/tools/er` 不影响 `/tools/mermaid` 的配额
- **L94-L99 超额 → 429 + `Retry-After` 头**

---

### 📄 文件名：`cpu_pool.py`（76 行）

- **文件职责**：重 CPU 任务的执行池（S5-02-2 / S5-03）。

#### 模块级状态

| 变量 | 行 | 作用 |
| --- | --- | --- |
| `_executor` | L30 | 进程池单例（懒创建） |
| `_broken` | L31 | **进程池坏过一次就别再试了**，避免每个请求都付一次失败开销 |

#### 核心函数

**`_get_executor() -> ProcessPoolExecutor | None`　L34-L47**
- **L36-L37 已标记坏 → 直接返回 None**
- **L38-L46 懒创建 + 创建期兜底**：**L40-L41 说明 `max_workers=1`** —— 导出是低频操作且已限流，一个 worker 足够；多开只是多占内存，并不会更快（GIL 换成多进程后瓶颈变成 CPU 核数）
- **L43 `except Exception`（带 `# noqa: BLE001`）** —— L43 注释：**兜底路径必须吞掉所有创建期异常**
- **L44-L45 失败则记 warning 并置 `_broken = True`**

**`async run_cpu_bound(fn, *args)`　L50-L68**
- **L53-L54 docstring 是一条硬约束**：`fn` 与其参数、返回值都必须能被 **pickle** —— 函数要定义在模块顶层，参数/返回值是普通数据结构。`build_data_dictionary(graph) -> bytes` 满足
- **L56-L58 进程池不可用 → 退化到线程池**
- **L59 `import asyncio` 在函数内部** —— 避免模块级导入开销
- **L62-L68 运行期失败同样兜底**：置 `_broken`，改用线程池重跑一次

**`shutdown()`　L71-L76**
- **L72 说明为什么需要它**：应用退出时回收子进程，**否则会留下孤儿进程**。由 `main.py:27` 的 lifespan 调用

> **模块 docstring L3-L19 是这个文件最有价值的部分**，回答了两个问题：
> - **为什么线程池不够**（L3-L12）：线程池能让出**事件循环**，但让不出 **GIL**。实测满额 20000 字符 DDL 导 Word（`build_data_dictionary` 单次约 340 ms）：线程池并发轻量请求 p95 **76.59 ms**、进程池 **35.75 ms**
> - **为什么保留线程池兜底**（L14-L19）：Windows 上 spawn 要重新导入模块、容器可能限进程数/内存、某些环境 fork 不安全 —— 这些都会让进程池**创建或提交任务时抛异常**。**真出问题时退化成线程池（慢但不坏）远好过让导出功能直接 500**

---

### 📄 文件名：`storage.py`（110 行）

- **文件职责**：云存储策略（S3-02-2）+ 预签名下载 URL（S3-02-3）。

#### 核心类/函数

**`class StorageError(Exception)`　L23-L24**

**`class Storage(Protocol)`　L27-L34** —— 策略接口，只有三件事：`backend` 属性、`exists(key)`、`presigned_url(key, *, expires_in)`。**L3-L4 docstring 说明目的**：换云厂商就是换一个实现，业务代码（`app/routers/shop.py` 的下载接口）一行不用改

**`sign_download(secret, key, expires) -> str`　L37-L42**
- **L42 `HMAC-SHA256(secret, "<key>\n<expires>")`**
- **L40 docstring 说明为什么把 key 也签进去**：否则**拿到一个合法链接就能改 key 去下别的文件**

**`verify_download(secret, key, expires, signature) -> bool`　L45-L49**
- **L47-L48 先查过期**
- **L49 `hmac.compare_digest`** —— L46 注释：**防时序侧信道**

**`class LocalStorage`　L52-L95**（本地目录后端）
- **`backend = "local"`　L59**
- **`__init__(root, base_url, secret)`　L61-L64**
- **`_path(key) -> Path`　L66-L72** —— **L68-L71 是关键安全检查：挡目录穿越**（`../../etc/passwd` 这种）。解析后必须仍在 root 之下，否则抛 `ValueError`
- **`put(key, data)`　L74-L78** —— L75 注释：生产由运营上传，**测试用来造商品文件**
- **`exists(key)`　L80-L84** —— **L83-L84 捕获 `ValueError` 返回 False**（非法 key 当作不存在，不往上抛）
- **`read(key)`　L86-L88** —— **L87 注释**：仅本地后端有，云端是客户端直连下载、不经过应用
- **`presigned_url(key, *, expires_in)`　L90-L95** —— 指向本站 `/shop/dl`，带 `key` / `expires` / `signature` 三个查询参数

**`build_storage(base_url) -> Storage`　L98-L110**
- **L99 说明为什么每次现读配置**：方便测试改 settings 后立刻生效
- **L105-L106 `local` → `LocalStorage`**
- **L107-L110 未知后端直接报错，不退回本地**。**L101-L102 docstring 说明理由**：**静默降级等于把文件从对象存储挪到应用目录，是安全性的降级，宁可起不来**

> **模块 docstring L6-L9 记了一个重要的范围决定**：阿里云 OSS / 腾讯云 COS 需要密钥，而沙箱里没有，**写出来的云适配器无法验证签名是否正确，所以刻意不写（TD-128）**。本地后端用的是与云厂商**同构**的机制（HMAC 签名 + 过期时间戳），所以预签名、过期、篡改、越权换 key 这几件事都是真实可测的

---

### 📄 文件名：`schemas.py`（76 行）

- **文件职责**：全部入参/出参的 Pydantic 模型（11 个类）。

#### 核心类

| 类 | 行 | 字段与约束 |
| --- | --- | --- |
| `RegisterIn` | L6-L8 | `username` 3-50、`password` **6-64** |
| `UserOut` | L11-L17 | `id` `username` `nickname` `avatar`；**L12 `from_attributes=True`** 让它能直接从 ORM 对象转 |
| `PasswordChangeIn` | L20-L24 | **L21 docstring**：新密码规则与 `RegisterIn` 保持一致，**避免两套标准** |
| `TokenOut` | L27-L29 | `token_type` 默认 `"bearer"` |
| `ErDiagramIn` | L32-L33 | `ddl` 1-**20000**（限流与性能测试都按这个上限算） |
| `MermaidIn` | L36-L37 | `text` 1-10000 |
| `ArticleIngestIn` | L40-L52 | `url` 1-500、`dynamic` 默认 False |
| `SupportIn` | L55-L56 | `text` 1-2000 |
| `DiagramIn` | L59-L61 | `name` 1-100、`content` 1-**500000**（L61 注释：drawio XML 可能较大） |
| `DiagramSummary` | L64-L72 | `id` `name` `update_time` `version` |
| `DiagramOut` | L75-L76 | 继承 Summary，加 `content` |

**`ArticleIngestIn`（L40-L52）的两条注释值得单独看**：

- **L43-L46 为什么只用长度卡 url、不做格式校验**：真正的校验是 `crawler.assert_public_url`（协议白名单 + 逐个解析结果必须 `is_global`）。**在 schema 里再写一套 URL 规则等于两处真相，SSRF 判定必须只有一处**。500 与 `sys_article.url VARCHAR(500)` 对齐，超长直接在入口挡掉
- **L50-L51 `dynamic` 默认 False**（TD-191）：True 时用无头浏览器渲染后再解析，给 httpx 抓不到正文的 SPA 站点兜底。**默认 False —— 渲染比一次 HTTP GET 贵一个数量级，不该是默认行为**

**`DiagramSummary.version`（L70-L72）** —— 乐观锁版本号（TD-65）。**响应里另有一个 ETag 头是同一个值**；这里也放一份，是因为**客户端不总能方便地读响应头，而列表页也需要知道每张图的当前版本**

---

### 📄 文件名：`site.py`（68 行）

- **文件职责**：站点级清单（S2-02-1）。**新增一个工具页只需要往 `TOOLS` 里加一条 —— 路由、首页导航、sitemap 自动跟上**（模块 docstring L3）。

#### 核心常量/类

**`SITE_NAME = "CodeMax 在线工具"`　L12**

**`@dataclass(frozen=True) class Tool`　L16-L22** —— 6 个字段：`key` `title` `path` `description` `keywords` `template`

**`HOME`　L25-L32** —— 首页（`key="home"`、`path="/"`、`template="index.html"`）

**`TOOLS`　L34-L59** —— **实测 3 个工具**：

| key | title | path | template |
| --- | --- | --- | --- |
| `er` | SQL DDL 转 ER 图 | `/tools/er` | `er.html` |
| `mermaid` | 自然语言生成 UML 类图 | `/tools/mermaid` | `mermaid.html` |
| `drawio` | Drawio 在线流程图 | `/tools/drawio` | `drawio.html` |

每条的 `description` 与 `keywords` 就是页面的 `<meta>` 内容（TDK）。

**`PAGES = (HOME, *TOOLS)`　L61** —— **实测 4 个页面**。路由与 sitemap 的完整清单

**`templates`　L63** —— `Jinja2Templates`，目录用 `Path(__file__).resolve().parent / "templates"`（**绝对路径**，所以从任何工作目录启动都能找到模板）

**`page_title(tool)`　L66-L68**
- **L68 首页用站名本身，工具页拼站名后缀** —— `f"{tool.title} - {SITE_NAME}"`

---

### 📄 文件名：`middleware.py`（154 行）

- **文件职责**：安全响应头 + 结构化请求日志（S5-03-3，对应 TD-90/91）。

#### 关键常量

| 常量 | 行 | 值 |
| --- | --- | --- |
| `_CDN` | L38 | `https://cdn.jsdelivr.net` |
| `_DRAWIO` | L39 | `https://embed.diagrams.net` |
| `CONTENT_SECURITY_POLICY` | L41-L54 | **10 条指令**拼接 |
| `_STATIC_SECURITY_HEADERS` | L57-L63 | **5 个安全头**，预先编码成 `(bytes, bytes)` |

**CSP 的 10 条指令**（L43-L52）：`default-src 'self'` / `script-src 'self' 'unsafe-inline' <CDN>` / `style-src 'self' 'unsafe-inline'` / `img-src 'self' data: blob:` / `frame-src <DRAWIO>` / `connect-src 'self'` / `object-src 'none'` / `base-uri 'none'` / `form-action 'self'` / `frame-ancestors 'self'`

**5 个安全头**（L58-L62）：`x-content-type-options: nosniff`（禁止 MIME 嗅探）/ `x-frame-options: SAMEORIGIN`（**不用 DENY 是留同源嵌套的余地**）/ `referrer-policy` / `permissions-policy`（关掉定位、麦克风、摄像头）/ `content-security-policy`

> **L37 的注释是一条硬约束**：这些外部源**由 `test_ops.py` 反向校验，不许漂** —— 测试会扫描模板与静态资源里出现的所有外部域，逐个断言它们确实在 CSP 白名单里。**以后谁加了新 CDN 忘了改 CSP，测试会红，而不是等上线后页面白屏**（L23-L25）

#### 核心类/函数

**`_header(scope, name) -> str`　L66-L70** —— 从 ASGI `scope` 里取请求头（拿不到就返回空串）。**L13-L14 docstring 说明为什么要这个辅助函数**：纯 ASGI 中间件拿不到 `Request` 对象，只能直接操作 `scope`

**`class SecurityHeadersMiddleware`　L73-L110**
- **`__init__(app, hsts_max_age=0)`　L76-L78**
- **`async __call__(scope, receive, send)`　L80-L110**：
  - **L81-L83 非 http 的 scope 直接放行**（websocket / lifespan 不加安全头）
  - **L88-L99 HSTS 只在真 https 时下发**。**L85-L87 的注释是关键**：反向代理终止 TLS 时应用看到的是 http，要靠 `X-Forwarded-Proto` 判断 —— 但这**只在 `TRUST_PROXY_HEADERS` 打开时可信，否则伪造一个头就能让站点被浏览器锁死一年**。**L93 就是这个条件**
  - **L101-L108 `send_with_headers` 闭包**：只在 `http.response.start` 时改头，**L105/L107 用 `setdefault` 而不是赋值** —— L74 docstring：**已存在的头不覆盖，让端点自己有机会定制**

**`class RequestLoggingMiddleware`　L113-L154**
- **L116-L117 docstring 说明 request id 的意义**：用户报障时让他把响应头里的 `X-Request-ID` 报上来，就能在日志里精确定位那一次请求 —— **没有它，线上排障只能靠时间戳猜**
- **L128 复用调用方给的 id，没有才生成**（`uuid4().hex[:16]`）
- **L132 `status = 500` 预置** —— L132 注释：若应用抛异常没走到 `response.start`，就按 500 记
- **L134-L139 `send_with_id` 闭包**：记录状态码并把 `X-Request-ID` 写回响应
- **L141-L149 是关键 try-except**：**异常也要留痕**（L144 注释：否则 500 在日志里是一片空白），用 `logger.exception` 带 traceback 记完后 **L149 `raise` 原样重抛**（不吞异常）
- **L150-L154 正常路径记一行 info**，含方法、路径、状态码、耗时（ms）、rid

> **模块 docstring L5-L25 回答了两个「为什么」**：
> - **为什么是纯 ASGI 中间件而不是 `BaseHTTPMiddleware`**（L5-L14）：第一版用后者写，功能全对，但把 S5-02 刚优化好的延迟又吃回去了 —— 实测客服接口 p50 从 14 ms 涨到 **21 ms**，「一个大 DDL 拖慢客服」的比值从 1.04 恶化到 **2.14**，两条性能回归测试当场变红。原因是它会为每个请求 spawn 任务并包装请求/响应流，叠两层就是双份开销（TD-166）
> - **为什么 CSP 里保留了 `'unsafe-inline'`**（L16-L21）：四个页面模板**全部**含内联 `<script>`，要上严格 CSP 就得把它们全改成外部文件 + nonce，那是前端重构。**所以现在这版 CSP 的目标是收窄来源而不是消灭内联**：仍然挡住了从任意第三方域加载脚本、`object-src`、`base-uri` 劫持和外部嵌套（TD-163）

---

### 📄 文件名：`wechat_pay.py`（210 行）

- **文件职责**：微信支付 APIv3 —— NATIVE 扫码下单（S3-01-1）+ 支付结果回调（S3-01-3）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `BASE_URL` | L51 | `https://api.mch.weixin.qq.com` | API 域名 |
| `NATIVE_PATH` | L52 | `/v3/pay/transactions/native` | **参与签名的 URL 就是这个路径** |
| `AUTH_TYPE` | L53 | `WECHATPAY2-SHA256-RSA2048` | Authorization 头的类型标识 |
| `TIMEOUT` | L54 | `10.0` | 秒 |

#### 核心类/函数

**`class WeChatPayError(Exception)`　L57-L58** —— 微信支付返回非 2xx，或报文缺少必需字段。

**`@dataclass(frozen=True) class PayConfig`　L62-L81**
- **L63 说明为什么 frozen**：**支付配置不该在请求处理途中被改**
- 7 个字段（`appid` `mchid` `serial_no` `private_key` `api_v3_key` `notify_url` `platform_cert`）
- **`configured`（property，L74-L76）** —— **下单所需六项缺一不可**（不含 `platform_cert`）。L75 docstring：**没配齐就直接拒绝下单，不要发出注定失败的请求**
- **`notify_ready`（property，L79-L81）** —— 回调只需 APIv3 密钥与平台证书两项

**`pay_config() -> PayConfig`　L84-L94** —— **L85 每次调用现取配置**，方便测试改 settings 后立刻生效。

**`new_order_no(now=None) -> str`　L97-L104**
- **L104 `CM` + 14 位时间 + 12 位随机十六进制 = 28 字符**（列上限 32）
- **L100-L101 docstring 说明设计理由**：随机后缀让**同一秒内的并发下单不撞号**，`sys_order.order_no` 的 UNIQUE 再兜一层；带时间前缀便于人工排查

**`canonical_string(method, url_path, timestamp, nonce, body)`　L107-L109**
- **L108 docstring 点出了最容易错的地方**：**第五行之后仍有一个换行符**

**`sign(method, url_path, body, private_key_pem, *, timestamp, nonce)`　L112-L116** —— SHA256withRSA 签名，结果 Base64。

**`auth_header(cfg, method, url_path, body, *, timestamp=None, nonce=None)`　L119-L129**
- **L122 说明为什么 timestamp / nonce 可注入**：便于测试复算签名
- L126-L129 拼出 `WECHATPAY2-SHA256-RSA2048 mchid="...",nonce_str="...",signature="...",timestamp="...",serial_no="..."`

**`async native_prepay(cfg, *, out_trade_no, description, total, transport=None)`　L132-L165**
- **L137 说明 `transport` 参数的存在理由**：只为测试注入 `httpx.MockTransport`，生产留空
- **L139-L151 是关键：先 `json.dumps` 成字符串、拿这串去签名、再把同一串 `encode` 后发出去**。L148-L149 用 `ensure_ascii=False` + `separators=(",", ":")` 固定序列化形式
- L152-L157 四个请求头
- L158-L159 `httpx.AsyncClient` 发 POST
- **L160-L161 非 200 → `WeChatPayError`**（错误信息只带响应体前 200 字符，避免日志爆炸）
- **L162-L164 缺 `code_url` → `WeChatPayError`**

**`_public_key(pem)`　L171-L175**
- **L172 说明为什么两种 PEM 都支持**：**新商户拿到的往往是「微信支付公钥」而不是平台证书**
- **L173-L175 靠 `BEGIN CERTIFICATE` 字样区分**

**`verify_notify_signature(platform_cert_pem, *, timestamp, nonce, body, signature)`　L178-L193**
- **L187 验签串只有三行**（请求签名是五行，别搞混）
- **L184-L185 docstring 强调**：`body` 必须是**原始报文主体**（路由里用 `await request.body()` 取原文），**反序列化后再重新序列化会让验签必然失败**
- **L192-L193 捕获四种异常**（`InvalidSignature` / `InvalidTag` / `ValueError` / `TypeError`）→ 包成 `WeChatPayError`。**`e or '签名值不是合法 Base64'`** 是因为 `InvalidSignature` 的 `str()` 是空串

**`decrypt_resource(api_v3_key, *, ciphertext, nonce, associated_data="")`　L196-L210**
- **L199-L200 docstring 记了一个省事的事实**：微信的 `ciphertext` 是 `Base64(密文 || 16 字节 GCM tag)`，**正好是 `cryptography` 的 AESGCM 期望的格式，不需要自己切 tag**
- **L202-L206 第一个 try-except**：Base64 非法 / 密钥长度不对 / tag 校验失败 → `WeChatPayError`
- **L207-L210 第二个 try-except**：解出来不是合法 JSON → `WeChatPayError`

> **模块 docstring L6-L31 是这个文件最重要的部分**，钉死了三个「错了在本地测不出来、只会在真机上验签失败」的细节（L19-L26）：
> 1. **参与签名的 body 必须与实际发出的字节逐字节相同** —— 中途任何一次重新序列化（键顺序、空格、中文转义）都会让验签失败
> 2. **参与签名的 URL 不含域名**，只有路径与查询串
> 3. **回调验签要用「原始报文主体」**，不能先反序列化再重新序列化
>
> **L28-L31 也诚实划定了验证边界**：沙箱里没有商户号、API 证书与公网回调地址，**真实下单与真实回调在此跑不通**。能真正验证的是签名/验签算法（自签密钥 + 自签证书 + 公钥验签）、AES-GCM 解密（冻结测试向量 + 篡改必须失败）与 HTTP 请求构造（`httpx.MockTransport`）

---

## 3. 执行逻辑流

### 3.1 一次请求穿过本层的顺序

```
应用启动
  │
  ├─ main.py:16-19   logging.basicConfig          ← 只设级别与格式，不动 uvicorn 的 handler
  ├─ main.py:22      enforce_production_settings  ← 不合规直接拒绝启动（startup_checks）
  │
请求进来
  │
  ├─ RequestLoggingMiddleware    L123   取/生成 X-Request-ID、起计时
  ├─ SecurityHeadersMiddleware    L80   包 send，准备安全头 + HSTS
  │    ↑ 洋葱模型：后加的先执行，所以日志包在最外层（main.py:32-33）
  │
  ├─ rate_limit 依赖    ratelimit.py:87   scope+IP 滑动窗口 → 超额 429
  ├─ get_current_user   deps.py:16        Bearer 或 cookie → 验签 → 查库 → 比 pwd
  │
  ├─ 业务逻辑（app/tools/ 或 app/ 根模块）
  │    ├─ 订单迁移      order_state.py:92    _cas 原子更新
  │    ├─ 重 CPU        cpu_pool.py:50       进程池，坏了退化线程池
  │    ├─ 存储          storage.py:98        预签名 URL
  │    └─ 微信支付      wechat_pay.py:132    签名 → 下单
  │
  └─ 响应出去
       ├─ SecurityHeadersMiddleware   setdefault 5 个安全头（+HSTS）
       └─ RequestLoggingMiddleware    写回 X-Request-ID、记一行日志（异常也记）

应用退出
  └─ main.py:27   cpu_pool.shutdown()   回收子进程，否则留孤儿进程
```

### 3.2 三个「同一个套路」在本层的落点

前两层各有并发正确性的实现，**根源都在本层**：

| 场景 | 本层落点 | 上层用法 |
| --- | --- | --- |
| 订单状态迁移 | `order_state._cas` L92-L98 | `shop.pay_notify` / `shop.mock_pay_confirm` |
| 一次性下载 | `order_state.mark_downloaded` L84-L89（返回 bool） | `shop.download_url` L194-L203 **必须看返回值** |
| 流程图乐观锁 | `models.SysDiagram.version` L94-L98 | `diagrams.update_diagram` L136-L146 |
| 授权码消费 | `models.OAuthCode.used` L127 | `oauth.token` L173-L179 |

**四处都是 `UPDATE ... WHERE <期望值>` + 查 `rowcount`。** 本层的注释反复强调同一句话：「先读出来比一比再写」在并发下两边都会读到同一个旧值、都通过检查、都写进去 —— **后写覆盖先写，锁等于没加**。

### 3.3 「配置错误」在本层的四道闸

| 闸 | 位置 | 拦什么 | 后果 |
| --- | --- | --- | --- |
| 启动自检 | `startup_checks.py:19-42` | mock 支付、默认密钥、关限流、不信任代理头 | **拒绝启动** |
| 下单前 | `wechat_pay.py:74-76` `configured` | 微信支付六项没配齐 | 503，不发注定失败的请求 |
| 回调前 | `wechat_pay.py:79-81` `notify_ready` | APIv3 密钥/平台证书没配 | 503 |
| 建存储时 | `storage.py:107-110` | 未知后端 | **直接报错，不静默退回本地** |

**共同点是「宁可起不来，也不要跑着但行为是错的」**（`startup_checks.py:3-4`）。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `4c9868d`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) 每个文件的类/函数与精确行范围（本文所有 Lxx-Lyy 的来源）
python -c "import ast,pathlib;[print(f'{p.name} {n.lineno}-{n.end_lineno} {n.name}') for p in sorted(pathlib.Path('app').glob('*.py')) for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]"

# 2) 配置项数与 .env.example 是否 1:1（本文 1.1 的「38 项」）
python -c "from app.config import Settings; fs=set(Settings.model_fields); env={l.split('=')[0].strip() for l in open('.env.example',encoding='utf-8') if l.strip() and not l.startswith('#') and '=' in l}; print(len(fs), len(env), fs^env)"
# 预期：38 38 set()      ← 对称差为空即 1:1

# 3) 表数、页面清单、状态机边数
python -c "from app import models, site, order_state as s; print(len([c for c in vars(models).values() if hasattr(c,'__tablename__')]), len(site.TOOLS), len(site.PAGES), sum(len(v) for v in s.ALLOWED.values()))"
# 预期：7 3 4 4

# 4) 本层的测试（16 个文件，已逐个确认存在）
python -m pytest tests/test_auth.py tests/test_auth_cookie.py tests/test_token_revocation.py tests/test_order_state.py tests/test_ratelimit.py tests/test_ops.py tests/test_download.py tests/test_wechat_pay.py tests/test_wechat_notify.py tests/test_site.py tests/test_schema_sync.py tests/test_diagrams.py tests/test_diagram_quota.py tests/test_diagram_concurrency.py tests/test_mock_pay.py tests/test_e2e.py -q
# 本机实测：198 passed

# 各模块 → 实际覆盖它的测试文件（实测 grep -rl "app.<模块>" tests/*.py）
#   config       → 15 个测试文件（改配置项影响面最广）
#   security/deps→ test_auth.py test_auth_cookie.py test_token_revocation.py
#   models       → 16 个测试文件；与建表 SQL 的一致性由 test_schema_sync.py 专门钉住
#   order_state  → test_order_state.py test_e2e.py
#   ratelimit    → test_ratelimit.py
#   storage      → test_download.py test_e2e.py
#   middleware / startup_checks / cpu_pool / database → test_ops.py
#   wechat_pay   → test_wechat_pay.py（签名）test_wechat_notify.py（验签+解密）
#   site         → test_site.py
#   timeutil / schemas → **无专属测试文件**，间接覆盖（timeutil 经 order_state/security，
#                        schemas 经各 API 测试的入参校验）
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动 `app/` 根目录任何文件后，本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> 背景（为什么文档里的数字比代码更容易腐烂、以及一次真实的漏改事故）见 `docs/ARCHITECTURE_GUIDE.md` 第 7 课 7.10。
