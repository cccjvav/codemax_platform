# 页面模板

Jinja 只负责 HTML 外壳、表单、语义结构与站点上下文；真实交互在 `app/frontend/`，不再把旧内联脚本行号当作实现位置。

- `base.html`：导航、登录/注册浮层。经典 `auth.js` 在页面交互脚本之前执行；OAuth 同意页关闭这套登录控件。
- `er.html` / `mermaid.html`：工具表单和结果容器；本地构建的 ES module 包含第三方依赖。
- `drawio.html`：第三方编辑器、云端文件与回收站管理、本地导入/下载。保存前通过 export 协议请求新 XML。
- `shop.html`：固定数字商品、支付状态、历史订单和链接重领。定制需求引导至站内客服，不混作数字商品下单。
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
| [`app/templates/base.html`](base.html) | `aedb758f9637` | L1–L123 |
| [`app/templates/drawio.html`](drawio.html) | `485d6c82ffba` | L1–L44 |
| [`app/templates/er.html`](er.html) | `8a0d4678bbeb` | L1–L25 |
| [`app/templates/index.html`](index.html) | `992d913b0f43` | L1–L11 |
| [`app/templates/mermaid.html`](mermaid.html) | `c4642e4f7e01` | L1–L20 |
| [`app/templates/mock_pay.html`](mock_pay.html) | `7ed3736365ca` | L1–L22 |
| [`app/templates/oauth_consent.html`](oauth_consent.html) | `04e7ac926dfa` | L1–L22 |
| [`app/templates/shop.html`](shop.html) | `a84e4c538395` | L1–L106 |
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
