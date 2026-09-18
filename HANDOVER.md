> 第十批：[停止后续本站退款发送与停办依据](review/RELEASE_BLOCKERS_PHASE10.md)。仅阻止新的发送尝试，不撤回渠道退款、不恢复下载。可靠通知后续核验仍待下一批。

> 第九批历史：[独立授权、固定请求与显式退款发送](review/RELEASE_BLOCKERS_PHASE9.md)。发送默认关闭；准备/授权不会自动发送，申请观察不是成功凭证。

# 交接与恢复工作

学习入口：[从零复盘](docs/CODE_READING_GUIDE.md)；文档站 reading.html 列明人工精读与待补项。已有全部纳入范围非生成非空源码的连续分段讲解与来源标识；覆盖不等于每句语义已认证，历史报告也不作为当前实现认证。

Windows 本机先按 [新手逐步验收](Windows新手逐步验收.md) 一路操作，深入维护见 [conda 指南](docs/WINDOWS_CONDA.md)；剩余项目依 [验收手册](docs/ACCEPTANCE_GUIDE.md) 分层执行，并非全部只能在 Windows。未执行项不转写成通过记录。

## 当前入口

- 尚未完成的工作按[当前队列](review/README.md)推进：先可靠通知后续核验，再补完整纠错/重新授权及运行恢复，再渠道关单/对账和服务生命周期；预发布/实机验收与可选扩展分开，不重复实现已经完成的功能。

- 第八批：[本地退款准备台账与幂等恢复](review/RELEASE_BLOCKERS_PHASE8.md)。0012；一单全额稳定号、本地保存恢复、独立复核事实；没有发送或自动退款授权。
- 第七批：[退款通知线索与显式核验](review/RELEASE_BLOCKERS_PHASE7.md)。可信入站去重后持久ACK；通知不直接变成退款凭证，管理页填号后仍独立查询；无新依赖/迁移、无真实资金/业务库操作。
- 第六批：[退款凭证与订单下载权益](review/RELEASE_BLOCKERS_PHASE6.md)。0011迁移，单笔全额退款核验/人工登记及订单绑定短链；本地双后端全量与53项新回归/13项反例已核对；精确提交的六项CI见最终交付消息，后续边界以该报告为准。

- 第五批：[异常复核待办](review/RELEASE_BLOCKERS_PHASE5.md)。无新schema/依赖，复核完成不等于资金问题解决；本批证据和后续集中该报告。

- 第四批：[可信查单与管理员工作台](review/RELEASE_BLOCKERS_PHASE4.md)，操作见[管理手册](docs/PAYMENTS_ADMIN_GUIDE.md)。本地双后端与反例已通过，最终六项CI绑定本批交付SHA，不继承第三批CI；外部商户/退款/实机仍未签收。

- 第三批：[资金与交付权益](review/RELEASE_BLOCKERS_PHASE3.md)。本地双后端全量、36项新回归及10种受控反例已核对，最终六项CI绑定交付SHA。0010迁移与storage快照需一起备份，人工确认和旧单绑定有新合同；尚不是真实收款上线签收。

- 第二批：[剩余发布阻断的当前实施](review/RELEASE_BLOCKERS_PHASE2.md)。本地全量SQLite 933/6skip、一次性非超级用户PG 938/1skip，43项新回归和7种被拒绝反例；最终六项CI绑定交付SHA。初始化/迁移指令已改变，旧部署先读数据库指南，不重跑历史full_init。

- 当前任务：[2026-09-15 交叉审查、第一批修复和未结项](review/README.md)。两份输入原文在 review；不能以本批通过等同完整生产签收。

- 当前 Agnes 真实聊天已通过；embedding 未确认，免费候选尚未接入：[阶段记录](manager/stages/agnes-integration.md)，[接入说明](docs/AGNES_AI.md)。
- 已交付的本地验收/外部模型任务：[阶段证据与下一步](manager/stages/windows-acceptance.md)；[管理约定与经验](manager/README.md)

- 第二批实现与迁移：[验收记录](docs/SECOND_REPAIR_ACCEPTANCE.md)
- 文档语义重做、排版和 CI 修复：[质量复核](docs/DOCUMENTATION_QUALITY_REVIEW.md)
- 当前架构：[架构指南](docs/ARCHITECTURE_GUIDE.md)；执行方式：[根 README](README.md)
- 原始问题与交叉分类：[交叉审查](docs/REVIEW_CROSSCHECK.md)

旧沙箱次数、过期测试数字、一次性下载旧规则不再放在当前交接入口；历史内容保留在 Git 记录，不用来覆盖当前指南。

## 已实现与需要注意的边界

凭据版本、已购链接重领、站内客户/管理员会话、Drawio 实时导出/版本与生命周期隔离、配额预算、模型缓存、文档结构及链接门禁已落地。
动态 Chromium 明确停用，静态抓取保留。LocalStorage 是当前唯一实现；未接 OSS/COS、开票、自动退款、外部通知或自助密码恢复。单个配置商品不能冒充多 SKU 目录。

存量库先备份、停写并核实版本；已有0008未建账本时走adopt-legacy-0008，已有账本用migrate到当前0014。更早版本先按数据库指南处理前置条件，0008会清授权码。当前full_init只接受空库；历史版本曾删表，任何版本都不用于盲目升级。

## 恢复环境

1. 确认分支与 git status，读取实际未提交改动，不重跑历史临时修复脚本。
2. 使用 Python 3.11 虚拟环境安装 requirements，前端 npm ci；不要把环境与构建临时日志提交。
3. 默认全量测试用 SQLite；真实 PostgreSQL 指向专用可丢弃测试库，绝不指向业务库。
4. 阅读变更源码和 README，再刷新文档指纹、构建离线站。不是先 --write 再假称完成语义审查。
5. 交付必须绑定最终 SHA，实际查询每个 CI job。GitHub 认证失败只在 Arena 重连，不向用户索要密码或 token。

## 保持的约束

- 固定使用本 Arena 会话分支，不切换或新建分支。
- 用户上传的新旧文档提案和汇总保留原文；不要当成全部已实现的规格。
- 不上传鹈鹕演示、素材或相关文档扩写。
- 没做真实浏览器/商户/模型联调就明确说明，不用 Node/mock 结果替代。
- 以当前代码和真实测试输出为准，不根据 job 名或历史注释猜根因。
