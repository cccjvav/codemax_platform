# AGENTS.md

codemax_platform — FastAPI + SQLAlchemy 2.0(async) + PostgreSQL 的毕设服务平台。

## 硬性工作流（用户要求）

1. **代码尽量简洁** —— 不写多余代码、不提前抽象、不加用不到的配置。
2. **写完必须测试** —— 每次代码改动后运行 `.venv/bin/python -m pytest -q`。
3. **不过则迭代** —— 测试失败必须修复后重跑，直到全绿；禁止跳过/注释/降标。

## 技术选型（已定，不要重新论证）

> 具体到实现层面的取舍（109 条，带编号 TD-xx、代价与"何时回头改"）全部集中在
> **`TECH_DECISIONS.md`**。做新功能时若产生新取舍，去那里追加一行，别只写在 docstring 里。

1. **原路线图里的 Java 库一律换成 Python 对应物**（本项目是 Python，不许为了对齐文档措辞引入 Java/Node 运行时 —— 违反上面第 1 条铁律）：
   - 导出 Word → `python-docx`（不是 Apache POI）
   - 爬虫 → `httpx` + `BeautifulSoup4`（不是 HttpClient + Jsoup）
   - 动态页面抓取 → 阶段四再定 `Selenium` 还是 `Playwright`
2. **前端用 Jinja2 SSR**，不引入 Node / Nuxt / Next。D3.js、Mermaid、Drawio 本来就是客户端渲染，SSR 只负责 HTML 外壳与 TDK。
   - 工具清单集中成一个 `TOOLS` 常量，同时驱动路由、sitemap 与导航（S2-02-1）
   - S2-02-1 只做 `sitemap.xml` + `robots.txt` + 工具页 TDK；S2-02-2 转化路径本轮跳过

## 项目速览

- 数据库初始化：`cd "database init" && python db_init.py`（幂等；测试账号 admin/123456）
- 配置：`.env`（模板 `.env.example`），`.env` 不提交
- Skills：`.claude/skills/`（fastapi-python、python-testing、codemax-workflow）
- 详细路线图：`ROADMAP.md`
- 实现取舍清单（含上线阻塞项）：`TECH_DECISIONS.md`
