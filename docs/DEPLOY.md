# 部署与上线（S5-03）

本文只覆盖**能用代码和配置文件解决**的部分。域名解析、SSL 证书签发这类
必须在云控制台/域名商处操作的步骤，只说明该做什么，不假装能自动化。

---

Windows/conda 开发运行、备份演练和自动化测试见 [本机指南](WINDOWS_CONDA.md)；浏览器与真实外部服务的签收标准见 [验收手册](ACCEPTANCE_GUIDE.md)。本机 mock 成功不等于生产商户联调通过。

## 发布阻断与日志边界（2026-09-15）

本指南不是生产验收证明。默认种子分离、迁移账本、支付凭证/冻结交付/全额退款门禁、显式发送/关单和只读日账已经实现；生产商户/权限/恢复尚未签收，第三方 OAuth 不在当前受信第一方边界内，见[唯一当前队列](../ROADMAP.md)。不要把默认 development 示例或新建两遍 full_init 当作安全上线/无损升级。

Docker 已传 `--no-access-log`，保留应用的有界、不含查询参数的访问日志。用其他命令启动 Uvicorn 时也应设置 `--no-access-log`；Nginx/LB 必须使用不含 `$request`/`$request_uri`/`$args` 的日志格式（可用 `$request_method $uri $status`），避免把短时下载 signature 写进日志。关闭应用一层不能证明代理/CDN已脱敏；需真实链路检查。普通日志不是持久财务审计账本。

生产绝对链接使用校验过的 `SITE_BASE_URL`（仅 HTTPS origin）；开发仍按请求地址生成。本批未新增 Host allowlist，代理仍应拒绝未知 Host，并核对直连 peer 与可信 CIDR。

## 1. 生产环境必须配好的 .env

`ENV=production` 会在**启动时**做自检，不合规直接拒绝启动（`app/startup_checks.py`）。
宁可起不来，也不要"跑着但行为是错的"——那几项配错的后果都是等到用户下单才发现。

| 配置 | 生产要求 | 配错的后果 |
|---|---|---|
| `ENV` | `production` | 不开启下面这些检查 |
| `SECRET_KEY` | 至少 32 字符的高随机性字符串，**不能**是 `dev-secret-change-me` | 任何人都能伪造 JWT |
| `SHOP_PAY_MODE` | `wechat` 或已安排人工核账的 `manual` | 若是 `mock`＝**免费发货**（TD-124） |
| `RATE_LIMIT_ENABLED` | `true` | 公开的 `/tools/*` 可被无限刷（TD-15） |
| `TRUST_PROXY_HEADERS` | `true`（在反向代理之后） | 所有用户被当成同一个 IP，限流形同虚设（TD-142） |
| `DB_PASSWORD` | 真实密码 | 连不上库，`/readyz` 返回 503 |

启动失败时会一次性列出**现有检查覆盖到的**问题（未覆盖商户真实可用性、商品完整性及架构版本等全部就绪条件），不是报一个改一个。

---

## 2. Docker隔离单机示例（不是完整生产方案）

Compose现在**只创建数据库服务，不自动挂SQL建表**。示例强制应用使用db容器的codemax_db，清空DATABASE_URL覆盖，避免误连.env中的外部库；这是本地例子，不支持借此配置任意生产数据库。

新建、已确认无数据的卷/空库按顺序运行，任何一步失败就停止：

```bash
docker compose build app
docker compose up -d db
docker compose run --rm app python "database init/db_init.py" init --confirm-database codemax_db
docker compose run --rm app python "database init/db_init.py" bootstrap-admin --username owner --confirm-database codemax_db
docker compose run --rm app python "database init/db_init.py" status --confirm-database codemax_db
docker compose up -d app
```

bootstrap交互输入新口令，不放命令行。应用启动前要具备当前迁移账本与非演示管理员；production会只读检查，不会偷偷升级。新库不创建任何OAuth客户端，production还须显式配置OAUTH_TRUSTED_CLIENT_IDS才能使用受信自有站点SSO。

**已有卷不能运行init作为升级。** 先停app、验证备份恢复；已明确为0008且没有账本时，把上面的init换成 `adopt-legacy-0008`；已有账本时换成 `migrate`。管理员已存在则不要重跑bootstrap。早于0008的库先按[数据库指南](../database%20init/README.md)核实历史前置条件；不得盲目重放0001–0008（0008会清授权码）。

`.dockerignore`继续排除.env、环境目录、Git、storage与测试，但**必须保留database init内的维护程序和迁移SQL**，运行时要核对其SHA。这是COPY数据用途，不是自动执行SQL。镜像/Compose实际启动、备份恢复及生产角色最小权限仍待独立环境验收。

Compose无反向代理和SSL，应用端口默认仅发布到127.0.0.1；不要把postgres超级用户示例当生产权限设计，不要使用docker compose down -v处理故障。

---

## 3. SSL 与域名（S5-03-2，需在云控制台操作）

**应用本身只跑 http**，TLS 在反向代理终止。这是刻意的：证书续期、HTTP/2、
连接管理都交给专门的组件，应用不碰。

nginx 最小配置要点：

```text
server {
    listen 443 ssl http2;
    server_name codemax.top;
    # 证书建议用 certbot 自动续期，别手工放
    ssl_certificate     /etc/letsencrypt/live/codemax.top/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/codemax.top/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;   # ← HSTS 靠它判断
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Request-ID      $request_id;  # ← 与应用的 request id 串起来
        # 请求体上限：代理层可以再收紧（例如 client_max_body_size 5m），但它只保护走代理的流量；
        # 应用自身另有解析前预算（MAX_REQUEST_BODY_BYTES 等），直连应用端口时同样生效。
    }
}
```

**请求体预算（A-01）**：应用在 FastAPI 读体/解析之前按路径限制请求体——默认 64 KiB，
`/tools/er-diagram`、`/tools/word-export` 128 KiB，`/diagrams` 新建/保存 4 MiB，微信回调由处理函数自己在 64 KiB
处按微信格式拒绝；声明超限立即 413 且不读体，无长度分块按实际字节计数，读体超过
`REQUEST_BODY_TIMEOUT_SECONDS`（默认 60 秒）返回 408，拒绝响应带 `Connection: close`。422 不再回显 `input`。
代理的 `client_max_body_size` 不能替代它（直连端口、内网调用不经代理），也不要把代理上限调到低于 4 MiB，
否则 drawio 大图保存会在代理层被 413。真实 h11/代理行为以部署环境实测为准，沙箱只验证了本地 uvicorn。

配好后应用会自动做两件事（都有测试）：
- 响应带 `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  —— 可信直接对端提供 https 转发头且启用代理头处理，或 ASGI 本身标记 https 时才下发；还取决于 HSTS_MAX_AGE。
  http 上不发，否则还在用 http 的环境会被浏览器锁死一年；不信任代理头时也不发，
  否则伪造一个头就能触发。
- 限流按真实客户端 IP 计（而不是全部算成代理 IP）。

微信支付回调地址 `WX_NOTIFY_URL` 必须是公网可达的 **https**。

---

## 4. 监控与报警（S5-03-3）

### 两个探针，别混用

| 端点 | 用途 | 是否查库 |
|---|---|---|
| `GET /healthz`（`/health` 是别名） | **存活**探针 | ❌ 不查 |
| `GET /readyz` | **就绪**探针 | ✅ 执行 `SELECT 1` |

**存活探针刻意不查数据库**：如果它查库，数据库一抖编排器就会把**健康的**应用实例
全部重启，把一次数据库故障放大成全站雪崩 —— 这是存活探针最经典的误用。
只有部署平台实际把 `/readyz` 配成就绪探针，才能按策略摘流而非重启。本仓库 Compose 没有自动负载均衡摘流配置；`unhealthy` 也不自动触发重启，不能将端点存在当成编排已接通。
库连不上时 `/readyz` 返回 **503**（不是 500），这样编排器能分清是应用坏了还是库坏了。

### 日志

每个请求一行：

```text
2026-09-03 01:02:11 INFO codemax.access POST /tools/er-diagram -> 200 (27.1ms) rid=786e42abcf314629
```

**`X-Request-ID` 是排障的关键**：用户报障时让他把响应头里的这个值报上来，
就能在日志里精确定位那一次请求。上游非空 `X-Request-ID` 当前直接沿用，否则生成新值，
这样一条链路能串起来查。异常也会记一行（带堆栈），500 在日志里不会是空白。

日志级别用 `LOG_LEVEL` 控制。request id 是排障标签，不经过身份认证，也不是可信授权或防篡改审计凭据。

### 报警建议

| 指标 | 阈值 | 说明 |
|---|---|---|
| `/readyz` 非 200 | 连续 3 次 | 数据库或应用异常 |
| 5xx 比例 | > 1% 持续 5 分钟 | 看日志里的 `rid` 定位 |
| `/support/ask` 的 `escalated` 比例 | 突增 | 说明 FAQ 语料没覆盖住，或 LLM 挂了 |
| `/tools/*` 429 数量 | 突增 | 要么被刷，要么限流阈值太紧 |

---

## 5. 已知未做（都需要外部资源）

- **OSS/COS 云存储**（S5-03-1 / TD-128）：无密钥，适配器写出来也无法验证签名。
  当前用 `STORAGE_BACKEND=local`，文件存在容器卷 `uploads` 里 —— 单机可用，
  **多实例部署会出问题**（A 实例写的文件 B 实例读不到）。上多实例前必须先接云存储。
- **BERT 意图路由**（S4-02-2 / TD-150）：接口与插槽已留好。
- **爬虫 robots.txt / 限速**（TD-133）：**已解决** —— `app/tools/politeness.py` 会读并遵守
  robots.txt（按域缓存 1 小时）、按域遵守 `Crawl-delay`（无则默认 2 秒）、全局并发上限 4。
  与部署无关，但多实例部署时每个实例各有一份缓存与节流窗口，对目标站的实际频率会按实例数放大。


## 2026-09-12 部署基线与存量升级

当前默认单实例（一个应用进程）。数据库锁协调付款、配额与客服幂等，但内存限流不会自动在多个进程/副本之间共享。先不要增加 worker 或副本。

Compose 默认 `${APP_BIND_HOST:-127.0.0.1}:8000:8000`，禁止无意公开后端绕过入口代理。`APP_BIND_HOST` 是 Compose 配置；需要暴露时显式修改并设置防火墙。
Docker 启动关闭 Uvicorn 的代理头重写，由应用根据直接对端和 `TRUSTED_PROXY_CIDRS` 统一判断。宿主反向代理通过 Docker 网关连接时，对端可能是网关 IP 而不是 127.0.0.1：确认实际对端后只加入该地址的精确 CIDR，不要直接信任全部私网。

存量库：备份并验证可恢复 → 停写 → 核准基线 → 接入/验证迁移账本并运行缺失迁移 → 部署匹配版本 → 核查角色与重登录 → 验证原订单/消息。详细步骤见[数据库指南](../database%20init/README.md)，旧验收记录只作为历史证据。

生产关闭 OpenAPI/Swagger/Redoc；开发环境才允许专门的 CDN CSP。Mermaid 已本地构建。Chromium 直接出网已停用，安装浏览器不能解除限制；必须先实现隔离渲染服务。真实支付/对象存储联调与模型阈值标定由对应启用功能决定，不冒充已部署验收。

## 当前模型提供方

默认 Agnes AI，已有 `.env` 应按 [Agnes 接入](AGNES_AI.md) 更新基址/模型并重启进程；环境变量优先级不变。不要把“免费”理解为无限 RPM/生产 SLA，也不把网络诊断成功当商户或模型验收。默认关闭 embedding，外部可用性不纳入普通单元测试。


## 第二批上线边界

下单前也会校验本地平台证书/公钥与32字节APIv3密钥，不可用返回503且不请求商户；须另外实测回调可达性与控制台登记值。微信回调要求WX_APPID/WX_MCHID与交易一致，币种CNY、类型NATIVE。WX_PLATFORM_CERT若为X509证书，Wechatpay-Serial必须匹配且证书在有效期；若是裸RSA公钥，另填WX_PLATFORM_KEY_ID，不要填成商户WX_SERIAL_NO。必须从可信商户渠道配置真实平台凭据；不是随回调动态下载证书，未实现自动轮换；预支付应答验签已接入。

OAUTH_TRUSTED_CLIENT_IDS使用JSON数组，例如自有站点确认后设为["tools"]。它们会拿到完整站点JWT，具有对应用户权限，不是第三方scope隔离；production回跳仅HTTPS。默认空数组拒绝生产SSO，不能因接入方便把不可信应用加入名单。

## 第三批升级检查

先停止写入，备份数据库及整个storage（含`.snapshots`），验证恢复后运行维护CLI的migrate，当前应确认完整账本至0017已登记。不要更改0001–0011旧文件或删账本。存储需支持同卷硬链接；文件复制512MiB/30秒、每进程两槽，失败即拒绝下单。更换STORAGE_LOCAL_ROOT时连同快照搬迁，不能只改环境变量。

历史渠道/交付字段未绑定的订单需管理员按原合同核准一次；不要批量套用当前配置。人工确认现在必填参考号、金额和核账依据；查看持久ledger，不拿stdout当唯一凭证。当前已有全额退款链路和只读日账差异核查，但非自动会计结案，真实商户/恢复仍待签收；第三批只保留[历史凭证边界](../review/RELEASE_BLOCKERS_PHASE3.md)，现行操作见支付工作台与退款运行手册。

## 第四批配置与管理入口

第四批当时无需新迁移/依赖；当前部署还必须完成下方至0017的全部迁移，保留全部快照。顶栏/admin/payments提供原单管理，不要开放生产/docs代替管理。Native应答现在也要求平台验签，不完整或已轮换凭据会失败并保留未知尝试；先经可信渠道配置证书/公钥ID并校准NTP。代理不能删微信四个验签头，服务端拒绝压缩与跳转。生产SITE_BASE_URL也作为Cookie财务写的来源基准，代理转发头仍须受信配置，不能用放宽所有来源解决403。

管理员可对当前凭据所属原单显式只读查微信并原子补记可信SUCCESS；该查单入口不发送远程关单/退款，不自动撤权，历史异常不等于待办结案。过程、故障恢复和外部签收见[工作台手册](PAYMENTS_ADMIN_GUIDE.md)。

第五批当时仅新增现有事件类型和读写界面，使用0010，无新服务/迁移。GET不扫描写任务，管理员主动查看；复核写增加同源/角色校验，后台或跨源客户端需遵循工作台手册。数据库和历史证据继续一起备份。当前显式全额退款发送/核验/可选登记见后续批次；部分退款、完整会计结案未实现，真实收款仍未验收。

## 第六批：0011与订单绑定下载门禁必须一起部署

第六批新增0011；当前完整账本为0017，见第十四批。停止写入、备份数据库和storage、确认可恢复后，通过原维护CLI执行migrate并核对status；没有执行你的业务库。新表只记录已核验成功的单笔全额退款，不回填历史退款。应用发布后旧key-only短链403，未退款客户可从订单历史重领；缓存/CDN不得缓存私有下载响应。

不要直接回滚到不识别refund_receipt的旧下载应用，否则已退款订单可能重新获权。回滚需要停发下载，并单独审查数据库与应用的一致性；已接纳的传输/已下载副本无法召回。仅本地存储已实现，未来云直连也须等价逐次授权。退款查询、人工记录的限制及操作见[管理手册](PAYMENTS_ADMIN_GUIDE.md)。退款申请/显式授权发送和通知核验已在后续批次实现；部分退款记账/权益与服务生命周期未实现，真实商户/生产恢复仍未验收；通知本身仍只是入站线索。


## 第七批：退款通知地址是独立接收口

`https://你的正式域名/shop/refunds/notify`与收款WX_NOTIFY_URL分开；最初通知子项无新环境变量/迁移，第十一批增加核验开关及0015。微信商户平台退款配置可登记默认通知URL，外部退款申请的显式notify_url会覆盖它；只有真实配置/指定到本站地址才可能收到对应通知，不能改收款地址来假装接通退款，不能承诺补发旧通知。可信商户/app/APIv3密钥和平台证书/显式公钥ID继续复用，不从入站请求下载密钥。

代理不得改写签名正文、重复签名头或自动压缩；接收64KiB/4秒应用预算，渠道要求5秒，应在专用环境实测时钟、TLS/代理、负载与数据库阻塞。不要用管理员登录中间件拦住服务器回调，也不能绕过路由验签。失败保留渠道重试，监测非2xx和需要复核清单；没有主动外部报警；独立核验消费者的部署见第十一批。备份PaymentEvent和原收款一起保存，204仅保证入站提交成功，退款成功/撤权仍走独立管理查询。无需也不得为此重建业务库；没有操作你的实际部署。


## 第八批：0012本地准备台账

无新依赖/环境开关。备份数据库与storage、停写并确认恢复方案后，用原维护CLI status/migrate到0012；不能重复init或删历史账本。新表不回填旧准备，原收款/退款/下载权益不变；production启动拒绝缺0012的包或未升级账本。此次没有执行你的部署/业务库。

这不是开启自动退款的发布：第八批准备POST仅保存；第九批另加默认关闭的独立授权和显式发送，没有定时消费。发送需要冻结完整请求及原号未知恢复，禁止自动消费历史准备。数据库连同PaymentEvent/RefundRequest一起备份；任何回滚都先停写评估版本与准备记录一致性，不删表换号。既有退款下载门禁仍不可绕过。


## 第九批：0013与默认关闭发送

当前完整账本0017；旧0001–0012不可改。按原CLI备份/停写/迁移/核验，绝不业务库pytest或重复init。WX_REFUND_SEND_ENABLED=false是默认；设true会开放可能转款的显式管理员按钮，不能在未完成商户/TLS/恢复验收时开启。没有发送worker，不能据此批量发送历史准备/授权。

SITE_BASE_URL必须是HTTPS域名来源；授权冻结其/shop/refunds/notify（无参数、最多256字节），覆盖商户平台默认退款回调。改域名不改历史请求正文，必须保留原回调可达或另行受控方案。切换商户/app不得发送旧商户请求。备份授权表和PaymentEvent；回滚先关发送/停写，禁止删授权/尝试换号。此次仅在可丢弃库执行，未操作部署。


## 第十批：0014与停止记录一起发布

当前完整账本0017，维护仍先备份/停写/核实恢复再migrate；不要改旧0001–0013或删停止记录回滚。WX_REFUND_SEND_ENABLED继续默认false；停止功能不要求开启它。新旧应用混跑不安全：旧发送代码不认识停止表，可能绕过新停止意图，须先停发并一致升级，不能凭加了表就宣称滚动发布安全。

备份新增停止表和PaymentEvent/原授权/准备一起保存。停止不能召回已开始的渠道请求，部署停写/回滚也须先核查在途尝试；原号查询仍必要。本批未操作你的实际部署/业务库，真实恢复、商户与代理仍待签收。


## 第十一批：0015与独立只读worker

先备份/验证恢复/停写，用原CLI升级至当前0017并核实校验和，再一致发布；不混跑忽略停止记录的旧发送代码。保持WX_REFUND_SEND_ENABLED=false。WX_REFUND_VERIFY_ENABLED默认false，入站仍持久建任务，关闭时不会自动查询；仅设置true并运行独立进程才处理，不随Web启动。

Linux运行同一环境的`python -m app.refund_worker --once`做一次有界周期；确认专用环境验收后由监督器运行不带--once的循环、管理退出/重启，日志不打印原始支付异常。使用与Web同一目标库/迁移包/可信商户配置；CLI先验证完整账本，绝不自动建表。任务无新第三方服务/依赖。多实例CAS/租约允许恢复，不保证恰好一次GET或全局共享限流；先按单worker部署，循环每周期至少等5秒。开关改动需重启进程，已领取GET可能完成；无自动外部报警，管理员需监测attention、逾期租约和积压。--once不表示清空队列。

不在本轮启用真实商户查询、生产进程或迁移业务库。独立测试环境验证停进程/到期恢复、断网/配置错误、签名错误、末次耗尽、重复通知和人手接管；真实TLS/代理/负载/恢复/Windows仍待实机签收。默认AUTO_RECORD关闭时已核验SUCCESS只留观察，仍需管理员独立查询登记；系统权限见第十三批。

补缺限制：每周期最多创建50个任务限制的是结果/写入数，不是数据库扫描页数；历史通知很多时，缺项反连接的扫描成本会增长，必须实测查询计划与负载后再扩容，当前没有持久高水位扫描器或吞吐SLA。任务投影不单独返回token；管理员事件中的attempt_id关联领取（无对外按token写入接口），它不是管理员认证凭据。

获准维护者可在自己的只读SQL会话统计积压（示例不由本轮执行）：

```sql
SELECT state, outcome, count(*) FROM refund_verification_job GROUP BY state, outcome;
SELECT id, notice_event_id, order_id, attempts, lease_until
FROM refund_verification_job WHERE state='running' AND lease_until <= now();
```

这是数据库观测，不是重置或补发命令；用管理页原号核查，不能UPDATE清次数或DELETE“修复”。

## 第十二批：没有新增迁移的人工调度控制

此人工接管子项使用0015，无独立新开关/依赖；当前另需0017且历史SQL不改。保持三个开关默认false，部署/商户/Windows验收未完成不启用业务环境。按既有停写/一致发布规则更新Web与产物。人工hold只栅栏该任务的后续提交，不召回GET、不暂停新通知任务；重排保留总次数，旧worker的token/状态校验仍有效。备份须包含队列和PaymentEvent；不得通过回滚数据库删除接管依据或复活旧租约。系统登记已补默认关闭的权限；外部报警及进程监督、商户实机验收仍未完成。

接管的界线是持久领取，而不是HTTP到达渠道：领取已提交的尝试，即使尚未发出HTTP，也可能在接管返回后继续GET；接管保证不再领取该暂停任务，并使旧结果不能覆盖当前任务，不是网络召回。


## 第十三批：0016与独立系统登记授权

先备份/恢复核实并停止Web、worker写入，该子项引入0016；现行以原CLI status/migrate到0017再一致部署。0016保留原人工凭证，允许空actor_id但仅系统固定名+唯一验证开始事件满足CHECK；旧全额/只追加约束仍在。不要删系统凭证/新列回滚，历史0001–0015不可改；没有替你升级业务库。

新增WX_REFUND_AUTO_RECORD_ENABLED=false，不启用即保持仅查询观察；明确授权并完成专用验收后才与VERIFY一起启用，SEND仍可且应保持false。三个开关不是同一个权限；AUTO只登记可信已完成退款，影响订单下载，不会发送资金。分别重启Web与独立worker（python -m app.refund_worker），仅编辑.env或重启Web不足；管理页显示Web配置，不检测worker健康。关开关也不是在途取消，hold只栅栏所选任务。

检查success_recorded/success_already_recorded/receipt_conflict及系统事件ID，持久凭证/终态审计/任务必须一致；未知数据库故障保留租约，恢复按原号再GET而非发送。原verified/attention不会自动复活，已耗尽仍手工独立查询。进程托管、报警、真实HTTPS/商户及Windows浏览器仍需单独验收，本批未激活生产。


## 第十四批：0017授权版本链

先备份数据库与storage并实际核实恢复，停止**所有Web和worker写入**，用原维护CLI status/migrate到0017，再一致部署并分别重启。旧0001–0016不可改，full_init只用于空库。未替你执行业务迁移或开启发送。旧应用假定一准备一授权，不能与新版本混跑；已有后继后不能删列/删历史回退为单授权模型。

0017撤掉preparation_id的全局单行唯一，新增唯一supersedes_id自外键及每准备一个NULL根的部分唯一索引。新版插入在用户→订单锁下核活跃管理员/正文/摘要、同准备已停止叶子、全单无发送开始/观察/查询/通知/核验/成功凭证，禁止分叉与删改。旧行supersedes_id为空且不回填新授权/停止，不发网络。原号、金额、商户与付款不可更改；新notify_url仍从正式SITE_BASE_URL派生，配置更改不会重写旧正文。

恢复演练只用专用测试库：commit前故障应无后继和审计；commit后丢ACK按原key读首次，不能新增另一后继。仅发送开始丢ACK也必须拒绝改正文。备份须覆盖全部版本、停止及事件，不能只导出当前叶子。进程监督/报警、真实网络/商户/Windows恢复验收仍未代办，不将合成回归当成生产演练。

## 第十五批：可选核验监督与告警检查

完整部署/恢复配方见[核验运行手册](REFUND_OPERATIONS.md)。新增--status-file与独立refund_health检查器，无新schema/依赖/API。私有本机runtime目录与商品storage隔离，不进入镜像构建上下文或Git；一个路径一个写者，不要删除.lock“解锁”。默认120秒freshness不是进程即时探活或商户成功证明。

Compose新增opt-in refund-verifier profile，固定示例db、SEND/AUTO_RECORD=false、无端口，VERIFY仍需显式授权；有限失败重启，覆盖镜像Web健康探针。模板未在本沙箱实际Docker执行，unhealthy本身不触发Docker重启。--alerts的业务attention也不得作为自动重启条件。生产仍需选择接收人/检查周期并实测权限、时钟、磁盘、恢复与真实TLS/商户；本次没有替你启动业务worker或外部告警。

## 第十六批：渠道关单门禁仍默认关闭

新增WX_ORDER_CLOSE_ENABLED=false，独立于退款SEND/VERIFY/AUTO_RECORD；不增迁移/依赖，完整账本仍0017。更新Web及前端产物后，原local closed不会自动触发渠道请求；核验worker也不调用关单。未完成专用商户/HTTPS/恢复验收，不开启业务门禁；本批没有真实关单或业务库操作。

开启只允许符合条件的管理员按钮，仍须原单最新5分钟内可信NOTPAY、手动原单/金额及确认。超时/未知先核原事件，同key仅恢复首次；新key需重新查询、距旧start至少60秒，已ack不再发送。更新/回滚都保留PaymentEvent与原收款/退款全历史，不用删start修复。见[管理手册](PAYMENTS_ADMIN_GUIDE.md)。

## 第十七批：日账CLI与私有报告

`WX_BILL_READ_ENABLED=false`保持默认，不加入Web启动/定时器/退款worker。授权操作另按[日账指南](WECHAT_BILLS_GUIDE.md)确认真实DATABASE_URL生效目标、商户、日期与0017完整账本；只读DB权限/OS账户与网站角色分开。报告保存在私有runtime/wechat-bills，既有Git/Docker忽略规则已排除runtime；不能通过反向代理映射、商品目录、镜像或日志导出这些文件。备份/保留期限与Windows ACL/NTFS硬链接需部署验收；不宣称已完成真实商户、银行结算或生产恢复。
