# `app/frontend/` 模块说明书

> 这里是**前端 JS 的源码目录**。浏览器不直接读这里的文件 —— 它们经 Vite 编译后
> 落到 `app/static/js/`，模板引用的是产物。
>
> 姊妹篇：`app/static/README.md`（产物与加载方式）、`app/templates/README.md`（谁引用）、
> `tests/README.md`（怎么测）。取舍见 `TECH_DECISIONS.md` 的 **TD-221 / TD-222 / TD-223**。

---

## 1. 为什么会有这个目录

原来的状态：JS 散在两处 —— `app/static/` 下的原始文件，以及 8 个模板里合计 **438 行**内联脚本（非空行口径；8 个模板中 5 个有）。
内联脚本**没法 lint、没法 import、没法单测**，攒到几百行就成了负担。

TD-221 定了方案：**引入 Vite 作为纯编译期工具链**，后端仍然是 Jinja2 SSR，不做全站 SPA。
于是源码集中到本目录，产物入库，服务器**不需要装 Node**（`node_modules` 实测 57 MB，
只在开发与 CI 时需要）。

## 2. 实测清单

| 源码 | 作用 | 产物 | 被谁加载 |
|---|---|---|---|
| `auth.js` | 全站登录态模块，IIFE 挂 `window.CodeMaxAuth` | `js/auth.js` 2.32 kB | `base.html`（经典脚本） |
| `er-layout.js` | ER 图**纯布局**算法，不 import d3、不碰 DOM | 并入 `js/er-page.js` | `tests/test_er_page.py` 用 node 直接 import |
| `er-page.js` | ER 图渲染（d3 已打包进来）+ 页面交互 | `js/er-page.js` 50.32 kB | `er.html`（经典脚本） |
| `drawio-page.js` | drawio 页交互（与 `/diagrams` 对接） | `js/drawio-page.js` 3.12 kB | `drawio.html`（经典脚本） |
| `mermaid-page.js` | mermaid 页交互 | `js/mermaid-page.js` 1.08 kB | `mermaid.html`（**`type="module"`**） |
| `mock-pay-page.js` | mock 支付页交互 | `js/mock-pay-page.js` 0.57 kB | `mock_pay.html`（经典脚本） |
| `shop-page.js` | 下单页状态机（下单 / 3 秒轮询 / 下载） | `js/shop-page.js` 2.90 kB | `shop.html`（经典脚本） |
| `package.json` | **只有 `"type": "module"`**，用于把 ESM 范围限定在本目录 | — | — |

> **8 个模板的内联 JS 现已全部归零**（438 → 168 → 0，非空行口径）。
> `shop-page.js` 是最后一个搬出来的，按原样搬迁、不引入框架。
> 框架方案已于 2026-09-08 **放弃**（**TD-226**；当时的分析见 TD-224）。

## 3. 三个必须知道的坑

### 3.1 根 `package.json` 刻意不写 `"type": "module"`

那会让 Node 把**全仓每个 `.js`** 都当 ES 模块，`module.exports` 全部失效。
TD-221 实测踩过：`tests/test_er_page.py` 报 `layoutEr is not a function`。
所以 ESM 范围靠本目录那个局部 `package.json` 声明；Vite 配置文件用 **`.mjs`** 后缀。

### 3.2 `er-layout.js` 不许 import d3

`tests/test_er_page.py` 用 node **直接** import 它来做前后端字段契约测试，
而 **CI 的测试 job 不装 `node_modules`**。一旦这半边引入 d3，契约测试就会在 CI 上炸
（本地有 `node_modules` 所以看不出来）。这也是 d3 只在 `er-page.js` 里 import 的原因。

### 3.3 产物的加载方式不能一律加 `type="module"`

实测各产物里 `import` / `export` 的出现次数：**只有 `mermaid-page.js` 是 1**
（mermaid 仍是 CDN 上的 ESM 包，产物保留了那条裸 import），其余都是 0。
用经典 `<script src>` 加载带 ESM 语法的文件会直接语法错 ⇒ 只有 `mermaid.html` 加了 module。

反过来 `auth.js` **刻意不加** module：`shop.html` 的内联脚本依赖
`window.CodeMaxAuth` **在 HTML 解析到那一行时就已就绪**，而 module 默认 defer。

## 4. 改完必须做的两件事

```cmd
npm run build
.venv\python -m pytest -q
```

`npm run build` 从**仓库根**跑（`vite.config.mjs` 与带 scripts 的 `package.json` 都在根上，
本目录那个 `package.json` 没有 scripts）。产物**必须一起提交** ——
CI 的「前端产物漂移检查」job 会重跑构建再 `git diff --exit-code -- app/static/js`，
漏提交就红（TD-221）。

## 5. 数字怎么复核

```cmd
.venv\python -c "import pathlib;[print(p.name,len(p.read_text(encoding='utf-8').split(chr(10)))) for p in sorted(pathlib.Path('app/frontend').glob('*.js'))]"
npm run build
```
