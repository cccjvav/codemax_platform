# `01a08bf5` 全面审查报告（只找问题，未做任何修改）

- **审查对象**：分支 `arena/01a08bf5-codemax-platform`（`889e3eb`），相对 `01a08b` 约 240 个文件变更（+21167/−10511）。
- **方法**：分支完整解包到临时目录做只读审查，覆盖全部 Python / JS / HTML / 模板 / 脚本 / 迁移 / 配置；跑了全量 pytest、ruff、文档指纹门禁、文档站构建、`npm ci + build` 漂移复核；用官方文档核实了 draw.io 新协议。
- **仓库状态**：审查过程零修改（`git status` 与开始时一致，仅 `fetch` 过远程分支做只读比对）。

## 实测基线

- pytest **838 collected = 832 passed + 6 skipped，0 failed**（与 `manager/stages/agnes-integration.md` 记录的 832/6 完全一致）
- ruff check 干净；文档指纹门禁 0 errors
- 文档站构建成功（47 路由 / 41 文档页 / 244 源码页 / 140 文件精读 0 待补）
- `npm run build` 复建零漂移；`pip check` 干净

## 上轮问题修复对照（01a08b 报告的去向）

**已修**：sql_ddl 字符串关键字误判（`hidden` 屏蔽 + 实测全过）、faq 维度漂移 500（`_semantic_key` + 维度/有限性校验）、RAG 指纹 UPDATE 不可见（改全文 SHA 指纹）、llm content=None、crawler 坏端口、skeleton 递归（改迭代 + 预算）、register/save_article 竞态、quota 竞态（`lock_user`）、CPU 错误污染进程池、mermaid CDN（已本地打包 + sha512 锁定）、CSP 收紧到 `script-src 'self'`、非 static 补 `no-store`、XFF 信任改 CIDR + 逐跳回溯、verify NUL / `compare_digest`、notify 时间戳校验、`a>button` 嵌套、admin 422 常量、shop 轮询重叠/离线、drawio 双 save 误报 412 与立即 `revokeObjectURL`、auth.js 422 显示 / initial refresh、sourcemap 注释、`shop.html 内联脚本`注释、`CODE_EXT` / `_render_home` 死代码（重构消除）、`html` 产物 ESM。

**仍 open（见下文编号）**：notify 非 UTF-8 包体 500；TEMP/UNLOGGED 静默丢表；括号 DEFAULT、MySQL 表 COMMENT 丢失；`/oauth/authorize` 无 rate_limit；`ROBOTS_TIMEOUT` 死常量；robots 1h 缓存 vs 文档；`yuan` 死代码 + 无 JS lint；er-word 导出 `revokeObjectURL` 仍立即释放；vite.config "90KB"；`ruff format` 31 文件漂移且 CI 不跑 format；`main.py html=True`；intent 三处小毛病；mock-pay `res.json()` 无守卫；X-Request-ID 反射（设计如此）；多实例限流（总览已明确声明）；`_ok` docstring。

**已 moot**：browser `status=200` 等动态渲染问题（功能已主动停用，503 fail-closed，有测试钉住）。

## P1：必须修

1. **根 README 模块表大面积过期**（手写数字，门禁管不到）：`app/tools` 1625行→实测2194；`app/routers` 9个/1132行→10个/1503行；`app/` 根 1328行→1759；迁移 5个→8个；模板 7个→9个；`app/static` 还写"前端脚本（`er.js`）"（er.js 早已不在，现为约 90 个分块 + css + 收款码）；tests 36文件/565用例→51文件/838；scripts 描述还是"建表体检 WASM PG"；"文首钉行号基准 commit"描述的是已废弃的旧机制（现为 SHA 指纹表）。同页"21 份文档/约 10 000 行"→实测 48 个 md；Skills 表只列 3/7（漏 finish-subitem、new-tool-page、pre-commit-review、schema-sync）。
2. **`pay_notify` 非 UTF-8 包体仍 500**（上轮 P2 未修）：`(await request.body()).decode("utf-8")` 在 try 之外。注意微信语义：解不出的包体重试也没用，按该函数自己的 docstring 应回 200 停止重推（记日志），而不是 400/500 引发重试风暴。
3. **`support-page.js` 用 `crypto.randomUUID()` 无降级**：非安全上下文（http + IP 访问）下该函数不存在，发送留言时在 try 之外抛 TypeError，用户无任何提示。加 `crypto.randomUUID ?? fallback`（Math.random 拼 UUIDv4 格式即可，nonce 只需唯一不需不可预测）。

## P2：应该修

4. **`.github/workflows/README.md` 虚构 CI 步骤**：称 frontend job 跑 "npm audit"，实际 `ci.yml` 只有 `npm ci + build + 漂移检查`。加 step 或改文档（二选一，不要悬空）。
5. **测试名 / 注释与断言自相矛盾**（会误导下一个修 bug 的人）：`test_e2e.py::test_full_chain_register_to_single_download` 名 + docstring + "第二次领链接必须被拒"注释 vs 实际断言 200 可重领；`test_shop_page_explains_recoverable_download` docstring 还写"一次性下载…必须说清楚" vs 断言已反转；`test_download.py` 模块 docstring"一次性下载双重校验"；`test_auth_cookie.py` 两处"烧掉额度"注释；`conftest.py:178` 注释。逻辑全对，只改名和注释。
6. **`app/routers/shop.py:54` 注释过期**："`script-src` 白名单里唯一的 cdn.jsdelivr.net"——业务 CSP 已无任何 CDN（仅 /docs 例外）。结论（服务端画码）仍成立，理由重写。
7. **TEMP/UNLOGGED TABLE 仍静默丢弃**（已实测复现）；括号 `DEFAULT (…)`、MySQL 表级 `COMMENT='…'` 仍丢失。至少 TEMP/UNLOGGED 值得支持。
8. **`app/static/pay_qr.png`（201KB）+ `.env.example` 默认指向它并称"项目配置的收款码"**：若这是真实个人收款码，等于敏感收款信息入库且公开展示。核实：占位则文档写明，真实则移出仓库改部署时挂载。
9. **Vite"固定文件名"注释与现实矛盾**：注释称关掉 hash 是为了避免 diff 噪音，但 mermaid/er 分块文件名实际带 hash（`chunk-2Q5K7J3B.js` 等约 70 个已入库）。机制自洽（复建验证零漂移），注释需更新为"入口固定、分块带 hash，靠 emptyOutDir + 漂移检查兜住"。
10. **draw.io 重写验证结论**：对照官方 embed 协议文档核实，`export` 响应的 `xml` 字段 + `message` 回显、`load` 事件、`autosave:1` 全部符合协议，相关性设计成立，**不是 bug**。但留三处硬化建议：① `ready` 完全依赖 embed 端发送 `load` 事件，建议 `autosave` / `save` 也置 `ready` 做兜底，否则某版 embed 行为变化则保存全灭；② `manage()` 并发点击会交错渲染出重复条目（缺类似 `refreshList` 的 serial 守卫）；③ `api()` 内 `res.json()` 无守卫，网关 500 HTML 时用户看到 SyntaxError 原文。
11. **四个过期注释**：`requirements.txt` 注释"全量 624 passed"→现 832 passed/838；`ci.yml` 注释"不需要…那 24 个依赖"→除 mistune 外实际 25 个不需要；`test_frontend_supply_chain.py` 注释 `allowed = ()  # mermaid 仍走 CDN` 自相矛盾；`faq.py:218` docstring 仍以 text-embedding-3-small framing 0.55 阈值，与 config"不声称适合 Agnes"矛盾。

## P3：小问题 / 优化

- `/oauth/authorize` GET 仍无 `rate_limit`（/token 有）；messages 三个 GET 与 `order_history` 无 rate_limit（POST 都有）。
- admin ingest 仍无审计日志（manual-confirm 有落盘，ingest 无，不对称）。
- `notify` / `mock-pay` / `drawio api` 的 `res.json()` 无守卫（归一：引 `errorText` 风格的统一解析）。
- `lock_user` 每次 UPDATE users 整行，PG 下高频调用产生死元组 churn；优化：PG 用 `SELECT … FOR UPDATE`，SQLite 保留 UPDATE（SQLite 不支持 FOR UPDATE，这是现状用 UPDATE 的原因，改时保持双库兼容）。
- `cpu_pool._get_executor` 首调用并发 race（双 spawn 泄漏一个 worker 进程）+ 函数内重复 `import asyncio`；`PublicTransport` 每请求重复 DNS（`_request` 已验一次）；`_semantic_key` 每查询算 FAQ 全语料 tuple（12 条可忽略，语料变大后缓存）。
- `check_docs_contract.py` 在非 git 目录直接 traceback（应友好报错）；已知后缀的非 UTF-8 文件会 crash 行数统计。
- `shop-page.js` 的 `yuan` 仍是死代码；全仓无 JS lint（建议 `node --check` 或 eslint，先零成本挡语法错）。
- `er-page.js` Word 下载的 `revokeObjectURL` 仍同步立即释放（drawio 已修 er 未修，Firefox 风险）。
- `support-page.js`：admin 未选会话直接发送会落入自己的客户线程且标 customer（建议未选 target 时禁用发送）；`inbox()` 无 serial 守卫。
- `messages` nonce 按 sender 全局唯一（跨会话复用同 nonce → 409；随机 UUID 下无实际影响，建议注释注明或改为 conversation-scoped）；`sender_role` 0/1 魔法数字建议常量。
- agnes-connectivity 的 `[agnes-live-test]` 提交信息触发：信任模型上等价于写权限（可接受），但易误触发花小额探测费；建议文档注明或收敛为仅 `workflow_dispatch`。
- `SHOP_PAY_MODE` 仍非 Literal（未知值行为待确认；STORAGE_BACKEND 未知值已 explicitly 报错）；`page_context` extra 覆盖、`Retry-After` window+1、`limit_attr` 请求时校验、PUT 无节流、`_ok` docstring"无包体"、`main.py html=True`、intent（单字"早" / `api` 无词界 / 引号 label）、`auth.js notify` 监听器异常打断后续——均为上轮小项，原样保留。
- `er-page.js` 注释"约 49kB"经核实仍然准确（分块合计 50526B）；`TRANSPORT` IPv6 裸地址 `copy_with` 边界未测（nit）。

## 明确验证过没问题的（后人勿复查）

draw.io 新协议对照官方文档成立；本地下载链接是绝对 URL（前端 `^https?` 校验不断本地下载）；`btn-manage` 已接线；`st-downloaded` 仍在；构建产物 er 含 import 故 `type=module` 必需且正确，drawio/support 无 ESM 语法；`DiagramSummary.version` 存在故 purge If-Match 可用；`errorText` 已导出；TD-229~237 全部有 `###` 定义（旧 TD 用 `~~` 删除线，两格式都算数）；全仓无 U+FFFD（之前看到的是显示 artifact）；CHECK 字符串里的 PRIMARY KEY 经实测 harmless（列名对不上）；`db.bind.dialect` 双库路径测试全绿；`lock_user` 所有调用方提交/回滚纪律正确；47 路由 = 42 装饰器 + 5 页面，与运行时测试一致；`order_history` 的 QR 只对非 http 码生成；`_callback` 会剥掉注册 URI 里预置的同名参数（防参数污染）；`_sign` 已绑定 user.id + credential_version；OAuthCode 发码带版本且 purge 旧码；全量 suite 零失败。

覆盖缺口声明：`Windows新手逐步验收.md`（687 行）与 `ARCHITECTURE_GUIDE.md` 只抽查未逐行读；`代码级文档方案新/旧.md` 按 HANDOVER 要求视为用户原稿未纳入一致性审计；真 PG 跑的并发 / 迁移幂等只在 CI（本地 SQLite 全绿，PG 结论引 stage 记录）。

---

## 给修复助手的提示词（可直接粘贴用）

```text
在分支 arena/01a08bf5-codemax-platform 上修复 01a08bf5 审查报告的问题，
P1→P2→P3 顺序。每条修完跑定向测试，最后全量 pytest + ruff check 干净，
并同步跑 scripts/check_docs_contract.py（只读模式须 0 errors；若改了源码，
先更新对应 README 人工解释再 --write，不要先 --write 冒充审查）。

P1（必须）：
1. 重写根 README 过期数字（实测当前值：tools 2194行/10模块、routers 1503行/
   10个、app根1759行/16文件、迁移8个、模板9个、tests 51文件/838用例、48个md；
   static/scripts/Skills表（7个）描述重写；"行号基准commit"机制描述改为SHA指纹表）。
   原则：要么写准当前数，要么删数字只留入口——不允许第三种状态。
   回归：tests/test_docs_site.py 加一节"根README模块表数字"断言，或把数字来源
   收到生成脚本里（推荐后者，一劳永逸）。
2. pay_notify 包体解码：把 .decode("utf-8") 包进 try，UnicodeDecodeError 时记日志
   并回 200（重试无用，按函数docstring必须止推），不要 500/400。回归测试喂
   非法字节断言 200 且订单无变化。
3. support-page.js：crypto.randomUUID 加降级（不存在时用 Math.random 拼
   UUIDv4 格式）。回归：在 Node harness 里删掉 crypto 跑发送路径不断言抛错。

P2（应该）：
4. workflows/README 的 "npm audit"：要么在 frontend job 加 npm audit step，
   要么删掉该词。同步检查 README 其他 job 描述与 ci.yml 一致。
5. 测试 relic 注释：重命名 test_full_chain_register_to_single_download（名/
   docstring/125行注释 vs 200断言矛盾）；改 test_shop_page_explains_
   recoverable_download 的 docstring、test_download.py 模块 docstring、
   test_auth_cookie 两处"烧掉"注释、conftest.py:178 注释。只改名和注释，
   不改断言；改完全量绿。
6. shop.py _qr_svg 注释：script-src 已无 CDN（仅/docs例外），重写理由。
7. sql_ddl：支持 TEMP/TEMPORARY/UNLOGGED TABLE（回归：三种前缀表可解析）；
   括号 DEFAULT 与 MySQL 表级 COMMENT 二选一支持或明确文档为"已知不支持"。
8. pay_qr.png：核实是否为真实收款码。占位→文档写明；真实→移出仓库，
   改部署挂载，轮换收款码。
9. vite.config"固定文件名"注释：更新为"入口固定、分块带hash、
   emptyOutDir+漂移检查兜住"（行为已验证正确，只改注释）。
10. drawio-page.js：autosave/save 事件也置 ready（load事件兜底）；manage()
    加 serial 守卫防并发双渲染；api() 的 res.json() 加守卫转人话错误。
11. 同步四个注释：requirements"624 passed"→832 passed/838（或删数字）、
    ci.yml"24个依赖"→25个、supply_chain 的"mermaid 仍走 CDN"注释、
    faq.py:218 阈值 docstring（0.55 不适用于任何现行模型，待标定）。

P3（顺手，有空就做）：oauth/authorize GET 加 rate_limit；messages GET 与
order_history 加 rate_limit；admin ingest 加审计日志（对齐 manual-confirm）；
notify/mock/drawio 的 res.json 统一守卫；lock_user PG 改 SELECT FOR UPDATE
（保持 SQLite 兼容）；cpu_pool 首调用加锁；er-word 导出 revokeObjectURL
延迟释放；support-page 未选会话禁用发送+inbox serial；sender_role 常量化；
agnes 工作流触发收敛；SHOP_PAY_MODE 改 Literal；删 yuan 并加 node --check；
ruff format 一次性格式化并纳入 CI（或文档声明不执行）。

不要动：draw.io export 相关性协议（已对照官方文档验证正确）；browser 停用
（安全决策）；下载可重领语义（产品决策，测试已对齐）；CONSOLIDATED_ERROR_
SUMMARY（历史快照，数字以其基准为准）；用户上传的两个文档方案原稿。
```
