# 部署与上线（S5-03）

本文只覆盖**能用代码和配置文件解决**的部分。域名解析、SSL 证书签发这类
必须在云控制台/域名商处操作的步骤，只说明该做什么，不假装能自动化。

---

Windows/conda 开发运行、备份演练和自动化测试见 [本机指南](WINDOWS_CONDA.md)；浏览器与真实外部服务的签收标准见 [验收手册](ACCEPTANCE_GUIDE.md)。本机 mock 成功不等于生产商户联调通过。

## 发布阻断与日志边界（2026-09-15）

本指南不是生产验收证明。默认种子已分离、迁移账本已落地；支付对账/商品权益、第三方 OAuth 范围、备份恢复仍未结项，见[当前台账](../review/README.md)。不要把默认 development 示例或新建两遍 full_init 当作安全上线/无损升级。

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
    }
}
```

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
`/readyz` 失败时编排器只是把实例摘出负载均衡，等它恢复，不重启。
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

先停止写入，备份数据库及整个storage（含`.snapshots`），验证恢复后运行维护CLI的migrate，当前应确认完整账本至0013已登记。不要更改0001–0011旧文件或删账本。存储需支持同卷硬链接；文件复制512MiB/30秒、每进程两槽，失败即拒绝下单。更换STORAGE_LOCAL_ROOT时连同快照搬迁，不能只改环境变量。

历史渠道/交付字段未绑定的订单需管理员按原合同核准一次；不要批量套用当前配置。人工确认现在必填参考号、金额和核账依据；查看持久ledger，不拿stdout当唯一凭证。退款/自动对账和真实商户仍阻断，接口与恢复边界见[第三批](../review/RELEASE_BLOCKERS_PHASE3.md)。

## 第四批配置与管理入口

第四批当时无需新迁移/依赖；当前部署还必须完成下方至0013的全部迁移，保留全部快照。顶栏/admin/payments提供原单管理，不要开放生产/docs代替管理。Native应答现在也要求平台验签，不完整或已轮换凭据会失败并保留未知尝试；先经可信渠道配置证书/公钥ID并校准NTP。代理不能删微信四个验签头，服务端拒绝压缩与跳转。生产SITE_BASE_URL也作为Cookie财务写的来源基准，代理转发头仍须受信配置，不能用放宽所有来源解决403。

管理员可对当前凭据所属原单显式只读查微信并原子补记可信SUCCESS；该查单入口不发送远程关单/退款，不自动撤权，历史异常不等于待办结案。过程、故障恢复和外部签收见[工作台手册](PAYMENTS_ADMIN_GUIDE.md)。

第五批当时仅新增现有事件类型和读写界面，使用0010，无新服务/迁移。GET不扫描写任务，管理员主动查看；复核写增加同源/角色校验，后台或跨源客户端需遵循工作台手册。数据库和历史证据继续一起备份，完整退款/资金结案仍未上线。

## 第六批：0011与订单绑定下载门禁必须一起部署

第六批新增0011；当前完整账本为0013，见第八批。停止写入、备份数据库和storage、确认可恢复后，通过原维护CLI执行migrate并核对status；没有执行你的业务库。新表只记录已核验成功的单笔全额退款，不回填历史退款。应用发布后旧key-only短链403，未退款客户可从订单历史重领；缓存/CDN不得缓存私有下载响应。

不要直接回滚到不识别refund_receipt的旧下载应用，否则已退款订单可能重新获权。回滚需要停发下载，并单独审查数据库与应用的一致性；已接纳的传输/已下载副本无法召回。仅本地存储已实现，未来云直连也须等价逐次授权。退款查询、人工记录的限制及操作见[管理手册](PAYMENTS_ADMIN_GUIDE.md)。退款申请、部分退款记账/权益、服务生命周期、真实商户与生产恢复演练仍未验收；通知仅接入下面的入站线索流程。


## 第七批：退款通知地址是独立接收口

`https://你的正式域名/shop/refunds/notify`与收款WX_NOTIFY_URL分开；该通知子项无新环境变量/迁移，当前应用还需0013。微信商户平台退款配置可登记默认通知URL，外部退款申请的显式notify_url会覆盖它；只有真实配置/指定到本站地址才可能收到对应通知，不能改收款地址来假装接通退款，不能承诺补发旧通知。可信商户/app/APIv3密钥和平台证书/显式公钥ID继续复用，不从入站请求下载密钥。

代理不得改写签名正文、重复签名头或自动压缩；接收64KiB/4秒应用预算，渠道要求5秒，应在专用环境实测时钟、TLS/代理、负载与数据库阻塞。不要用管理员登录中间件拦住服务器回调，也不能绕过路由验签。失败保留渠道重试，监测非2xx和需要复核清单；本批没有主动报警/消费者。备份PaymentEvent和原收款一起保存，204仅保证入站提交成功，退款成功/撤权仍走独立管理查询。无需也不得为此重建业务库；没有操作你的实际部署。


## 第八批：0012本地准备台账

无新依赖/环境开关。备份数据库与storage、停写并确认恢复方案后，用原维护CLI status/migrate到0012；不能重复init或删历史账本。新表不回填旧准备，原收款/退款/下载权益不变；production启动拒绝缺0012的包或未升级账本。此次没有执行你的部署/业务库。

这不是开启自动退款的发布：第八批准备POST仅保存；第九批另加默认关闭的独立授权和显式发送，没有定时消费。发送需要冻结完整请求及原号未知恢复，禁止自动消费历史准备。数据库连同PaymentEvent/RefundRequest一起备份；任何回滚都先停写评估版本与准备记录一致性，不删表换号。既有退款下载门禁仍不可绕过。


## 第九批：0013与默认关闭发送

当前完整账本0013；旧0001–0012不可改。按原CLI备份/停写/迁移/核验，绝不业务库pytest或重复init。WX_REFUND_SEND_ENABLED=false是默认；设true会开放可能转款的显式管理员按钮，不能在未完成商户/TLS/恢复验收时开启。没有worker，不能据此批量发送历史准备/授权。

SITE_BASE_URL必须是HTTPS域名来源；授权冻结其/shop/refunds/notify（无参数、最多256字节），覆盖商户平台默认退款回调。改域名不改历史请求正文，必须保留原回调可达或另行受控方案。切换商户/app不得发送旧商户请求。备份授权表和PaymentEvent；回滚先关发送/停写，禁止删授权/尝试换号。此次仅在可丢弃库执行，未操作部署。
