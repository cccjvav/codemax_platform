# 技术实现取舍清单（TECH DECISIONS）

> 本项目所有**实现层面的取舍**集中在此，按编号引用（如 TD-41）。
> 模块 docstring 里只留与该文件强相关的局部说明，全局取舍以本文为准。
> 更新日期：2026-09-01 ｜ 对应代码：分支 `arena/01a0599b-codemax-platform`（本文随代码同步更新，不钉 commit）

## 阅读方式

- **取舍**＝选了什么；**放弃了什么**＝另一条路；**代价**＝现在要承受的后果；**何时回头改**＝触发条件。
- 大部分取舍是"毕设阶段够用、且符合 AGENTS.md 第 1 条（代码尽量简洁）"的结果，不是能力缺失。

## ⚠️ 上线阻塞项（这些必须在上线前处理，不是可选项）

| 编号 | 事项 | 为什么阻塞 |
| --- | --- | --- |
| ~~TD-15~~ | ~~公开工具端点无限流~~ **已解决** | `app/ratelimit.py` 内存滑动窗口：工具 30 次/60s、LLM 10 次/60s、注册登录 10 次/60s，超额 429 + `Retry-After`（多实例部署的代价见 TD-141） |
| TD-44 | JWT 存 localStorage | 一旦有 XSS，token 直接被读走 |
| TD-64 | 流程图无配额、无软删除 | 可被刷库；删除不可恢复 |
| TD-70 | JWT 无 `jti`、无法吊销 | 改密码/封号后旧 token 仍然有效 |
| ~~TD-80~~ | ~~集成测试跑在 SQLite 上~~ **已解决** | 现在 `TEST_DATABASE_URL` 可整套跑真 PostgreSQL 16.2（真库 209 passed / SQLite 208+1 skip），见 TD-121 |
| ~~TD-84~~ | ~~无 CI~~ **已解决** | `.github/workflows/ci.yml`：三个 job（ruff 静态检查 / SQLite / 真 PostgreSQL 16 service 容器），PG job 另建库把建表脚本连跑两遍验证幂等。已实跑：run 33512433132（push）与 33512433325（pull_request）均 `success`，两个 job 全部 step 通过。注释头也已在 `4b145b5` 修正（原先 `2f3223a` 纯重命名时把激活前那段「待激活/从未跑过」的注释一起搬了进来）。GitHub App 已于 2026-09-01 取得 Workflows 写权限，workflow 可直接改并 push |
| TD-90/91 | 无日志、无监控、无安全响应头 | 线上出问题无法定位 |
| TD-113 | 微信支付未经真机联调 | 沙箱无商户号/证书/公网回调，签名与报文只能算法级验证 |
| TD-109 | 无超时关单，二维码过期后订单一直挂着 | 待支付单会无限堆积，且过期二维码扫码必失败 |
| TD-124 | 模拟支付通道若在生产误开＝免费发货 | 默认是 `wechat`，且生产模式下两个端点一律 404；上线前务必确认 `.env` |
| TD-133 | 爬虫不读 robots.txt、不限速、无抓取间隔 | 可能被目标站封 IP，也有合规风险 |

---

## 一、技术选型（语言与库）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-01 | 导出 Word 用 `python-docx`（`app/tools/word.py`） | Apache POI（Java 库） | 无 | 除非要复用 Java 生态 |
| TD-02 | 爬虫用 `httpx` + `BeautifulSoup4`（阶段四落地） | HttpClient + Jsoup（Java 栈） | 无 | — |
| TD-03 | 动态页面抓取推迟到阶段四再定 Selenium / Playwright | 现在就锁定方案 | 阶段四开工前要先做一次选型 | S4-01-1 开工时 |
| TD-04 | 前端 Jinja2 SSR，不引入 Node / Nuxt / Next | SSR 框架的水合、路由、构建能力 | 交互全靠手写原生 JS，无组件复用 | 页面数量或交互复杂度显著上升时 |
| TD-05 | DDL 解析用正则 + 自写字符扫描器，不引入 sqlparse / sqlglot | 现成语法树的完备性 | 需自己维护转义、注释、括号边界（已因此修掉 10 个 bug） | 要支持存储过程、触发器、分区表等复杂 DDL 时 |
| TD-06 | 依赖全部钉死版本（`requirements.txt` 20 行中 18 行带 `==`） | 自动获取补丁更新 | 需手工升级 | — |
| TD-07 | 运行时不引入 Node，`node` 仅用于测试期 | — | 测试环境需要 node（沙箱内置 v22） | — |

## 二、DDL 解析器（`app/tools/sql_ddl.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-10 | 多词类型只取首词 | 完整类型名 | `DOUBLE PRECISION`→`DOUBLE`、`CHARACTER VARYING(50)`→`CHARACTER`，ER 图类型显示不精确 | 需要精确类型（反向生成建表脚本、字段级校验）时 |
| TD-11 | `DEFAULT` / `COMMENT` 只认单引号字面量 | MySQL 的双引号字符串 | MySQL 下 `COMMENT "x"` 取不到值。不放开是因为 PostgreSQL 里双引号是标识符，放开会误判 | 明确只服务 MySQL 时加方言开关 |
| TD-12 | 不支持 PostgreSQL 美元引用字符串 `$$ ... $$` | — | 含 `$$` 的 DDL（少见）会解析错乱 | 需要解析函数体 / 触发器时 |
| TD-13 | 未闭合的字符串 / 块注释视为延续到结尾 | 错误恢复能力 | DDL 本身语法有错时，出错点之后的内容全部失效 | — |
| TD-14 | 悬空外键（目标表不在同一份 DDL 内）在图上直接丢弃（`er.js:51`） | 用虚线标出"未解析的引用" | 用户看不出自己漏贴了哪张表 | 有用户反馈困惑时加提示 |
| TD-15 | `POST /tools/er-diagram`、`/tools/mermaid`、`/tools/word-export` 均不鉴权（引流工具要 SEO 收录、要游客能直接用），改用**按 IP 限流**防滥用 | 鉴权这道防线 | 限流是进程内存的（TD-141），且不认登录身份，同一 IP 下的多个用户共享配额 | 需要按用户配额或分布式限流时 |
| TD-16 | DDL 上限 20000 字符（`schemas.py:26`） | 超大脚本 | 超限返回 422 | 有真实大 schema 需求时 |

## 三、LLM → Mermaid（`app/tools/llm.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-20 | 只实现 OpenAI 兼容 `/chat/completions`，不做 provider 抽象 | LangChain / 多 provider 策略模式 | 换非兼容协议的模型要改代码 | 需要接入不兼容 OpenAI 协议的模型时 |
| TD-21 | `temperature` 固定 0.2，无重试、无流式输出 | 可调参数、失败重试、打字机效果 | 偶发失败直接 502 给用户 | 上线后按失败率加重试与流式 |
| TD-22 | 只校验首关键字属于 Mermaid 图类型，不校验语法 | 语法级校验（需引入 mermaid parser） | 语法错的图会在前端渲染失败 | — |
| TD-23 | Prompt 硬编码为模块常量 `SYSTEM_PROMPT` | Prompt 版本管理 / AB 测试 | 改 Prompt 需要发版 | 需要持续调优时 |
| TD-24 | 未配 `LLM_API_KEY` 时直接 502，不做本地兜底 | 模板化兜底输出 | 演示环境未配密钥时该工具不可用 | — |
| TD-25 | 请求超时固定 60s | 按模型分级超时 | 慢模型会让请求长时间挂住 | — |
| TD-26 | 输入上限 10000 字符（`schemas.py:30`） | 长代码输入 | 超限返回 422 | — |

## 四、Word 导出（`app/tools/word.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-30 | 固定 6 列：字段 / 类型 / 主键 / 可空 / 默认值 / 注释 | 索引、唯一约束、外键明细列 | 导出的数据字典不含索引信息 | 需要交付正式数据字典时 |
| TD-31 | 文件名固定 `data_dictionary.docx` | 按表名 / 时间戳命名 | 多次导出同名覆盖 | — |
| TD-32 | 用 python-docx 默认模板 + `Table Grid` 样式 | 企业模板 / 封面页 | 样式朴素 | — |
| TD-33 | 只输出文字表格，不内嵌 ER 图图片 | 图文并茂 | 需在 Word 里自行插图 | 需要图文一体时（要引入图片渲染依赖） |

## 五、前端与渲染

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-40 | d3 / mermaid / drawio 全部走公网 CDN（jsdelivr、`embed.diagrams.net`） | 本地打包自托管 | 离线、内网或 CDN 被墙时页面不可用 | 部署到国内且 CDN 不稳时改自托管 |
| TD-41 | ER 图用固定网格布局（每行 3 个，`er.js` `COLS_PER_ROW=3`） | 力导向 / 层次布局 | 表多时外键连线交叉、观感一般 | S5-02-2 渲染性能与体验优化时 |
| TD-42 | 样式内联在 `base.html`，不抽 CSS 文件、无构建步骤 | 样式复用与压缩 | 每个页面都加载全部样式（实测 1689 字节，可忽略） | — |
| TD-43 | Drawio 的 `autosave` 事件只更新内存里的 XML，不自动写库（`drawio.html:75`） | 自动云同步 | 用户不点"保存到云端"就会丢改动 | 有用户反馈丢图时加防抖自动保存 |
| TD-44 | JWT 存 `localStorage`（`drawio.html:47`） | HttpOnly Cookie + CSRF | XSS 时 token 可被读走 | **上线前评估改 Cookie** |
| TD-45 | 无 PNG / SVG 导出 | 图片导出 | 只能截图 | — |

## 六、SEO 与站点（`app/site.py`、`app/routers/site.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-50 | 页面路由由 `PAGES` 循环生成，且 `include_in_schema=False` | 显式路由声明 | `/docs` 里看不到页面路由（只有 API） | — |
| TD-51 | sitemap 只输出 `<loc>` | `<lastmod>` / `<changefreq>` / `<priority>` | 搜索引擎抓取优先级信号偏弱 | 内容更新频繁后补 `lastmod` |
| TD-52 | 无缓存头、无 ETag | CDN / 浏览器缓存 | 每次都回源 | 上量后加 |
| TD-53 | 无 i18n | 多语言 | 仅中文 | — |
| TD-54 | Drawio 页面对游客开放（能画不能存） | 所有工具都需登录 | 游客产出的图不落地 | 不改——这本来就是引流设计 |
| TD-55 | S2-02-2「引流→变现」转化路径本轮跳过 | 工具页的转化引导 | 目前工具页没有任何商业化入口 | 商业平台上线后 |

## 七、流程图存取（`app/routers/diagrams.py`）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-60 | 非本人记录一律返回 **404**（不是 403） | 明确告知"无权限" | 调试时不易区分"不存在"与"没权限" | 不换（避免探测他人资源是否存在） |
| TD-61 | 列表接口不返回 `content` 大字段 | 一次请求拿全 | 打开某张图要再发一次请求 | — |
| TD-62 | 列表无分页，按 `update_time` 倒序全量返回 | 分页 / 游标 | 图多时响应变大 | 单用户图数量上百时 |
| TD-63 | `content` 上限 500000 字符（`schemas.py:35`） | 超大图 | 超限返回 422 | — |
| TD-64 | 无版本历史、无软删除、无每用户配额 | 可恢复、防滥用 | 删除即永久；可被刷库 | **上线前加配额** |
| TD-65 | 无协同编辑、无乐观锁 | 多人同时编辑 | 后写覆盖先写 | 有协同需求时 |

## 八、认证与 SSO

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-70 | 无状态 JWT，payload 只有 `sub` + `exp`（`security.py:21`） | `jti` / 黑名单 / 吊销表 | token 无法主动失效，改密码或封号后旧 token 仍有效 | **需要强制下线时** |
| TD-71 | 双平台共用同一个 `SECRET_KEY` | 每平台独立密钥 | 一处泄露影响全平台 | 安全评审要求时 |
| TD-72 | 每次请求按 `username` 查库（`deps.py:20`） | 按主键 id 查 / 缓存 | 多一次非主键查询 | 压测发现瓶颈时 |
| TD-73 | 无 PKCE | 公共客户端的授权码保护 | 移动端 / 纯前端客户端场景偏弱 | 接入纯前端客户端时 |
| TD-74 | `redirect_uri` 精确匹配，不支持多回调或通配 | 灵活性 | 一个客户端只能配一个回调地址 | 客户端需要多环境回调时 |
| TD-75 | 无 `refresh_token` | 长会话 | 到期需重新走一遍授权 | — |
| TD-76 | OAuth 错误统一返回 **400**（含 `invalid_client`） | 401 语义 | 与部分 RFC 建议不一致 | 对接严格客户端时 |
| TD-77 | 授权码 10 分钟有效 + 原子 CAS 消费 | — | — | — |
| TD-78 | 无授权同意页：`/authorize` 需先持有 Bearer token 才签发 | 用户可见的"同意授权"交互 | 不符合经典 OAuth 的用户点同意流程 | 面向真实第三方开放时必须补 |
| TD-79 | 注册时"先查后插"判重（`auth.py`） | 依赖唯一约束 + 捕获 `IntegrityError` | 并发注册同名用户可能返回 500 而非 400。**未修**：该竞态在 SQLite 测试里无法复现，无法为其写出会红的测试 | 有真实并发注册场景时 |

## 九、测试策略

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-80 | 集成测试**默认**跑内存 SQLite（`StaticPool`），但可用 `TEST_DATABASE_URL` 整套跑真 PostgreSQL | 只用一种库 | 要真库覆盖就得跑两遍 | ~~上生产前跑一次真库集成测试~~ **本轮已做到**（起库步骤见 HANDOVER §9） |
| TD-81 | LLM 用假客户端 + `httpx.MockTransport`，绝不真打网络 | 真实模型行为验证 | Prompt 的实际效果无法自动验证 | — |
| TD-82 | ER 前端契约测试用 node 真跑 `er.js`；无 node 时退化为静态字段检查 | 纯 Python 测试 | 需要 node；退化分支覆盖较弱 | — |
| TD-83 | `scripts/check_schema_pg.mjs`（PGlite 真 PG 体检）**不接入 pytest** | 每次改动都验建表脚本 | 需手动跑，且要装约 26MB 的 npm 包 | 有 CI 之后接进去 |
| TD-84 | CI 内容一开始只能入库为 `docs/github-actions-ci.yml`，由仓库管理员 `git mv` 到 `.github/workflows/ci.yml` 才生效 | push 即生效的 CI | `arena-ai-coding-agent[bot]` 当时无 Workflows 写权限，push 被 `remote rejected ... without workflows permission` 拒绝，只能靠仓库管理员 `git mv` / `git am` 绕行。**该限制已于 2026-09-01 解除**（用户授予 Workflows: Read and write），现在 workflow 改动可直接 push。保留本条是为了记录那段历史与绕行办法 |
| TD-85 | 并发类修复（授权码 CAS）在 SQLite 上只验证原子语义；真 PG 上有 `test_code_single_use_under_real_concurrency` 真并发跑 | — | SQLite 跑时该条 skip | ~~有 PG 集成测试环境时补~~ **已补**（去掉 rowcount 检查后真 PG 上 5/5 变红） |
| TD-121 | 测试库可用 `TEST_DATABASE_URL` 切到真 PostgreSQL（`tests/conftest.py`），默认仍是内存 SQLite | 只支持一种库 | 要真库覆盖得跑两遍（SQLite 55s / PG 59s） | — |
| TD-122 | 连真库时引擎用 `NullPool` | 默认连接池（更快） | 每个用例重连；换池会报 `got Future attached to a different loop`（asyncpg 连接绑事件循环，pytest-asyncio 每用例新 loop） | — |
| TD-123 | 沙箱里的 PostgreSQL 用 PyPI 的 `pgserver`（自带 PG 16.2 二进制），数据目录 `/tmp/pgdata` | apt 安装（Debian 源在沙箱不可达） | `/tmp` 不在工作区快照里，沙箱重启后要重新建库 | 有 CI 后改用 services 容器 |
| TD-124 | 加了「模拟支付通道」：`SHOP_PAY_MODE=mock` 时下单不调微信、`code_url` 指向本站模拟收银台、`POST /shop/mock-pay/confirm` 直接置为已支付 | 无商户号时的真实支付联调 | **开着就等于免费发货**（已列入上线阻塞项）；默认 `wechat`，生产模式下这两个端点一律 404 | 拿到商户号后 `.env` 改回 `wechat` 即可，代码不用动 |
| TD-125 | 模拟支付复用与真实回调**完全相同**的状态机与幂等逻辑（`mark_paid`），只在"谁触发迁移"上不同 | 为演示单写一套逻辑 | 无 | 不换（演示跑通的路径＝生产路径，不会漏测） |
| TD-126 | 模拟流水号写成 `MOCK-<订单号>` | 伪造一个像微信的流水号 | 一眼能认出是演示数据，但也意味着上线前要清库 | 上线前清理演示订单 |
| TD-127 | 模拟收银台的 `code_url` 用请求的 `base_url`，不用 `SITE_BASE_URL` | 统一用站点域名 | 反向代理下若 `base_url` 不准，链接会指错 | 需要固定域名时 |
| TD-128 | 云存储只实现**本地后端**；阿里云 OSS / 腾讯云 COS 的适配器刻意不写 | 多云真适配 | 换云厂商要新写一个实现（`Storage` 接口已留好，业务代码不用改） | 拿到 OSS/COS 密钥时（S3-02-1） |
| TD-129 | 「已下载」标记发生在**发出链接时**，不是文件真的被下载时 | 按实际下载记账 | 领了链接没下载也算已下载；链接被转发后在 TTL 内可重复下载 | 需要精确计量时改用云端访问日志 |
| TD-130 | 预签名 URL 自己用 `HMAC-SHA256(SECRET_KEY, "key\nexpires")` 签 | 用云厂商 SDK 签 | 与 `SECRET_KEY` 耦合：换密钥会让已发出的链接立刻失效 | 换云后端时交给 SDK |
| TD-131 | 未知存储后端**直接报错**，不静默退回本地 | 容错回退 | 配置写错会导致下载不可用（503） | 不换（静默降级＝把文件从对象存储挪到应用目录，是安全性降级） |
| TD-132 | 商品只有一个对象 key（`STORAGE_PRODUCT_KEY`） | 商品↔文件映射表 | 加商品要改配置甚至改代码 | 需要多 SKU 时（与 TD-108 一起改） |
| TD-133 | 爬虫**不读 robots.txt、不限速、无抓取间隔** | 合规与礼貌性约束 | 高频抓取可能被目标站封 IP，也有合规风险（已列入上线阻塞项） | 真正批量抓取前必须补 |
| TD-134 | BeautifulSoup 用 stdlib 的 `html.parser`，不用 `lxml` | lxml 对畸形 HTML 更宽容、更快 | 极端畸形的页面解析结果可能有差异 | 遇到解析不出来的真实页面时换 |
| TD-135 | LLM **只负责指认 CSS 选择器**，正文提取仍由 BeautifulSoup 按选择器做 | 让 LLM 直接吐正文 | LLM 选错选择器就提不到内容（可重试） | 不换：让 LLM 吐正文会改写/杜撰原文 |
| TD-136 | SSRF 防护用 `is_global` 且**逐个**检查解析出的地址 | 只查第一个地址 / 只做协议白名单 | 仍存在 DNS rebinding 的理论窗口（校验与请求之间重新解析） | 需要彻底防时改成按已校验的 IP 直连 |
| TD-137 | `published_at` 按源站**原文字符串**存，不解析成 `datetime` | 统一的日期类型 | 不能按时间排序/筛选 | 需要按发布时间排序时再加一个解析后的列 |
| TD-138 | 抓取 + 解析目前只是**服务层函数，没有 HTTP 端点** | 后台管理页一键抓取 | 只能从代码/测试里调用 | 有了管理员角色再加端点 —— 公开端点等于给任何人一个「让服务器抓任意 URL + 烧 LLM token」的入口 |
| TD-139 | 文章去重只看 `url` 唯一，不做正文相似度判断 | 内容指纹去重 | 同一篇文章换个 URL（如带 utm 参数）会重复入库 | 出现明显重复时加正文哈希 |
| TD-140 | LLM 指认的选择器匹配不到就**直接报错**，不自动重试 | 自动重试 N 次 | 页面小改版就会失败，需要人工看 | 需要无人值守跑批时加重试 + 告警 |
| TD-141 | 限流用**进程内存**滑动窗口，不引入 Redis | 分布式限流 | 多进程 / 多实例部署时每个进程各算各的，实际配额变成 N 倍；进程重启配额清零 | 上多实例部署时换 Redis |
| TD-142 | 默认**不信任** `X-Forwarded-For`，只取 socket 对端地址 | 开箱即用的反代支持 | 部署在 nginx 之后所有用户会共用代理 IP 的配额，必须显式打开 `TRUST_PROXY_HEADERS` | 部署到反代之后立刻打开（否则限流过严） |
| TD-143 | 测试里**默认关闭**限流（`tests/conftest.py`） | 全量用例都在限流下跑 | 几十个用例共用同一个客户端 IP，开着会互相挤爆配额；限流本身由 `tests/test_ratelimit.py` 显式打开来测 | 换成每用例独立 IP 时可去掉 |
| ~~TD-144~~ | ~~CI 里把 actions 钉在 `actions/checkout@v4` 与 `actions/setup-python@v5`~~ **已解决** | 仓库管理员 `aa87c52` 已升到 `actions/checkout@v7` / `actions/setup-python@v7`（上游 v7.0.1 / v7.0.0）。实跑验证：run 33524519419（push）与 33524753544（pull_request）均 `success`，step 名已是 `Run actions/checkout@v7`，且两个 job 的 annotations **已为空**——原先那条 `Node.js 20 is deprecated ... forced to run on Node.js 24` 消失。代价：v7 的行为在沙箱里无法预验证，只能靠 push 后 CI 实跑兜底 |

## 十、工程与运维

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-90 | 无日志框架、无监控告警 | 可观测性 | 出问题只能看 uvicorn 控制台输出 | 阶段五 S5-03-3 |
| TD-91 | ~~无限流~~（限流已做，见 TD-15/141）、无 CORS 配置、无安全响应头 | 基础防护 | 跨域策略靠默认，缺 `X-Content-Type-Options` 等安全头 | **上线前**（CORS 与安全头） |
| TD-92 | `SITE_BASE_URL` 默认 `https://codemax.top`，靠 `.env` 覆盖 | 按请求 Host 自动探测 | 配错会产出错误的 canonical / sitemap 绝对地址 | 部署时确认 |
| TD-93 | `.env` 不入库，仅提供 `.env.example` | — | 新环境需手工 `cp` | — |
| TD-94 | `/static` 只放 `er.js`，HTML 全部走 SSR | 静态 HTML | 旧地址 `/static/er.html`、`/static/mermaid.html` 已 404 | 若外部已有旧链接需加 301 |

## 十一、订单与支付（阶段三，进行中）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-100 | 状态机只有 3 个状态：`pending` / `paid` / `downloaded`（`app/order_state.py`） | `closed`（超时关闭）、`refunded`（退款） | 未支付订单不会被自动关闭，超时只能靠 `create_time` 自行判断 | S5-01-1「超时未支付」测试，或需要退款流程时 |
| TD-101 | 重复支付通知按**幂等**处理：已是目标状态时返回 `False` 而不是报错 | "重复通知即异常"的严格语义 | 真正的异常重复通知不会报错，只能靠日志发现 | 接入微信回调后补告警 |
| TD-102 | 状态迁移用原子 CAS（`UPDATE ... WHERE status=<期望值>` + 校验 rowcount） | 先查后改的直观写法 | 迁移结果要靠 rowcount 判断，不能靠内存对象 | 不换（与授权码消费同一套路） |
| TD-103 | 状态机目前只是领域层，**尚无 HTTP 端点** | 可被 API 触发 | 迁移暂时只能在代码与测试里发生 | S3-01-1 下单接口 + S3-01-3 回调接口落地时 |
| TD-104 | 状态值用 `VARCHAR(20)` 字符串，不用 PG 枚举或整数 | 数据库层取值约束 | 非法状态值只能靠应用层 `STATES` 常量拦（已测） | 需要库级强约束时 |
| TD-105 | 先 `json.dumps` 成字符串 → 签这串 → 把同一串 `encode` 后 `content=` 发出（`app/wechat_pay.py`） | `httpx` 的 `json=` 自动序列化 | 报文构造代码略啰嗦 | 不换（已有变异测试：改用 `json=` 会 `InvalidSignature`） |
| TD-106 | 商户私钥以 PEM 文本放 `WX_PRIVATE_KEY`，不落盘、不进 git | 从 `apiclient_key.pem` 路径读取 | `.env` 里多行 PEM 要用双引号并写 `\n` | 需要读证书文件时再加 `WX_PRIVATE_KEY_PATH` |
| TD-107 | 只实现 NATIVE（扫码）一种支付方式 | JSAPI / APP / H5 | 换支付方式要另写一套下单 | 需要小程序内支付时 |
| TD-108 | 下单只有一个 SKU，商品名与价格取自 `SHOP_PRODUCT_NAME` / `SHOP_PRODUCT_AMOUNT` | 商品表 + 多 SKU | 加商品要改配置甚至改代码 | S3-03 商品展示或需要多 SKU 时 |
| TD-109 | 已有未支付订单就复用同一单并返回**缓存的 `code_url`**，不再调微信 | 每次重新下单（可拿到新二维码） | 二维码过期后只能等超时关单，而超时关单还没做（见 TD-100） | 加超时关单或二维码主动刷新时 |
| TD-110 | 下单接口「新建」与「复用」都返回 200，靠响应体 `reused` 区分 | 用 201/200 区分 | REST 语义不如状态码精确 | 需要严格 REST 语义时 |
| TD-111 | 订单先落库再调微信下单 | 先拿到 code_url 再落库 | 微信侧成功而我们落库失败时会出现"用户付了但没单"（回调按 `out_trade_no` 兜底） | 回调实现后确认兜底路径 |
| TD-112 | `transaction_id` 列与 `code_url` 一起加（前者 S3-01-3 才写） | 分两次改表 | 暂时有一列没人写 | S3-01-3 落地即消解 |
| TD-113 | 支付部分只做算法级验证：自签 RSA 密钥验签名、`httpx.MockTransport` 断言请求、AES-GCM 用冻结向量 | 真机端到端联调 | 微信侧接口变更、证书配置错误只能在上线时暴露 | 拿到商户号后补一轮真机联调 |
| TD-114 | 平台证书 / 微信支付公钥放 `.env`（`WX_PLATFORM_CERT`），不自动下载轮换 | 调 `/v3/certificates` 自动拉取、解密并缓存 | 证书轮换后要手动换 `.env`，否则回调**一律**验签失败 | 证书轮换成为运维负担时 |
| TD-115 | 回调端点不走 JWT 鉴权，身份**完全**靠平台证书验签 | 再叠一层 IP 白名单 | 验签一旦有洞＝任何人可伪造支付成功（3 条测试守着） | 需要纵深防御时加 IP 白名单 |
| TD-116 | 回调应答体用微信规定的 `{"code","message"}`，不用 `HTTPException`（那是 `{"detail"}`） | 全局统一的错误响应格式 | `shop` 路由里错误应答有两套写法 | 引入全局异常处理时统一 |
| TD-117 | 非 `TRANSACTION.SUCCESS`、或 `trade_state != SUCCESS` 的通知一律回 200 但不处理 | 回 4XX 让微信重推 | 退款等事件被静默忽略（回 200 是为了不被无限重推） | 做退款流程时 |
| TD-118 | 回调金额与订单金额不符 → 回 400 让微信重推，不吞掉 | 回 200 忽略 | 会被持续重推（刻意的：金额不符必须暴露出来） | 接入告警后改成 200 + 告警 |
| TD-119 | 支付信息与状态迁移在**同一次提交**内完成（先写字段再 CAS），重复通知靠 `mark_paid` 提前返回不覆盖 | `SELECT ... FOR UPDATE` 行锁 / 分两次提交 | 真并发下败者理论上可覆盖，但微信重复通知带的是同一个 `transaction_id`，无害；分两次提交则会留下 `status=paid` 而 `transaction_id` 为空的窗口 | 出现部分支付 / 多笔支付时 |
| TD-120 | 平台证书与「微信支付公钥」两种 PEM 都支持（按 `BEGIN CERTIFICATE` 判断） | 只支持其中一种 | 判断依赖 PEM 头部字符串 | 微信只保留一种形态时 |

---

## 十二、静态检查（ruff）

| 编号 | 取舍 | 放弃了什么 | 代价 | 何时回头改 |
| --- | --- | --- | --- | --- |
| TD-145 | 只引入 **ruff** 一个 linter，规则集在 `ruff.toml` 里**显式列出**（F/E4/E7/E9/I/B/DTZ/ASYNC/SIM/C4） | mypy 静态类型检查；ruff 默认规则集 | 无类型检查兜底，参数/返回值类型写错只能靠测试或运行期发现 | 若出现「类型不匹配」类线上问题，再单独评估 mypy |
| ~~TD-146~~ | ~~`sys_order.paid_at` 用应用侧无时区本地时间，与 `create_time`（数据库时间）不一致~~ **已解决** | 排查后发现同一批无时区 `TIMESTAMP` 列里其实混了**三种**时钟基准：① `create_time`/`update_time` = 数据库服务器本地时间（`CURRENT_TIMESTAMP`）；② `paid_at` = 应用服务器本地时间（`datetime.now()`）；③ `oauth_code.expires_at` = UTC 但被 `.replace(tzinfo=None)` 抹成裸值。**第 ③ 种 ruff 抓不到**——它确实给 `now()` 传了时区，问题出在事后抹掉，DTZ 规则不看这一步。现已统一：10 个时间列全部改 `TIMESTAMPTZ` / `DateTime(timezone=True)`，应用侧一律写带时区 UTC。代价：SQLite 不支持时区，读回来仍是裸值，故 `app/routers/oauth.py` 加了 `_as_utc()` 归一化，否则在 SQLite 上比较会抛 `TypeError: can't compare offset-naive and offset-aware datetimes`（实测 3 个用例红过）。既有数据用 `database init/migrate_0001_timestamptz.sql` 迁移 |

**为什么刻意不引入 mypy**：实测 `mypy app main.py --ignore-missing-imports` 在 26 个源文件里报
21 个错误，逐条看过后大多属于类型摩擦而不是 bug（例如两条 `AsyncClient(transport=...)`
报的是测试注入的 transport 类型不匹配，是测试写法问题）。给一个不是按严格类型写的代码库
上 mypy，产出主要是噪音，会稀释真正有价值的告警。

**ruff 规则集为什么写死**：实测 ruff 0.16.5 的**默认**集是一批具体规则而非按前缀整族启用
——含 B008 却不含 B904/B905，含 RUF007 却不含 RUF001。所以「按前缀 select」比默认更严。
写死后升级 ruff 不会静默引入新规则让 CI 突然变红。

**刻意不选的规则**：
- `E501`（行太长）：实测超 88 列的有 516 行、最长 189 列，中文注释天生宽，强行折行只产生无信息量的 diff。
- `PLR` 整族：参数个数、分支数一类主观规则，噪音大于收益。
- `RUF` 整族：`RUF001/002/003` 会把中文标点判为「歧义 unicode 字符」，本仓库全是中文注释，开了等于全屏红。

**B008 白名单**：FastAPI 的 `Depends()` 必须写在默认参数位置，这是框架用法不是 bug。
不加 `extend-immutable-calls` 白名单会有 **28 条**误报（实测），足以淹没真正的问题。

---

## 维护约定

1. 新增功能时若做了取舍，**在本文追加一行并给编号**，不要只写在模块 docstring 里。
2. 修掉某条取舍时不要删行，把「何时回头改」改成「已于 `<commit>` 处理」，保留决策痕迹。
3. 上线阻塞项清单要随进度同步收缩。
