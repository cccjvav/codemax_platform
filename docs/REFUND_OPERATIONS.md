# 核验进程监督、告警与恢复手册

## 1. 先认清这三个信号

- **Web能打开**：不证明独立核验进程正在运行。
- **核验进程近期完成一轮**：不证明商户配置正确、队列已清空或退款成功。
- **可信全额退款凭证**：仍须走原有签名核验/独立授权和原子登记流程，不能用健康JSON、日志、通知SUCCESS替代。

第十五批不加依赖、迁移或API。完整账本仍至0017；VERIFY/SEND/AUTO_RECORD均默认关闭。没有短信、邮件、Webhook或外部监控接收方，不承诺有人自动收到通知。所谓告警是**本地固定代码、退出状态和日志状态变化**，需要你本人或另行配置的监督器查看。工作台仍展示Web配置，不读取主机文件，也没有新健康网页。

## 2. 本地运行接口

以下只是有权操作的**专用测试部署**配方，不要求立即在业务环境执行。先确认备份与数据库目标、完成0017并使用相同代码包，选择单worker。只有明确授权查询后才将该部署的VERIFY设true；SEND保持false，AUTO_RECORD默认false。状态目录不能在app/static或storage（商品目录）内，父目录应只由部署账号管理，不在共享网络盘。

```text
python -m app.refund_worker --status-file runtime/refund-verifier.json
python -m app.refund_health --status-file runtime/refund-verifier.json
python -m app.refund_health --status-file runtime/refund-verifier.json --alerts
```

第一条常驻；后两条在另一终端或监督任务里执行，不启动worker、不读.env、不连接数据库或商户。检查结果输出JSON，正常退出0，异常/告警退出1，非法命令参数退出2。`--max-age`可设1–3600秒，默认120；不要任意放大掩盖故障。删除状态文件不会重排任何任务，检查会失败。

`--once`仍是一轮而不是清空积压；其最终状态为completed，健康检查必须失败，因为进程已退出。启用状态文件是可选参数，旧命令兼容；要监督时必须给worker与checker**同一个绝对路径或同一工作目录下的路径**。每个路径只允许一个写进程，同机第二个写者立即非零退出，不覆盖第一进程状态。不同路径不会阻止多worker，数据库CAS仍负责任务竞争；本示例不鼓励多实例。

状态文件含实例随机标识、PID、递增序号、最后完成时间、状态和有界聚合数字，不含订单号、用户、商户、退款号、租约token、凭据或原始错误。POSIX写出0600，临时文件fsync后同目录替换；Windows文件ACL需本机核实。`.lock`是操作系统文件锁而不是“见到文件就算占用”，进程崩溃后自动释放；**不要删锁文件解决冲突**，否则可能产生两个不同锁对象。

## 3. 何时认为异常

启动先写starting，15秒预算验证完整迁移账本；每轮总预算60秒（修复旧收件箱、领取、GET、结果保存、队列汇总），成功后才写running；轮间至少5秒。失败写failed并非零退出，正常取消写stopped且不清租约。模块导入/状态盘故障可能无法更新文件，此时依靠旧文件自然过期和监督器退出状态。

健康检查只接受近期running：缺文件、坏格式、超4096字节、未来超过5秒、超过最大年龄、starting/stopped/failed/completed均非零。**这是滞后信号**：SIGKILL、断电或文件写失败后，先前running可能仍被认为正常最多120秒；没有通过PID探活来假装无延迟证明。系统时钟需要校准，时钟回拨可能触发异常。事件循环完全阻塞或磁盘挂起时协作超时未必执行，需要外部监督，不能把asyncio.timeout当操作系统强杀。

带`--alerts`另外评估：

| 固定代码 | 条件 | 应对 |
| --- | --- | --- |
| needs_operator | attention数大于0，包含主动hold | 原单查看原因；hold可能是预期状态，不要自动解除 |
| expired_lease | running租约已过DB时间 | 检查进程/DB；有剩余预算由原领取逻辑恢复，不能清token/次数 |
| query_retry | 有retry任务，即使退避尚未到期 | 排查配置/签名/网络或渠道处理中；不是允许重发退款 |
| due_backlog | 到期pending/retry至少50项 | 检查吞吐与积压；不自动扩大并发或重置次数 |
| overdue_queue | 最早到期pending/retry已等至少300秒 | 检查工作循环，注意是到期等待年龄，不是订单年龄 |

汇总使用DB时钟和只读聚合，不把verified纳入活动数；数据库大表仍需实际负载验证，60秒预算不是容量保证。expired与due分开计数。stdout仅在进程状态/告警代码组合变化时打印固定结构（包括清除告警），每轮仍刷新私有文件；重启可重复打印，不承诺外部恰好一次通知，也不存完整告警历史。静态问题持续时应周期执行checker，而不是只等新日志。

## 4. Compose监督模板：默认不启动

仓库`docker-compose.yml`新增`refund-verifier` profile。它使用相同镜像和示例db，显式清空.env的外部DATABASE_URL，避免误连；没有HTTP端口，不挂商品卷，不自动建表。镜像创建非root可写runtime目录，状态用专用卷。模板强制SEND/AUTO_RECORD=false，仅做查询观察；已有系统登记授权也不会被本模板暗中继承，后续若要使用AUTO须另行明确部署审查，不能靠改.env猜它生效。

仅在Docker/Compose已安装且**专用测试库已迁移**、VERIFY已显式授权后：

```text
docker compose --profile refund-verifier up -d --build refund-verifier
docker compose --profile refund-verifier ps -a
docker compose logs --tail 50 refund-verifier
docker compose exec refund-verifier python -m app.refund_health --status-file /srv/app/runtime/refund-verifier.json --alerts
docker compose stop refund-verifier
```

`init: true`转发信号，停机宽限15秒；SIGTERM协作取消，不宣称能召回已到渠道的GET。`restart: on-failure:5`仅进程非零退出时最多重试5次；配置错误仍会停住，须人工排查。**Docker unhealthy本身不会重启容器**，本模板也没有自动重启unhealthy的第三方服务。容器停止/无法exec也是监控失败，不能忽略退出码。健康探针覆盖镜像Web `/healthz`，改成本地freshness，不加`--alerts`，防止人工hold或积压造成重启风暴。

本沙箱没有Docker，模板只做配置文本回归，未执行Compose/镜像/容器重启验收。Linux子进程信号测试不等于Docker已验收。不要执行`down -v`，那会删数据库/商品卷；不要在主机或公开网页暴露状态目录。

## 5. 恢复演练与签收

只用可丢弃环境运行 `python -m pytest -q tests/test_refund_health.py tests/test_refund_verification.py tests/test_system_refunds.py`。测试中使用合成通知、独立数据库连接和签名替身，不调用真实商户。新增进程恢复用例在出站边界使用阻塞替身，真实启动进程、领取提交、发SIGTERM/SIGKILL，再启动新进程；替身的verified仅是测试观察，不是签名或退款凭证。

须检查：
1. 默认关闭时非零退出、零查询；不存在状态不算健康；--once完成不冒充常驻。
2. 运行正常时freshness通过；人工attention触发alerts但不触发重启，清除条件后告警检查恢复。
3. 杀进程后原running租约/次数不消失；未过期不能偷领，过期后新token处理，旧结果必须拒绝。测试可人为前移租约来提速，**业务恢复绝不能照抄改表**。
4. 重启保留原订单/退款号/授权/停止/队列总次数；不会创建新准备或退款POST。未知发送继续原号独立核验，不走重新授权。
5. DB断连/账本不全/校验和漂移应非零失败且日志不含DSN/平台原文；修好原因后同代码包重新启动，不能删账本或跳过校验。
6. 硬杀健康最多滞后120秒，操作系统释放单写锁，重启实例标识改变；磁盘不可写不得继续刷新假健康。

生产还需分别签收：实际Docker或Windows服务监督、重启策略/启动顺序、监控周期/接收人、权限/磁盘满、时钟偏差、TLS/商户、积压负载、数据库与storage备份恢复。此次没有做生产备份恢复、真实资金或外部报警送达演练。Windows步骤见根目录[新手逐步验收](../Windows新手逐步验收.md)；Linux结果不能替它签字。
