# 待仓库管理员应用的 CI 补丁

这个补丁改的是 `.github/workflows/ci.yml`。**本会话推不上去**：
`arena-ai-coding-agent[bot]` 没有 Workflows 写权限，push 会被
`remote rejected ... without 'workflows' permission` 拒绝（TD-84 / TD-144）。
而且只要分支历史里带着 workflow 改动，**之后每一次 push 都会被拒**，
所以这类改动只能以补丁形式交接。

## 在 Windows（cmd.exe）上应用

```cmd
cd /d C:\你的路径\codemax_platform
git am docs\patches\0001-ci-lint-job-ruff-check.patch
git push origin arena/01a0599b-codemax-platform
```

> 注 1：cmd **不展开** `*` 通配符，所以要写完整文件名，不要写 `0001-*.patch`。
> 注 2：`git am` 需要本地已配置身份，否则报 `fatal: empty ident name ... not allowed`：
> ```cmd
> git config user.name "你的名字"
> git config user.email "你的邮箱"
> ```

## 这个补丁做什么

给 CI 加第三个 job `lint`，每次 push / PR 跑 `ruff check .`（规则集见 `ruff.toml`，TD-145）。

- 只装 ruff，不装整个 `requirements.txt`（里面有 pgserver，自带 PostgreSQL 二进制，体积大，lint 用不上）
- 版本用 `grep -oE '^ruff==[0-9.]+' requirements.txt` 从 `requirements.txt` 取，避免两处版本号漂移
- 已在本地验证：YAML 可解析、三个 job（lint / test-sqlite / test-postgres）、
  `ruff check .` 通过。**但 v7 actions 与新 job 在 GitHub 上的实跑仍需 push 后由 CI 确认。**

## 应用之后

```cmd
git rm -r docs\patches
git commit -m "chore: 删除已应用的 CI 补丁目录"
git push origin arena/01a0599b-codemax-platform
```

（或者告诉我，我来删——删普通目录我有权限。）
