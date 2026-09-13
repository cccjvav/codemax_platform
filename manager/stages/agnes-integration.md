# Agnes 默认接入与网络诊断

日期：2026-09-13。用户明确选择暂用 Agnes AI，并授权定位、修复和更新项目配置。

## 实现与文档

- Settings/LLMClient/.env.example 默认基址 Agnes、模型 agnes-2.5-flash，显式 stream=false；旧私有配置需按 [接入说明](../../docs/AGNES_AI.md) 更新，不写入真实 key。
- LLM_EMBED_ENABLED=false，关闭时预热不请求、查询不使用语义缓存；空模型在请求前拒绝。原语义/并发/标定测试保留并明确 opt-in。
- 错误区分网络/HTTP/响应/配置，保留状态而隐藏上游正文；models 不再作为已知模型 chat 的前提。
- 根新手指南、深入文档、目录 README 和源码讲解同步；历史报告中的旧模型事实不批量改写。

## 网络证据与尚未解决的部分

- Arena：DNS 与公共 Google DNS 均为 104.18.18.62 / 104.18.19.62；TCP 443 成功，发出 TLS ClientHello 后断开。curl SSL_ERROR_SYSCALL/35，Python connectivity 为 ConnectError，没有 HTTP 响应。
- 同环境 api.github.com 返回 HTTP/2 200；Agnes 的 wiki/platform 域名也无法通过此运行时 curl 建立 HTTPS。不是模型字段或业务数据库导致，尚未定位实际断开的网关/出口设备。
- GitHub runner 对照已执行：[run 34767857601](https://github.com/cccjvav/codemax_platform/actions/runs/34767857601)，代码提交 `4b221ebfa9c528531a3b834029449cd3ec1ead19`。curl 得到 HTTP=401、curl_exit=0，随后同一提交/锁定依赖的 Python connectivity 步骤 success；无 key、保留 TLS 校验。`live-chat` 为 skipped，不能算已鉴权。后续仅拓展调度入口，HTTP/Python实现未变。
- 结论：已获得可用的远端诊断/后续测试路径，问题收敛到 Arena 直连环境的 TLS/网络路径差异，不是项目 JSON 或默认模型引起这次握手断开；还没定位具体网关策略，也未修复 Arena 直连。
- 鉴权与真实模型：恢复后的沙箱没有保存原 key。不会伪造已验证状态，也不要求换 key；读取仓库 Secret 元数据返回 HTTP 403 Resource not accessible by integration，不能确认它是否已配置；这不是 GitHub 登录失效或 Agnes 拒绝 key 的证据。调度 Actions API 同样返回 403；已增加用户授权的显式标记提交入口，利用可用的固定分支 push 触发，普通提交不发真实请求。由用户在私有设置确认/保存原 key 后继续，不索要 GitHub token。
- FAQ 向量/标定：提供方公开文档尚未确认，默认禁用，不作为聊天不可用的理由。

## 验证与交付

- 本地 SQLite 全量：832 passed、6 skipped，300.99 秒，退出 0。
- 可丢弃 PostgreSQL 16.2、非超级用户角色：837 passed、1 skipped，333.71 秒，退出 0，临时集群已清理。仅真实向量标定跳过。
- Agnes 新回归 28 项；与探测/文档/精读/README 合并定向 125 passed。Ruff、pip check、Vite/产物漂移、文档完整构建及 data-only 通过。
- 140 文件/1489 段解释，零待补；41 文档页、244 源码页，不作为语义或真机认证。
- 内存变异：去掉关闭向量保护→1 failed；错误正文回显→7 failed；connectivity 保留 key→5 failed。原实现不落盘改坏，恢复/原实现定向通过。
- 网络工作流和最终提交六项 CI 需绑定各自实际 headSha；最终消息给出结果/链接，不继承旧提交绿色状态。
- Linux/Node/合成向量并不代替 Windows、Mermaid 浏览器或真实账号测试。

## 元复盘与后续

本轮教训：网页通道可达不等于运行时 TLS 可达；应把网络诊断和鉴权调用分开，并用手动私有 secret 入口解决凭据无法跨环境恢复的问题，而不是将 key 写进仓库。既有工作流已要求分层证据，此次无需再为此升级 Skill 版本。分环境对照已确认 GitHub runner 可达；下一步是通过私有 Secret 提供原 key，在该可达路径做真实聊天/Mermaid；向量增强待官方支持证据。
