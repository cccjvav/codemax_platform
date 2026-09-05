# `.github/workflows/` 模块说明书

> **行号基准 commit：`1d5499c`**（2026-09-04）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 复算方式见文末「附：行号与数字怎么复核」。
>
> 姊妹篇：`app/README.md`、`app/routers/README.md`、`app/tools/README.md`、
> `app/templates/README.md`、`app/static/README.md`、`database init/README.md`、`docs/ROOT_FILES.md`、`tests/README.md`、`scripts/README.md`。

---

## 1. 模块概述

### 1.1 定位

**只有一个文件**：`ci.yml`，229 行。它是本仓库唯一的 CI 流水线（TD-84）。

实测结构（用 YAML 解析取真值，不是靠数缩进）：

```text
4 个 job
  lint            显示名「静态检查（ruff）」                      4 步   无 service
  docs            显示名「文档站构建（scripts/build_docs_site.py）」 5 步   无 service
  test-sqlite     显示名「测试（SQLite 后端）」                   5 步   无 service
  test-postgres   显示名「测试（真 PostgreSQL 16）」               6 步   service: postgres:16
permissions: contents: read  +  pull-requests: write
on: push（所有分支）+ pull_request
concurrency: group=ci-${{ github.ref }}  cancel-in-progress=true
```

### 1.2 为什么要四个 job 而不是一个

| job | 回答的问题 | 为什么不能合并 |
| --- | --- | --- |
| `lint` | 代码风格与静态错误 | **只装 ruff，不装整个 `requirements.txt`** —— L57-L58 注释：里面有 `pgserver`（自带 PG 二进制，很大），lint 用不上。分开跑**几十秒就出结果**，不用等依赖装完 |
| `test-sqlite` | 逻辑对不对（快） | 默认后端，内存 SQLite |
| `test-postgres` | **在真库上**对不对 | 有些问题**只在真库出现**（见 3.3） |

> **反过来的教训也要记着**：`AGENTS.md` 里有一条排障提醒 ——
> **「把 job 名当根因是排障陷阱」**。CI 曾在「真 PostgreSQL 16」job 上红，
> 但那条失败的测试用的其实是**内存 SQLite**。job 名只说明**在哪跑的**，不说明**为什么红**。

### 1.3 全文件统一的四条约定

**① 失败时把 pytest 输出发成 PR 评论**（TD-194）。这是本文件最有故事的一段，见 3.2。

**② job 名写死，不用表达式。** L23-L24 注释：**实测 `${{ job.name }}` 与 `${{ github.job }}` 在 `run` 步骤里都渲染成空串**（金丝雀 run `33811898659` / `33812612241` 的评论可以证实）。所以 L97 / L100 / L171 / L174 的 job 名都是硬编码的中文串。

**③ 版本号只有一处真相。** L62 装 ruff 时用 `pip install "$(grep -oE '^ruff==[0-9.]+' requirements.txt)"` —— **从 `requirements.txt` 里抠版本号**，避免两处漂移。

**④ `set -o pipefail`。** L85 / L159 —— 因为 pytest 的输出走了 `| tee`，**没有 `pipefail` 的话管道退出码取的是 `tee` 的 0**，测试全红 CI 也会绿。

---

## 2. 文件级详细说明书

### 📄 文件名：`ci.yml`（229 行）

#### 文件头注释（L1-L25）—— 这段是全文件信息密度最高的部分

- **L4-L7 四个 job 各自干什么**
- **L9-L11 历史**：`2f3223a` 由仓库管理员从 `docs/github-actions-ci.yml` 纯重命名激活；`aa87c52` 把 actions 升到 v7（消 Node 20 弃用告警，TD-144）；`b47044b` 加入 lint job
- **L13-L16 权限历史**：本会话的 GitHub App **已于 2026-09-01 获得 Workflows 写权限**，现在可以直接改并 push 本文件。此前它被 `remote rejected ... without 'workflows' permission` 拒绝过多次，当时的绕行办法（`git mv` / `git am` 补丁）已不再需要
- **L18-L24 关于日志**：**CI 日志正文在本沙箱取不到** —— 下载被重定向到 `productionresultssa18.blob.core.windows.net`，实测 HTTP 000（**DNS 能解析、`api.github.com` 同时是 200，所以是出口被墙而不是权限问题**）。绕行办法就是下面每个 test job 末尾那一步

#### 顶层配置

**`permissions`　L31-L33**
- **L28-L30 注释解释了一个容易踩的坑**：默认 token 只有 `contents: read`（日志里的 `GITHUB_TOKEN Permissions` 可以证实），发 PR 评论必须显式开 `pull-requests: write`。**写了这个块就取代默认权限，所以 `contents: read` 要一并写上，否则 checkout 会失败**

**`on`　L35-L38** —— `push` 到**所有分支**（`branches: ["**"]`）+ 所有 `pull_request`。

**`concurrency`　L41-L43**
- **L40 注释**：同一分支连续推送时**取消上一次还在跑的构建**，省 Actions 分钟
- `group: ci-${{ github.ref }}` + `cancel-in-progress: true`

#### job 1：`lint`（L46-L65）

**L47 `name: 静态检查（ruff）`** —— **这个显示名会被 PR 评论硬编码引用**（见约定②）。

**L50-L55 checkout + setup-python 3.11 + pip 缓存**

**L59-L62 安装 ruff**
- **L57-L58 注释说明为什么不装整个 requirements**：里面有 `pgserver`（自带 PG 二进制，很大），lint 用不上
- **L62 `pip install "$(grep -oE '^ruff==[0-9.]+' requirements.txt)"`** —— **版本号从 `requirements.txt` 抠**，避免两处漂移

**L64-L65 `ruff check .`**

#### job 2：`test-sqlite`（L67-L107）

**L71-L76 checkout + setup-python**

**L78-L81 装完整依赖**

**L83-L86 pytest**
- **L85 `set -o pipefail`** —— **没有它，`| tee` 会把退出码吞成 0**
- **L86 `python -m pytest -q 2>&1 | tee pytest-output.txt`** —— **同时落一份文件**，给下面的评论步骤用

**L91-L107 「失败时把 pytest 输出发到 PR 评论」**（TD-194）
- **L92 `if: failure() && github.event_name == 'pull_request'`** —— **两个条件都要**：push 事件没有 PR 号，`gh pr comment` 会失败
- **L93-L94 `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`**
- **L96-L105 拼 Markdown 正文**：
  - **L97 标题里硬编码 job 显示名**「测试（SQLite 后端）」
  - **L99 run 链接** —— 用 `github.server_url` / `github.repository` / `github.run_id` 拼
  - **L100 job 名 + commit SHA**
  - **L103 `tail -n 300 pytest-output.txt 2>/dev/null || echo "(没有 pytest 输出文件)"`** —— **取末尾 300 行**（pytest 的失败摘要在末尾），文件不存在也不报错
- **L106-L107 `gh pr comment ... --body-file`** —— 用文件而不是 `--body`，避免正文里的引号把 shell 命令打断

#### job 3：`test-postgres`（L109-L181）

**L113-L126 `services.postgres`**
- **L115 `image: postgres:16`**
- **L116-L119 环境变量** —— 用户/密码都是 `postgres`，库名 `codemax_test`
- **L120-L121 端口映射 `5432:5432`**
- **L122-L126 `options`** —— **健康检查**：`pg_isready`，10s 间隔、5s 超时、重试 5 次。**没有它，pytest 可能在 PG 还没起来时就开跑**

**L128-L132 job 级环境变量**
- **L129 `TEST_DATABASE_URL: postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/codemax_test`** —— **这是让 pytest 走真库的开关**（`tests/conftest.py` 读它）
- **L130-L132 `PGPASSWORD` / `PGHOST` / `PGUSER`** —— 给下面的 `psql` 命令用

**L149-L155 「建表脚本在真 PostgreSQL 上执行（连跑两遍验证幂等）」**
- **L147-L148 注释说明分工**：建表脚本与 ORM 模型的一致性由 `tests/test_schema_sync.py` 保证；**这里再验一次它真的能在 PostgreSQL 上执行，而且可以重复执行**
- **L151 建一个专门的库 `codemax_ddl`**（与测试库分开）
- **L152-L153 同一个脚本跑两遍** —— **第二遍验幂等**
- **L152/L153 `-v ON_ERROR_STOP=1`** —— **没有它，`psql` 遇到错误会继续跑并以 0 退出**，CI 会绿着放行一个建不出表的脚本
- **L154-L155 打印表数** —— 便于人工核对

**L157-L160 pytest**（与 job 2 同样带 `pipefail` + `tee`）

**L165-L181 失败评论步骤** —— 与 job 2 **结构完全相同**，只有 L171 / L174 的 job 名不同

---

## 3. 执行逻辑流

### 3.1 一次 push 触发什么

```text
git push
  │
  ├─ concurrency 检查：同一分支上一次还在跑？→ 取消它（省 Actions 分钟）
  │
  ├─ 触发**两条** run（push 一条、pull_request 一条，若在 PR 分支上）
  │    ⚠️ 查 CI 状态时两条都要看，只看一条会漏
  │
  └─ 四个 job **并行**跑
       ├─ lint            ~1 分钟（只装 ruff）
       ├─ docs            ~1 分钟（装 mistune，渲染整站并校验页数）
       ├─ test-sqlite     ~5 分钟
       └─ test-postgres   ~7 分钟（要起 PG service + 跑两遍建表脚本）
            │
            └─ 任一 test job 失败且是 PR 事件
                 → 发 PR 评论（含 pytest 输出末 300 行）
```

### 3.2 「失败评论」这一步是怎么来的（TD-194）

**问题**：CI 日志正文在沙箱里取不到。

**排查过程**（`AGENTS.md` 的「沙箱注意」段有完整证据链）：

| 实测 | 结果 | 说明什么 |
| --- | --- | --- |
| `gh api repos/.../actions/jobs/<id>/logs` | **成功返回签名 URL** | **拿到 URL 正是需要权限的那一步 ⇒ 权限本来就够** |
| `getent hosts productionresultssa18.blob.core.windows.net` | **解析成功** | 不是 DNS 问题 |
| `curl` 那个签名 URL | **HTTP 000** | **出口被墙** |
| 同时刻 `api.github.com` / `github.com` | **200** | 只有 blob 域名不通 |

**结论**：**不是权限问题，加权限无效。** 于是改成「把日志摘要发到 PR 评论」—— 评论走 `api.github.com`，沙箱读得到。**TD-192 就是这么才拿到真因的**（此前只能靠人工去网页复制）。

**这一步本身也踩过坑**（L23-L24 注释）：

- **`${{ job.name }}` 与 `${{ github.job }}` 在 `run:` 步骤里都渲染成空串** —— 而 `${{ github.sha }}` 是正常的。所以 job 名只能写死
- **加 `if: failure()` 的步骤必须金丝雀实测** —— 故意推一个会失败的 commit，确认评论真的发出来了，而不是等真出问题时才发现这一步本身是坏的

### 3.3 四个 job 各能抓到什么、抓不到什么

| 问题类型 | lint | docs | test-sqlite | test-postgres |
| --- | --- | --- | --- | --- |
| 未使用 import、裸 except、`datetime` 没带时区 | ✅ | — | — | — |
| 业务逻辑错 | — | — | ✅ | ✅ |
| 建表脚本与 ORM 不一致 | — | — | ✅（`test_schema_sync.py`，**只解析不执行**） | ✅（**真执行两遍**） |
| **建表脚本在 PostgreSQL 上语法不接受** | — | — | **❌ 抓不到** | ✅ |
| **只在真库出现的时间/类型行为** | — | — | **❌** | ✅ |
| 性能回归 | — | — | ✅（`tests/test_perf.py`） | ✅ |
| **文档站构建脚本坏掉 / 生成页数不对** | — | ✅ | ❌（`test_docs_site.py` 只测纯函数，不渲染整站） | ❌ |
| **`DOC_GROUPS` 漏登记新写的 README** | — | ✅ | ✅（`test_docs_site.py` 会查文件存在性） | ✅ |

**第 4 行是关键**：`tests/test_schema_sync.py` 用**项目自己的 DDL 解析器**读 `full_init.sql` 与 ORM 比对，**它不执行 SQL**。所以「PostgreSQL 其实不接受这句」这类问题只有 `test-postgres` 的 L152-L153 能抓到。

### 3.4 CI 状态的三条查询通道（沙箱里能用的）

| 通道 | 命令 | 能拿到什么 |
| --- | --- | --- |
| run/job 结论 | `gh run view <rid> --json jobs` | 哪个 job、哪一步失败（**没有 traceback**） |
| 检查注解 | `gh api repos/.../check-runs/<job_id>/annotations` | 只有 `Process completed with exit code 1.` + `path:line` |
| **PR 评论** | `gh pr view <n> --json comments` | **pytest 输出末 300 行，含 traceback —— 唯一能拿真因的通道** |

> **`gh run list` 没有 `--commit` 参数**；`--json` 不支持 `jobs` 字段；
> **进行中的 run 其 `conclusion` 是空串**（不是 `null`）。这几条都踩过。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `1d5499c`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) job 结构与步骤数（本文 1.1 的那组数字）—— 用 YAML 解析，不要靠数缩进
python -c "
import pathlib, yaml
d = yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8'))
print('job 数:', len(d['jobs']), '→', list(d['jobs']))
for n, j in d['jobs'].items():
    print(f'  {n:<15} {j[\"name\"]:<24} 步骤={len(j[\"steps\"])}  services={list(j.get(\"services\", {})) or \"—\"}')
print('permissions:', d['permissions'])
print('concurrency:', d['concurrency'])
"
# 预期：3 个 job；lint 4 步 / test-sqlite 5 步 / test-postgres 6 步（service: postgres）
#       permissions = {'contents': 'read', 'pull-requests': 'write'}

# 2) 本地跑一遍 lint job 做的事
.venv/bin/ruff check .

# 3) 本地跑一遍 test-sqlite job 做的事
.venv/bin/python -m pytest -q

# 4) 真库 job 的配方见 HANDOVER.md §9（起 pgserver + createdb + TEST_DATABASE_URL）
#    本机实测（SQLite 侧）：561 passed, 4 skipped
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动 `ci.yml` 后本文对应的行号与
> 计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> **改 `ci.yml` 的三条注意**：
> ① 改 job 显示名 → **必须同步改两处 PR 评论里硬编码的 job 名**（L97/L100 与 L171/L174）；
> ② 加 `if: failure()` 的步骤 → **必须金丝雀实测**，确认评论真的发出来；
> ③ 加新依赖 → 确认 `lint` job 不会因为多装东西而变慢（它刻意只装 ruff）。
