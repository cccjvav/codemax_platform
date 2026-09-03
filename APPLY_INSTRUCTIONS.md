> ⚠️ **本文件是一次性传输文档，内容已过期，仅作历史留存。**
> 它是早期会话之间搬运补丁用的，描述的是**当时**的仓库状态（测试条数、待办顺序等都已失效）。
> **不要照它操作。** 现在的事实来源是：`AGENTS.md`（命令与规矩）、`HANDOVER.md`（现状与下一步）、
> `ROADMAP.md`（进度）、`TECH_DECISIONS.md`（每条取舍的编号与代价）。
> 合并 PR #3 后本文件会被删除。

# 阶段一补丁应用说明（给新会话助手）

## 文件清单
- `PHASE1_TRANSFER.patch` —— 合并补丁（3 个提交合成一个，推荐）
- `0001-...patch` `0002-...patch` `0003-...patch` —— 分片补丁（备用）

## 应用步骤（新会话里执行）
```bash
# 1. 把补丁放到沙箱后：
git config user.name "cccjvav"
git config user.email "66018270+cccjvav@users.noreply.github.com"

# 2. 应用合并补丁
git am PHASE1_TRANSFER.patch
# 或应用分片补丁：
# git am 0001-*.patch 0002-*.patch 0003-*.patch

# 3. 验证（应看到 3 个新提交，且出现 app/ tests/ 目录）
git log --oneline -4
git ls-tree -r --name-only HEAD | grep '^app/'

# 4. 装依赖 + 跑测试（16 个应全绿）
pip install -r requirements.txt
python -m pytest -q

# 5. 推送 + 开 PR 合入 main
git push origin <你的分支名>
gh pr create --base main --head <你的分支名> \
  --title "feat: 阶段一（多模块工程 + JWT 认证 + OAuth2 授权码 SSO）" \
  --body "见 HANDOVER.md"
```

## 防误判检查
若助手说"已合并/无差别"，让它跑：
```bash
git log origin/main..HEAD --oneline
```
输出为空 = 补丁未应用（先执行上面步骤）；输出 3 条提交 = 已就位，直接推送。

## 验证恢复成功
```bash
git ls-tree -r --name-only HEAD | grep '^app/'   # 应有 app/ 全部文件
python -m pytest -q                              # 16 passed
```
