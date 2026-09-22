---
name: finish-subitem
description: 子任务交付、精确提交 CI 验证与阶段元复盘；不使用历史固定测试数量或章节号。
version: 2.0
---

# 子任务收尾

先服从根 AGENTS；权威工作流见 `.claude/skills/codemax-workflow/SKILL.md`。本清单不是另一份项目状态表。

## 提交前

- [ ] 逐项对照本轮需求和阶段文件；通过/失败/未执行/阻塞分别记录。
- [ ] 用实际命令输出记 Ruff、相关/全量 pytest、必要真 PostgreSQL、前端漂移、文档门禁；不拿历史固定 passed 数当断言。
- [ ] 真库必须是可丢弃测试库，步骤见 `docs/ACCEPTANCE_GUIDE.md`；Windows 见 `Windows新手逐步验收.md`。没有 HANDOVER 的历史 §9 配方。
- [ ] 改过的源码都有当前分段解释；README 语义已审阅后才刷新指纹；新 Markdown 已登记并暂存供 git ls-files 门禁检查。
- [ ] 没有真实 key/.env、日志、商品、环境目录、无关作品或鹈鹕；选择性暂存，不用 git add -A。
- [ ] 如阶段关闭或已解决 P1，按权威工作流执行元复盘，更新经验；需要演化才改源、升版本和同步副本。

## 提交、推送、核对（Linux 沙箱示例）

下列占位符先换成实际审核文件/提交说明路径，不整段盲贴。

```bash
git branch --show-current
git status --short
git diff --check
git add <本次逐项审核的文件路径>
git diff --cached --stat
git diff --cached
git commit -F <仓库外的提交说明文件>
git push origin arena/01a0bf7a-codemax-platform
git rev-parse HEAD
git ls-remote origin refs/heads/arena/01a0bf7a-codemax-platform
```

必须在本固定分支；远端 tip 等于本地 SHA。认证失败请用户在 Arena 重连 GitHub，不索要凭证。

## 当前 SHA 的 CI

```bash
gh run list --branch arena/01a0bf7a-codemax-platform --limit 20 --json databaseId,headSha,status,conclusion,url
```

选 `headSha` **完全等于本地完整 SHA** 的 CI run；未出现则等待，不能拿上次绿色交差。

```bash
gh run watch <实际run_id> --exit-status
gh run view <实际run_id> --json headSha,status,conclusion,jobs,url
```

检查工作流要求的**全部 jobs**，不限于旧 Skill 的两个 job。失败则读对应日志修复，生成新提交后重新绑定新 SHA；cancelled/排队/运行中/取不到证据都不是 success。不擅自合并 PR，也不引用写死的历史 PR 编号。

## 面向用户的交付

先说交付文件和打开方法，再列真实验证结果、提交/CI 链接、仍未执行的环境与下一步。Linux pytest 或 Node VM 通过不代表 Windows/Conda/浏览器/商户/外部模型已验收。
