# GitHub Actions 持续集成

## 模块职责

`ci.yml` 是当前常规六项 CI 工作流，push 与 pull_request 触发。同一 ref 的新运行可取消旧运行；取消不是通过。
判断交付时检查 headSha、整体 conclusion 与每个 job，而不是只看分支上最近一条绿色记录。

## 文件与入口

`agnes-connectivity.yml` 是独立网络对照，不改变六项 CI：固定分支修改本工作流时仅运行无凭证 curl，保留 TLS 校验、不跟重定向、记录 HTTP 与退出码；失败非零。curl 可达后用同一提交、Python3.11及锁定依赖复验无 key 连通性；只有 workflow_dispatch 显式 live_chat=true 或修改工作流的提交说明带 [agnes-live-test] 标记时才执行四项有界的带凭证探测（列表、聊天、Mermaid、一次探索性向量调用），AGNES_API_KEY 只在最后一步注入，不传给安装阶段。此 job 的跳过不是真实模型通过。

| Job | 做什么 | 边界 |
| --- | --- | --- |
| `lint` | 按 requirements 中的 Ruff 版本静态检查 | 不运行程序，也不证明注释正确 |
| `audit` | 安装 Python 依赖，pip-audit 严格扫描 | 保留既有 ecdsa 上游无修复版本例外，不是零安全通报承诺 |
| `frontend` | npm ci、构建、检查提交产物是否漂移、npm audit | 必须提交全部分块；不改上游模板字符串去消掉行尾空白；不是视觉 E2E |
| `docs` | 构建离线站并校验数据/页面规模 | 构建入口执行 README 契约和本地链接检查；人工语义不能自动验收 |
| `test-sqlite` | 全量 pytest 默认后端 | 有 PostgreSQL 专用 skip，不能据此证明数据库并发 |
| `test-postgres` | PostgreSQL 16 服务、专门初始化检查库、全量测试库 | 独立空库运行init/status，第二次init必须拒绝；另验维护锁、账本回滚与旧数据保留 |

pytest 管道启用 pipefail，避免 tee 成功掩盖测试失败。失败评论是辅助取证；日志不可下载或无 PR 可评论时，仍以实际 job 状态为准。
contents:read 用于 checkout；pull-requests:write 用于失败评论。权限可用性和网络条件是环境事实，不把过去某次连接成功当成永久保证。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`.github/workflows/agnes-connectivity.yml`](agnes-connectivity.yml) | `efbbecbc14c4` | L1–L88 |
| [`.github/workflows/ci.yml`](ci.yml) | `f56faf645017` | L1–L335 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

checkout 的源码 → 安装锁定依赖 → 检查/构建/测试 → 对应提交的运行结果。不要提交本地凭据、临时数据库或未请求的演示资产。
相同 SHA 可能有 push 与 PR 两种运行，应区分取消、失败与成功。任何例外都须有明确范围与原因，不得关掉整个扫描任务来掩盖失败。

## 变更与验证

使用 `gh run list` 找当前分支和 SHA，`gh run view` 看 job 与步骤；需要时 `gh run watch` 等待结束。认证失败应在 Arena 重新连接 GitHub，不索要令牌。
改工作流时同步核对本表，不手写固定步骤数/秒数。完整本地命令见根 README 和 tests/README.md。

本次用户确认 Secret 后，授权 job 还会读取一次模型列表、对已知 Agnes 2.5 Flash 做一次 embedding 兼容性探索；仅该子进程暂开增强，不改变应用默认。各退出码用 notice 分别记录，chat/Mermaid 为 job 成功条件；列表或探索失败不会伪装成通过。

远端日志下载受限时，run_probe 把现有探测脚本已脱敏的摘要/模型 ID 转为有界、转义后的 notice，保留 HTTP 错误分类供 API 取证；不发布提供方原始正文，也不改变检查的退出码。

## 2026-09-15 交叉审查增量

第一批历史（f7e1cdc）：frontend开始执行 npm audit --audit-level=high 和手写JS的 node --check；六job各设20分钟超时，checkout不保留Git凭据。full_init连续执行仅证明可重复重建，绝不是无损迁移幂等。当前仍有PR评论写权限/可变action版本等待办，见审查台账。

第二批：PG job不再重复执行破坏性SQL，改测维护CLI和拒绝覆盖；tests/test_db_admin另外自建非超级用户的一次性PG，不使用业务DSN。真实商户、浏览器与Docker启动仍不由这六job证明。

## 第十二批：全量测试预算按实测调整

运行35396917535的PG检查注解明确为“exceeded the maximum execution time of 20m0s”；SQLite虽通过也用19分48秒，PG取消不是测试通过，也不推断为断言失败。仅两套完整测试job改35分钟，四个其他job仍20分钟。pytest仍全量、pipefail保留，未增skip/continue-on-error、未删测试或弱化断言。test_docs_site回归核对范围/预算和失败传播；最终提交须重新等待全部六项CI，不能沿用取消运行。日志下载EOF时可通过check-run annotations取得取消原因，不归咎GitHub鉴权。
