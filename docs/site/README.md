# 文档站（docs/site/）

把仓库里的**架构导读**与**代码级逐行说明书**做成可读 + 可视化的静态网站。

## 30 秒上手

```bash
pip install mistune                     # 只这一步是额外依赖，纯 Python、零传递依赖
python scripts/build_docs_site.py       # 生成整站（约 1 秒）
```

然后**直接用浏览器打开 `docs/site/index.html`** —— 不需要起服务器，也不需要联网。

## 里面有什么

| 页面 | 内容 |
| --- | --- |
| `index.html` | 首页：全站统计、可视化入口、按分组的文档卡片 |
| `d/<文档>.html` | **20 份文档**逐份渲染，带右侧目录、代码位置可点击 |
| `s/<源码>.html` | **91 个源码文件**带行号展示，顶部列出该文件的全部函数/类，点一下跳到定义行 |
| `graph.html` | **模块依赖图**（SVG）：40 个核心模块 / 85 条依赖，悬停高亮，点节点进文档 |
| `routes.html` | **路由地图**：32 条路由，标注鉴权与限流，可按方法/鉴权/限流/路径过滤 |
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
| 模块依赖图 | `ast` 解析 `import` / `from ... import` | 72 个模块 / 179 条边 |
| 路由地图 | `ast` 解析 `@router.*` 装饰器**与函数签名** | 32 条路由 |
| 符号索引 | `ast` 遍历 `FunctionDef` / `ClassDef` | 250+ 个符号 |
| 文档清单 | 扫仓库 `.md` | 20 份 / 8 992 行 |

**两个提取上的坑（已在代码注释里写明）**：

- **相对导入必须解析**。本仓库大量使用 `from ..models import X`、`from .config import Y`。
  只看 `n.module.startswith("app")` 会漏到**只剩 5 条边**，真实是 **179 条**。
- **鉴权有两种写法**：装饰器的 `dependencies=[Depends(require_admin)]`，
  以及函数签名里的 `user: User = Depends(get_current_user)`。**只看装饰器会得出
  「需鉴权 0 条」这种明显错误的结论**，实际是 16 条。

## 生成物不入库

`docs/site/` 下只有 **`style.css`、`site.js`、本 README** 是手写的源文件，照常入库；
其余（`index.html`、`graph.html`、`routes.html`、`symbols.html`、`d/`、`s/`、`data/`）
都是生成物，已在 `.gitignore` 里排除。**改完文档或代码后重跑一次构建脚本即可。**

## 复核

```bash
python scripts/build_docs_site.py --data-only      # 只生成 data/*.json，看提取结果
python -c "
import json
g = json.load(open('docs/site/data/graph.json', encoding='utf-8'))
r = json.load(open('docs/site/data/routes.json', encoding='utf-8'))
print('模块', len(g['nodes']), '依赖边', len(g['edges']))
print('路由', len(r), '需鉴权', sum(1 for x in r if x['auth']), '限流', sum(1 for x in r if x['rate_limit']))
"
# 预期：模块 72 依赖边 179 / 路由 32 需鉴权 16 限流 8
```
