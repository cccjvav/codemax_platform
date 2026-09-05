# AGENTS.md

codemax_platform — FastAPI + SQLAlchemy 2.0(async) + PostgreSQL 的毕设服务平台。

> 本文件是**仓库级硬约定**，写给编码 agent 看，不是给人的项目介绍（那是 `README.md`）。
> 原则：能被工具确定性执行的规则不写在这里，这里只放**需要判断**的部分。
> 确定性闸门有两个：**ruff**（规则集见 `ruff.toml`）与 **pytest + CI**。

## 命令（精确调用，别自己造）

| 动作 | 命令 |
| --- | --- |
| 装依赖 | `.venv/bin/python -m pip install -r requirements.txt` |
| 建库建表（**仅空库**：`full_init.sql` 会 `DROP TABLE ... CASCADE`，对已有数据等于清库） | `cd "database init" && python db_init.py && cd ..` |
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
- **有新实现/新功能 → 同步更新 `docs/ARCHITECTURE_GUIDE.md`，不留到"以后再补"。**
  - 新子系统、新机制、新的横切关注点 → **补一课**，沿用四段式：
    ① 核心概念大白话 → ② 生活比喻 → ③ 落到哪个文件（附行数与实测数字）→ ④ 行业术语对照。
  - 已有课讲的行为变了 → **改对应小节**，并在改动处说明"原来是什么、为什么变"。
  - 课末预告（`x.11 下一课预告` 之类）与实际下一课**必须一致**。
- **被文档记录过的数字变了 → 全仓同步，不许只改一处。** 测试条数、路由条数、配置项数、
  TD 条数、阈值都算。扫描用 `--exclude-dir`，**不要**用 `| grep -v "\.venv"` 过滤 ——
  那些行本身就含 `.venv/bin/python`，会把要找的行**全部滤掉**并给出"已无残留"的假结论
  （已踩过，见 `ARCHITECTURE_GUIDE.md` 7.10）：
  ```
  grep -rn --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ \
       -E "41[0-9] (passed|条)|1[0-9][0-9] 条 TD" .
  ```
- **写进文档的每一条命令与每一个文件路径，必须先实跑/`ls` 确认存在，再落笔。**
  已踩三次同一个坑（`app/tools/README.md` 引了 3 个不存在的测试文件，`app/README.md` 引了 9 个），
  光靠"下次记得"不管用，所以这里钉成硬约束：
  - 引测试文件 → **不要按模块名猜文件名**。用实测覆盖关系：
    ```
    grep -rl "app\.<模块>" tests/*.py
    ```
    拿到的清单再逐个 `test -f` 确认。**没有专属测试文件的模块要如实标注"间接覆盖"，
    不许假装它有**（本项目 `app/timeutil.py` 与 `app/schemas.py` 就属于这种）。
  - 引行号 → **先取后写，不要写完再校**。行号一律来自
    `ast` 的 `lineno/end_lineno`（见三份 README 附录的复算命令），**不许按 docstring 长度估**
    （`app/routers/README.md` 靠估，首轮回验抓出 22 处偏差）。
  - 引数字 → 当场跑命令取，不凭记忆（`PROFESSIONAL_KEYWORDS` 实测 31、旧记录写 30）。
  - **验证脚本自己也要先被验证**：行号校验器栽过两版（把 `class`/`async` 当符号名、
    漏 `AnnAssign` 常量、点号名 `Limiter.allow` 只取类名、同名方法字典被覆盖）。
    扫描类命令先 `assert` 能命中一个**已知**目标，再信它报的"无残留"。

- 修 bug 时补一个能复现该 bug 的回归测试。

## 「做完」的定义（七条全中才算完成）

1. `.venv/bin/ruff check .` → **All checks passed!**
2. `.venv/bin/python -m pytest -q` → **526 passed, 4 skipped**（4 条 skip 的实测构成见 `README.md`：2 条并发需真库 + 1 条真浏览器 + 1 条语义阈值标定需 embedding key）
   （跳过的那条是真并发测试，SQLite 的 StaticPool 复现不了竞态，见 TD-85）。
3. 真 PostgreSQL 上 → **528 passed, 2 skipped**（此前随机红的绝对阈值性能用例已按实测换掉，见 `TECH_DECISIONS.md` TD-183/186）（起库配方见 `HANDOVER.md` §9）。
4. 已提交并推送，`git ls-remote` 能看到新 tip。
5. 关键逻辑改动做过**变异测试**：把实现改坏 → 确认对应用例变红 → 改回来。
   抓不到的变异要如实记为「等价变异，不可捕获」，不得当成已覆盖。
6. 新取舍已进 `TECH_DECISIONS.md`，相关文档的计数已同步。
7. **`docs/ARCHITECTURE_GUIDE.md` 已跟上这次的实现**：新子系统/新机制补一课（四段式），
   已有行为变了就改对应小节；且**被文档记录过的数字已全仓同步**（用上面 ALWAYS 里那条
   `--exclude-dir` 扫描命令自查，别用管道 `grep -v`）。

## 技术选型（已定，不要重新论证）

> 具体到实现层面的取舍（160 条，带编号 TD-xx、代价与"何时回头改"）全部集中在
> **`TECH_DECISIONS.md`**。做新功能时若产生新取舍，去那里追加一行，别只写在 docstring 里。

1. **原路线图里的 Java 库一律换成 Python 对应物**（本项目是 Python，不许为了对齐文档措辞引入 Java/Node 运行时 —— 违反上面第 1 条铁律）：
   - 导出 Word → `python-docx`（不是 Apache POI）
   - 爬虫 → `httpx` + `BeautifulSoup4`（不是 HttpClient + Jsoup）
   - 动态页面抓取 → **已选 Playwright**（TD-03/191）。它是**可选依赖**，不在 requirements.txt 里：
     `pip install playwright` 后还要 `playwright install chromium`；没装时端点返回 503 + 安装命令
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
| **文档体系入口：目录树、技术栈、分层、子模块索引** | `总览.md` |
| **文档化 QA 审查报告（覆盖率 / 链接 / 格式）** | `DOCUMENTATION_SUMMARY.md` |
| **文档站（离线静态站，可读+可视化）的用法与构建** | `docs/site/README.md` |
| **架构讲解（七课，面向没读过代码的人；新实现要同步更新）** | `docs/ARCHITECTURE_GUIDE.md` |
| 实现取舍与上线阻塞项（160 条 TD-xx，其中 2 条仍为上线阻塞项：TD-113、TD-124） | `TECH_DECISIONS.md` |
| 沙箱状态恢复、真库配方、已踩过的坑 | `HANDOVER.md` |
| 路线图与子项进度 | `ROADMAP.md` |
| 人在本机怎么跑起来（含 Windows cmd 步骤） | `README.md` |

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
- **CI 日志正文取不到 —— 是沙箱出口限制，不是 GitHub 权限问题，别去申请权限。**
  实测证据（2026-09-04）：
  1. `gh api repos/<repo>/actions/jobs/<id>/logs` **能拿到签名下载地址**
     （拿到这一步正是需要权限的那一步，说明权限没问题）；
  2. 该地址主机 `productionresultssa*.blob.core.windows.net` **DNS 能解析**（20.209.x.x），
     但 HTTPS 请求返回 **HTTP 000 / EOF**；
  3. 同一时刻 `api.github.com` 与 `github.com` 均为 **200**。
  ⇒ 沙箱只放行 GitHub 自己的域，Azure Blob 不在白名单内。**加权限改变不了这一点。**
  另外本令牌是**安装令牌**（`gh api /app` → 401 "A JSON web token could not be decoded"），
  所以「App 主动申请新权限 → GitHub 发邮件给安装方批准」这个流程**也无法由本侧发起**，
  只有 App 所有者（Arena 侧）能发起。

  **能用的替代通道（都走 `api.github.com`）：**

  | 要看什么 | 命令 | 能拿到什么 |
  | --- | --- | --- |
  | 每个 job / step 的结论 | `gh run view <rid> --json jobs` | 结论 + 步骤名 |
  | **哪一步**失败 | `gh api repos/<repo>/check-runs/<job_id>/annotations` | 仅 `Process completed with exit code 1.` + `path:line`（实测**没有 traceback**，只定位到步骤） |
  | pytest 失败输出**全文** | TD-194 的 PR 评论（workflow 里 `if: failure()` 那步） | `tail -n 300`，含 traceback —— **这是唯一能拿到真因的通道** |

  通过的 job 注解为空数组 `[]`，所以「有注解」本身就等价于「这一步失败了」。
