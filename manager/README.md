# 项目管理入口

## 职责与事实源

这里承接阶段证据和可复用经验，不另立一套全局当前状态。

- [AGENTS](../AGENTS.md)：最高优先级的仓库硬边界。
- [HANDOVER](../HANDOVER.md)：全局导航、恢复与当前阶段入口；相当于参考项目 CONTEXT 的职责。
- [ROADMAP](../ROADMAP.md)：保留历史路线与阶段，不把历史勾选当本轮验收结果。
- [本仓库工作流](SKILL.md)：可阅读的逐字分发副本；编辑源是 `.claude/skills/codemax-workflow/SKILL.md`。
- [阶段目录](stages/README.md)：任务范围、证据与下一步。
- [经验与技能进化](experience.md)：可复用教训及规则版本变更。

## 维护规则

借鉴 web_agent v13 的元复盘闭环，不照搬它的隐私收集、业务约束或验收结论。不创建重复 CONTEXT、agents、模板堆或空阶段。新增阶段应在此目录导航并在 HANDOVER 更新当前入口；已关闭阶段保留历史证据。

阶段关闭/已解决 P1 时复盘；有理由才修改可访问的 Skill 源、升版本、同步副本，并留进化记录。`tests/test_docs_site.py` 验证源/副本一致及现行提交示例不回退到危险旧命令；文档站注册/本地链接门禁覆盖这些 Markdown。平台是否自动加载 Skill 不在此承诺范围内。

第六批历史状态与证据集中[退款凭证与订单下载权益](../review/RELEASE_BLOCKERS_PHASE6.md)。

当前第十三批范围、证据与后续集中[系统自动登记授权与审计](../review/RELEASE_BLOCKERS_PHASE13.md)，元复盘见experience；不复制竞争测试台账。
