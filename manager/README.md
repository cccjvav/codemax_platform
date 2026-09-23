# 项目管理入口

## 职责与事实源

这里承接阶段证据和可复用经验，不另立一套全局当前状态。

- [AGENTS](../AGENTS.md)：最高优先级的仓库硬边界。
- [HANDOVER](../HANDOVER.md)：全局导航、恢复与当前阶段入口；相当于参考项目 CONTEXT 的职责。
- [ROADMAP](../ROADMAP.md)：唯一现行工作队列，含优先级、阶段目标、验收条件与执行环境；旧计划从 Git 追溯。
- [本仓库工作流](SKILL.md)：可阅读的逐字分发副本；编辑源是 `.claude/skills/codemax-workflow/SKILL.md`。
- [阶段目录](stages/README.md)：任务范围、证据与下一步。
- [经验与技能进化](experience.md)：可复用教训及规则版本变更。

## 维护规则

借鉴 web_agent v13 的元复盘闭环，不照搬它的隐私收集、业务约束或验收结论。不创建重复 CONTEXT、agents、模板堆或空阶段。新增阶段应在此目录导航并在 HANDOVER 更新当前入口；已关闭阶段保留历史证据。

阶段关闭/已解决 P1 时复盘；有理由才修改可访问的 Skill 源、升版本、同步副本，并留进化记录。`tests/test_docs_site.py` 验证源/副本一致及现行提交示例不回退到危险旧命令；文档站注册/本地链接门禁覆盖这些 Markdown。平台是否自动加载 Skill 不在此承诺范围内。

当前任务以 [ROADMAP](../ROADMAP.md) 为准（2026-09-23 起为验收前复核 V 组）；证据见[审计索引](../review/README.md)最新条目。先独立交叉复核，再按 ROADMAP 实施，不把历史各批次的“当前”横幅堆回入口。
