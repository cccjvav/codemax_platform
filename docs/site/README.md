# 文档站（docs/site/）

把仓库里的**架构导读**与**代码级逐行说明书**做成可读 + 可视化的静态网站。

## 30 秒上手

**Linux / macOS（bash）**

```bash
pip install mistune
python scripts/build_docs_site.py
```

**Windows（cmd.exe）** —— 见下方 [§ Windows 使用指南](#windows-使用指南cmdexe)，
那里有从克隆到打开页面的完整逐步命令。

两条命令的作用：`mistune` 是唯一额外依赖（纯 Python、零传递依赖），
第二条把整站生成出来（约 1 秒）。

然后**直接用浏览器打开 `docs/site/index.html`** —— 不需要起服务器，也不需要联网。

## Windows 使用指南（cmd.exe）

下面是从零到打开页面的完整步骤。**每行一条命令，逐行粘贴回车即可** ——
没有 `&&` 串联、没有 `ls`/`cat`/`grep` 这类 cmd 不认的命令、没有需要 shell 展开的通配符。

### 第 1 步：进到仓库目录

```cmd
cd /d 你的路径\codemax_platform
```

> `cd /d` 的 `/d` 是必需的：不加的话跨盘符（比如从 `C:` 到 `D:`）切不过去。

### 第 2 步：确认 Python 版本

```cmd
python --version
```

> 期望 **Python 3.11.x**。本站点的构建脚本只用到标准库 + `mistune`，
> 3.9 以上都能跑（不像主程序那样卡在 `pgserver` 没有 3.13 发行版的问题上）。

### 第 3 步（可选，推荐）：用虚拟环境

如果你已经为这个项目建过 `.venv`，直接激活它：

```cmd
.venv\Scripts\activate
```

还没有就先建一个：

```cmd
python -m venv .venv
```

```cmd
.venv\Scripts\activate
```

> 激活成功后命令行提示符前面会出现 `(.venv)`。
> **只想生成文档站的话这一步可以完全跳过**，用系统 Python 也行。

### 第 4 步：装 mistune

```cmd
pip install mistune
```

> `mistune` 是**纯 Python、零传递依赖**的一个包，几 MB，装完不会动到别的东西。
> 只有构建文档站需要它，主程序运行时不需要。

### 第 5 步：生成整站

```cmd
python scripts/build_docs_site.py
```

> 注意是**反斜杠** `scripts\build_docs_site.py`；写成正斜杠 `scripts/build_docs_site.py`
> Python 也认，两种都行。
>
> 成功时最后两行是：
>
> ```text
> ✅ 静态站已生成：文档 20 页 + 源码 91 页 + 首页与 3 个可视化页 → docs/site/
>    打开方式：直接在浏览器打开 docs/site/index.html（无需服务器、无需联网）
> ```

### 第 6 步：打开

```cmd
start docs\site\index.html
```

> `start` 会用你的默认浏览器打开它。**不需要起任何服务器** ——
> 整站是预渲染的静态 HTML，直接读本地文件。

### 常见问题

| 现象 | 原因与解法 |
| --- | --- |
| `'python' 不是内部或外部命令` | Python 没进 PATH。改用 `py -3.11 scripts\build_docs_site.py`，或去 python.org 重装时勾上「Add Python to PATH」 |
| `'pip' 不是内部或外部命令` | 同上，改用 `python -m pip install mistune` |
| `No module named mistune` | 第 4 步没跑，或跑在了另一个环境里。先确认提示符前有没有 `(.venv)` |
| 页面打开是空白 | 大概率是浏览器拦了本地 `fetch`。**搜索框**用的是 `fetch('data/search.json')`，在 `file://` 下可能被拦；页面会显示提示，此时用浏览器自带的 `Ctrl+F` 搜索即可。**正文、目录、图表、跳转都不受影响** |
| 想要搜索框也能用 | 起个本地服务：`python -m http.server 8000 --directory docs\site`，然后浏览器开 `http://localhost:8000` |
| 改了文档后页面没变 | 文档站是**预渲染**的，改完 `.md` 要重跑第 5 步 |

### 与主程序的 Windows 指南的关系

`docs/WINDOWS_LOCAL_RUN.md` 讲的是**怎么把服务跑起来**（建库、配 `.env`、起 uvicorn）；
本节讲的是**怎么看文档站**。两者互不依赖 —— **看文档站不需要数据库、不需要 `.env`、
不需要装 `requirements.txt` 里那 25 个依赖**，只要有 Python 和 `mistune`。

## 里面有什么

| 页面 | 内容 |
| --- | --- |
| `index.html` | 首页：全站统计、可视化入口、按分组的文档卡片 |
| `d/<文档>.html` | **21 份文档**逐份渲染，带右侧目录、代码位置可点击 |
| `s/<源码>.html` | **91 个源码文件**带行号展示，顶部列出该文件的全部函数/类，点一下跳到定义行 |
| `graph.html` | **模块依赖图**（SVG）：40 个核心模块 / 85 条依赖，悬停高亮，点节点进文档 |
| `routes.html` | **路由地图**：39 条路由，标注鉴权与限流，可按方法/鉴权/限流/路径过滤 |
| `symbols.html` | **符号索引**：250+ 个函数与类，点「源码」直达定义行、点「文档」跳到说明书 |

**代码 ↔ 文档双向跳转**：文档正文里的 `app/routers/diagrams.py:147` 这类引用被自动
识别并变成链接，点进去就是带行号、且目标行已高亮的源码页；源码页顶部又能跳回对应说明书。

## 为什么是「预渲染 + 零 CDN」

原本打算做客户端渲染（浏览器里用 marked.js 现场渲染 Markdown），实测后**放弃**了，
三条硬事实：

1. **本沙箱 `cdn.jsdelivr.net` 不可达**（实测 5 个库全部 HTTP 000）—— 客户端方案在这里
   根本跑不起来，也就**无从验证**。无法验证的东西不该交付。
2. **mermaid 的 ESM 构建要带 206 个 chunk、共 17 MB** —— 不可能塞进仓库。
3. **highlight.js 的 npm 包里没有现成的浏览器包**（只有 CJS/ESM 源）。

所以改成：

- Markdown 由 **`mistune`（纯 Python、零依赖）服务端渲染**
- 依赖图由 **Python 直接生成 SVG**，不依赖 D3
- 搜索与过滤用 **几十行原生 JS**（`site.js`），不依赖任何库

结果是一个**完全离线、双击即开**的站点。

> 唯一的取舍：`总览.md` §5.1 的 **Mermaid 图在本站里以源码形式展示**（不加载 mermaid.js）。
> 页面上会有提示条；在 GitHub 上打开同名 `.md` 能看到渲染后的图，
> 而且文档里紧接着就有一份**纯文本缩进版**（§5.2），信息不丢。

## 数据来源：全部从代码里真实提取

站点里的图**不是手画的**，由 `scripts/build_docs_site.py` 扫代码生成：

| 数据 | 提取方式 | 实测规模 |
| --- | --- | --- |
| 模块依赖图 | `ast` 解析 `import` / `from ... import` | 78 个模块 / 206 条边 |
| 路由地图 | `ast` 解析 `@router.*` 装饰器、函数签名，**外加 `app/site.py` 的 `Tool(path=...)` 页面清单** | 39 条路由 |
| 符号索引 | `ast` 遍历 `FunctionDef` / `ClassDef` | 250+ 个符号 |
| 文档清单 | 扫仓库 `.md` | 21 份（行数随文档增改而变） |

**两个提取上的坑（已在代码注释里写明）**：

- **相对导入必须解析**。本仓库大量使用 `from ..models import X`、`from .config import Y`。
  只看 `n.module.startswith("app")` 会漏到**只剩 5 条边**，真实是 **206 条**。
- **鉴权有两种写法**：装饰器的 `dependencies=[Depends(require_admin)]`，
  以及函数签名里的 `user: User = Depends(get_current_user)`。**只看装饰器会得出
  「需鉴权 0 条」这种明显错误的结论**，实际是 18 条。

## 生成物不入库

`docs/site/` 下只有 **`style.css`、`site.js`、本 README** 是手写的源文件，照常入库；
其余（`index.html`、`graph.html`、`routes.html`、`symbols.html`、`d/`、`s/`、`data/`）
都是生成物，已在 `.gitignore` 里排除。**改完文档或代码后重跑一次构建脚本即可。**

## 复核

先看提取结果（只生成 `data/*.json`，不渲染 HTML）：

```text
python scripts/build_docs_site.py --data-only
```

> **预期输出**：`模块 78 个 · 依赖边 206 条 · 路由 39 条 · 符号 268 个 · 文档 21 份`
>
> （路由数只随**路由**变，可以当断言用。**模块数与依赖边把 `tests/` 也算进去** ——
> 所以新增一个测试文件就会 +1 模块、+若干依赖边，这不是 bug。
> 文档行数会随文档增改而变，不必对齐。）

再核对路由的鉴权与限流计数（**单行命令，bash 与 cmd 都能直接粘贴**）：

```text
python -c "import json;r=json.load(open('docs/site/data/routes.json',encoding='utf-8'));print('路由',len(r),'需鉴权',sum(1 for x in r if x['auth']),'限流',sum(1 for x in r if x['rate_limit']))"
```

> **预期输出**：`路由 39 需鉴权 18 限流 9`
>
> ⚠️ 这里刻意写成**单行**：多行的 `python -c "…"` 在 Windows `cmd.exe` 里会被换行截断。
