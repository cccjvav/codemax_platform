---
name: pre-commit-review
description: >
  提交前的自查闭环：残留引用 → 硬编码 → 死代码 → 时区 → 变异测试 → 文档同步。
  发现问题就地修，修完重跑；无法验证的范围如实记录。
  Trigger: 任何代码改动完成、准备 git commit 之前。
version: 2.0
---

# pre-commit-review

> 全程不需要用户介入。**目标是提交前把问题拦在自己手里**，
> 而不是等 CI 或等用户发现。

## 检查单（逐项过，别跳）

### 1. 残留引用
- [ ] 被改名/删除的函数、变量、配置项，全仓 `grep` 一遍还有没有引用
- [ ] 删掉的代码有没有留下孤儿 import、孤儿注释、孤儿测试
- [ ] 文档里提到的路径/命令是否还成立（`grep -rn "<旧名>" --include="*.md"`）

### 2. 硬编码
- [ ] 密钥、token、密码 —— 只用私有被忽略 `.env` 或临时进程变量；`.env.example` 只放配置名与无真实秘密的示例
- [ ] URL / 域名 —— 走 `settings.SITE_BASE_URL` 等配置，不写死
- [ ] 金额 —— 走 `settings.SHOP_PRODUCT_AMOUNT`，单位是**分**
- [ ] 魔法数字 —— 提成常量或写清来源（例如限流窗口、TTL）

### 3. 静态检查（先跑这个，下面几项大半它会自动报）
```bash
.venv/bin/ruff check .          # 必须 All checks passed!
.venv/bin/ruff check . --fix    # 能自动修的先修（import 排序、未用 import 等）
```
规则集与理由见 `ruff.toml`（TD-145）。**不要**为了让它闭嘴就随手加 `# noqa`；
确实是取舍就在 `TECH_DECISIONS.md` 记一条 TD 再 noqa，并注明编号。

### 4. 死代码
- [ ] 没用到的 import、没用到的局部变量（ruff 的 F401/F841 会报；测试里尤其常见：
      一个没用到的 `alice = await auth_headers(...)` 往往意味着**这个用例没在测它名字所说的东西**）
- [ ] 用不到的分支、永远为真的判断

### 5. 时间与并发（本项目踩过）
- [ ] `datetime.now()` 不带 tz —— 注意 `paid_at` 用应用本地时间、而 `create_time`
      用数据库 `func.now()`，两者混用会在跨时区部署时产生矛盾数据
- [ ] async 函数里有没有阻塞调用（`subprocess.run`、同步 IO、`time.sleep`）
- [ ] 「读-判断-写」有没有改成原子条件更新（参考 `app/routers/oauth.py` 的
      `rowcount` 写法，TD 里有记录）

### 6. 测试
- [ ] `.venv/bin/python -m pytest -q` → **本轮新增后全量通过**（数量以本次输出记入阶段证据）
- [ ] 改了表结构 → 真库那一遍也要跑（专用可丢弃库，配方见 `docs/ACCEPTANCE_GUIDE.md`）
- [ ] 修 bug → 补了一个能复现该 bug 的回归测试
- [ ] **关键逻辑做过变异测试**：把实现改坏 → 确认对应用例变红 → 改回来。
      记录格式：`去掉 X → N 个用例红`。
      抓不到的变异先区分等价变异、测试缺口或运行错误；不能一律归为等价，更不能当成已覆盖。

### 7. 不该进仓库的东西
- [ ] `.env`、`storage/`、`__pycache__/`、`.venv/`、临时脚本（`/tmp/*.py` 不要挪进仓库）
- [ ] `git status --porcelain` 逐行看一遍，确认没有意料之外的文件

### 8. 文档同步
- [ ] 新取舍 → `TECH_DECISIONS.md` 加 TD-xx（含放弃了什么 / 代价 / 何时回头改）
- [ ] 不在多个入口手工同步 TD/测试数量；本次证据只放对应阶段文件
- [ ] 可复用经验 → `manager/experience.md`；阶段关闭/已解决 P1 按 codemax-workflow 做元复盘
- [ ] `HANDOVER.md` 保持当前导航；阶段状态 → `manager/stages/`，不复制一份冲突的路线图

## 提交

```bash
git status --porcelain                      # 再确认一遍
git add <本批明确审核过的文件>               # 不批量纳入无关作品或用户文件
git commit -F /tmp/msg.txt                  # 用 -F，不要用带引号的 -m
branch=$(git branch --show-current)         # 同时遵守当前会话的固定分支限制
git push origin "$branch"
git ls-remote origin "refs/heads/$branch"   # 确认远端 tip
```

CI 是全局闸门，每次 push 都必须核对最终 SHA 的全部 jobs（不仅修改 workflow 时）；
按 `finish-subitem` 筛选 headSha，再 watch/view。历史绿色或只看两个 job 都不算本次完成。

## 收尾报告（提交后必须给出）

```
改了：<文件与要点>
ruff：All checks passed!
测试：SQLite <N> passed + <N> skipped；真库 <N> passed
变异测试：<去掉 X → N 个红> ×若干
提交：<short sha>，已推送，远端 tip 已确认
未核实：<跑不了的、以及为什么>
```
