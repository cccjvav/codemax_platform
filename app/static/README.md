# `app/static/` 模块说明书

> **行号基准 commit：`1d5499c`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/templates/README.md`（谁引用这里的脚本）、`app/README.md`、`app/routers/README.md`、
> `app/tools/README.md`、`database init/README.md`、`tests/README.md`、`scripts/README.md`。

---

## 1. 模块概述

### 1.1 定位

**只有一个文件**：`er.js`，149 行。它是 ER 图的**客户端渲染代码**。

这个目录之所以存在，是因为技术选型定了 **Jinja2 SSR、不引入 Node 打包工具**（`AGENTS.md`）—— 没有构建步骤，所以 JS 必须是**浏览器能直接执行的原始文件**，由 FastAPI 的 `StaticFiles` 原样吐出去：

```python
# main.py:48-52
app.mount("/static", StaticFiles(directory=.../ "app" / "static", html=True), name="static")
```

引用它的只有一处：**`app/templates/er.html:19`** 的 `<script src="/static/er.js"></script>`。

### 1.2 一个关键设计：纯函数与 DOM 分离

文件头 L1-L3 的注释就写明了：

> `layoutEr` 是**纯函数**，不碰 DOM / d3，**可被 node 直接 require** —— 后端契约测试
> （`tests/test_er_page.py`）就是拿接口真实返回喂给它，保证字段两端对得上。

配合 L149 的 `if (typeof module !== "undefined") module.exports = { layoutEr, nodeHeight };` —— **同一份代码既在浏览器里跑，也能被测试直接 require**。

这么做的收益：**布局逻辑（算坐标）与绘制逻辑（画 SVG）分开**，前者可以脱离浏览器测试。后端返回的字段名一旦改了（比如 `from_table` 改成 `source`），测试会当场红，而不是等页面上画不出连线才发现。

### 1.3 实测结构

```text
app/static/
├── er.js        149 行   7 个常量 + 3 个函数
├── auth.js      120 行   全站共享登录态模块（S2-02-2）
└── pay_qr.svg    17 行   manual 支付模式的占位收款码（S5-04）
```

---

## 2. 文件级详细说明书

### 📄 文件名：`auth.js`（120 行）

- **文件职责**：全站共享的登录态模块，挂在 `window.CodeMaxAuth` 上。
  顶栏的「登录 / 注册 / 退出」与登录浮层都由它驱动，`drawio.html` 与 `shop.html` 直接调用。

#### 为什么是外部文件，而不是写在 `base.html` 里

`base.html` 是**所有**页面的父模板，包括 OAuth 同意页 —— 那是发放授权码的安全关键页，
`tests/test_oauth_consent.py` 明确断言它渲染出来**一个 `<script>` 都没有**。
做成外部文件后由 CSP 的 `script-src 'self'` 覆盖，连 `'unsafe-inline'` 都不需要（TD-163 的方向）。

配合 `base.html` 里的 `{% if auth_ui %}` 开关：同意页传 `auth_ui=False`，
浮层、顶栏登录控件与这个脚本**全都不渲染**。

#### 暴露的接口

| 方法 | 作用 |
| --- | --- |
| `open(mode)` | 打开浮层，`mode` 为 `"login"` 或 `"register"` |
| `close()` | 关闭浮层 |
| `refresh()` | 重新问后端 `GET /auth/me` 拿登录态 |
| `onChange(fn)` | 订阅登录态变化（drawio 用它刷新流程图列表） |
| `user` | 当前用户对象（getter，未登录为 `null`） |

#### 两个不能改的地方

1. **「是否已登录」必须问后端。** 登录态在 HttpOnly cookie 里，脚本读不到（TD-44），
   所以唯一可靠的判断是 `GET /auth/me`。用 localStorage 自己记正是 TD-44 要消灭的做法。
2. **注册完必须再登录一次。** `POST /auth/register` 只返回 `UserOut`，**不写 cookie**；
   写 cookie 的只有 `POST /auth/login`。所以浮层里注册成功后会紧接着自动登录。


### 📄 文件名：`pay_qr.svg`（17 行）

**它是什么**：`SHOP_PAY_MODE=manual` 时下单页展示的那张收款码图片。
默认这张是**占位图**（灰底虚线框 + 「此处应放真实收款码」），上线前必须换成自己的：

| 换成什么 | 怎么拿 |
| --- | --- |
| 微信个人收款码 | 微信「我」→「服务」→「收付款」→「二维码收款」→ 保存图片 |
| MobilePay 收款码 | App 里生成的收款二维码截图 |

**直接覆盖本文件即可**，代码与配置都不用动（路径见 `SHOP_MANUAL_QR`，默认 `/static/pay_qr.svg`）。

**为什么是 SVG 而不是 PNG**：纯文本、可代码评审、1.4 KB，而且不含任何脚本，
不受 CSP `img-src` / `script-src` 约束。

**为什么它只是个静态文件、不是接口**：`manual` 模式没有商户号，拿不到支付回调（TD-205）。
所以这张图和具体订单无关 —— 全站共用一张，用户扫码付钱后由**管理员**在后台确认到账
（`POST /shop/orders/{order_no}/confirm`），下单页才会转成「已支付」。
这也是为什么 `shop.html` 里要写「付款后由客服核对到账并确认」：
用户必须知道为什么不会自动到账，否则会以为系统坏了。

---

### 📄 文件名：`er.js`（149 行）

- **文件职责**：把 `POST /tools/er-diagram` 返回的 `{tables, edges}` 画成 ER 图。

#### 关键常量（L5-L11，全部大写）

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `NODE_W` | L5 | `230` | 表节点宽度（px） |
| `HEAD_H` | L6 | `30` | 表头高度 |
| `ROW_H` | L7 | `24` | 每个字段一行的高度 |
| `PAD_BOTTOM` | L8 | `8` | 底部留白 |
| `COLS_PER_ROW` | L9 | `3` | **每行放 3 张表**（网格布局的列数） |
| `GAP_X` | L10 | `90` | 横向间距 |
| `GAP_Y` | L11 | `70` | 纵向间距 |

#### 核心函数

**`nodeHeight(table)`　L13-L15**
- **L14 `HEAD_H + 字段数 × ROW_H + PAD_BOTTOM`**
- **`(table.columns || [])`** —— 字段缺失时按 0 算，不抛错

**`layoutEr(graph)`　L18-L73**（**纯函数，本文件的核心**）
- 输入 `{tables, edges}`，输出 `{nodes, links, width, height, headH, rowH}`
- **L19-L24 分行** —— `Math.floor(i / COLS_PER_ROW)` 把表按 3 个一行分组
- **L26-L45 排布节点**：
  - **L30 `rowH = Math.max(...rowTables.map(nodeHeight))`** —— **行高取该行最高的表**（L17 注释），否则矮表会和下一行重叠
  - **L36 `x = c * (NODE_W + GAP_X)`** —— 列位置
  - **L37 `y`** —— 整行共用一个 y
  - **L42 `byName.set(t.name, node)`** —— 建名字到节点的映射，后面连线要用
  - **L44 `y += rowH + GAP_Y`** —— 下一行的 y
- **L47-L62 生成连线**：
  - **L49-L50 按表名找两端节点**
  - **L51 是关键容错** —— **`if (!s || !t) return null;`**，注释写明：**悬空外键（目标表不在 DDL 内）：跳过，不让整图崩掉**
  - **L55 `label`** —— `from_column → to_column`
  - **L56-L59 端点坐标** —— 从**源表右边缘中点**连到**目标表左边缘中点**
  - **L62 `.filter(Boolean)`** —— 把上一步产生的 `null` 滤掉
- **L64-L72 返回**：
  - **L64 `widest`** —— 最宽一行有几张表
  - **L68 `width`** —— **L68 行内注释**：**空输入不能算出负宽度**（所以有 `widest ? ... : 0`）
  - **L69 `height = Math.max(y - GAP_Y, 0)`** —— 减掉最后多加的一个间距，同样兜住 0
  - **L70-L71 把 `headH` / `rowH` 一起返回** —— 绘制阶段要用，避免两处硬编码

**`renderEr(selector, graph)`　L75-L147**（**依赖全局 `d3`**）
- **L76 先调 `layoutEr`** —— 所有坐标都来自它，本函数只负责画
- **L77-L80 准备 SVG**：
  - **L79 `viewBox` 用 `Math.max(L.width, 320)` / `Math.max(L.height, 200)`** —— 兜住最小可视尺寸
  - **L80 `.html("")`** —— **清空上一次的结果**（否则重复渲染会叠加）
- **L81 `root = svg.append("g")`** —— 所有元素挂在这个 `g` 上，**缩放时整体变换**
- **L82 缩放** —— `d3.zoom().scaleExtent([0.2, 3])`，变换写进 `root` 的 `transform`
- **L84-L94 画连线** —— **L91-L94 用三次贝塞尔曲线**：`M x1,y1 C mx,y1 mx,y2 x2,y2`（`mx` 是两端中点），所以线是平滑的 S 形而不是直线
- **L96-L106 画连线标签** —— 文字放在两端中点上方 4px，`text-anchor: middle` 居中
- **L108-L113 每张表一个 `<g>`** —— `transform: translate(x, y)`
- **L114-L120 表体矩形** —— 白底蓝边，圆角 6
- **L121-L126 表头矩形** —— 蓝底，高度 `L.headH`
- **L127-L134 表名文字** —— **L134 `n.comment ? \`${n.name}（${n.comment}）\` : n.name`**，有中文注释就带上
- **L135-L144 字段列表**：
  - **L142 `y = headH + (i + 0.75) * rowH`** —— 0.75 是为了让文字在行内**视觉居中**（基线偏移）
  - **L143 主键用橙色 `#b45309`，其余用 `#334155`**
  - **L144 `PK ` 前缀 + `字段名: 类型`**
- **L146 `return L`** —— **把布局结果返回给调用方**（测试就是靠这个断言坐标）

**L149 条件导出**
```js
if (typeof module !== "undefined") module.exports = { layoutEr, nodeHeight };
```
- **`typeof module !== "undefined"` 是关键** —— 浏览器里没有 `module`，这行不会执行；node 里才有
- **只导出 `layoutEr` 与 `nodeHeight`，不导出 `renderEr`** —— 后者依赖 `d3` 与 DOM，node 里跑不了

---

## 3. 执行逻辑流

### 3.1 浏览器里的完整链路

```text
用户在 er.html 粘 DDL → 点「生成 ER 图」
  │
  ├─ er.html:79   POST /tools/er-diagram  {ddl}
  │                 ↓
  │               app/routers/tools.py:23-36
  │                 → run_in_threadpool(parse_ddl, ddl)     ← app/tools/sql_ddl.py
  │                 → 返回 {tables, edges}
  │
  ├─ er.html:82   renderEr("#er-canvas", data)              ← 本文件 L75
  │                 ├─ layoutEr(graph)      L18   纯函数，算出所有坐标
  │                 │    ├─ 分行（每行 3 张表）        L19-L24
  │                 │    ├─ 排布节点 + 建 byName 映射   L26-L45
  │                 │    └─ 生成连线（悬空外键跳过）    L47-L62
  │                 └─ 用 d3 画：连线 → 标签 → 表体 → 表头 → 表名 → 字段
  │
  └─ 用户可缩放（0.2 ~ 3 倍）                                L82
```

### 3.2 后端契约是怎么被钉住的

`tests/test_er_page.py` 的做法（`AGENTS.md` 明确要求「测试要真的执行前端代码，不许退化成静态检查」）：

```text
1. 起 TestClient，用**项目自己的 database init/full_init.sql** 当输入
   （tests/test_er_page.py:18 的 FULL_INIT_SQL）
2. 真调 POST /tools/er-diagram，拿到接口真实返回
3. 把返回的 JSON 写进临时文件
4. 用 subprocess 真跑 node：require('app/static/er.js') → layoutEr(真实返回)
5. 断言 nodes / links 的数量与坐标
```

**这条链的价值**：它同时验了**后端解析对不对**和**前端字段名对不对**。任何一端改了字段名（`from_table` / `to_table` / `from_column` / `to_column` / `primary_key` / `comment`），测试当场红。

> **`ruff.toml:51` 为 `tests/test_er_page.py` 开了 `ASYNC221` 白名单** ——
> 因为它用阻塞式的 `subprocess.run` 真跑 node，这是**有意的**（注释 L47-L50 写明）。

### 3.3 为什么 `layoutEr` 里的三处容错都不能省

| 位置 | 容错 | 省掉的后果 |
| --- | --- | --- |
| L14 / L20 / L35 / L47 | `|| []` | 后端少返回一个字段就 `TypeError`，整页白屏 |
| **L51** | **悬空外键返回 `null`** | **DDL 里引用了没定义的表 → 整图崩掉**（这是最常见的情况：用户只贴了部分建表语句） |
| L68-L69 | `widest ? ... : 0` / `Math.max(..., 0)` | 空输入算出**负宽度**，`viewBox` 非法，SVG 不渲染 |

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `1d5499c`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) 常量与函数清单（本文 1.3 与 2 的依据）
python -c "
import re, pathlib
js = pathlib.Path('app/static/er.js').read_text(encoding='utf-8')
print('常量:', re.findall(r'^const ([A-Z_]+) =', js, flags=re.M))
print('函数:', re.findall(r'^function (\w+)', js, flags=re.M))
print('导出:', re.findall(r'module.exports = \{([^}]*)\}', js))
"
# 预期：常量 7 个（NODE_W HEAD_H ROW_H PAD_BOTTOM COLS_PER_ROW GAP_X GAP_Y）
#       函数 3 个（nodeHeight layoutEr renderEr）
#       导出 layoutEr, nodeHeight（**不含 renderEr**）

# 2) 谁引用了 er.js（本文 1.1 说的「只有一处」）
grep -rn --exclude-dir=.venv --exclude-dir=.git --include="*.html" --include="*.py" "er\.js" app/ tests/
# 实测 6 处命中，但**真正在浏览器里加载它的只有 1 处**：
#   app/templates/er.html:19   <script src="/static/er.js">   ← 唯一的真实加载点
# 其余 5 处都在 tests/test_er_page.py 里（L4/L17/L20/L33/L53），是测试引用与注释。

# 3) 真跑一遍前端代码（本文 3.2 的那条链，需要 node）
python -m pytest tests/test_er_page.py -q
# 本机实测：5 passed（用 node 真实执行 layoutEr）
# 本机实测：见下

# 4) 确认 CSP 允许这个脚本来源（本文件由 'self' 提供，不涉及外部域）
python -c "from app.middleware import CONTENT_SECURITY_POLICY as c; print([d for d in c.split('; ') if 'script' in d])"
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动 `er.js` 后本文对应的行号与
> 计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **改字段名要三处同步**：`app/tools/sql_ddl.py`（产出）、本文件（消费）、
> `tests/test_er_page.py`（契约）。
