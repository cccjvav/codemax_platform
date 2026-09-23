# 项目交接：先核实状态，再继续实现

本页是唯一恢复入口；[ROADMAP](ROADMAP.md) 是唯一当前任务队列；[全仓审计与交叉复核报告](review/FULL_REPOSITORY_HANDOFF_2026-09-19.md)、[2026-09-20 接手独立复核](review/FULL_REPOSITORY_REVIEW_2026-09-20.md) 与 [2026-09-23 第二次接手复核](review/FULL_REPOSITORY_REVIEW_2026-09-23.md) 提供证据，不另建第二份全局台账。阶段报告仅证明对应日期/基线。

**2026-09-20 接手状态**：基线 `ea11619`（远端 tip，六项 CI success）；六项诊断独立复现并同意评级，H-02 关闭；新增 F-09（限流器满桶拒绝所有新客户端）列为 ROADMAP A-07；复核报告提交 `48247ce`（六项 CI success）只写文档。第一修复批次（TD-260）已落地：请求体预算中间件（1 MiB / `/diagrams` 2 MiB / 回调自管 64 KiB）、422 去 `input` 回显、LLM 响应 1 MiB 预算 + 深嵌套归 LLMError + 总时限 + index `type is int`；A-01、A-05 完成，A-02 只剩并发/额度策略；四项诊断已换成默认套件回归，探针文件只剩 A-03/A-04 两项。第二修复批次（TD-261）已落地：限流器满桶从到期队头回收过期桶、满且全活跃仍拒绝并区分 capacity/quota（文案 + 每分钟一条 warning）、IPv6 /64 归并、`GET /shop/dl` 挂 `download` 桶；A-07、A-08 完成，F-09 维持门槛订正为 ≈ 300 请求/秒。第三修复批次（TD-262）已落地：同用户预支付进程内单飞 + `POST /shop/orders` `order` 桶（`RATE_LIMIT_AUTH`，用户确认取值）、`require_login_origin` 表单登录来源检查；A-03、A-04 完成，`tests/audit_handoff_probes.py` 六项诊断全部换成默认套件回归后已删除。第四批（O-09 清理 + TD-263 UI）已落地：TD-214/TD-217 文字订正、`_payload` 死参数、`crawler.py` 裸 assert、7 处无效 noqa；WCAG AA 配色、浮层 dialog 语义/Esc/焦点归还、`:disabled`/`:focus-visible`、`support.css` 去 `:has()`，bundle 重建。第五批（TD-264）已落地 A-02 后半：每进程 LLM 在途并发上限 4、满则 503 + Retry-After 5 不排队、客服转人工、mermaid 页自动重试一次，不做站内日额度（用户确认）。至此 G1 的 A-01～A-09 全部完成。第六批（TD-265）落地 O-03：三条建库路径的 PG 目录等价回归，修正 `sys_user.role` NOT NULL 与账本 DDL 同源两处漂移。第七批（TD-266）落地 O-01：下载出口快照校验按 inode 状态跳过重复全量哈希（权益判断不缓存）。第八批（TD-267）落地 O-08 的 action SHA 钉住与 CI 权限收窄（镜像 digest 与 pip 哈希锁列为未做）。第九批（TD-268）落地 O-07：重定向逐跳 robots、域状态 LRU 上限、Crawl-delay 上限。第十批（TD-269）落地 O-05：ALTER 外键、隐式父键、标识符折叠。G4 的 O-01/O-03/O-05/O-07 与 O-08 第一部分至此完成；剩余 A-06、O-02/O-04/O-06、O-08 后半与 G2/G3 各项都需要产品/经营决策或真实环境，每批经用户确认。 2026-09-22 起进入**验收前复核**：第一批（TD-270）按 Windows 指南在真 PG 上回放第 7～13 步，功能全部符合，修了 422 英文文案、模拟收银台返回路径、ER 表头对比度、静态缓存/gzip、维护 CLI 连接失败提示、文档里的旧分支名；后续批次继续按页面/文档/CLI 分组复核。

**2026-09-23 第二次接手状态**：本会话分支 `arena/01a0cdc6-codemax-platform`，起点 = 上一会话 tip `3e408a1`（TD-270，六项 CI success，run 35786058282）。接手人独立复核前两轮 F-01～F-09、A-01～A-09 及 TD-260～TD-270 修复，均成立；本地全量 SQLite 1822 passed / 7 skipped（两次），文档契约、完整文档站、Vite 零漂移通过。首次在沙箱用**真实 Chromium** 渲染全部页面（桌面/手机）并跑 axe-core 与完整购买/客服/管理员流程。新发现见[第二次接手复核](review/FULL_REPOSITORY_REVIEW_2026-09-23.md)：N-01（P2 回归：TD-270 的会话过期处理会清空 Drawio 未保存的图与客服草稿）、N-02（P2：流程图存储无每 IP/全站上限，单 IP 15 秒写入约 196 MB）、手机订单管理页横向溢出、ER 节点文字溢出与连线遮挡、单标签页“返回商城”落回落地页（bfcache 被 no-store 拒绝）等 UI 问题，以及测试 81% 时间耗在 bcrypt（测试进程降到 cost 4 全量 1276 s→220 s）。本次提交**只改文档**（登记报告、修正当前入口漂移、分支名），修复批次 C1～C9/O-11～O-17 列入 ROADMAP，每批经用户确认后实施。

**发布状态（2026-09-23）**：`d61098d`（TD-271，复核报告 + 文档同步）已推送到 `arena/01a0cdc6-codemax-platform`，[CI 35869547675](https://github.com/cccjvav/codemax_platform/actions/runs/35869547675) 六项 success（SQLite pytest 19m55s、PG pytest 23m57s）。该节的“认证失效、尚未推送”记录已过期。

**接手状态（2026-09-23 晚，TD-272 批次 1）**：新接手人独立复核后按用户确认施工，落地两项**无新依赖、无新业务规则**的修复：V-03（测试进程 bcrypt 成本 4：全量 1276 s → 219 s，1822 passed；生产成本由 `production_cost` fixture 单独复验）与 V-04/V-06 的一部分（跳过导航、表单控件字体继承、窄屏 16px 输入框与 ≥24px 命中区、顶栏 208px → 147px、工具页可见页面标题、整站每页单 h1、管理员入口按角色显隐、管理页长订单号换行/只读分区/危险动作红框、客服时间对比度 4.34:1 → 6.92:1、Drawio 首屏不再重建 iframe、未配置模型时的访客文案）。修完再用沙箱内真实 Chromium 复测：横向溢出消失、axe-core 全部状态 0 违规、小于 24px 的可点目标归零。**2026-09-23 晚 TD-273（批次 2，P2 回归）**：会话到期与主动退出分开 —— Drawio 到期不重建编辑器、同一账号重登可继续保存，客服草稿保留；换账号仍必须重建（账号隔离不退化）。**仍未实施**：V-02（流程图写入限流，P2）、V-05（已购用户“已购买，去下载”＋返回商城恢复订单状态，用户已确认改法）、V-06 余项（网页改密码入口、Mermaid 按需加载）、V-07/V-08。批次 2 起仍需逐批确认（尤其 V-02/V-08 涉及配置项与工作流合同）。

**推送状态（2026-09-23 晚）**：本会话最后两个提交（TD-272 后端/前端修复与 TD-273 会话到期修复）**只在本地**，尚未出现在远端。本会话后段 `GH_TOKEN` 失效（`gh api user` 返回 401 Bad credentials，`git push` 报 "Invalid username or token"），需要用户在 Arena 重新连接 GitHub 后再推送，并按推送后的最终 SHA 核对六项 CI —— 不要用 `d61098d` 的绿色代替，也不要把本地提交当作已发布。接手人先 `git log --oneline -5` 与 `git ls-remote` 对照。

## 本次交接范围

- **当前（2026-09-23）**：复核基线 `3e408a1`（TD-270）；本轮只新增复核报告与文档同步，不改运行逻辑、依赖、schema 或历史 SQL。完整迁移仍为 **0017**。
- 以下为 2026-09-19 审计时的范围，保留作历史：审计业务代码基线：`7f2e125dd6dc42fb7b31d8c295f176c8e3cb2f50`，包括第十七批日账只读下载/差异报告。完整迁移仍为 **0017**。
- 本轮不增加支付功能、依赖或 schema，不操作真实商户/业务数据库。当时新增六项**诊断探针**、全仓审计、优先队列和文档清理（探针已在 TD-260/TD-262 修复后转成默认套件回归并删除）。
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
git ls-remote origin refs/heads/arena/01a0cdc6-codemax-platform
gh run list --workflow ci.yml --branch arena/01a0cdc6-codemax-platform --limit 5 --json databaseId,headSha,status,conclusion,url
gh run view <匹配HEAD的run-id> --json headSha,status,conclusion,jobs,url
```

本会话固定 `arena/01a0cdc6-codemax-platform`；不切分支、不推其他分支。认证诊断依实际仓库/推送结果，不凭 `gh api user` 的权限错误断言 Git 不可用。

## 已核实的上轮发布

最近一次核实（2026-09-23）：`3e408a17b057b176a2fb5da5959f29b249d35f62`（TD-270）为上一会话分支 `arena/01a0bf7a-codemax-platform` 的远端 tip，[CI 35786058282](https://github.com/cccjvav/codemax_platform/actions/runs/35786058282) 六项 success。本会话提交须按自己的精确 SHA 重新核验。更早记录（历史）：`2a601344f9a65a4f445871343e464e2cc82625e5` 已推送且含阶段十七；[CI 35469332541](https://github.com/cccjvav/codemax_platform/actions/runs/35469332541) 现已逐job确认六项 success。此前401/PG状态未知的阻断已解除，不再要求重复重连。本次从同分支快进取得用户上传提交 `19f236e`，不继承旧提交绿色；后续增补按自己的最终SHA复验。

## 接手顺序

1. 读 AGENTS、manager/SKILL 和审计报告；核实实际 HEAD、远端和全部 CI。
2. 读最新复核报告（当前为 2026-09-23），独立复核其新发现与最近修复批次；六项探针与 G1 已全部关闭，不要重跑已删除的探针文件。付款/许可方案仍按 2026-09-19 报告第9节对照，不把外项目声明当作源码证据。
3. 按 ROADMAP 当前余项（验收前复核的 C/O 批次、A-06、O-02/O-04/O-06、O-08 后半）分批处理；先评审方案，再增加保护性回归（新测试须在旧代码上先红）。
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
