# 可复用经验与技能进化

## 元管理：现行 Skill 不应引用已失效的收尾环境

- 日期：2026-09-13；标签：交接、Skill、CI、文档漂移。
- 场景：本轮审阅旧 finish-subitem/pre-commit-review，发现旧分支、固定 PR/测试数量、HANDOVER 旧章节以及只看两个 CI job 的示例；其中 finish-subitem 还建议 git add -A。
- 根因：Skill 留在旧项目阶段，而当前导航和门禁已经变化；只在 AGENTS 声明优先级不足以消除旧指令被再次执行的风险。
- 处理：直接修复可访问的 Skill 源，收尾改成选择性暂存、当前固定分支、最终 SHA 全部 jobs；状态集中阶段文件，经验集中本页，不在多个入口重复固定计数。
- 预防：阶段收尾同时审阅流程本身；源与分发副本做逐字检查，提交代码块做旧危险示例回归检查。历史文字保留不是继续执行旧命令的授权。
- 证据：[本轮阶段](stages/windows-acceptance.md)、`tests/test_docs_site.py`。

## 外部依赖：网页可达与进程 API 联调必须拆分

- 日期：2026-09-13；标签：TLS、模型、授权、验收。
- 场景：用户授权远端用所给 key；网页抓取能见到 API 的 token-required 响应，而 Python/httpx 和 curl 在 TLS 阶段断开。
- 根因边界：尚未定位具体网络设备/服务方策略，不能断言是哪一方故障。它发生在 HTTP 前，不能判 key 权限，更不能用营销页上的名称替代模型 ID。
- 处理：带凭证请求只发到用户指定的 HTTPS 提供方；诊断再用不带凭证的请求。记录网络异常、协议层与未完成能力；不绕证书、不把密钥发到第三方代理、不循环消耗额度。
- 预防：显式探测工具拆开 models/chat/mermaid/embeddings，复用真实 LLMClient；默认测试只用 MockTransport。向量返回有效还须另测 FAQ 标定，Mermaid 前缀有效还须浏览器渲染。
- 证据：[本轮阶段](stages/windows-acceptance.md)、[新手指南](../Windows新手逐步验收.md)、`tests/test_probe_llm.py`。

## 技能进化

### 2026-09-13：本仓库工作流 → v2.0

- 触发：此次交接/收尾规则审查发现实质性陈旧指令；不是仅因参考版本提高就复制升级。
- 参考：web_agent project-manager v13，固定提交 `4d518c1df1f1f2791235a8d49f64807b112af9fd`。采用阶段关闭/已解决 P1 的元复盘机制，而非自动模型学习。
- 旧版：本仓库三个 Skill 未显式版本化；新版统一声明 v2.0，版本号只属于本仓库。
- 权威源：`.claude/skills/codemax-workflow/SKILL.md`；同步副本：`manager/SKILL.md`；配套修订：finish-subitem、pre-commit-review。
- 行为变化：单一事实源映射、阶段/经验按需生长、授权外部服务分层证据、严守数据边界、元复盘→必要源修改→版本→同步→日志。
- 验证：源/副本逐字及提交示例回归见 `tests/test_docs_site.py`；本轮实际运行结果只记阶段页。
- 未做：不声称修改不可访问的全局 Skill 源/安装；不复制上游私密值管理方案；不覆盖 AGENTS 硬边界。
- 回看条件：出现新的流程诱发返工、阶段结构失控或多处状态冲突时再评估；无规则缺陷时记录“无需改 Skill”，不强制无意义版本上涨。
