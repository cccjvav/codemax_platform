# Agnes AI 接入与连接排错

本项目已按用户选择默认使用 **Agnes AI / agnes-2.5-flash**。这是配置和协议适配，不把文档相符或 MockTransport 通过等同于真实账号联调成功。

## 1. 现行配置：已有 .env 不会自动被覆盖

在实际启动目录的私有 `.env` 修改同名行，不重复追加；保留数据库和商品配置：

```dotenv
LLM_BASE_URL=https://apihub.agnes-ai.com/v1
LLM_MODEL=agnes-2.5-flash
LLM_API_KEY=在本机填写你原来的密钥
LLM_EMBED_ENABLED=false
LLM_EMBED_MODEL=
```

不要求换 key。上面是占位符，不要把真实值提交到仓库、聊天日志或前端。修改后停止并重启 Python 进程。环境变量优先于 `.env`，Windows 新手按[逐步指南](../Windows新手逐步验收.md)清除当前终端里可能继承的同名变量。

旧环境常见陷阱：

- 只换 key 而保留 `https://api.openai.com/v1`，请求会发给错误提供方。
- 只换基址而保留旧默认 `gpt-4o-mini`，不是本项目选定的 Agnes 模型。
- 基址重复写 `/v1` 或末尾再加 `/chat/completions`，客户端会拼错端点。
- 把聊天模型名填进 embedding，不能获得有效向量。

## 2. 为什么协议相符

官方[快速开始](https://wiki.agnes-ai.com/en/docs/quickstart)和[2.5 Flash 文档](https://wiki.agnes-ai.com/en/docs/agnes-25-flash)给出：

- `POST https://apihub.agnes-ai.com/v1/chat/completions`
- `Authorization: Bearer ...`、JSON 请求。
- `model`、`messages`，支持 system/user 和 temperature。
- 非流式返回 `choices[0].message.content`。

`app/tools/llm.py` 使用同一格式，显式 `stream=false`，不增加 Responses/Messages 的第二套解析器，不自动切到付费 Pro、不自动重试。Mermaid 复用聊天客户端并检查图类型前缀；浏览器语法/渲染仍需另外验。

模型 ID 已有官方依据，因此 **models 列表不是聊天的前置条件**。`/models` 不支持或权限不同，不代表已知模型的 `/chat/completions` 一定不能用。

官方[价格页](https://wiki.agnes-ai.com/en/docs/pricing)列出 2.5 Flash 当前价格为零；[配额说明](https://wiki.agnes-ai.com/zh-Hans/docs/tokenplan)仍有限频规则。免费不等于无限并发或 SLA，以账号控制台与实际响应为准，不把历史宣传承诺当永久价格保证。

## 3. embedding 默认真正关闭

目前查阅的官方目录/文本模型文档没有确认 `/embeddings` 或可用向量模型 ID。不能据此宣布永不支持，也不能先填 OpenAI 的向量模型来凑配置。

当前实现：

- `LLM_EMBED_ENABLED=false`：FAQ 预热直接返回，不发向量请求；查询也不使用语义缓存，词袋 FAQ/规则/聊天继续工作。
- 即使聊天 key 有值，也不会自动开启 embedding。
- 空 `LLM_EMBED_MODEL` 在客户端发请求前失败，不把空 ID 送给提供方。
- 开关关闭时清理预热缓存；已有语义结果不会绕过查询端开关继续被使用。
- 真正的向量/阈值测试保留，默认使用合成响应；真实标定要求显式开启、key 和向量模型全部配置。

将来提供方确实给出兼容向量模型时，再填写 `LLM_EMBED_MODEL`、设 `LLM_EMBED_ENABLED=true`、执行 `probe_llm.py embeddings` 和真实 FAQ 标定。当前客户端仍共用 LLM_BASE_URL/LLM_API_KEY；不能只填写另一家服务的模型 ID 就认为跨服务配置完成。若需要其他向量提供方，应另行增加独立客户端配置，不偷换聊天提供方。

## 4. 分层诊断命令（Windows VS Code CMD）

先激活 Conda、进入应用目录并设置 UTF-8：

```cmd
conda activate codemax
set PYTHONUTF8=1
cd /d C:\work\codemax_platform
python scripts/probe_llm.py connectivity
```

该模式可在没有 key 时运行；即使私有配置有 key，也不发送 Authorization。得到 `HTTP REACHED: 401` 等，只表示 TLS/HTTP 已可达；不是模型或鉴权通过。404/503 同样只表示到达了 HTTP 层。

然后在有真实私有 key 的环境逐项执行，观察每项退出码：

```cmd
python scripts/probe_llm.py chat
echo %ERRORLEVEL%
python scripts/probe_llm.py mermaid
echo %ERRORLEVEL%
```

不先调用 models、不默认调用 embedding；两条命令只发送固定非敏感示例。错误保留类型/类别和 HTTP 状态，不回显服务端正文，避免 key 被意外带回日志。

| 结果 | 含义和下一步 |
| --- | --- |
| `ConnectError` / `network (ConnectError)` / TLS EOF | 未取得 HTTP；核对该运行环境出口/网关，不换模型或重建数据库解决 |
| `http HTTP 401` | 到达鉴权层，核对 key 是否完整、有效；不输出 key |
| `http HTTP 402/403` | 核对余额/权限/key 类型，403 也可能涉及安全规则，不能只凭码断言原因 |
| `http HTTP 404` | 核对端点与模型，特别是重复 `/v1`；models 与 chat 分开判 |
| `http HTTP 429` | 降低频率，核对配额；不切换多个 key 叠额度 |
| `http HTTP 5xx` | 服务端/上游问题，人工稍后重试，不无限循环 |
| `response` | 收到的结果不符合非空文本/向量/图类型合同，不能只因 HTTP 200 就记通过 |
| `ValueError` / `configuration` | 核对显式基址/模型/key/embedding 开关；不把原始异常正文贴到公开日志 |

`connectivity` 保留 TLS 证书校验，不跟重定向；不要加 `verify=False`、`curl -k` 或把 key 发给陌生代理。

## 5. 沙箱网络对照与远端执行路径

本轮在 Arena 运行时观察到：公共 DNS 与本地 DNS 都给出相同 Cloudflare 地址；TCP 443 已连接，TLS ClientHello 后断开，未收到 HTTP。对照 api.github.com 可收到 HTTP。说明失败发生在特定连接路径的 TLS 层，而不是项目 JSON 或数据库初始化；具体断开设备/策略尚未确认。

新增独立工作流 `.github/workflows/agnes-connectivity.yml`：

1. 只有修改该工作流的固定分支 push、或手动触发才运行，不污染常规六项 CI。
2. 默认在 GitHub runner 无 key 请求同一 API，记录 HTTP 状态和 curl 退出码；随后用同一提交和锁定依赖的 Python 脚本复验，避免只对比不同 curl 程序。网络失败会失败，不把 000 伪装成可达。
3. HTTP 状态也出现在步骤名称/notice，日志下载受阻时可从 GitHub job 元数据取证。
4. 可选的真实 chat/Mermaid job **只在手动勾选 live_chat 时运行**；只在最后执行步骤注入 GitHub Secret，不给安装依赖步骤传 key。

若 Arena 出口仍受阻而 GitHub runner 可达，可以用这个远端入口继续测试，不必更换 Agnes。真实 key 没有存入本仓库或恢复后的沙箱，不能凭旧会话的授权制造一次不存在的鉴权成功。需要远端实测时，在 GitHub 项目 **Settings → Secrets and variables → Actions → New repository secret** 保存你原来的 key，名称 `AGNES_API_KEY`；这是私有凭据配置，不是更换 key。

之后由 Actions 页面选择 **Agnes connectivity diagnosis → Run workflow**，分支必须为 `arena/01a08bf5-codemax-platform`，勾选 `live_chat`。也可以让本会话通过 `gh workflow run agnes-connectivity.yml --ref arena/01a08bf5-codemax-platform -f live_chat=true` 执行。普通 push 不会使用这个 secret，不运行真实模型测试。

**当前实际对照、鉴权和 CI 结果统一见[本轮阶段记录](../manager/stages/agnes-integration.md)。** GitHub 的网络 job 成功不代表两个真实请求已跑；live-chat skipped 必须写未执行。
