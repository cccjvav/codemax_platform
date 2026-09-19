# 项目交接：先核实状态，再继续实现

本页是唯一恢复入口；[ROADMAP](ROADMAP.md) 是唯一当前任务队列；[全仓审计与交叉复核报告](review/FULL_REPOSITORY_HANDOFF_2026-09-19.md) 提供证据，不另建第二份全局台账。阶段报告仅证明对应日期/基线。

## 本次交接范围

- 审计业务代码基线：`7f2e125dd6dc42fb7b31d8c295f176c8e3cb2f50`，包括第十七批日账只读下载/差异报告。完整迁移仍为 **0017**。
- 本轮不增加支付功能、依赖或 schema，不操作真实商户/业务数据库。新增六项**诊断探针**、全仓审计、优先队列和文档清理；探针通过表示复现待修行为，**不是安全验收通过**。
- 用户新上传的[支付架构提示词-纯净版.txt](支付架构提示词-纯净版.txt)已取得并保留原文，评估集中审计报告第9节；先确认主体/渠道准入，不把桌面离线许可套进网页文件商城，也不以旧报告代替。
- 本地验证结果、局限与已删文档见报告；Windows、真实浏览器、商户、TLS/代理、生产备份恢复等仍未签收。

## 不混淆四种状态

| 层次 | 权威证据 |
| --- | --- |
| 实现 | 当前源码；第十七批基线 `7f2e125` 的实现已在本地提交 |
| 本地验证 | 本轮报告中的实际命令/结果；不借用阶段十七早期全量覆盖最终修改 |
| 远端发布 | 固定分支远端 SHA 必须等于交付提交；dry-run 不算上传 |
| 最终 CI | 同一 SHA 的六个普通 CI jobs 全部 success；Agnes 专项不替代它 |

审计开始时远端仍是第十六批 `56f4aa7`，CI 35455979865 不能证明阶段十七或本轮交接。最终提交不能在参与自身哈希的文档里写入自己的 SHA；**交付消息给出精确 SHA/run URL**，接手人仍须复核：

```bash
git branch --show-current
git rev-parse HEAD
git ls-remote origin refs/heads/arena/01a08bf5-codemax-platform
gh run list --workflow ci.yml --branch arena/01a08bf5-codemax-platform --limit 5 --json databaseId,headSha,status,conclusion,url
gh run view <匹配HEAD的run-id> --json headSha,status,conclusion,jobs,url
```

本会话固定 `arena/01a08bf5-codemax-platform`；不切分支、不推其他分支。认证诊断依实际仓库/推送结果，不凭 `gh api user` 的权限错误断言 Git 不可用。

## 已核实的上轮发布

`2a601344f9a65a4f445871343e464e2cc82625e5` 已推送且含阶段十七；[CI 35469332541](https://github.com/cccjvav/codemax_platform/actions/runs/35469332541) 现已逐job确认六项 success。此前401/PG状态未知的阻断已解除，不再要求重复重连。本次从同分支快进取得用户上传提交 `19f236e`，不继承旧提交绿色；后续增补按自己的最终SHA复验。

## 接手顺序

1. 读 AGENTS、manager/SKILL 和审计报告；核实实际 HEAD、远端和全部 CI。
2. 独立执行报告中的六项有界合成探针，检查调用链，不把测试数量当安全证明；按报告第9节对照已收到的付款/许可方案，独立复核“已实现/需纠正/可选扩展/待审批”，不把外项目声明当作源码证据。
3. 按 ROADMAP 的 G1 处理公开接口资源上限、LLM 协议边界、预支付并发/限流及登录来源；先评审方案，再增加保护性回归。不要保持“复现漏洞即通过”的诊断断言来冒充修复测试。
4. 确认首发类型：演示、免费工具、固定文件收费、定制开发服务的门槛不同；不得自动把部分退款、云存储、多实例或全自动会计系统变成首发必须功能。
5. 分批实施并更新对应模块说明/精读/技术决策；每批选择性提交、真实推送并核对精确 SHA 六项 CI。

## 现有能力与不可越过的边界

资金链已实现可信收款、只追加凭证、原订单冻结交付、退款撤权、显式准备/授权/发送、原号未知恢复、停止/发送前纠错、退款核验队列/人工接管/可选系统登记、本地监督、渠道关单和日账只读核查。**不要重新实现一遍，也不要把这些功能存在等同于生产可收款。**

`WX_REFUND_SEND_ENABLED`、`WX_REFUND_VERIFY_ENABLED`、`WX_REFUND_AUTO_RECORD_ENABLED`、`WX_ORDER_CLOSE_ENABLED`、`WX_BILL_READ_ENABLED` 默认均关闭；具体名称和操作以配置与运行手册为准。准备不代表发送授权，通知不等于成功凭证，关单 ACK 不等于资金结案，日账差异为零不等于会计关账。

当前交付是固定数字文件快照，不是报价/合同/里程碑/验收型定制服务。用户本人可提供人工客服，已有管理员/客户对话网页；没有接入外部开票或人工客服供应商。完整会计结算、部分退款、发送后纠错、云存储/多实例都未被本轮新增或验收。

## 操作与学习入口

- [新手逐步验收](Windows新手逐步验收.md)：Windows、VS Code 集成 **CMD + Conda + 系统 Node**；不改成 venv 教程。
- [验收手册](docs/ACCEPTANCE_GUIDE.md)、[部署](docs/DEPLOY.md)、[数据库维护](database%20init/README.md)：先备份并验证恢复，停全部写者，再按账本迁移；禁止业务库 pytest 或重复 init。
- [支付工作台](docs/PAYMENTS_ADMIN_GUIDE.md)、[退款运行](docs/REFUND_OPERATIONS.md)、[日账指南](docs/WECHAT_BILLS_GUIDE.md)：真实操作须独立授权与环境签收。
- [代码阅读](docs/CODE_READING_GUIDE.md)、[架构](docs/ARCHITECTURE_GUIDE.md)、[文档政策](docs/DOCUMENTATION_POLICY.md)：机器覆盖/连续讲解不是逐行语义认证。
- [历史证据索引](review/README.md)、[管理阶段](manager/stages/README.md)、[管理经验](manager/experience.md)：不恢复多份“当前状态”。Pelican 测试及相关文档不上传。
