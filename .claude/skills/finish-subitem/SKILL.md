---
name: finish-subitem
description: >
  一个 ROADMAP 子项做完后的固定收尾流程：跑两套测试、TD 入账、六处文档同步、
  提交推送、刷新 PR、看 CI。
  Trigger: 某个 ROADMAP 子项（如 S4-01-2）实现完成、准备交付时。
---

# finish-subitem

> **一个子项 = 一个提交。** 会话被钉在单分支上，所以子项累积在同一个 PR 里，
> 但提交必须分开，便于逐个回滚。
> 收尾没做完就不算完成 —— 文档漂移比代码 bug 更难发现。

## 流程（按顺序）

### 1. 先过 pre-commit-review
走 `pre-commit-review` 技能的检查单。**这一步不过就不要往下走。**

### 2. 两套测试都要绿
```bash
.venv/bin/python -m pytest -q                       # 418 passed, 2 skipped
TEST_DATABASE_URL="postgresql+asyncpg://postgres@/codemax_test?host=/tmp/pgdata" \
  .venv/bin/python -m pytest -q                     # 419 passed, 1 skipped
```
真库起不来时按 `HANDOVER.md` §9 重建；实在起不了要在提交信息里写明「真库未跑」。

### 3. 文档同步（六处，一个都不能漏）

| 文件 | 改什么 |
| --- | --- |
| `ROADMAP.md` | 勾掉该子项 |
| `TECH_DECISIONS.md` | 本子项产生的取舍，每条一个 TD-xx（含代价与「何时回头改」）；解决掉的旧 TD 用 `~~TD-xx~~ **已解决**` 划掉而**不是删行** |
| `AGENTS.md` + `HANDOVER.md` | TD 计数、测试基线数字、§1 当前状态（HEAD / PR 提交数） |
| `HANDOVER.md` §6 / §7 | 新踩的坑 / 已完成清单 |
| **该文件所属目录的 `README.md` + 所有上级入口** | **新增/删除/改名任何代码文件时必做**，见下方「第六处的细则」 |
| **`docs/ARCHITECTURE_GUIDE.md`** | **有新实现/新功能就必须跟上**：新子系统或新机制**补一课**（四段式：① 大白话 → ② 生活比喻 → ③ 落到哪个文件+实测数字 → ④ 行业术语对照）；已有课讲的行为变了就**改对应小节**并写明"原来是什么、为什么变"；课末预告要与实际下一课一致 |

#### 第六处的细则：新增代码文件后，「上级入口」一个都不能漏

**这一条是被真实事故逼出来的**：新增 `scripts/build_docs_site.py`（898 行）之后，
只写了代码没动文档，结果 ——

- `scripts/README.md` 里**完全没提这个文件**（覆盖率从 90/90 掉到 90/91）
- `DOCUMENTATION_SUMMARY.md` 里 5 处「90 个代码文件」全部过期
- `总览.md` 的目录树与索引表还写着 `scripts/ · .mjs（1）`「68 行」
- `AGENTS.md` 文档地图、根 `README.md` 都没有新东西的入口

**新增一个代码文件时，按这张清单逐个过**：

| # | 要改的地方 | 改什么 |
| --- | --- | --- |
| 1 | **该文件所属目录的 `README.md`** | 加一节文件级说明书（职责 / 关键函数带行号 / 执行流程），并更新该 README 的「实测结构」与文首「行号基准 commit」 |
| 2 | `总览.md` | §4 目录树的该目录一行、§7 索引表的行数、§0.2 快速导航（若是核心入口）、**§8 附录的预期数字** |
| 3 | `DOCUMENTATION_SUMMARY.md` | 覆盖率数字、按后缀统计、交付清单 |
| 4 | `AGENTS.md` 文档地图 | 新文档要有一行入口 |
| 5 | 根 `README.md` 「文档」段 | 新文档要能从根 README 点进去 |
| 6 | `docs/site/` | 若新增了文档，`scripts/build_docs_site.py` 的 `DOC_GROUPS` 要加进去，然后重跑构建 |

**核完不能只看，要跑命令**（下面「计数命令」里的覆盖率脚本就是干这个的）。
**「我以为改全了」不算数，跑出来 0 遗漏才算数。**

> 同类事故已经发生过两次：第一次是 `scripts/` 整个目录没有 README（靠覆盖率脚本抓出），
> 第二次就是本条。**根因都是「按自己记得的清单改」而不是「扫一遍反推」**。

计数命令（改完必须核一遍，别手写数字）：

```bash
# 覆盖率：每个代码文件是否被本目录 README 提及（0 遗漏才算过）
python -c "
import pathlib
EX = {'.venv','.git','__pycache__','.pytest_cache','.ruff_cache','node_modules'}
CODE = {'.py','.js','.mjs','.sql','.html','.yml','.yaml','.toml','.ini','.json','.css','.xml'}
GEN = ('docs/site/d','docs/site/s','docs/site/data')
GENF = ('docs/site/index.html','docs/site/graph.html','docs/site/routes.html','docs/site/symbols.html')
files = [p for p in pathlib.Path('.').rglob('*')
         if p.is_file() and p.suffix.lower() in CODE and not (set(p.parts) & EX)
         and not str(p).replace(chr(92),'/').startswith(GEN)
         and str(p).replace(chr(92),'/') not in GENF]
miss = []
for f in files:
    own = pathlib.Path('docs/ROOT_FILES.md') if str(f.parent)=='.' else f.parent/'README.md'
    if not own.exists() or f.name not in own.read_text(encoding='utf-8'): miss.append(f)
print('代码文件:', len(files), ' 未被本目录 README 提及:', len(miss))
for m in miss: print('   ❌', m)
"
```


```bash
# TD 条目总数
grep -oE '^\| (~{0,2})TD-[0-9]+' TECH_DECISIONS.md | grep -oE 'TD-[0-9]+' | sort -u | wc -l
# 待处理的上线阻塞项
awk 'NR>=10 && NR<=30' TECH_DECISIONS.md | grep -E '^\| ' | grep -vE '^\| ~|^\| 编号|^\| ---' | wc -l
# 本分支提交数（PR 正文要用）
git rev-list --count 9622a38..HEAD

# 陈旧数字扫描（测试条数 / TD 条数散落全仓，改一处必扫全仓）
# ⚠️ 必须用 --exclude-dir；**不要**用 `| grep -v "\.venv"` —— 目标行本身就含
#    `.venv/bin/python`，那样会把要找的行全滤掉，得到"已无残留"的假结论。
grep -rn --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ \
     -E "41[0-9] (passed|条)|1[0-9][0-9] 条 TD" .
```

扫描结果里合法的例外只有两类，其余都要改：
- 历史记录（如变异实验"当时 417 全绿"、`ARCHITECTURE_GUIDE.md` 7.10 的"原值"列）
- 恰好含该数字的 commit SHA（如 `9614172` 里有 "417"）

### 4. 提交并推送
```bash
git add -A
git commit -F /tmp/msg.txt
git push origin arena/01a0599b-codemax-platform
git ls-remote origin refs/heads/arena/01a0599b-codemax-platform   # 必须与本地 HEAD 一致
```

提交信息里**必须**包含：做了什么、两套测试的实际数字、变异测试证据
（`去掉 X → N 个红`）、以及**未核实项**（跑不了的和原因）。

### 5. 刷新 PR 正文
```bash
gh pr view 3 --json body --jq '.body' > /tmp/cur_body.md
# 改 /tmp/cur_body.md 里过时的数字（提交数、CI 运行数、已完成清单）
gh api -X PATCH repos/cccjvav/codemax_platform/pulls/3 -F body=@/tmp/cur_body.md --jq '.commits'
gh pr view 3 --json state,commits --jq '"\(.state) \(.commits|length)"'   # 回读确认
```
⚠️ **不要用 `gh pr edit`** —— 它失败时也返回 0，会静默不生效。
⚠️ **不要合并**，除非用户在本轮明确说要合并。

### 6. 看 CI
```bash
gh run list --branch arena/01a0599b-codemax-platform --limit 4
gh run watch <run_id> --exit-status --interval 15
```
两个 job（SQLite / 真 PostgreSQL 16）都要 `success`。
CI 日志正文在本沙箱取不到（重定向到 `*.blob.core.windows.net` 后 SSL 失败），
只能引用 job/step 的 `conclusion` 字段 —— 引用时别写成「日志显示 N passed」。

## 交付报告模板

```
子项：<S4-01-2 标题>
做了什么：<要点>
测试：SQLite 418 passed + 2 skipped；真 PostgreSQL 16.2 419 passed + 1 skipped
变异测试：<去掉 X → N 个红> ×若干
提交：<short sha>，已推送，远端 tip 已确认
CI：<run id> push / pull_request 均 success
文档：ROADMAP 已勾；TD 新增 <编号>，总数 <N>；阻塞项 <N> 条
未核实：<跑不了的、以及为什么>
下一步：<建议>
```

## 沙箱被回收时（会发生在任意两轮之间）

`.venv` / `/tmp/pgdata` / `storage/` 会消失，`.git` 会被重置回 `main` 的原始提交，
已推送的工作全部变成「未提交改动」。**先 `git ls-remote`，别断定工作丢了。**
恢复顺序见 `HANDOVER.md`。
