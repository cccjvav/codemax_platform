---
name: new-tool-page
description: >
  新增一个工具页（如「JSON 格式化」）的完整清单。清单即真相：改 TOOLS 一处，
  路由、首页导航、sitemap 自动跟上，模板和测试要手工补。
  Trigger: 新增工具页、新增前端页面、往导航里加入口。
---

# new-tool-page

> **设计要点**：`app/site.py` 的 `TOOLS` 常量是唯一真相来源。
> `app/routers/site.py` 在导入时用 `for _tool in PAGES: router.add_api_route(...)`
> 生成路由，`/sitemap.xml` 也读同一份 `PAGES`，`base.html` 的导航用 `{% for t in tools %}`
> 渲染。**所以不要手工注册路由、不要手工往 sitemap 里加 URL。**

## 检查单（按顺序）

### 1. 清单 — `app/site.py`
往 `TOOLS` 元组里加一条 `Tool(...)`，六个字段一个都不能少：

| 字段 | 要求 |
| --- | --- |
| `key` | 短标识，会成为 pytest 用例 id（`ids=[p.key for p in PAGES]`），用 ascii |
| `title` | 导航文案 + `<title>` 主体；页面标题会自动拼成 `title - SITE_NAME` |
| `path` | 以 `/tools/` 开头，短横线小写 |
| `description` | 写进 `<meta name="description">`，**这是 SEO 正文**，写人能搜的句子 |
| `keywords` | 逗号分隔，别堆砌 |
| `template` | `app/templates/` 下的文件名 |

### 2. 模板 — `app/templates/<template>`
- [ ] 第一行 `{% extends "base.html" %}`，内容写在 `{% block content %}` 里
- [ ] **不要**自己写 `<html>` / `<title>` / TDK / 导航 —— `base.html` 已经出了，
      重写会导致 `test_page_tdk_and_nav` 断言的字符串出现两次或对不上
- [ ] 需要新样式就往 `base.html` 的 `<style>` 里加（目前没有 per-page CSS 机制）
- [ ] 需要 JS 就放 `app/static/<name>.js`，模板里 `<script src="/static/<name>.js">`
      （`/static` 由 `main.py` 挂载到 `app/static/`）

### 3. 后端接口（如果需要）
- [ ] 加到 `app/routers/tools.py`，路由层只做参数校验与调用，逻辑放 `app/tools/`
- [ ] **必须挂限流**：`dependencies=[Depends(rate_limit("<scope>", "RATE_LIMIT_TOOLS"))]`；
      调 LLM 的用 `"RATE_LIMIT_LLM"`（更严，因为花钱）。漏了就是 TD-15 回归
- [ ] 前端 fetch 用**相对路径**（`/tools/xxx`），不要写死域名

### 4. 测试
`tests/test_site.py` 会**自动**覆盖新页面，不用手写：
- `test_every_page_route_registered` — 路由是否生成
- `test_home_lists_every_tool` — 首页有没有入口
- `test_page_tdk_and_nav` — 按 `PAGES` 参数化，**加一条 Tool 就多一个用例**，
  校验 `<title>` / description / keywords / canonical / 导航
- `test_sitemap_matches_page_list_exactly` — sitemap 与清单严格一致

需要**手工补**的：
- [ ] 该工具自身逻辑的用例（解析、导出等）放 `tests/test_<name>.py`
- [ ] 涉及前端 JS 的，沿用 `tests/test_er_page.py` 的做法（node 执行真实 JS，
      不要退化成静态字符串检查）

### 5. 文档
- [ ] `README.md` 的页面表加一行
- [ ] `ROADMAP.md` 勾掉对应子项
- [ ] 有取舍就记 TD-xx（例如「CDN 引入 D3 而非本地打包」）

## 验证

```bash
.venv/bin/python -m pytest tests/test_site.py -q   # 新页面应自动多出一个用例
.venv/bin/python -m pytest -q                      # 全量：208 passed, 1 skipped
```

起服务人工看一眼（页面是不是真能渲染，测试只看字符串）：

```bash
.venv/bin/python -m uvicorn main:app --reload
# 浏览器打开 http://127.0.0.1:8000/tools/<新页面>
```

## 常见漏项

| 症状 | 原因 |
| --- | --- |
| `test_sitemap_matches_page_list_exactly` 红 | 手工改了 sitemap 而不是改 `TOOLS` |
| `test_page_tdk_and_nav` 红但页面看着正常 | 模板里自己又写了一遍 `<title>` 或 meta |
| 页面 200 但导航少一项 | 模板没 `{% extends "base.html" %}` |
| 接口能刷爆 | 忘了挂 `rate_limit` 依赖 |
