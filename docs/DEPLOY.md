# 部署与上线（S5-03）

本文只覆盖**能用代码和配置文件解决**的部分。域名解析、SSL 证书签发这类
必须在云控制台/域名商处操作的步骤，只说明该做什么，不假装能自动化。

---

## 1. 生产环境必须配好的 .env

`ENV=production` 会在**启动时**做自检，不合规直接拒绝启动（`app/startup_checks.py`）。
宁可起不来，也不要"跑着但行为是错的"——那几项配错的后果都是等到用户下单才发现。

| 配置 | 生产要求 | 配错的后果 |
|---|---|---|
| `ENV` | `production` | 不开启下面这些检查 |
| `SECRET_KEY` | 长随机串，**不能**是 `dev-secret-change-me` | 任何人都能伪造 JWT |
| `SHOP_PAY_MODE` | `wechat` | 若是 `mock`＝**免费发货**（TD-124） |
| `RATE_LIMIT_ENABLED` | `true` | 公开的 `/tools/*` 可被无限刷（TD-15） |
| `TRUST_PROXY_HEADERS` | `true`（在反向代理之后） | 所有用户被当成同一个 IP，限流形同虚设（TD-142） |
| `DB_PASSWORD` | 真实密码 | 连不上库，`/readyz` 返回 503 |

启动失败时会一次性列出**全部**问题，不是报一个改一个。

---

## 2. 用 Docker 起（推荐）

```bash
docker compose up -d --build
```

`docker-compose.yml` 会：
- 起一个 PostgreSQL 16，**首次启动**自动执行 `database init/full_init.sql` 建表
  （挂到 `/docker-entrypoint-initdb.d/`；已存在的库不会重复初始化）

> **已有数据的库不会自动升级。** `full_init.sql` 开头是 `DROP TABLE ... CASCADE`，
> 对已有库跑它等于清库。升级请用增量脚本，按编号顺序执行（每个都可重复跑）：
>
> 迁移脚本没有挂进容器（compose 只挂了 `full_init.sql`），所以从宿主机用管道喂进去；
> `-T` 是关掉伪终端，少了它 stdin 重定向不生效：
>
> ```
> docker compose exec -T db psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 < "database init/migrate_0001_timestamptz.sql"
> docker compose exec -T db psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 < "database init/migrate_0002_password_changed_at.sql"
> docker compose exec -T db psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 < "database init/migrate_0003_diagram_deleted_at.sql"
> docker compose exec -T db psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 < "database init/migrate_0004_diagram_version.sql"
> docker compose exec -T db psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 < "database init/migrate_0005_user_role.sql"
> docker compose exec -T db psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 < "database init/migrate_0006_order_single_pending.sql"
> ```
>
> 0001 = 时间列统一 `TIMESTAMPTZ`（TD-146）；0002 = `sys_user.password_changed_at`（TD-70）；
> 0003 = `sys_diagram.deleted_at` + 索引升级（TD-64）；0004 = `sys_diagram.version` 乐观锁列（TD-65）；
> 0005 = `sys_user.role` 管理员角色列（TD-138/188）；
> 0006 = `sys_order` 部分唯一索引「同一用户最多一张待支付单」（TD-199）。
>
> ⚠️ **0006 执行前必须先清存量重复**：同一用户若已有 ≥2 张 pending 单，建唯一索引会失败。
> 脚本头部给了排查与批量关单的 SQL，先跑排查那条确认再决定。
> 全新部署只需 `full_init.sql`，不用跑这些。
- 等数据库健康检查通过后再起应用
- 给应用注入 `DB_HOST=db`、`TRUST_PROXY_HEADERS=true`

`.dockerignore` 已排除 `.env`、`.venv`、`.git`、`storage`，密钥不会进镜像
（有测试守着：`test_dockerfile_does_not_copy_secrets_or_venv`）。

⚠️ compose 里**没有**反向代理和 SSL。生产上必须在前面加一层 nginx 或云负载均衡。

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
  —— **只在 `X-Forwarded-Proto: https` 且 `TRUST_PROXY_HEADERS=true` 时**才下发。
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
就能在日志里精确定位那一次请求。上游网关若已给了 `X-Request-ID`，应用会沿用，
这样一条链路能串起来查。异常也会记一行（带堆栈），500 在日志里不会是空白。

日志级别用 `LOG_LEVEL` 控制。

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
- **爬虫 robots.txt / 限速**（TD-133）：与部署无关，但上线前应当处理。
