# 浏览器交互源码

保持原生 JS + Vite，不引入前端框架或 Node 生产服务。文件与行为对应如下：

| 文件 | 输入 / 输出与关键边界 |
| --- | --- |
| `auth.js` | `/auth/me` 与登录/退出表单；维护共享用户快照、顺序号与可退订监听器；初始网络错误有兜底，失败退出不假装成功 |
| `er-layout.js` | 图数据到布局，纯函数；Node 测试无需安装 d3 |
| `er-page.js` | DDL 表单、D3 图与 Word 文件；第三方代码来自本地构建 |
| `mermaid-page.js` | 自然语言表单到 Mermaid 展示；strict 模式，不允许模型放宽为 loose |
| `drawio-page.js` | 检查消息 origin/source，以关联的 export 请求读取实时 XML；文档或账号切换替换 iframe 上下文、拒绝旧响应；串行保存并保留 ETag 冲突 |
| `shop-page.js` | 主动下单、无重叠状态轮询、历史订单、短时链接重领；取消/账号切换清理状态，不自动再次下单 |
| `support-page.js` | 客户自己的消息或管理员选中的会话；分页、轮询、UUID 重试去重、账号/会话 epoch、纯文本渲染 |
| `mock-pay-page.js` | 开发模拟支付按钮，不代表真实商户联调 |

公开 HTML 外壳不代表私人 API 公开。权限判断始终在服务端；用户 role 只决定显示管理控件，不能授权请求。
用户在 Drawio 切换文件前应保存或导出需要的工作；换账号会清除账号所属内容。站内客服不是即时在线或邮件通知系统，管理员需要打开页面处理留言。

## 关键函数与状态约定

| 文件 / 函数 | 输入、结果与副作用 |
| --- | --- |
| auth.refresh、onChange | GET 当前用户并发布用户快照；用顺序号拒绝旧响应；订阅返回退订函数。open/close/setMode 只操作登录界面，不授权 API |
| drawio.validXml、exportXml | 导入只接收有界 mxfile/mxGraphModel；exportXml 返回 Promise，关联当前 iframe 导出事件，10 秒无响应拒绝。不能用 autosave 缓存替代显式导出 |
| drawio.resetEditor、syncAuth | 文档/账号切换作废旧上下文并替换 iframe；清除旧账号数据。loading 阻止云文件未返回时提前装载临时内容 |
| drawio.api、save、refreshList、manage | API 处理错误、无效 JSON 及 204；save 串行取得实时 XML，用 ETag 更新；412 保留编辑内容。管理列表用独立序号拒绝旧响应，提供软删/恢复/明确确认后永久删 |
| shop.render、poll、stop | 根据订单状态显示界面；pollBusy 防请求重叠，stop 清计时器，取消按钮另外递增 buySeq 并清订单/补单订阅；状态查询不能偷偷下新单或领链接 |
| shop.buy、doBuy、loadHistory | 主动下单才 POST；buySeq/登录订阅处理旧响应和退订；历史分页读取不等于新订单；失败时给用户可重试路径 |
| support.endpoint、onUser、reset | 由当前用户和管理员选择决定会话 endpoint；账号/会话切换递增 epoch、abort 请求、清除列表和输入，不靠 DOM 隐藏保护数据 |
| support.render、poll、inbox | 使用 textContent 渲染，按消息 ID 去重；维护 oldest/newest，轮询在上次完成后再排 4 秒定时；inbox 游标与消息游标分开，并用请求序号拒绝乱序结果 |
| support 表单提交 | 管理员未选客户不能发送；UUID 优先 crypto，HTTP旧环境降级仅用于幂等而非凭据；一次发送保留 client_nonce，响应丢失可重试；成功也不直接把读取游标跳到 POST ID，否则会漏掉中间管理员回复 |
| er-layout.nodeHeight、layoutEr | 图数据 → 排版位置；无 DOM/网络。页面脚本负责 D3 渲染、错误提示和 Word 附件请求 |
| mermaid-page / mock-pay-page 事件处理 | 前者调用同源生成 API 并捕获渲染错误；后者只是开发模拟付款确认，不证明真实收款 |

以上函数多在模块闭包内，并非公共 window API。事件绑定、DOM ID 与模板需要共同修改；JS 当前只提供文件级自动索引，这些解释是人工核对内容。

## 模块职责

手写浏览器交互源码，按页面构建；共享登录态由 auth.js 管理。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/frontend/auth.js`](auth.js) | `056b278cd0ad` | L1–L156 |
| [`app/frontend/drawio-page.js`](drawio-page.js) | `439de924e889` | L1–L180 |
| [`app/frontend/er-layout.js`](er-layout.js) | `d9049d416c84` | L1–L80 |
| [`app/frontend/er-page.js`](er-page.js) | `8d3fcd84b285` | L1–L164 |
| [`app/frontend/mermaid-page.js`](mermaid-page.js) | `55a8c861255e` | L1–L57 |
| [`app/frontend/mock-pay-page.js`](mock-pay-page.js) | `4a6d81a7fac4` | L1–L28 |
| [`app/frontend/package.json`](package.json) | `8b4333b81f4f` | L1–L14 |
| [`app/frontend/payments-admin.js`](payments-admin.js) | `ffc056e64819` | L1–L195 |
| [`app/frontend/shop-page.js`](shop-page.js) | `78be39e48f4f` | L1–L236 |
| [`app/frontend/support-page.js`](support-page.js) | `dc4dd3149064` | L1–L127 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

Jinja 模板加载 app/static/js 中的构建产物；浏览器请求使用同源相对路径。

## 变更与验证

修改后运行 npm run build 并提交对应产物；异步操作需要处理退出登录、换账号和过期响应。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

## payments-admin.js：管理员收款工作台

reset/clearDetail递增会话与详情序号，清私人数据/输入、abort旧请求；request携带同源Cookie/no-store，只有当前会话401/403才能清屏，旧账号错误不能清掉新账号。list用独立序号和50条替换分页，detail用详情序号和独立事件游标，textContent呈现原合同、凭证和事件；不拼HTML、不写浏览器存储。operate先校验手输单号/依据，人工分数和参考流水，或核查/原文件绑定，再确认弹窗、禁止重叠操作。结果丢失先刷新凭证，不自动重试写操作；旧账号已提交的服务端操作不保证取消，但迟到结果不会回填新账号。按钮/字段与payments-admin.html同步，构建入口在vite.config.mjs；来源和构建产物都执行Node回归，仍需真实浏览器验收。

## 第五批：复核与游标上下文

payments-admin.detail显示复核状态/操作人/说明，按新资料重新待办；operate的review分支要求共同手输单号和160字内说明，带资料摘要/版本/稳定请求ID。5xx或丢响应保留原请求，4xx或读到本请求已提交则允许重新核对，旧账号响应仍丢弃。nonce只是幂等标识，不是认证秘密，现代crypto.randomUUID优先，旧开发上下文使用随机降级并由数据库唯一约束兜底。list的游标绑定筛选参数，改变范围不复用旧游标；空页有游标显示继续提示。没有自动复核/后台轮询；保存后需刷新左侧清单。

## 第六批：退款与原付款分开展示

payments-admin把退款查询/人工已退款登记纳入原有账号、详情代次和busy保护。独立字段存原商户退款号或人工退款流水/金额/带时区成功时间，确认弹窗明确不发起退款；来源从原收款凭证决定，已退款人工表单隐藏。失败保留原字段，先刷新记录再按原流水/时间重试，不另造退款号；切换账户清空字段和退款证据。所有证据用textContent，不插HTML。shop-page先看refunded展示停止下载说明；paid/downloaded仍是历史付款/领链状态。Node VM测试源码和提交bundle，不代表浏览器实机验收。
