# 全仓体检报告 · 基于最新代码 `99662ca`（2026-09-06）

> **只诊断，不修改、不提交。** 工作树已 fast-forward 到远端 `arena/01a0599b-codemax-platform` 的最新提交
> **`99662ca`**（无 merge 提交、无 push），除本文件外没有改动任何实现或文档。
> 本文**取代**此前两份基于旧快照 `2e808ef` 的报告（`CODE_REVIEW_2026-09-05.md` / `CODE_REVIEW_2026-09-06.md`，可删）。

## 0. 本轮基线与验证手段

| 项目 | 实测值 |
| --- | --- |
| HEAD | `99662ca`（= 远端最新，比上次审的 `2e808ef` 多 16 个提交 / 73 文件 / +5631 行） |
| `pytest -q`（SQLite） | **561 passed, 4 skipped**（282 s）— 与 README/HANDOVER/AGENTS 记载一致 ✔ |
| `ruff check .` | All checks passed ✔ |
| 探针 | bcrypt 阻塞、用户名边界、密码字节截断、时序差、未登录下单、HEAD/OPTIONS —— 均真实运行后删除 |

**先说结论**：这条分支在我上次看的快照之后又完成了一轮高质量迭代（新增商城前端 `shop.html`、全站登录浮层 `auth.js`、人工确认收款 S5-04、意图三级级联 S4-02-5、语义 FAQ、部分唯一索引 TD-199）。新代码的注释质量与边界处理**明显高于**平均水平——`buySeq` 防乱序、监听器退订、`user_id` 在 flush 前取值防 MissingGreenlet、轮询接口刻意只读，这些都是踩过坑才写得出来的。下面挑出来的是仍然存在或新引入的问题。

---

## 1. 我上一轮结论的处置（诚实对账）

### 1.1 已被上游修复 —— 我的报告作废

| 原编号 | 问题 | 上游修复 |
| --- | --- | --- |
| P0-1 | SSO 换来的 token 对改过密码的用户必 401 | `aaf02c8` / TD-197 ✔ |
| P0-2 | 一次 302 绕过 SSRF 防护 | `4cdfaca` / TD-196，`crawler.py:119` 已改 `follow_redirects=False` 手动逐跳 ✔ |
| P0-3 | 迟到支付不落 `transaction_id` / `paid_at` | `b5ae042` / TD-198，`mock` 与真回调都改成 `in (PENDING, CLOSED)` ✔ |
| P1-1 | 并发下单刷出多张 pending 单 | `6ee6698` / TD-199，部分唯一索引 `uq_sys_order_user_pending` + `IntegrityError` 回读复用 ✔ |
| P1-7(部分) | `python-multipart` 0.0.6 ReDoS | 已升到 **0.0.26** ✔ |
| P2-2 | `cryptography` / `httpx` / `mistune` 声明与实际不符 | 全部显式声明并写了理由 ✔ |
| P2-4 | `build_docs_site.py` docstring 与实现相反 | 已修，且 CI 新增「文档站构建 + 页数核对」job ✔ |
| D-1 | README 把 `/health` 说成"查库" | 已改为"只是 `/healthz` 的别名，同样不查库" ✔ |
| D-4 | DEPLOY 漏 `migrate_0005` | 已补，`migrate_0006` 也已列入 ✔ |
| A-14 | 登录 UI 不在 `<form>` 里、无 label、无 autocomplete | 已重做成全站登录/注册浮层（`base.html:90` 真 `<form>` + label + `autocomplete`）✔ |

### 1.2 我要撤回的一条（我的错）

**A-7「未登录 `POST /shop/orders` 返回 503 泄露支付配置」—— 假阳性，撤回。**
上一轮探针里，同一个 `httpx.AsyncClient` 在更早的步骤登录过，cookie 被自动带上，所以那次请求其实是**已认证**的。本轮在干净客户端上重测：

```
POST /shop/orders  (无凭证)  ->  401  {"detail":"未登录"}
```

依赖注入顺序本来就保证鉴权先于函数体。**是我的探针有污染，不是代码有问题。**

### 1.3 仍然存在（实测复现，行号已更新到 `99662ca`）

| 编号 | 问题 | 位置 | 本轮实测 |
| --- | --- | --- | --- |
| **A-1** | bcrypt 同步跑在事件循环 | `app/routers/auth.py:48/66/90/95`、`app/security.py:18-23` | 5 个并发登录墙钟 **1315 ms**，**事件循环延迟峰值 1292 ms** |
| **A-1b** | `/oauth/token` 也做 bcrypt，且**零限流**、匿名可达 | `app/routers/oauth.py:161` | `grep rate_limit app/routers/oauth.py` → 无 |
| **A-2** | `ENV` 是自由字符串：拼错 → 四项生产硬检查全跳过 **且** 登录 Cookie 丢 `Secure` | `app/config.py:92`、`app/routers/auth.py:39` | 本轮给十几个配置项加了 `Field(gt=0)` 约束，唯独漏了这个最关键的 |
| **A-5** | 用户名零校验 | `app/schemas.py:7` | `'   '` / `'a\nb\tc'` / `'<script>x</script>'` / `admin1`+`ADMIN1`+`Admin1` **全部 201** |
| **A-6** | 密码 bcrypt 静默截断到 72 字节 | `app/schemas.py:8` | `verify("密"*24, hash("密"*64))` → **True** |
| **A-8** | 登录失败时序差 = 用户名枚举 | `app/routers/auth.py:66` | 用户不存在 **8.1 ms** vs 密码错 **263 ms**，差 **33×** |
| **A-9** | uvicorn 未信任代理头 → 预签名下载链接退化成 `http://`／内网 host | `Dockerfile:30`（CMD 无 `--proxy-headers`）、`app/routers/shop.py:367` | 未改 |
| **A-10** | DB 密码未 URL 编码 | `app/config.py:105` | 未改 |
| **A-3** | CDN 无 SRI + 浮动大版本 | `er.html:18`（`d3@7`）、`mermaid.html:19`（`mermaid@11`） | 未改 |
| **A-4** | `securityLevel: "loose"` + LLM 输出 | `app/templates/mermaid.html:20` | 未改 |
| **A-15** | 无 `engine.dispose()`；`.dockerignore` 未排除 `tests`；无 `HEALTHCHECK` | `main.py`、`.dockerignore`、`Dockerfile` | 未改 |
| **P1-2** | RAG 检索全表 `select(Article)` + jieba **同步跑在事件循环**，挂在不鉴权的 `/support/ask` | `app/tools/support.py:150` | 未改 |
| **P1-3** | 微信回调**无时间戳新鲜度校验、无 `Wechatpay-Serial` 校验** | `app/wechat_pay.py:178-193` | 只用 timestamp 拼验签串，不比对时间 |
| **P1-4** | `MAX_BYTES` 整包下完才判 | `app/tools/crawler.py:136` | 未改（重定向已修，体积仍未流式） |
| **P1-6** | `/shop/dl` 整包读进内存 | `app/routers/shop.py:354` | 未改 |
| **P1-7** | `python-jose==3.3.0`（CVE-2024-33663 算法混淆 / CVE-2024-33664 JWT bomb，3.4.0 修） | `requirements.txt:9` | 未升；CI 仍无 `pip-audit` |
| **P1-9** | `oauth_code` 只写不清 | — | 未改 |
| **A-12** | 生产自检仍只有 4 项（缺 `SITE_BASE_URL`、`DB_PASSWORD` 空、`SECRET_KEY` 长度、生产用 `STORAGE_BACKEND=local`） | `app/startup_checks.py` | 未改 |
| **D-5** | HANDOVER §3 结构树严重过期 | `HANDOVER.md` §3 | **比上次更过期**：`shop.py` 仍写成"`/shop/ping`（SSO 验证）"，而它已是整条支付链路；缺 8 个 app 根模块、3 个 router、7 个 tools 模块，也没有 `shop.html` / `auth.js` |
| **D-6** | ROADMAP 难点表仍写"BERT 意图路由" | `ROADMAP.md:25` | 未改（正文 L135-136 是诚实的，只有速查表没跟上） |

---

## 2. 🆕 新代码引入的问题

### N-1 ⛔ 启动时 `await warm_semantic_index()` 会把启动挂在一次外部 HTTP 调用上，最长 60 秒

- `main.py:33` 在 lifespan 里 **`await`** 语义索引预热，它会真打一次 `/embeddings`（`app/tools/llm.py:97`），而 `LLMClient.timeout` 默认 **60.0 s**（`llm.py:55`）。
- 注释写着"best-effort，绝不让启动失败"——**异常确实吞了，但时间没兜住**。LLM 网关抽风/被墙/DNS 黑洞时，应用要等满 60 秒才开始监听业务流量。容器编排器看到的是"启动探针一直不过"，可能直接判定失败并反复重启；滚动发布时每个副本都要各挨一次。
- 而且注释里"刻意 await 而不是丢后台任务"的理由（避免行为随机）是成立的，所以**正解不是改成后台任务，而是给预热单独设一个短超时**：例如 `dataclasses.replace(default_llm, timeout=5.0)`，超时就退回词袋并记 WARNING。
- 相关：预热失败后**没有任何重试**，进程在整个生命周期里都退回词袋检索，而 `/support/ask` 不会告诉任何人"语义层其实没在跑"。建议 `/readyz` 或日志里暴露 `semantic_ready()`。

### N-2 🐛 订单过期后，商城页会每 3 秒**永远**轮询下去

`app/templates/shop.html:164`：

```js
if (!o.expired && !timer) timer = setInterval(poll, 3000);
...
if (o.status !== "pending") stop();
```

- 订单过期（`expired=true`）但状态仍是 `pending` 时：**不满足** `o.status !== "pending"` 所以不 `stop()`，而 `show("pending")` 也只在切换到别的分组时才清定时器。于是一张已经失效的二维码页面会以 3 秒一次的频率**无限**打 `/shop/orders/{no}`。
- 用户开着标签页去吃个午饭 = 1200 次请求；而 `GET /shop/orders/{order_no}`（`shop.py:167`）**没有挂任何限流**，每次都要查一次库。
- 修法：`render()` 里 `if (o.expired) stop();`，或给轮询加总时长上限与指数退避。

### N-3 🔓 人工确认收款（S5-04）**没有在数据库里留下操作人**

`app/routers/shop.py::confirm_paid_manually` 返回体里有 `"confirmed_by": admin.username`，但那只是 HTTP 响应——**库里没写**。`sys_order` 有一个现成的 `remark VARCHAR(255)`（`models.py:106`）也没用上。

- manual 模式的语义是"机器收不到回调，所以由人告诉系统钱到了"。这条路径**唯一的凭证就是那次点击**，事后却查不到是哪个管理员、基于什么确认的（`transaction_id` 只写死成 `MANUAL-<order_no>`，不含任何收款流水信息）。
- 多管理员场景下，误确认/内部舞弊没有任何追溯手段；访问日志（`middleware.py`）记的是路径与状态码，不记用户。
- 修法：把 `confirmed_by` + 确认时间（+ 可选的收款流水号入参）落到 `remark` 或新增列；这属于钱的路径，值得一条 TD。

### N-4 ⚙️ `.coveragerc` 已入库，但**没有任何流程会执行它**

- 文件本身写得很好（`concurrency = thread,greenlet` 那条踩坑记录很有价值，TD-201）。
- 但 `pytest-cov` **不在 `requirements.txt` 里**，`pytest.ini` 没有 `--cov`，CI 三个 job 也没有任何覆盖率步骤（本轮新增的是"文档站构建"job）。
- 也就是说：89% → 97% 这个数字是某次手工测量的快照，**不会随代码演进被复核**，下次有人想跑还得先手动装包。要么把它接进 CI（哪怕不设阈值，只上传报告），要么在文件头写明"仅供手工排查"。

### N-5 📉 文档数字漂移一处

`TECH_DECISIONS.md:31`（TD-80 那行）写着 **"SQLite 541 + 4 skipped"**，实测是 **561**。同一份文档里其它 15 处 `561 passed` 都是对的，只有这一处漏改。（我把 `.claude/skills/*`、AGENTS、HANDOVER、README、tests/README 的 561/563 全查过一遍，其余一致；HANDOVER 的"36 个测试文件 / 38 个 .py / 565 用例"也与实际相符 ✔。）

### N-6 小事

- `app/templates/shop.html` 用 `box.innerHTML = o.qr_svg` 注入服务端生成的 SVG。内容来自 `segno`、可信，注释也解释了为什么内联；但同一段代码里对 `qr_image` 特意避开了 `innerHTML`——两处口径不一致，建议统一（`DOMParser` 解析后 `appendChild`）。
- `GET /shop/orders/{order_no}` 是给 3 秒轮询用的只读接口，没有 `Cache-Control: no-store`，也没有限流。
- CI 三个 job 都没有 `timeout-minutes`；仍无 `pip-audit`。

---

## 3. 建议的修复顺序

1. **A-1 / A-1b** —— 唯一匿名可触发的可用性缺陷（1.3 秒事件循环停顿），且 `/oauth/token` 零限流。
2. **N-1** —— 一次外部 HTTP 抽风就能让启动卡 60 秒。
3. **A-2**（`ENV` 用 `Literal`）、**A-8**（消时序差）、**A-5**（用户名校验）、**A-6**（密码字节长度）—— 都在十几行内。
4. **N-3**（人工收款审计）、**N-2**（过期后无限轮询）—— 钱的路径与前端资源浪费。
5. **P1-7**（`python-jose` 升级 + `pip-audit` 进 CI）、**A-9**（代理头）、**A-12**（启动自检）。
6. **A-3 / A-4**（前端 CDN 与 mermaid 加固）、**P1-2 / P1-3 / P1-4 / P1-6 / P1-9**。
7. **N-4 / N-5 / D-5 / D-6 / A-15** 工程与文档收尾。

---

## 附：可直接粘给另一个助手执行的提示词（对齐 `99662ca`）

```text
你在 cccjvav/codemax_platform 仓库工作（FastAPI + SQLAlchemy 2.0 async + PostgreSQL + Jinja2 SSR）。
当前基线是 arena/01a0599b-codemax-platform 的 99662ca：pytest -q = 561 passed / 4 skipped（SQLite），
真库 563 passed / 2 skipped，ruff check . 全绿。
先读 AGENTS.md 并严格遵守：ruff + pytest 双闸门、一个子项一个提交、新取舍写进 TECH_DECISIONS.md、
同步 docs/ARCHITECTURE_GUIDE.md、文档里记过的数字全仓同步、
修 bug 必须先补一个能复现它的回归测试（先看它红，再改实现，再看它绿）。

================ 第一梯队 ================

【A-1】bcrypt 同步跑在事件循环，一次登录让全站停顿 0.3 秒
  实测（99662ca）：bcrypt verify 287ms；5 个并发登录墙钟 1315ms，事件循环延迟峰值 1292ms。
  位置：app/routers/auth.py:48/66/90/95 调 app/security.py:18-23 的同步 passlib API。
  放大器：app/routers/oauth.py:161 的 /oauth/token 也做一次 bcrypt，且该端点**没有任何 rate_limit**
  （grep 可证），匿名 4 QPS 即可把单进程实例 p99 拖到秒级。
  修：bcrypt 调用改走 run_in_threadpool（或复用 app/cpu_pool.py）；给 /oauth/token 挂
  rate_limit("token","RATE_LIMIT_AUTH")。补测试：并发 N 次登录时事件循环延迟（asyncio.sleep 漂移）
  不超过阈值。这与 TD-159/183/186/193「重 CPU 不留在事件循环」是同一条原则，改完补 TD。

【N-1】启动被一次外部 HTTP 调用挂住，最长 60 秒
  main.py:33 在 lifespan 里 await warm_semantic_index()，它真打 /embeddings
  （app/tools/llm.py:97），而 LLMClient.timeout 默认 60.0（llm.py:55）。异常吞了但时间没兜住：
  LLM 网关不可达时应用要等满 60 秒才开始服务，编排器会当成启动失败并反复重启。
  修：预热用单独的短超时（如 replace(default_llm, timeout=5.0)），超时退回词袋并记 WARNING；
  保留「await 而不是后台任务」的原设计（理由见该处注释）；另把 semantic_ready() 暴露到日志或 /readyz。

【A-2】ENV 拼错一个字母就静默关掉所有生产防护
  app/config.py:92 ENV 仍是自由字符串。写成 "Production" 时 startup_checks.py:21 直接 return []
  （四项硬检查全跳过），且 app/routers/auth.py:39 的 secure=settings.ENV=="production" 变 False
  （登录 Cookie 丢掉 Secure）。本轮已给十几个配置加了 Field 约束，唯独漏了这个最关键的。
  修：ENV: Literal["development","production"]。

【A-8】登录失败的耗时差是用户名枚举侧信道
  实测：用户不存在 8.1ms（短路不跑 bcrypt）vs 密码错 263ms，差 33 倍。/oauth/token 对未知
  client_id 同理。修：不存在时也对固定 dummy hash 跑一次 verify。

【A-5 / A-6】注册入参边界
  A-5：app/schemas.py:7 只有长度限制，实测 '   '、'a\nb\tc'、'<script>x</script>'、
      admin1/ADMIN1/Admin1 全部 201。SSO 下第三方按 username 关联账号会张冠李戴。
      修：字符集正则 + strip + 大小写不敏感唯一（PG: unique index on lower(username)），
      并为存量数据准备 migrate_0007。
  A-6：max_length=64 卡的是字符数，bcrypt 卡的是 72 字节。实测 verify("密"*24, hash("密"*64)) 为 True。
      修：按 UTF-8 字节数校验并给人话提示（或先 SHA-256 再 bcrypt），补 TD。

================ 第二梯队 ================

【N-3】人工确认收款没有落库审计
  app/routers/shop.py::confirm_paid_manually 只在**响应体**里回 confirmed_by，库里没写；
  transaction_id 写死成 MANUAL-<order_no>，不含任何收款信息；sys_order 已有的 remark 列没用上。
  manual 模式下这次点击是唯一收款凭证，却查不到是谁、依据什么确认的。
  修：把确认人与确认时间（可选：收款流水号入参）落库，补 TD，并在 tests/test_manual_pay.py 加断言。

【N-2】订单过期后前端无限轮询
  app/templates/shop.html:164：expired=true 但 status 仍是 pending 时，既不满足 stop() 条件、
  也不会清定时器，于是每 3 秒打一次 GET /shop/orders/{no}（该端点无限流、每次查库）。
  修：render() 里 o.expired 即 stop()，或加轮询总时长上限 + 退避；顺带给该端点加 Cache-Control: no-store。

【P1-7】python-jose 仍是 3.3.0（CVE-2024-33663 算法混淆 / CVE-2024-33664 JWT bomb，3.4.0 修）
  升级并把 pip-audit 接进 CI 的 lint job；给三个 job 都加 timeout-minutes。

【A-9】反向代理后下载链接退化成 http:// 或内网地址
  app/routers/shop.py:367 用 request.base_url 拼预签名 URL，但 Dockerfile:30 的 uvicorn CMD
  没有 --proxy-headers / --forwarded-allow-ips（默认只信任 127.0.0.1 的转发头），
  compose 里代理与应用不同 IP → 拿到 http:// 或内部 host → HTTPS 页面按混合内容拦截。
  修：CMD 加 --proxy-headers --forwarded-allow-ips=*，或改用 SITE_BASE_URL 拼；
  并在 docs/DEPLOY.md 说明「应用的 TRUST_PROXY_HEADERS」与「uvicorn 的转发头信任」是两套开关。

【A-12】补齐生产启动自检（现在只有 4 项）
  补：SITE_BASE_URL 仍是默认/localhost、DB_PASSWORD 为空、SECRET_KEY 长度 < 32
  （现在只比对是否等于默认值）、生产用 STORAGE_BACKEND=local。

【A-10】app/config.py:105 拼库连接串时对 DB_USER / DB_PASSWORD 做 quote_plus
  （密码含 @ : / # ? 时会连错主机）。

================ 第三梯队 ================

【A-3】er.html:18 的 d3@7、mermaid.html:19 的 mermaid@11 是浮动大版本且无 integrity，
  而 CSP 白名单是整个 cdn.jsdelivr.net；同源页面上就是全站登录浮层。
  修：钉精确版本 + integrity + crossorigin，或 vendor 进 app/static/ 并收紧 CSP。
【A-4】mermaid.html:20 的 securityLevel:"loose" 关掉了净化，而输入是 LLM 依据用户任意文本生成的。
  改回默认 strict 或过滤输出。
【P1-2】app/tools/support.py:150 的 RAG 检索仍是全表 select(Article) + jieba，同步跑在事件循环，
  且挂在不鉴权的 /support/ask。至少 run_in_threadpool，最好加缓存或改 PG 全文检索。
【P1-3】app/wechat_pay.py:178 verify_notify_signature 补 Wechatpay-Timestamp ±5 分钟新鲜度校验
  与 Wechatpay-Serial 校验。
【P1-4】app/tools/crawler.py:136 的体积上限改成流式累计（现在整包下完才判）。
【P1-6】app/routers/shop.py:354 /shop/dl 改 FileResponse/StreamingResponse。
【P1-9】给 oauth_code 加过期清理。
【A-15】main.py lifespan 补 await engine.dispose()；.dockerignore 补 tests；Dockerfile 加 HEALTHCHECK
  并显式 --workers 1（限流 TD-141、robots 缓存 TD-171、进程池都假设单进程）。
【N-4】.coveragerc 已入库但没有任何流程执行它：pytest-cov 不在 requirements、pytest.ini 无 --cov、
  CI 无覆盖率步骤。要么接进 CI（哪怕只上传报告不设阈值），要么在文件头写明「仅供手工排查」。
【N-5】TECH_DECISIONS.md:31（TD-80 那行）写的「SQLite 541 + 4 skipped」应为 561。
【D-5】HANDOVER.md §3 结构树严重过期：shop.py 仍写成「/shop/ping（SSO 验证）」，
  缺 middleware/ratelimit/storage/wechat_pay/order_state/cpu_pool/timeutil/startup_checks、
  routers/{admin,health,support}、tools/{crawler,extract,faq,intent,politeness,support,browser}，
  也没有 templates/shop.html 与 static/auth.js。按当前代码重写。
【D-6】ROADMAP.md:25 难点速查表仍写「BERT 意图路由」，实际是「规则 → 语义 → LLM 判定」三级级联
  （S4-02-5，正文 L135-136 已说清）。把表格改成与正文一致。
【N-6】app/templates/shop.html 对 qr_svg 用 innerHTML、对 qr_image 却特意避开 innerHTML，
  两处口径统一一下（DOMParser + appendChild）。

做完后：ruff check . 全绿；pytest -q 全绿（条数会变，按 AGENTS.md 的扫描命令把全仓文档里的数字
一次性同步，注意 .claude/skills/ 下四个 SKILL.md 也记着基线数字）；每个子项一个提交并 push 到
当前会话分支；不要合并 PR。
```
