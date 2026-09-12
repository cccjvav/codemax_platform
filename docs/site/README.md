# 离线文档站

运行 `python scripts/build_docs_site.py` 后打开本目录 `index.html`。脚本先检查 README 契约，再生成文档、源码、导入图、路由表和限定符号索引。

- `d/`：登记的 Markdown 页面；`s/`：Git 清单识别的源码与配置页面。
- `data/code-manifest.json`：源码 SHA-256、归属、行数、Python 符号与源码 docstring。不是语义正确性证书。
- `data/meta.json`：本次可复算统计；不再把历史固定页数当作当前真相。
- `data/search-index.js`：静态搜索数据。先于 `site.js` 加载，支持 file:// 打开及站点子目录部署，不依赖 fetch 的本地文件权限。
- TOC、搜索和标题 ID 来自同一渲染结果；跳过围栏示例，重复标题有唯一 ID。
- 源码相对链接改写到站内文件页；范围以起始行锚点与 end 参数显示。
- 符号卡可展开参数签名、返回注解与源码说明；缺 docstring 明确提示，默认隐藏测试符号但可切换。
- 每次构建清理旧 d/s 页面，仅覆盖生成目录，不删除手写 CSS、JS 和 README。

文档站生成物被 Git 忽略。手写 `style.css` 与 `site.js` 必须提交。文档源码中旧日期的性能/部署记录属于历史证据；历史实现结果见 [第二批验收](../SECOND_REPAIR_ACCEPTANCE.md)；当前 Windows/conda 操作见 [本机指南](../WINDOWS_CONDA.md)，排版与搜索的实际检查见 [验收手册](../ACCEPTANCE_GUIDE.md)。

## 模块职责

离线文档站的手写样式和交互源；HTML 与 data JSON 是忽略的生成物。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`docs/site/site.js`](site.js) | `5be91521deb1` | L1–L159 |
| [`docs/site/style.css`](style.css) | `228042a660d7` | L1–L277 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

scripts/build_docs_site.py 静态生成源码、符号、路由、文档页；无需应用数据库。

### 交互与排版职责

`doSearch` 根据标题/小节搜索索引，序号阻止旧搜索结果覆盖新输入；它不是全文语义搜索。
`highlightHashLine` 显示起止源码范围；`wireGraph`、`wireRoutes`、`wireSymbols` 只过滤已有 DOM，不改业务代码。
`style.css` 管宽屏三栏、窄屏堆叠、长签名换行、表格滚动与键盘焦点；移动覆盖规则必须放在基础规则之后。

## 变更与验证

修改站点时跑文档测试和完整构建；文档站不应依赖在线 CDN 或生产服务。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。
