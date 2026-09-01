# 待仓库管理员应用的 CI 补丁

这两个补丁改的是 `.github/workflows/ci.yml`。**本会话推不上去**：
`arena-ai-coding-agent[bot]` 没有 Workflows 写权限，push 会被
`remote rejected ... without 'workflows' permission` 拒绝（TD-84 / TD-144）。
所以只能以补丁形式交接。

```bash
git am docs/patches/000*.patch          # 两个都应用
git push
```

若只想应用其中一个：

```bash
git am docs/patches/0001-*.patch        # 仅修注释头（纯注释，零风险）
# 或
git am docs/patches/0002-*.patch        # 仅升 actions 到 v7（消 Node 20 告警）
```

- `0001`：`.github/workflows/ci.yml` 里还留着「待激活 / 从未在 GitHub Actions
  上真正跑过」的注释头 —— 那是 CI 激活**之前**写的，`2f3223a` 纯重命名时把它
  一起搬了进来，现在是错的。改为已激活 + 实跑 run 编号 + 未核实项说明。
- `0002`：`actions/checkout@v4`→`@v7`、`actions/setup-python@v5`→`@v7`。
  沙箱不跑 Actions，v7 的行为**未经本地验证**，只能靠 push 后 CI 实跑确认；
  单独成一个提交就是为了 CI 变红时可只回滚它。

**应用并推送后请删掉本目录**（与 `PHASE1_TRANSFER.txt` 等一次性传输文件同理）。
