# 从零复盘：全项目功能链路与分段代码精读

这份入口是给第一次完整复盘项目的人用的，不是新的历史审查报告。目标不是背函数名，而是能解释：**一个操作怎么走到数据库、为什么这样分层、哪里可能失败、改一处会影响什么。**

当前已在第一批基础上补齐其余 **120 个源码文件**：合计 **136 个文件、1446 个连续逻辑块、21768 行源码**。240 项清单中另有 99 个生成文件、4 个空包文件、1 个讲解数据文件；**非空、非生成源码没有待补项**。其中 56 个文件为人工分段说明，80 个 Python 文件为人工功能契约＋AST 语句导读。它们解释协作、关键分支与副作用，但**不是“每行代码已经语义认证”**，也不把 AST 语法事实冒充人工设计分析。

## 1. 你从哪里开始读

1. 先按 [conda 本机指南](WINDOWS_CONDA.md) 确认环境，能运行测试或打开文档站即可，不必先开通模型和商户。
2. 读本页的术语和完整链路，再进入对应源码的精读段落。
3. 运行 `python scripts/build_docs_site.py`，打开 `docs/site/index.html`，选择 **“精读覆盖与缺口”**。
4. 覆盖表逐项链接到源码和本目录 README。有讲解的源码页会显示**解释与带行号原代码并排**；窄屏上下排列，段落可折叠，下方仍保留完整源码。
5. 每个非空非生成源码页都有分段说明；若以后新增文件忘写，构建会因缺解释失败。生成物、空文件和讲解数据有明确不同状态。

站点的 `data/reading.json` 是可复算的当前状态；实际讲解保存在 [code_reading_notes.json](code_reading_notes.json)，普通读者不必手读 JSON。源码变动后旧讲解的指纹/范围不匹配会阻止构建，不能悄悄把旧解释贴到另一段代码。

### 这次覆盖什么、如何看待数字

覆盖启动/身份/ORM/schema，全部路由与工具，浏览器 JS/模板/CSS/SVG，初始化与迁移 SQL，部署/环境/依赖/测试配置，全部测试及文档构建脚本。先用第 7 节选择一条业务链，再在覆盖表按路径进入，不必从第一行 JSON 开始读。

“有分段讲解”指第一行到末行归入连续逻辑块，原代码逐行可对照；**不是每个标点各写一句中文**。简单语句可合并；Python 辅助导读逐项呈现可见控制流，人工契约解释上下文与不能省的保护。源码、注释或解释都可能有遗漏，疑问应回到实现和测试继续核查，而不是用覆盖率压过疑问。

统计以构建输出 `data/reading.json` 为准；本页数值是本轮完成时快照，不要求未来新增代码仍保持这个数量。

## 2. 哪些文档该相信到什么程度

| 材料 | 应如何使用 | 不能据此推出什么 |
| --- | --- | --- |
| 当前源码与对应测试 | 确认实现和已经覆盖的场景；实际执行结果注明环境 | 测试通过不代表所有未测分支正确 |
| 目录 README | 模块地图、函数契约、输入输出和维护规则 | 一张函数表不是逐行教程 |
| 本页与源码旁的人工精读 | 顺着一条调用链理解语句、设计原因与限制 | 指纹通过不等于自动语义认证 |
| 源码 docstring/注释 | 就近解释，需对照函数体核查 | 注释也会过期，不能盖过实现 |
| 历史审查报告/验收快照 | 理解当时问题、决定、测量和修复过程 | 旧测试数、旧限制不自动覆盖现行行为 |
| 用户上传的原始方案 | 保留设计建议与比较背景 | 提案不等于已实现规格 |
| 生成依赖图 | 观察静态 import 关系 | 不是真实运行时调用顺序图，也不覆盖动态调用 |

不要求给所有 Markdown 做“语义认证”。现行学习文档、对应代码契约与精读必须认真核对；历史材料仍检查能否打开/链接是否有效，但不改写历史结论来伪装成当前状态。

## 3. 每个函数你都应该能回答的八个问题

1. **谁调用它？** 浏览器点击、FastAPI 路由、Depends、普通函数调用还是定时回调？
2. **输入来自哪里？** 不只写类型，还要写 URL/JSON/表单/数据库/配置，以及可信程度。
3. **返回什么？** Python 对象不一定就是最终 HTTP JSON；说明序列化和状态码。
4. **按什么顺序处理？** 特别标出 await、循环、条件判断和锁前后的重新读取。
5. **改变了什么？** 数据库、文件、Cookie、DOM、内存缓存或外部请求；明确是否提交事务。
6. **怎样失败？** 输入拒绝、权限拒绝、冲突、超时、不可用；哪些异常捕获，哪些继续抛出。
7. **为什么不能简化？** 例如为什么先查重后仍捕获唯一冲突、为什么不能跳过角色查库。
8. **如何验证和修改？** 对应测试在哪里；改参数/字段/状态是否连带迁移、模板、JS 和构建产物。

不是给每个函数机械补八句模板。字段类和短 helper 可以合并讲，但上述信息不能用“处理数据”“实现功能”之类空话替代。

## 4. 先掌握这张语法—项目对照表

| 写法/概念 | 大白话 | 本项目落点 |
| --- | --- | --- |
| `import` | 引用工具；模块顶层语句通常在首次导入时执行 | 导入 settings 会创建配置对象，函数体一般不会因此全部运行 |
| `async def` / `await` | 可以在等待时让出执行权；不自动把慢计算变快 | 数据库/HTTP 用 await；bcrypt 要交给线程池 |
| `Depends(f)` | 框架先替你准备参数/检查条件 | get_current_user 和 get_db 由 FastAPI 调用，不是每个路由手写一遍 |
| `yield` 依赖 | 先交出资源，使用完后退出上下文 | get_db 交出会话并最终关闭，但不会自动 commit |
| `@router.post(...)` | 给函数登记 HTTP 路径、方法与规则 | 装饰器在导入时登记，函数在匹配请求后才执行 |
| `response_model` | 最终响应的字段白名单与格式 | 返回 User ORM 对象，但 UserOut 不输出密码哈希 |
| ORM model / Pydantic schema | 前者描述库中怎么存，后者描述请求/响应怎么长 | models.User 与 schemas.UserOut 不可混为一谈 |
| `db.add` / `commit` / `refresh` | 准备写入 / 提交事务 / 重新读取数据库值 | add 本身不是持久保存；refresh 也不是提交 |
| 唯一约束 / 事务 / CAS | 数据库兜底 / 一组原子操作 / 条件成立才更新 | 并发注册、重复消息、订单状态、图版本分别使用 |
| Cookie / Bearer / JWT | 浏览器携带方式 / HTTP 头携带方式 / 令牌格式 | 同一 JWT 可经不同方式传输，JWT 签名不是加密 |
| 闭包 / IIFE | 函数保留内部状态 / 定义后立即执行 | auth.js 把私有 user/listeners 包在一个返回接口里 |
| `onclick = f` / `setTimeout(f, ...)` | 登记以后要做的事，不是当场执行 f | 消息按钮、轮询和账号变化通知 |
| `epoch` / `authSeq` | 给一轮界面操作贴标签，丢弃迟到结果 | 不是数据库 credential_version，也不是管理员权限 |
| `textContent` | 把内容当文本，而非 HTML 代码 | 用户名和消息正文；不等于所有输出上下文都自动安全 |
| 模板继承 / block | 共享页面壳，再填各页主体 | base.html 提供登录控件，support-center 填会话界面 |
| 构建产物 | 工具把手写源码和依赖变成浏览器要加载的文件 | 编辑 app/frontend，npm run build 更新 app/static/js |

## 5. 第一条完整链路：启动 → 注册 → 登录 → 私人操作 → 改密

### 5.1 启动不是执行所有函数

```text
conda 环境里的 python -m uvicorn main:app
  → 导入 main 及其依赖
  → Settings / engine 对象装配、路由装饰器登记
  → 生产配置自检
  → FastAPI 注册路由/中间件/静态资源
  → lifespan 预热 FAQ（未配置模型可降级）
  → 接收 HTTP 请求
```

注意 `.env` 从当前目录读取，而静态文件目录由 `__file__` 计算；这是为什么“在任意目录启动”可能找得到 JS，却读不到正确数据库配置。

### 5.2 注册和登录为什么是两个请求

```text
base.html 登录表单
  → auth.js submit 回调
  → POST /auth/register（JSON）
  → RegisterIn 校验 → auth.register → ahash_password
  → User 写入 → commit → refresh → UserOut（201，无 Cookie）
  → POST /auth/login（form-urlencoded）
  → 查用户 → 验密码/状态 → 签 JWT → Set-Cookie + TokenOut
  → auth.js refresh → GET /auth/me → 更新 user → notify 其他页面
```

示例用户 `learner_a` 的明文密码不会写入 User.password；库存、图和消息也不会因登录被创建。注册成功不等于登录成功，所以两阶段中任一失败都需要对应提示。

注册先 SELECT 仍不能避免两个并发请求同时认为用户名可用，最终 INSERT 的唯一约束和 IntegrityError/rollback 才兜住冲突。不能为了“代码更短”删掉第二层。

### 5.3 一次私人 API 请求的依赖树

```text
客户 GET /support/messages
  → 请求日志/安全头中间件
  → 路由依赖 get_current_user
      → 取 Bearer，缺少才回退 Cookie
      → decode_token 验签/有效期/声明
      → get_db 会话 → 按用户名查 User
      → 用户状态、credential_version、改密时间比较
  → my_messages 只传 user.id 给 history
  → 查询消息 → 转成响应 → 关闭会话
```

管理员路由还会在 get_current_user 之后执行 require_admin。接口中出现 `user: User = Depends(...)` 并不是允许请求方随意传一个 User 对象。

### 5.4 改密为什么会让旧令牌失效

假设数据库版本为 4，两台浏览器的 JWT 都带 ver=4。改密锁住用户、验证旧密码、更新哈希并把版本写成 5，然后提交，再签 ver=5 的新 token。

另一台浏览器下次请求时，令牌签名即使仍正确且未过 exp，也会因为 **4 不等于 5** 被拒绝。版本比较补上了秒级时间戳不能可靠区分同秒多次改密的问题。

退出仅清当前浏览器 Cookie，不等于撤销所有已复制的 Bearer。把“退出”“改密”“过期”三个概念混在一起，会误判测试和真实安全行为。

## 6. 第二条完整链路：客户留言 → 管理员回复 → 重试与分页

### 6.1 从模板到数据库，再回到界面

```text
GET /support/center
  → messages.center → page_context → Jinja support-center.html + base.html
  → 浏览器加载本地构建的 auth.js / support-page.js / support.css
  → auth.onChange → onUser → poll
  → POST /support/messages（body + UUID nonce）
  → MessageIn → 当前用户 → send_message → SupportMessage → commit
  → 后续 GET /support/messages?after=... → history → payload
  → render：textContent + 消息 ID 去重 + 游标更新
```

数据库里一条消息至少有三种身份信息：`customer_id` 表示属于哪个客户的对话；`sender_id` 表示谁发的；`sender_role` 表示本条发送者当时以何身份发送。管理员回复时 customer_id 与 sender_id 通常不同。

管理员打开同一路径，但会多出客户会话侧栏；获取和写入他人会话的 API 都要求 require_admin。前端看到 role=1 后显示按钮，不是最终授权。

### 6.2 服务器收到，但客户端没看到成功怎么办

客户端为一次发送生成 UUID。若响应丢失，它保留这次发送的 body/nonce，再点重试。数据库唯一约束按 `(sender_id, client_nonce)` 去重；相同内容返回原消息，换正文却沿用 nonce 则 409。

这不是“相同文字永远只能发一遍”。新 UUID 是新的一次发送，客户完全可以在不同时间发送相同文字。

AbortController 只尝试取消浏览器侧请求，不会自动回滚已提交的数据库事务。所以取消请求、检查 epoch、UUID 去重需要一起存在，彼此不替代。

### 6.3 消息游标和发消息的 ID 不能混用

假设最后读到 ID 10，管理员随后发 ID 11，客户发送得到 ID 12。如果发送成功后把读取游标直接跳到 12，那么下次 after=12 就漏掉 11。

因此源码 POST 成功只清输入并促发读取，不直接提高 newest；history 在服务端按读取方向和最多 50 条限制返回，render 在实际接收消息后更新游标。

### 6.4 为什么还要读测试

精读 `tests/test_support_messages.py`：每条用例都解释了造数、请求、字段/状态断言及未验证的范围。它不只是看 200，还看权限隔离、数据库去重、分页顺序和角色降权。

这些是进程内 HTTP/数据库测试，不是真实浏览器。前端逻辑另有 Node 回归，实际页面还需 [验收手册](ACCEPTANCE_GUIDE.md) 的浏览器操作；三者结论不能替换。

## 7. 其余完整链路：从功能入口进入对应精读

以下文件均已有分段说明。路线表先建立模块关系，后面的展开说明负责把关键先后顺序和容易误解的限制串起来；精确签名、每段代码与断言在源码页查看。

| 功能 | 按这个方向读 | 必须问清的细节 |
| --- | --- | --- |
| OAuth/SSO | [oauth 路由](../app/routers/oauth.py) → security/deps → OAuthClient/OAuthCode → oauth_consent 模板 | 客户端不等于用户；回调精确匹配；同意表单如何绑定用户；授权码如何一次性兑换；改密后旧码为什么不能续签 |
| ER 与 Word | [tools 路由](../app/routers/tools.py) → [sql_ddl](../app/tools/sql_ddl.py) / [word](../app/tools/word.py) → [cpu_pool](../app/cpu_pool.py) → er-page/er-layout | 字符串中的逗号/括号与语法符号如何区分；不同 schema 同名表；纯解析与 DOM 布局；超时为何不等于任务终止 |
| Mermaid | tools.mermaid → llm.generate_mermaid → LLMClient.chat → mermaid-page | 输入/输出体量，模型错误映射，代码围栏清理，strict 渲染；不要把模型输出当作可信 HTML |
| Drawio 云端 | drawio-page → [diagrams 路由](../app/routers/diagrams.py) → SysDiagram/lock_user | iframe origin/source，export 关联当前 XML，ETag/CAS，删除/恢复/永久删，各类配额，旧账号异步结果隔离 |
| 商城付款与下载 | shop-page → [shop 路由](../app/routers/shop.py) → [order_state](../app/order_state.py) / [wechat_pay](../app/wechat_pay.py) / [storage](../app/storage.py) | 单位为分，pending 并发唯一，回调核验和幂等，closed 后仍可能收款，领链接不等于收完文件，短时签名与购买权益分开 |
| 文章采集 | [admin 路由](../app/routers/admin.py) → crawler/politeness → extract → Article | 管理员入口、DNS/IP 固定、robots/节流、大小/重定向边界、字段宽度和 upsert；dynamic 目前停用 |
| 智能客服 | [support 路由](../app/routers/support.py) → tools/support → intent/faq/llm | 规则、FAQ、语义索引和文章检索的先后条件；缓存何时失效；未命中与不可用区别；转人工入口不等于留言已送达 |
| 站点与运维 | routers/site → app/site → templates；middleware/ratelimit/startup_checks/health | SEO 地址与实际请求基址、可信代理、CSP 局部例外、内存限流与多进程、存活与就绪的区别 |

相关现行契约集中在 [公共模块](../app/README.md)、[路由](../app/routers/README.md)、[工具](../app/tools/README.md)、[前端](../app/frontend/README.md)，不是另建一套互相矛盾的 README。

### 7.1 订单从“想买”走到“拿到文件”

```text
点击购买 → shop-page.doBuy → POST /shop/orders
  → 确认支付模式可用 → 关闭过期 pending → 复用或创建订单
  → manual：静态码；mock：本地演示；wechat：签名请求 Native 下单
  → 返回待付订单 → 前端只用 GET 轮询状态
确认到账 → manual 管理员 / mock 买家 / 微信签名回调
  → order_state.mark_paid → 条件 UPDATE + 提交流水/付款时间
主动点击下载 → POST /shop/download/{no}
  → 查本人订单及已付状态 → 查文件 → 签短时 URL
  → mark_downloaded → 浏览器 GET /shop/dl → FileResponse
```

- **钱用整数分**。页面的“199.00”是显示转换，服务端订单 `amount` 才是核账依据。
- “先查没有 pending 再新增”会竞争，数据库部分唯一索引兜底；不能只加一个网页按钮禁用。
- **过期标志不等于已执行关单**：轮询只读，再次下单才关闭过期 pending。没有另一个自动跑的关单定时任务。
- `closed → paid` 是合理的迟到收款；二维码关掉不证明银行没收钱。重复回调不能覆盖第一笔收款元数据。
- `downloaded` 表示发过链接；并发/中断/过期后可以再领。链接在期限内是 bearer 凭据，拿到它的人不必再登录，不能公开传播。
- 当前文件 key 来自配置，不是订单冻结的商品版本；换文件影响历史买家下载到的内容，运营要考虑版本和备份。
- 微信回调目前验证时间窗、签名、解密、成功事件、订单号和金额，**没有额外核对解密结果中的 appid/mchid/currency**；Native 下单也未在本函数验证响应签名。讲解如实界定，不把已有测试夸成完整商户安全验收。

对照 `test_order_state`、`test_wechat_pay`、`test_wechat_notify`、`test_manual_pay`、`test_download`、`test_e2e` 与前端两份 shop 测试。后缀 `e2e` 在这里主要指服务层旅程，不是银行/浏览器端到端。

### 7.2 OAuth：用户同意和客户端兑换是两扇门

1. 浏览器带本站登录态访问 `authorize`，服务端核启用客户端与**精确回调 URI**。
2. GET 只画同意页。隐藏字段可被改，所以服务端 HMAC 绑定用户、凭据版本、客户端、回调与 state。
3. POST 拒绝就带 error 回跳；批准前锁用户并重新核状态/版本，之后生成短时 code、提交并重定向。
4. 接入客户端用自己的密钥、code、同一回调到 token 端点兑换；访问令牌不直接塞进回跳 URL。
5. 锁当前用户并条件消费 `used=False` 的 code，只有一次能成功；旧凭据版本的 code 不能在改密后续出新会话。

`state` 用于客户端将返回对应到发起请求；本站签了它不代表替外部客户端实现了全部登录安全逻辑。`oauth_consent.html` 没脚本，`base.auth_ui=False` 同时去掉共享登录脚本；网页隐藏字段、CSP、回调精确匹配承担不同责任。

### 7.3 Drawio：当前 XML、界面代际、数据库版本分别管理

- `exportXml` 明确发送 export 请求并等对应回包，10 秒不响应就显示未保存；不能把最后一次 autosave 当成当前全部编辑。
- 消息同时核 `origin` 和当前 iframe 的 `source`。切账号/文档会替换 iframe，旧窗口的迟到消息不能继续写当前状态。
- `epoch` 拦界面旧请求；`ETag/If-Match` 拦服务器旧版本覆盖。两者互不替代，返回 412 后不能偷偷重试强制覆盖。
- 用户行锁把同一人的“读额度→新增/恢复”串起来；活跃数量、含回收站总数量、总 UTF-8 字节分别检查。
- 软删除无 If-Match，只进回收站；永久删除必须在回收站并带版本，成功后不可恢复。

尝试在纸上排列“开始保存 A → 切账号 B → A 回包”的时间线，再对照源码中的每一个 epoch 检查。Node 测试覆盖宿主逻辑；真实 diagrams.net 消息、触控/键盘和视觉仍按验收手册检查。

### 7.4 DDL、ER、Word：同一份图数据的两个出口

以 `amount NUMERIC(10,2), note TEXT DEFAULT 'a,b(c)'` 为例：逗号有三种位置，只有引号外且括号深度为零的逗号才分字段。

```text
DDL 字符串 → _scan 识别引号/转义/注释
  → _iter_tables / _read_balanced 找表体
  → _split_top_level / _parse_column / _parse_constraint
  → parse_ddl 消解 schema 同名、COMMENT ON → {tables, edges}
      ├─ er-layout：纯坐标计算 → er-page：D3 SVG/缩放
      └─ word：XML合法字符清理 → python-docx → 内存 bytes → 附件响应
```

这是**提取器，不执行输入 SQL**。复杂方言可能不支持，复合外键列数不匹配也不是完整语法验证。数据库初始化脚本虽然也是 `.sql`，却真的执行且会 DROP 表，不能混淆这两种路径。

Word 路由先限表/字段数量，再进入有界 CPU 池。响应超时不等于计算已经停了；shield 后实际任务结束才释放名额，否则攻击者可不断制造“超时但仍在算”的任务。导出结构测试不证明桌面 Word 的分页/字体已验收。

### 7.5 采集：先决定能不能访问，再讨论提取什么

`admin.ingest_article` 先通过管理员依赖。静态抓取逐个检查 DNS 结果皆为公网，传输固定已校验 IP，同时保留 Host/SNI；每个跳转重新检查，解压后字节也受上限控制。

`politeness` 另负责 robots 与时间安排：401/403 拒绝，404/410 放行，成功响应解析规则，其余/错误默认拒绝；同域在锁内预订时隙，锁外等待，全局信号量是本进程口径。

网页先压成有深度/节点/字符上限的骨架，LLM 只返回受限 JSON 选择器，再在原始 HTML 真正抽文本。`save_article` 拒 NUL/列宽溢出，以 URL upsert 并提交；模型出错是 502，提取/入库字段不合约是 422。

**动态抓取仍安全停用。** 测试替换 `_goto` 的“成功”仅验证周边流程；安装 Playwright/Chromium 不会让当前真实 `_goto` 自动变安全可用。

### 7.6 智能客服：回答、引用、置信度都不是人工消息

1. 显式人工关键词直接给人工入口；无需再花钱做模型分类。
2. 规则先看 FAQ 与关键词，低置信度才尝试语义 FAQ 和 LLM 分类。
3. FAQ 直接给固定答案；闲聊调模型；专业问题读取文章、检索片段，再让模型据材料回答。
4. 缺模型/缺资料/调用失败按具体分支降级到人工入口；真正发送消息须另走站内消息 API。

词袋排序融合归一 BM25 和余弦，绝对 `confidence` 不能拿“本次最大值归一成 1”替代。语义阈值要用真实模型正负样例标定，不是概率承诺。

语义缓存绑定 provider/模型/地址/密钥摘要/transport/FAQ 语料；跨 await 还核快照未被替换。文章缓存用 id/title/content 指纹识别变化，缓存的是索引，**每次仍查文章库**。标签日志会记录原问题，运营需设置隐私/保留策略。

### 7.7 网页、代理和运维：可见页面不等于健康系统

`app.site` 清单 → `routers.site` 登记 → Jinja 外壳 → 本地 JS bundle。`base` 先定义登录控件和 Auth 再插页面主体，经典脚本不能倒置顺序。`textContent` 处理用户文本；服务端生成的二维码 SVG 是另一条受控 HTML 路径，不能据此允许所有接口回任意 HTML。

`/healthz` 不查库，证明进程还能应答；`/readyz` 有时限地 SELECT 1，证明数据库探针成功；生产自检只核部分配置，均不代替真实模型/商户/文件验收。

代理信任判断从 socket 直接对端开始，不能仅打开 `TRUST_PROXY_HEADERS` 就相信任意 XFF。Compose 的默认回环宿主绑定与容器网桥 CIDR要单独配置；`SITE_BASE_URL` 的 SEO 地址和请求推导的下载基址不是一个变量的两个别名。

### 7.8 测试与构建：从哪里知道“没漏讲、没贴错”

```text
Git 已跟踪及未忽略新增文本
  → check_docs_contract：owner、四小节、源码指纹
  → code_reading：解释method、来源hash、连续段界、无漏项
  → build_docs_site：结构数据 + 实际HTML + 本地链接/行范围
  → pytest：门禁反例、函数/HTTP/Node/隔离PG回归
  → npm ci/build：源与提交bundle一致
  → 当前提交SHA的六项GitHub CI
```

- AST 语句导读按当前语句列出赋值、条件、循环、await、抛错、返回与断言；它**不自动推断业务理由**。理由、协作与真实限制由人工契约提供，两种来源在页面上标明。
- 每个测试先辨别 `client/db/monkeypatch/tmp_path` 等准备条件，再看具体请求与 assert，不把测试名称当验收结论。部分旧名称仍含“一次性下载”等历史词，当前实现与实际断言优先。
- `build_docs_site --data-only` 不写 HTML 页面，但标题/代码块统计也走真实 Markdown 渲染，**仍需 Mistune**；不需要应用数据库/.env。本轮补了缺依赖非零退出测试，纠正了旧“仅标准库”说明。
- 四个空 `__init__.py` 是包标记；99 个生成文件讲构建来源；notes JSON 自身由下一节解释 schema，不能自存自己的 SHA。没有把它们伪装成逐行人工分析第三方源码。

## 8. 不同文件类型怎么读，不能只盯 Python

| 类别 | 复盘方法与边界 |
| --- | --- |
| Python 运行代码 | 看导入和顶层副作用，再看类/函数/异常/调用方；空 `__init__.py` 没有语句，作用是包标记 |
| 浏览器 JavaScript | 把 DOM ID、事件绑定、请求方法/体格式、页面状态与对应模板一起对照；闭包和匿名回调同样是代码，不只解释具名函数 |
| HTML/Jinja | 区分服务器模板语法与浏览器 DOM；看继承、条件、脚本加载顺序、表单字段和转义上下文 |
| CSS/SVG | 看谁引用、选择器优先级、布局/媒体规则、hidden 和焦点；样式也影响可操作性，但不能承担 API 鉴权 |
| SQL 初始化/迁移 | 按语句读事务、表/列/约束/索引、默认值和破坏性影响；full_init 会删表，迁移 0008 重跑也会清授权码 |
| 配置与构建 | `.env.example`、requirements、package.json、vite.config、Dockerfile、Compose、CI、pytest/ruff/coverage 等都要解释读者、加载时机、默认值、生产差异和验证命令；入口见 [根文件说明](ROOT_FILES.md) |
| 测试 | 看 fixture 隔离、造数、真实/模拟依赖、每个断言抓什么故障、跳过理由；测过返回码不等于测过显示效果 |
| 文档构建工具 | build_docs_site 提取与渲染；check_docs_contract 管清单/指纹；code_reading 管人工分段记录；它们不执行应用代码来推断语义 |
| 生成 JS/锁文件 | 保留来源、构建命令、版本/完整性与测试；不逐行重述压缩第三方库，也不把自己的业务入口误当第三方而漏掉 |
| 二进制资源 | 当前 `app/static/pay_qr.png` 是收款码图片，被 manual 配置引用；没有“代码行”，需要解释用途/替换/合规边界，不能仅看图片就认证收款主体或证明支付到账 |
| Git/忽略规则 | `.gitignore` 影响哪些新文件可进入清单，`.dockerignore` 影响镜像内容，`.gitattributes` 影响换行；不是无关紧要的装饰文件 |
| 历史 Markdown/原方案 | 按时间和提交理解，不纳入“现行每行代码解释完成”的分母；仍保留导航与原文 |

函数索引目前用 Python AST。JS/HTML/CSS/SQL 的分段解释是人工写的，不宣称已有跨语言完整语法树；自动覆盖表也不证明动态调用关系全被识别。

## 9. 一次精读的练习方式

先挑一条不涉及真实费用的路径，例如客户留言：

1. 口述正常流程，每个箭头说出输入和输出；不知道的地方停下来查源码，不用模块名猜。
2. 给对象取具体值：用户 ID 7、消息 ID 10、nonce 为同一个测试 UUID，然后在纸上跟踪变化。
3. 选一个失败分支：未登录、重复 nonce、两种游标同时提供或账号切换。预测 HTTP/DOM/数据库分别发生什么。
4. 找到对应测试，区分哪些依赖是真实实现，哪些是 mock；再用专用测试环境运行。
5. 做一个“如果删掉这句会怎样”的推演。不要直接在业务库删安全判断；受控变异只能在隔离环境，复原后再跑测试。
6. 最后回答：改一处字段，哪些 schema、model、SQL、接口、JS、测试、文档与构建产物需要一起改？

看完一段仍无法解释“为什么”，应记录具体文件/函数/问题，继续补讲；不要仅因为源码行都有颜色或注释就签收。

## 10. 讲解数据的格式、来源与维护

`docs/code_reading_notes.json` 是权威讲解数据，不是每次构建重新让模型生成的输出。普通读者打开站点即可；维护者需要理解：

| 字段 | 含义与限制 |
| --- | --- |
| `version: 1` | 当前 schema 版本，只接受整数 1 |
| `files` / `path` | 被解释的仓库相对源码；必须在清单里且不得重复 |
| `sha256` | 当时核对的源文件原始字节摘要；README 自动表刷新不会替它更新 |
| `method: manual` | 人工整理分段说明；首批省略该字段时兼容为 manual |
| `method: guided` | 人工整理模块/功能契约，加基于当前 Python AST 的语句导读；不自动推断设计意图 |
| `blocks[].end` | 结束行；第一段起于 1，后段从上一 end+1 起，最后必须到 EOF |
| `title` / `explanation` | 有实质内容的标题与解释；以纯文本转义显示，不执行其中 HTML |

**为何 notes 自己没有“自己的 SHA”？** 往文件写入自己的新摘要会再次改变文件，形成自引用循环。只有精确路径 `docs/code_reading_notes.json` 单列为 `note_data`，其 schema/来源/维护由本节和 docs/README 说明；README 文件表及外部 `code-manifest.json` 仍记录它的实际 SHA。其他 JSON 不能借这个例外逃避讲解，测试有反例。

### 以后改代码，不能只一键刷新摘要

1. 先核实现、调用者和测试，再修改对应模块契约/段落；业务语义改了不能沿用旧文字。
2. 新文件也须补解释；现有源码改动要复核段界，确认后仅更新该文件的 notes 摘要。不要批量覆盖所有 SHA 掩盖过期说明。
3. 运行 `python scripts/check_docs_contract.py --write` 更新 README 自动表；它不代写人工说明、不刷新 notes 中的源摘要。
4. 运行 `python -m pytest tests/test_docs_contract.py tests/test_docs_site.py tests/test_code_reading.py -q` 和 `python scripts/build_docs_site.py`。完整构建及 `--data-only` 均拒绝缺讲解；低层 helper 的诊断模式才允许展示待补状态。
5. 业务/前端有变时运行相应和全量测试、npm 构建；最终以实际提交 SHA 的 CI 为准。生成站点无需提交，源说明和必要 bundle 要提交。

本轮完成的是**全量源码可对照的功能与语句讲解**，不是自动语义证明、真实商户验收、模型阈值标定或 Windows/浏览器/Word 桌面验收。后几项继续按 [验收手册](ACCEPTANCE_GUIDE.md) 在正确环境执行；不能因为文档和测试变全就将未执行事项写成通过。

## 11. 本轮自动化验证记录（2026-09-12）

| 检查 | 结果与边界 |
| --- | --- |
| 全量 SQLite | 774 passed / 6 skipped；跳过真实 PG 并发/迁移及真实 embedding 标定 |
| 全量一次性 PostgreSQL | 779 passed / 1 skipped；测试使用非超级用户、独立拥有的可丢弃数据库，结束后清理；仅真实 embedding 标定跳过 |
| 文档专项 | 67 passed；包含缺讲解、坏 method、狭义自引用、转义及两种 CLI 缺依赖反例 |
| 门禁受控变异 | 暂时关闭完整性拒绝、去掉转义、隐藏 guided 来源分别被针对性断言抓住；源码逐字节恢复后再验证 |
| 整站 | 33 文档页、240 源码页；94 Python 模块、289 静态导入边、47 路由、1188 符号；实际 HTML 本地链接/源码范围检查通过 |
| 前端与基础检查 | npm build 与提交 bundle 无漂移；Ruff、pip check、git diff --check 通过 |
| 实现变更范围 | 应用 Python 无修改，业务 bundle 无变化；仅修正少量源码注释/模板文案，增加文档门禁与依赖错误处理及测试 |

同一工作区的 npm 构建与文档扫描必须串行：Vite 会清空再写静态目录，最初并行扫描遇到暂时缺失的 chunk；改为串行后完整站点通过。这是执行顺序问题，不把那次失败删去或称为应用运行故障。

这些记录在 Linux 沙箱执行，不是 Windows/conda、真实浏览器、桌面 Word、商户或真实模型验收。Vite 大 chunk 提醒与 Passlib 的 crypt 弃用警告仍存在，未用隐藏警告来制造通过。最终提交与其对应 GitHub CI 结果见交付消息；不能用此快照替代未来提交的复验。
