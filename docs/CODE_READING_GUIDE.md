# 从零复盘：功能链路、代码精读与尚未补齐的范围

这份入口是给第一次完整复盘项目的人用的，不是新的历史审查报告。目标不是背函数名，而是能解释：**一个操作怎么走到数据库、为什么这样分层、哪里可能失败、改一处会影响什么。**

先说明现状：原有目录 README 提供模块职责和函数契约；符号索引提供签名、docstring 和源码定位。**这些不是“每行代码已经讲解并认证”。** 本次新增部分核心链路的人工分段精读，其余源文件保留可见的待补状态，不把全量源码清单变成全量语义审核结论。

## 1. 你从哪里开始读

1. 先按 [conda 本机指南](WINDOWS_CONDA.md) 确认环境，能运行测试或打开文档站即可，不必先开通模型和商户。
2. 读本页的术语和两条完整链路，再进入对应源码的精读段落。
3. 运行 `python scripts/build_docs_site.py`，打开 `docs/site/index.html`，选择 **“精读覆盖与缺口”**。
4. 覆盖表逐项链接到源码和本目录 README。有讲解的源码页会显示**解释与带行号原代码并排**；窄屏上下排列，段落可折叠，下方仍保留完整源码。
5. 没有分段精读的文件会明确显示“源码与模块契约；分段精读待补”，不是空白，也不冒充已经讲完。

站点的 `data/reading.json` 是可复算的当前状态；实际讲解保存在 [code_reading_notes.json](code_reading_notes.json)，普通读者不必手读 JSON。源码变动后旧讲解的指纹/范围不匹配会阻止构建，不能悄悄把旧解释贴到另一段代码。

### 本批精读覆盖什么

- 启动和身份：`main.py`、`config.py`、`database.py`、`security.py`、`deps.py`、`routers/auth.py`、`frontend/auth.js`。
- 数据与接口形状：`models.py`、`schemas.py`。
- 站内客服：`routers/messages.py`、`frontend/support-page.js`、`templates/support-center.html`、`static/support.css`。
- 对应迁移与测试：`migrate_0007_support_messages.sql`、`migrate_0008_credential_revision.sql`、`tests/test_support_messages.py`。

“有分段讲解”指源码从第一行到末行均归入连续的人工解释块，包括导入、类/字段、装饰器、异常、事件绑定和末尾装配；**不意味着每个标点各写一句中文，更不证明解释永远正确**。多个相邻简单语句可同段，关键分支必须解释意图和失败行为。

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

## 7. 其他功能的复盘路线：已有契约，分段精读仍待补

以下路线给你入口和检查问题，不把这些模块标成已经逐段讲完。函数签名以站点符号索引为准；后续逐个补入精读数据时，覆盖表才会改变。

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

## 10. 如何继续补到你要求的全面程度

剩余优先级：订单/支付/存储与 OAuth → Drawio/CPU/SQL 工具 → 抓取/模型/FAQ → 模板样式/部署配置 → 全部测试与构建工具。各文件的精读状态以生成覆盖表为准，这个顺序不是已完成声明。

维护者为一个文件补精读时必须：

- 读完整源码、实际调用者和相关测试，解释关键条件、异常、副作用和限制；不从旧注释直接复制结论。
- 在 notes 的 files 数组添加 path、当次源码 SHA-256、按顺序排列的 blocks；每段 end 是结束行，起始行由前段自动推导，最后覆盖到文件末尾。
- 写有内容的 title/explanation；不能只写“导入模块”“处理逻辑”。原代码由构建器读取，不手动再复制一份漂移的源码。
- 源码变动后先复核文字和段界，再更新 notes 指纹；`check_docs_contract --write` 不会替你刷新精读指纹或代写解释。
- 运行文档测试、结构门禁、整站链接/行号校验；再核对业务测试。覆盖表仍必须展示所有未补项。

可机械验证的是文件身份、当前字节、段界、完整分段、链接和输出转义；**解释是否准确、为什么这样设计以及读者是否真的理解，不能被这些数字认证。** 本次建立了可继续逐项完成的机制和核心实例，尚未宣称全项目每个函数与每一行已经完成精读。
