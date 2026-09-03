# AGENTS.md

codemax_platform — FastAPI + SQLAlchemy 2.0(async) + PostgreSQL 的毕设服务平台。

> 本文件是**仓库级硬约定**，写给编码 agent 看，不是给人的项目介绍（那是 `README.md`）。
> 原则：能被工具确定性执行的规则不写在这里，这里只放**需要判断**的部分。
> 确定性闸门有两个：**ruff**（规则集见 `ruff.toml`）与 **pytest + CI**。

## 命令（精确调用，别自己造）

| 动作 | 命令 |
| --- | --- |
| 装依赖 | `.venv/bin/python -m pip install -r requirements.txt` |
| 建库建表（幂等） | `cd "database init" && python db_init.py && cd ..` |
| 跑测试（SQLite，日常） | `.venv/bin/python -m pytest -q` |
| 跑测试（真 PostgreSQL） | `TEST_DATABASE_URL="postgresql+asyncpg://postgres@/codemax_test?host=/tmp/pgdata" .venv/bin/python -m pytest -q` |
| 静态检查 | `.venv/bin/ruff check .` |
| 静态检查（自动修） | `.venv/bin/ruff check . --fix` |
| 跑单个用例 | `.venv/bin/python -m pytest tests/test_x.py::test_y -q` |
| 起服务 | `.venv/bin/python -m uvicorn main:app --reload` |

以上是沙箱（Linux）路径。用户本机是 **Windows + cmd.exe**，对应写法见 `README.md`
的「在 Windows（cmd.exe）上跑起来」——给他命令时必须用 cmd 语法。

## NEVER（硬边界，违反等于返工）

- **不得合并 PR**，除非用户在本轮明确说要合并。合并后沙箱内的后续改动无法同步，等于白做。
- **不得为了让测试通过而降标**：禁止 `skip` / 注释掉用例 / 放宽断言 / 删测试。
- **不得引入 Java 或 Node 运行时**。node 只允许作为测试期工具去执行前端代码。
- **不得提交 `.env`**。新增配置项必须同时写进 `.env.example`。
- **不得把没验证过的结论写成事实**。跑不了的要明确标注「未核实」及原因。

## ASK（先问用户，别自己拍板）

- 新增第三方依赖之前。
- 改数据库表结构之前（`app/models.py` 与 `database init/full_init.sql` 必须一起改）。
- 改 `.github/workflows/**` 之前说一句：本会话已有 Workflows 写权限、可以直接 push，
  但 CI 是**全局**闸门，改坏会让之后每一次 push 都红。改完必须 `gh run watch` 看到绿。
  另注：`concurrency` 配了 `cancel-in-progress: true`，连续推送会把上一次还在跑的
  运行标成 `cancelled`（不是失败），统计成功率时别把它算成红。

## ALWAYS

- 每次代码改动后跑 pytest；红着就不算完成。
- 每次 `git commit` 之后**立刻** `git push`，并用 `git ls-remote origin refs/heads/<branch>`
  确认远端 tip（`git log origin/<branch>` 在本沙箱会报 ambiguous argument，不可用）。
- 一个 ROADMAP 子项 = 一个提交。提交信息用 `git commit -F <file>`，不要用带引号的 `-m`。
- 产生新的实现取舍 → 去 `TECH_DECISIONS.md` 追加一行带编号的 **TD-xx**（含放弃了什么、
  代价、何时回头改），**不要只写在 docstring 里**；同时同步 AGENTS.md / HANDOVER.md 的计数。
- 修 bug 时补一个能复现该 bug 的回归测试。

## 「做完」的定义（六条全中才算完成）

1. `.venv/bin/ruff check .` → **All checks passed!**
2. `.venv/bin/python -m pytest -q` → **353 passed, 1 skipped**
   （跳过的那条是真并发测试，SQLite 的 StaticPool 复现不了竞态，见 TD-85）。
3. 真 PostgreSQL 上 → **354 passed**（起库配方见 `HANDOVER.md` §9）。
4. 已提交并推送，`git ls-remote` 能看到新 tip。
5. 关键逻辑改动做过**变异测试**：把实现改坏 → 确认对应用例变红 → 改回来。
   抓不到的变异要如实记为「等价变异，不可捕获」，不得当成已覆盖。
6. 新取舍已进 `TECH_DECISIONS.md`，相关文档的计数已同步。

## 技术选型（已定，不要重新论证）

> 具体到实现层面的取舍（111 条，带编号 TD-xx、代价与"何时回头改"）全部集中在
> **`TECH_DECISIONS.md`**。做新功能时若产生新取舍，去那里追加一行，别只写在 docstring 里。

1. **原路线图里的 Java 库一律换成 Python 对应物**（本项目是 Python，不许为了对齐文档措辞引入 Java/Node 运行时 —— 违反上面第 1 条铁律）：
   - 导出 Word → `python-docx`（不是 Apache POI）
   - 爬虫 → `httpx` + `BeautifulSoup4`（不是 HttpClient + Jsoup）
   - 动态页面抓取 → 阶段四再定 `Selenium` 还是 `Playwright`
2. **前端用 Jinja2 SSR**，不引入 Node / Nuxt / Next。D3.js、Mermaid、Drawio 本来就是客户端渲染，SSR 只负责 HTML 外壳与 TDK。
   - 工具清单集中成一个 `TOOLS` 常量，同时驱动路由、sitemap 与导航（S2-02-1）
   - S2-02-1 只做 `sitemap.xml` + `robots.txt` + 工具页 TDK；S2-02-2 转化路径本轮跳过

## 目录约定

- `app/routers/` 只做 HTTP 层；业务逻辑放 `app/tools/`、`app/storage.py`、
  `app/wechat_pay.py`、`app/order_state.py`。
- **新增一个工具页**：往 `app/site.py` 的 `TOOLS` 常量加一条，路由、导航、sitemap 自动跟上。
- **改表结构**：`app/models.py` 与 `database init/full_init.sql` 必须同步，
  `tests/test_schema_sync.py` 会拦不一致。
- 配置只从 `app/config.py` 的 `Settings` 读，不要在模块里散着 `os.getenv`。
- 新增 ruff 规则或白名单要去 `ruff.toml` 改并写清理由，**不要**在代码里散着加 `# noqa`（现有两处 `# noqa: DTZ005` 是 TD-146 的已知取舍，不要照抄）。

## 文档地图

| 要看什么 | 去哪 |
| --- | --- |
| 实现取舍与上线阻塞项（111 条 TD-xx，其中 8 条待处理） | `TECH_DECISIONS.md` |
| 沙箱状态恢复、真库配方、已踩过的坑 | `HANDOVER.md` |
| 路线图与子项进度 | `ROADMAP.md` |
| 人在本机怎么跑起来（含 Windows cmd 步骤） | `README.md` |
| 阶段传输文件（**合并 PR #3 之后删除**） | `PHASE1_TRANSFER.txt` / `APPLY_INSTRUCTIONS.md` / `PHASE2_TRANSFER.txt` / `APPLY_PHASE2.md` |

## 技能库（按需显式加载，不要每次全读）

`.claude/skills/`（按需显式加载，**不要每次全读**）：

| 技能 | 触发时机 |
| --- | --- |
| `codemax-workflow` | 任何写代码 / 改代码 / 加功能的请求 |
| `fastapi-python` | 写路由、依赖注入、异步 DB |
| `python-testing` | 写或改测试 |
| `schema-sync` | 增删改数据列、加表、加索引（含 `test_schema_sync.py` 抓不到的部分） |
| `new-tool-page` | 新增工具页 / 前端页面 / 导航入口 |
| `pre-commit-review` | 代码改完、准备 `git commit` 之前 |
| `finish-subitem` | 一个 ROADMAP 子项做完、准备交付时 |

## 沙箱注意（会咬人）

- 沙箱可能在两轮之间被回收：`.venv`、`/tmp/pgdata`、`storage/` 会消失，且 `.git` 会被重置回
  `main` 的原始提交，而已推送的工作全部变成「未提交改动」。
  恢复顺序见 `HANDOVER.md`，**别在 `git ls-remote` 之前断定工作丢了**。
- CI 日志正文取不到（下载会重定向到 `*.blob.core.windows.net` 然后 SSL 失败），
  只能引用 job/step 的 `conclusion` 字段。
