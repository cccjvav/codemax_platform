---
name: pre-commit-review
description: >
  提交前的自查闭环：残留引用 → 硬编码 → 死代码 → 时区 → 变异测试 → 文档同步。
  发现问题就地修，修完重跑，直到零问题再提交。
  Trigger: 任何代码改动完成、准备 git commit 之前。
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
- [ ] 密钥、token、密码 —— 一律走 `.env`，新增项同时进 `.env.example`
- [ ] URL / 域名 —— 走 `settings.SITE_BASE_URL` 等配置，不写死
- [ ] 金额 —— 走 `settings.SHOP_PRODUCT_AMOUNT`，单位是**分**
- [ ] 魔法数字 —— 提成常量或写清来源（例如限流窗口、TTL）

### 3. 死代码
- [ ] 没用到的 import、没用到的局部变量（测试里尤其常见：
      一个没用到的 `alice = await auth_headers(...)` 往往意味着**这个用例没在测它名字所说的东西**）
- [ ] 用不到的分支、永远为真的判断

### 4. 时间与并发（本项目踩过）
- [ ] `datetime.now()` 不带 tz —— 注意 `paid_at` 用应用本地时间、而 `create_time`
      用数据库 `func.now()`，两者混用会在跨时区部署时产生矛盾数据
- [ ] async 函数里有没有阻塞调用（`subprocess.run`、同步 IO、`time.sleep`）
- [ ] 「读-判断-写」有没有改成原子条件更新（参考 `app/routers/oauth.py` 的
      `rowcount` 写法，TD 里有记录）

### 5. 测试
- [ ] `.venv/bin/python -m pytest -q` → **208 passed, 1 skipped**
- [ ] 改了表结构 → 真库那一遍也要跑（**209 passed**，配方见 `HANDOVER.md` §9）
- [ ] 修 bug → 补了一个能复现该 bug 的回归测试
- [ ] **关键逻辑做过变异测试**：把实现改坏 → 确认对应用例变红 → 改回来。
      记录格式：`去掉 X → N 个用例红`。
      抓不到的变异要如实写成「等价变异，不可捕获」，**不得当成已覆盖**

### 6. 不该进仓库的东西
- [ ] `.env`、`storage/`、`__pycache__/`、`.venv/`、临时脚本（`/tmp/*.py` 不要挪进仓库）
- [ ] `git status --porcelain` 逐行看一遍，确认没有意料之外的文件

### 7. 文档同步
- [ ] 新取舍 → `TECH_DECISIONS.md` 加 TD-xx（含放弃了什么 / 代价 / 何时回头改）
- [ ] TD 总数变了 → `AGENTS.md` 与 `HANDOVER.md` 里的计数一起改
- [ ] 新踩的坑 → `HANDOVER.md` §6
- [ ] 完成/未完成状态 → `HANDOVER.md` §7 与 `ROADMAP.md`

## 提交

```bash
git status --porcelain                      # 再确认一遍
git add -A
git commit -F /tmp/msg.txt                  # 用 -F，不要用带引号的 -m
git push origin arena/01a0599b-codemax-platform
git ls-remote origin refs/heads/arena/01a0599b-codemax-platform   # 确认远端 tip
```

⚠️ 改动若涉及 `.github/workflows/**`：**先别提交**。本会话的 GitHub App 无
Workflows 写权限，这类提交一旦进了分支历史，**之后每一次 push 都会被拒**；
只能以 `git format-patch` 导出、放到 `docs/patches/` 交给用户 `git am`（见 TD-84）。

## 收尾报告（提交后必须给出）

```
改了：<文件与要点>
测试：SQLite <N> passed + <N> skipped；真库 <N> passed
变异测试：<去掉 X → N 个红> ×若干
提交：<short sha>，已推送，远端 tip 已确认
未核实：<跑不了的、以及为什么>
```
