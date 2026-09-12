# GitHub Actions 持续集成

## 模块职责

`ci.yml` 是当前唯一工作流，push 与 pull_request 触发。同一 ref 的新运行可取消旧运行；取消不是通过。
判断交付时检查 headSha、整体 conclusion 与每个 job，而不是只看分支上最近一条绿色记录。

## 文件与入口

| Job | 做什么 | 边界 |
| --- | --- | --- |
| `lint` | 按 requirements 中的 Ruff 版本静态检查 | 不运行程序，也不证明注释正确 |
| `audit` | 安装 Python 依赖，pip-audit 严格扫描 | 保留既有 ecdsa 上游无修复版本例外，不是零安全通报承诺 |
| `frontend` | npm ci、构建、检查提交产物是否漂移、npm audit | 必须提交全部分块；不改上游模板字符串去消掉行尾空白；不是视觉 E2E |
| `docs` | 构建离线站并校验数据/页面规模 | 构建入口执行 README 契约和本地链接检查；人工语义不能自动验收 |
| `test-sqlite` | 全量 pytest 默认后端 | 有 PostgreSQL 专用 skip，不能据此证明数据库并发 |
| `test-postgres` | PostgreSQL 16 服务、专门初始化检查库、全量测试库 | full_init 两遍只在可丢弃库执行；迁移保留数据另有回归 |

pytest 管道启用 pipefail，避免 tee 成功掩盖测试失败。失败评论是辅助取证；日志不可下载或无 PR 可评论时，仍以实际 job 状态为准。
contents:read 用于 checkout；pull-requests:write 用于失败评论。权限可用性和网络条件是环境事实，不把过去某次连接成功当成永久保证。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`.github/workflows/ci.yml`](ci.yml) | `38969a98f87b` | L1–L306 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

checkout 的源码 → 安装锁定依赖 → 检查/构建/测试 → 对应提交的运行结果。不要提交本地凭据、临时数据库或未请求的演示资产。
相同 SHA 可能有 push 与 PR 两种运行，应区分取消、失败与成功。任何例外都须有明确范围与原因，不得关掉整个扫描任务来掩盖失败。

## 变更与验证

使用 `gh run list` 找当前分支和 SHA，`gh run view` 看 job 与步骤；需要时 `gh run watch` 等待结束。认证失败应在 Arena 重新连接 GitHub，不索要令牌。
改工作流时同步核对本表，不手写固定步骤数/秒数。完整本地命令见根 README 和 tests/README.md。
