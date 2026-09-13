# Windows 新手验收与管理适配

日期：2026-09-13。承接已完成的全量精读交付，不重做上一轮源码讲解。当前全局导航仍在 [HANDOVER](../../HANDOVER.md)。

## 范围与交付

- [Windows新手逐步验收.md](../../Windows新手逐步验收.md)：VS Code 集成 CMD＋Conda＋系统 Node 的连续路线；独立演示库/测试库、建库失败停止、模拟支付、私人图、站内留言、模型与退出重启。
- `scripts/probe_llm.py`：显式单次 models/chat/mermaid/embeddings 探测，复用项目 Settings/LLMClient；HTTPS、禁止带凭证/查询的基址、显式模型/提供方选择、不回显响应和异常正文。密钥只在私有忽略配置/环境中。
- [管理工作流](../SKILL.md)、[经验](../experience.md)：映射现有 HANDOVER/ROADMAP，修正旧 Skill，增加元复盘，不新建竞争状态源。
- 扩大健康检查至全部现行 Skill，另修 schema-sync/new-tool-page 旧示例和通用测试约定；不把签名/docstring 当测试依据。
- 文档站导航、目录 README、受影响源码的连续段界与指纹同步；原 Windows/Conda/分层验收指南保留为深入参考。

## 实际远端提供方探测

用户已明确授权所给 key 用于远端小量测试；**不以先换 key 为前提**。未将 key 存入仓库/报告/持久环境；未发送项目源码或客户资料。

目标基址：`https://apihub.agnes-ai.com/v1`。

| 层/能力 | 实际动作与结果 | 可得结论 |
| --- | --- | --- |
| 网页抓取通道 | 不带凭证访问 models 得到 token-required JSON | 仅该抓取通道到达了端点；未验证鉴权 |
| Python 运行时 TLS | 授权后的 httpx GET `/models`，ConnectError，TLS/SSL EOF，未取得 HTTP 状态/正文 | 鉴权尚未可判，不是已证实的无效 key |
| 无凭证复核 | curl HEAD 默认 TLS、强制 TLS1.2、IPv4＋HTTP1.1 均报 SSL_ERROR_SYSCALL/35 | 更换本地 HTTP 库/协议选项未解决此运行时连接问题 |
| 对照 | 同运行时 curl 访问 api.github.com 得到 HTTP/2 200；代理环境变量未配置 | 不是所有 HTTPS 都失败；具体断开方尚未确定 |
| 鉴权/模型列表 | 没有成功的授权后 HTTP 响应，没有真实模型清单 | 未完成；不使用营销名称充当支持证明 |
| 聊天与 Mermaid | 前置连接/模型 ID 未确认，未继续发送猜测模型请求 | 未执行；离线 MockTransport 结果不替代它 |
| embedding | 同上，且尚无确认可用的向量模型 ID | 未执行；不判提供方一定不支持 |
| FAQ 阈值标定 | 前置 embedding 未通过 | 未执行；保持原配置，不为通过而降阈值 |

停止继续盲重试；没有关闭 TLS 验证、透传密钥到其他代理或打印凭证。后续在可正常连通该提供方的运行时，从 models 开始再按聊天、Mermaid、embedding、标定逐层执行。新手指南提供可重复命令，但**写出步骤不等于已替用户做完真实联调**。

## 验证记录

以下为本轮实际运行结果，不沿用上一提交的绿色结果。

- 探测工具定向离线测试：28 passed；均使用虚构凭证和 MockTransport，不代表真实提供方兼容。
- SQLite 完整回归：804 passed、6 skipped，337.47 秒；跳过为五个真 PG 专属场景和真实 embedding 标定。
- 文档/精读/README/探测定向回归：97 passed；Ruff、pip check、Vite 构建与 bundle 漂移检查通过。
- 文档完整构建与 data-only 均通过：39 文档页、242 源码页；138 文件/1473 段连续讲解，零待补（不是语义认证）。
- 受控内存变异：允许 models 跟重定向、打印异常正文、模拟 Skill 副本漂移，分别导致对应测试 1 failed；没有修改磁盘实现，全量使用原实现。
- PostgreSQL 全量：809 passed、1 skipped，372.35 秒，退出码 0；独立无超级权限测试角色（rolsuper=false）与可丢弃 PostgreSQL 16.2 集群，已清理。仅真实 embedding 标定跳过。
- 后续全 Skill 审阅仅改变管理文档与命令门禁覆盖范围，97 项定向回归重跑通过；注入 new-tool-page 固定 passed 例子的内存变异亦被拒绝（1 failed）。最新提交全量由精确 SHA CI 复验。
- CI 权威证据是交付提交的 GitHub Actions 全部 jobs；最终交付消息提供精确 SHA/run 链接，不能用历史绿色替代，也不把自身提交哈希写入参与生成该哈希的本文。
- Windows/VS Code/Conda、桌面 Word、浏览器 Drawio 与真人多账号流程：本 Linux 沙箱未执行，指南中的证据表保持未执行。
- 真实支付/公网部署/云存储：不在本轮免费本地演示验收范围，没有新增已接通声明。

## 元复盘与下一步

- [x] 复核参考 v13 与本仓库管理冲突；用现有 HANDOVER 承接 CONTEXT 职责，不另建全局状态。
- [x] 发现旧 Skill 实质性缺陷，修权威源至本仓库 v2.1，同步可访问副本；记录[技能进化](../experience.md)。
- [x] 完成本轮所有本地门禁与受控反例；推送后的精确 SHA/全部 CI jobs 由最终交付消息关联外部记录。
- [ ] 在可连通目标提供方的运行时完成真实模型分层联调；执行者需可读取私有配置，不能靠公开文档传 key。
- [ ] 用户按新手指南完成本机人工项目，填写仓库外证据表；通过/失败/未执行分别保留。

当前收尾结论：管理/工具/指南交付与外部实际验收分开；后两项不因前者完成而自动勾选。网络受阻没有足够证据归因于用户网络或 GitHub 认证，不再要求无依据的重授权。
