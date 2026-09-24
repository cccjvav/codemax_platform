# Agnes AI 接入与连接排错

本项目已按用户选择默认使用 **Agnes AI / agnes-2.5-flash**。已用用户配置的 GitHub Secret 完成真实聊天与 Mermaid 前缀检查；向量探测没有取得有效向量。具体证据与不能推断的范围见第 5 节，不把 MockTransport、模型列表或图前缀通过等同于完整上线验收。

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
- 把聊天模型名填进 embedding，不能据此推定它具有向量能力；需要端点、模型和真实向量响应证据。

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
4. 可选的真实 chat/Mermaid job **只在手动勾选 live_chat，或修改工作流且提交说明明确带 `[agnes-live-test]` 标记时运行**；只在最后执行步骤注入 GitHub Secret，不给安装依赖步骤传 key。

### 已完成的真实调用（2026-09-13）

用户已经确认 `AGNES_API_KEY` 已设置，不需要重复配置或更换 key。两次显式授权的固定分支推送完成了探测；第二次为补充日志下载受阻时的安全 notice 取证，并非无限重试。

- 首次：[run 34769292339](https://github.com/cccjvav/codemax_platform/actions/runs/34769292339)，提交 `aeb4fb2`，四项退出码依次为 0/0/0/1。
- 完整摘要：[run 34769446500](https://github.com/cccjvav/codemax_platform/actions/runs/34769446500)，提交 `bba8ab0`。模型列表通过、聊天返回 4 字符非空文本、Mermaid 返回 287 字符且图类型前缀通过；内容不公开。这不是浏览器渲染验收。
- 对 `POST /v1/embeddings`、模型 `agnes-2.5-flash` 的探索返回 **HTTP 500**，未取得有效向量。500 只表明此请求的服务端/上游失败，不能区分暂时故障、端点路由或模型能力，更不能证明 Agnes 所有模型都不支持 embedding。
- 本次列表有 12 个 ID，没有提供可确认的向量模型：Agnes 文本、图像和视频系列。列表只是清单，不是每个模型的能力认证；不根据名字或清单缺项证明能力不存在。
- 首次无 key curl 得到 HTTP 401，第二次 HTTP 200，退出码均为 0；两次同提交的锁定 Python 客户端 connectivity 均通过。它们只证明 HTTPS 可达，状态变化不能当作 key 有效性证明。带 Secret 的聊天成功才是本轮真实调用证据。

工作流把脚本已经脱敏的摘要/模型 ID 截断并转义后发布为 notice，以便在日志下载受阻时通过 job 元数据取证。不记录 key、响应正文或向量。**live-chat 的成功条件是 chat 和 Mermaid 均通过，不能把绿色 job 当作四项全通过。** 探索性的向量开关/model 只作用于那个子进程，项目默认仍关闭。

Arena 运行时直连的 TLS 问题尚未修复，具体断开设备未知；GitHub runner 是已验证可用的替代测试路径。仓库 Secret 不会自动写进 Windows 私有 `.env`。

### 将来需要重测时

当前 GitHub 集成可推送、读取 Actions 元数据，但 workflow_dispatch API 返回权限 403。已授权的重测可由本会话修改本工作流、同步源码讲解，并以带 `[agnes-live-test]` 的明确标记提交推送到本会话的 `arena/*` 分支；每次共四项探测，普通提交不使用 Secret。空提交或只改说明文档不满足路径过滤，不应为猜测模型 ID 批量触发。

**已实测的触发陷阱（2026-09-24，`fee88eb`）**：判定用的是**整条提交说明的子串匹配**，所以在提交说明里引用 `[agnes-live-test]` 这段字面值（哪怕只是想说明这个标记本身）也会真正触发带密钥的四项探测。TD-277 的提交说明就引用了它，于是四项探测在 `arena/01a0cdc6-…` 分支上实际跑了一次：列表 12 个模型 ID、聊天与 Mermaid 通过（Mermaid 228 字符，前缀检查，不等于浏览器渲染通过）、探索性 embedding 仍是 HTTP 500。这个结果只能当「该 SHA 上提供方可用」的一次真实调用证据，不能当成 embedding 可用性的证明；后续写提交说明时不要照抄该标记。

有 Actions 调度权限时也可手动运行：`gh workflow run agnes-connectivity.yml --ref "$(git branch --show-current)" -f live_chat=true`。不需要交出 GitHub token、切换/合并默认分支或关闭证书校验。

若继续确认 Agnes embedding，向官方支持询问：是否开放 `/v1/embeddings`、确切模型 ID、当前账号权限、请求示例及费用/限额。提供脱敏时间与 HTTP 状态，不发送 key。官方目录未列出相关说明与本次 500，只能支持“尚未确认可用”，不能支持“确定不存在”。

## 6. 免费 embedding 候选与本项目建议

核对日期：2026-09-13。**聊天模型像客服负责写回答；embedding 模型像索引员，把问题转成数字向量，帮助找到意思相近的 FAQ。两者不必来自同一家。** 让聊天模型编造数字数组，不等于可靠的向量检索。

| 候选 | 真实向量能力与免费方式 | 代价/边界 |
| --- | --- | --- |
| **BGE-M3，经 Ollama 本地运行** | 官方模型支持多语言检索；Ollama ID `bge-m3`，下载约 1.2GB。本地推理不付云 API 调用费 | 占 CPU/RAM/磁盘/电力，文件大小不是运行内存；不是免费云托管服务，速度需在本机验 |
| **Qwen3-Embedding-0.6B，经 Ollama 本地运行** | 专门的多语言文本向量模型；Ollama ID `qwen3-embedding:0.6b`，当前 Q8 文件约 639MB，本地无 API 账单 | 小体积候选，不保证比 BGE-M3 在本项目更准；官方建议查询任务指令，需按查询/文档用途适配与标定 |
| **Google Gemini Embedding 2** | 官方 `gemini-embedding-2` 提供原生 `embedContent`；价格页 Standard 文本输入 Free Tier 为 Free of charge | 独立 Google key、账号/地区资格和项目额度；不是无限免费，Batch 没有该免费层；面向 EEA/瑞士/英国用户的 API 应用受付费服务条款限制 |

官方依据：

- BGE：[官方模型说明](https://huggingface.co/BAAI/bge-m3)、[Ollama 模型页](https://ollama.com/library/bge-m3)。支持 100 多种语言、原模型 dense 向量维度 1024；Ollama 接口输出范围不能从原模型的稀疏/多向量能力直接推定。
- Qwen：[官方 0.6B 模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[Ollama 对应版本](https://ollama.com/library/qwen3-embedding:0.6b)。不要把 8B 的评测成绩或维度冒充 0.6B 的效果。
- Ollama：[OpenAI 兼容文档](https://docs.ollama.com/api/openai-compatibility)明确支持 `/v1/embeddings` 的 model、字符串/字符串数组 input；也有原生 `/api/embed`。本项目只需 dense 向量，不必为此引入大型 Python 推理依赖。
- Gemini：[向量文档](https://ai.google.dev/gemini-api/docs/embeddings)、[价格表](https://ai.google.dev/gemini-api/docs/pricing#gemini-embedding-2)、[限额](https://ai.google.dev/gemini-api/docs/rate-limits)、[地区](https://ai.google.dev/gemini-api/docs/available-regions)、[条款](https://ai.google.dev/gemini-api/terms)。RPM/TPM/RPD 按项目限制，实际额度在 AI Studio 查，不能通过多 key 叠加。丹麦在地区清单中，但地区可用不等于免费上线许可。

**Gemini 的重要限制：** 当前条款要求面向欧洲经济区、瑞士或英国用户开放 API 应用时使用 Paid Services（API 项目关联有效账单账户），不能把价格表的免费栏当作免费上线承诺。免费服务一般涉及数据改进用途，但 EEA/瑞士/英国用户的数据处理有付费条款例外；不要一概说所有免费用户的数据都用于训练。学习测试只用非敏感示例，正式客户数据先核对适用条款。其 OpenAI 兼容页目前示例使用 `gemini-embedding-2-preview`，与原生文档/价格表的 `gemini-embedding-2` 不同，不能未经实测就承诺只改基址即可接入。

硅基流动另有官方 embeddings API，但本轮未确认 **当前免费**的具体托管型号/价格，国内和国际站清单也不能混用；不把旧博客中的“免费 BGE-M3”当成今天可用的承诺，暂不列为已确认免费推荐。

### 推荐路线，不等于本轮已安装

优先考虑 **Agnes 聊天 + 本地 BGE-M3 向量**；更在意文件体积可比较 Qwen3-Embedding-0.6B。两者都只是候选，尚未在你的 Windows 或此项目实际部署，不宣称 FAQ 准确率已经通过。

后续接入需把向量 base URL/key 与聊天配置分开。**现在不能把 LLM_BASE_URL 改成 Ollama 来“顺便开启向量”，那会把 Agnes 聊天也改走。** 本轮只改诊断与证据，不安装 Ollama、不增加依赖/schema、不更换聊天提供方，也没有伪造尚不存在的配置变量。

本地 Ollama 一般由 Python 后端调用同机服务；不能让浏览器访问它自己的 localhost，也不能为了远端预览把无鉴权 Ollama 端口直接暴露到公网。现有 probe CLI 强制 HTTPS，不能直接拿它测默认本机 HTTP Ollama；接入时需专门设计仅本机允许 HTTP 的边界，不全面放宽远端校验。

实际启用前要用真实中文 FAQ 正例、近义句和易混淆负例验证向量格式、相关性与阈值，再重新生成索引；现有 0.55 不是跨模型通用标准。替换模型、维度或查询预处理后不能混用旧向量。

完整执行记录与下一步统一见[阶段记录](../manager/stages/agnes-integration.md)。
