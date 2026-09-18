# 页面模板

Jinja 只负责 HTML 外壳、表单、语义结构与站点上下文；真实交互在 `app/frontend/`，不再把旧内联脚本行号当作实现位置。

- `base.html`：导航、登录/注册浮层。经典 `auth.js` 在页面交互脚本之前执行；OAuth 同意页关闭这套登录控件。
- `er.html` / `mermaid.html`：工具表单和结果容器；本地构建的 ES module 包含第三方依赖。
- `drawio.html`：第三方编辑器、云端文件与回收站管理、本地导入/下载。保存前通过 export 协议请求新 XML。
- `shop.html`：固定数字商品、支付状态、历史订单和链接重领。定制需求引导至站内客服，不混作数字商品下单。
- `payments-admin.html`：管理员订单登录壳、筛选、合同/凭证/事件、人工确认/主动查单/历史绑定表单；hidden强制隐藏避免grid样式覆盖，所有数据另经鉴权API。
- `support-center.html`：公开的登录提示外壳；私人消息、会话列表及管理员操作都由鉴权 API 提供。
- `oauth_consent.html`：无脚本同意表单；签名绑定用户与凭证版本，回调 query 保留。
- `mock_pay.html`：开发用模拟支付，生产启动检查禁止启用。

修改 DOM ID 时同步检查页面脚本；不要用用户/模型文本拼接 HTML。客户消息由 `textContent` 渲染。

## 模块职责

Jinja 页面外壳、表单与导航；交互实现放在 frontend。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/templates/base.html`](base.html) | `d6b4935d2d68` | L1–L124 |
| [`app/templates/drawio.html`](drawio.html) | `485d6c82ffba` | L1–L44 |
| [`app/templates/er.html`](er.html) | `8a0d4678bbeb` | L1–L25 |
| [`app/templates/index.html`](index.html) | `992d913b0f43` | L1–L11 |
| [`app/templates/mermaid.html`](mermaid.html) | `c4642e4f7e01` | L1–L20 |
| [`app/templates/mock_pay.html`](mock_pay.html) | `4f5516de45ef` | L1–L22 |
| [`app/templates/oauth_consent.html`](oauth_consent.html) | `2a8858b00ebd` | L1–L22 |
| [`app/templates/payments-admin.html`](payments-admin.html) | `5ac70b5fb510` | L1–L126 |
| [`app/templates/shop.html`](shop.html) | `03d88eb2ba78` | L1–L107 |
| [`app/templates/support-center.html`](support-center.html) | `19912173fdfb` | L1–L30 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

site.py 提供页面上下文；HTML 自动转义，不信任模型、消息和用户文本中的标记。

## 变更与验证

页面 ID 变更要同步脚本和模板测试；新增承诺必须与实际支付、交付和人工服务能力一致。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

payments-admin增加复核状态、三种处理进度及两个投影筛选。保留真实到账/永久绑定各自的确认文案；完成本轮复核不是退款成功，没有伪造退款按钮。

## 第六批界面合同

payments-admin.html增加成功退款凭证区、原商户退款号查询与人工已完成全额退款记录表单，复用完整单号/依据确认；不提供发起退款按钮。shop.html增加st-refunded，供refunded字段选择，不把原支付状态改成取消。字段与app/frontend脚本同步构建后再验证；浏览器与真实商户仍需单独签收。


第七批增加finance-refund-notice独立线索区和finance-refund-prefill填号按钮（type=button、默认disabled）。与成功退款凭证区分开，按钮不能触发表单submit；是否可见/可用由脚本配合原凭证渠道与当前通知决定，真正权限仍在API。


第八批增加request-view、refund-request表单、request-amount及request-prefill。准备表单明确一单一笔全额、未发送/未授权自动发送/不撤权；使用共同手输单号/依据。两个填号按钮均type=button，保存准备的submit只调用本地接口。现行控制列表和脚本同步；页面壳不嵌入私人准备数据。


## 第九批财务模板

新增submission-view、客户原因、手动原准备号/全额以及独立authorize/send表单。发送区默认hidden，文案明确可能真实转款；new-attempt按钮是type=button只准备下一尝试，不提交。正式启用须商户/恢复验收；普通核验按钮仍不转账。


## 第十批停止控件

新增stop-view和refund-stop表单，默认hidden；明确只阻止新的本站发送，不能撤回已有尝试/渠道退款，不释放原号或改变下载。submit发送到stop路径，不复用真实send动作。无需开启退款发送开关才能使用。


## 退款核验队列

payments-admin新增只读finance-verification-view，与通知线索/成功凭证分区；不增加按钮或改变十一类显式操作。后台查询成功观察不直接等于权益撤销，文本由payments-admin.js安全填充。

## 第十二批：所选核验任务的人工入口

payments-admin新增verification-control-view和verification-control表单（任务ID、hold/retry选项及独立提交按钮）。仍需页面公共原单确认和依据；说明暂停只作用当前任务，其他通知不受影响，在途GET不能召回，8次总预算不重置。控件隐藏/快照不是服务端权限，路由必须再次验证。
