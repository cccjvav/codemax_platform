# 静态资源与前端产物

`support.css` 是站内客服布局样式：两栏由 `support-page.js` 按管理员角色切 `.with-inbox` class，不再依赖 `:has()`（旧内核不支持，TD-263）；支付码图片是公开素材，不是到账凭证。人工支付必须由管理员实际核验到账。

`js/` 全部由 Vite 生成，包括页面入口和 Mermaid/D3 共用分块。不得只提交入口文件而遗漏其相对导入的分块；CI 用干净构建核对漂移。
修改 `app/frontend/` 后运行 `npm run build`，再检查源码及相关产物。生产后端不需要 Node。

Mermaid 11.17.2 已从运行时 CDN 改成本地锁定依赖；包体较大并有构建体积警告，这不是构建失败，也没有通过提高警告阈值隐藏。文档站独立使用原生 JS/SVG，不加载业务 Mermaid 包。

## 模块职责

样式、图像与提交到仓库的 Vite 产物。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`app/static/favicon.svg`](favicon.svg) | `f53a579480dd` | L1–L10 |
| [`app/static/js/abnfDiagram-VCTEODGH.js`](js/abnfDiagram-VCTEODGH.js) | `9e5522255869` | 生成物，见模块构建说明 |
| [`app/static/js/arc.js`](js/arc.js) | `fd7da114edcf` | 生成物，见模块构建说明 |
| [`app/static/js/architecture-7GRP2DOG.js`](js/architecture-7GRP2DOG.js) | `509d093d6593` | 生成物，见模块构建说明 |
| [`app/static/js/architectureDiagram-5GKGNRK7.js`](js/architectureDiagram-5GKGNRK7.js) | `d662d68bd83b` | 生成物，见模块构建说明 |
| [`app/static/js/array.js`](js/array.js) | `63126646dbf6` | 生成物，见模块构建说明 |
| [`app/static/js/auth.js`](js/auth.js) | `c1872e5784d3` | 生成物，见模块构建说明 |
| [`app/static/js/blockDiagram-I7D4REHJ.js`](js/blockDiagram-I7D4REHJ.js) | `306f38f17142` | 生成物，见模块构建说明 |
| [`app/static/js/c4Diagram-7LVT6UL2.js`](js/c4Diagram-7LVT6UL2.js) | `3c7c024dd767` | 生成物，见模块构建说明 |
| [`app/static/js/channel.js`](js/channel.js) | `d881e73b015d` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-2Q5K7J3B.js`](js/chunk-2Q5K7J3B.js) | `a2970e1985d8` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-4HAMMTFA.js`](js/chunk-4HAMMTFA.js) | `de48c728c2d3` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-5VM5RSS4.js`](js/chunk-5VM5RSS4.js) | `233a2a3d3d97` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-75Z2AOVW.js`](js/chunk-75Z2AOVW.js) | `27055f755654` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-DU6HZSFF.js`](js/chunk-DU6HZSFF.js) | `bf6054f8d791` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-F27PBJKO.js`](js/chunk-F27PBJKO.js) | `9e857f41db94` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-FOHPRMQF.js`](js/chunk-FOHPRMQF.js) | `6883597ec9cf` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-GMAD6QVW.js`](js/chunk-GMAD6QVW.js) | `cb2d277d9c40` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-GVQU2GXP.js`](js/chunk-GVQU2GXP.js) | `73876d221c3b` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-IMKFNOWR.js`](js/chunk-IMKFNOWR.js) | `8909f1e07f0d` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-JWPE2WC7.js`](js/chunk-JWPE2WC7.js) | `2173f1e31f5c` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-L3NEJ4N5.js`](js/chunk-L3NEJ4N5.js) | `908d6cc2585c` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-OSK3NFVY.js`](js/chunk-OSK3NFVY.js) | `19766d40d816` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-P2QGCYS3.js`](js/chunk-P2QGCYS3.js) | `a7166951da7e` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-POPQ4Y6H.js`](js/chunk-POPQ4Y6H.js) | `5497ff512135` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-PWAF6VOD.js`](js/chunk-PWAF6VOD.js) | `7c0d1931188b` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-SHT3W25Y.js`](js/chunk-SHT3W25Y.js) | `ac2020fe7a01` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-SVP7TREG.js`](js/chunk-SVP7TREG.js) | `0f2367b7324f` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-TICWLB2K.js`](js/chunk-TICWLB2K.js) | `3a2ea28375fe` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-X3CZISLH.js`](js/chunk-X3CZISLH.js) | `64c6b905dff6` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-XXDRQBXY.js`](js/chunk-XXDRQBXY.js) | `55e31a4fcaeb` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-Y2CYZVJY.js`](js/chunk-Y2CYZVJY.js) | `a74e3669f356` | 生成物，见模块构建说明 |
| [`app/static/js/classDiagram-ZZMXUADV.js`](js/classDiagram-ZZMXUADV.js) | `efff43178c21` | 生成物，见模块构建说明 |
| [`app/static/js/classDiagram-v2-VYDZK3BY.js`](js/classDiagram-v2-VYDZK3BY.js) | `efff43178c21` | 生成物，见模块构建说明 |
| [`app/static/js/cose-bilkent-JH36ORCC.js`](js/cose-bilkent-JH36ORCC.js) | `4965d942a1ec` | 生成物，见模块构建说明 |
| [`app/static/js/cynefin-OW5HDTMX.js`](js/cynefin-OW5HDTMX.js) | `8d8f21a01e95` | 生成物，见模块构建说明 |
| [`app/static/js/cynefinDiagram-5FMLGOSQ.js`](js/cynefinDiagram-5FMLGOSQ.js) | `26af6813acdd` | 生成物，见模块构建说明 |
| [`app/static/js/cytoscape.esm.js`](js/cytoscape.esm.js) | `bd28566dcfa2` | 生成物，见模块构建说明 |
| [`app/static/js/dagre-GXQ25YYZ.js`](js/dagre-GXQ25YYZ.js) | `793e1ddfcf9a` | 生成物，见模块构建说明 |
| [`app/static/js/dagre.js`](js/dagre.js) | `78551b6f390c` | 生成物，见模块构建说明 |
| [`app/static/js/defaultLocale.js`](js/defaultLocale.js) | `098cc4b380fb` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-S7CK7UJ4.js`](js/diagram-S7CK7UJ4.js) | `5f041c50ea21` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-UQ7AKVKN.js`](js/diagram-UQ7AKVKN.js) | `d20dcaabcda4` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-VSXAHHWV.js`](js/diagram-VSXAHHWV.js) | `60f8b5313892` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-VX7I27RA.js`](js/diagram-VX7I27RA.js) | `1373a7498fea` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-Z3DM3KII.js`](js/diagram-Z3DM3KII.js) | `372dc41eec58` | 生成物，见模块构建说明 |
| [`app/static/js/dist.js`](js/dist.js) | `7b1e508a3391` | 生成物，见模块构建说明 |
| [`app/static/js/drawio-page.js`](js/drawio-page.js) | `60180eac737f` | 生成物，见模块构建说明 |
| [`app/static/js/ebnfDiagram-PWID7BFC.js`](js/ebnfDiagram-PWID7BFC.js) | `ae05bf314f9d` | 生成物，见模块构建说明 |
| [`app/static/js/er-page.js`](js/er-page.js) | `78f1066eb7a6` | 生成物，见模块构建说明 |
| [`app/static/js/erDiagram-RLTQ6QDP.js`](js/erDiagram-RLTQ6QDP.js) | `cc19e69b836a` | 生成物，见模块构建说明 |
| [`app/static/js/eventmodeling-NTZA5JFV.js`](js/eventmodeling-NTZA5JFV.js) | `6d880858817b` | 生成物，见模块构建说明 |
| [`app/static/js/flowDiagram-HODETNUW.js`](js/flowDiagram-HODETNUW.js) | `5018e3a596bc` | 生成物，见模块构建说明 |
| [`app/static/js/ganttDiagram-EL5Y4UJY.js`](js/ganttDiagram-EL5Y4UJY.js) | `9f7c66cc7235` | 生成物，见模块构建说明 |
| [`app/static/js/gitGraph-4MIJSDKK.js`](js/gitGraph-4MIJSDKK.js) | `7c0174a3e844` | 生成物，见模块构建说明 |
| [`app/static/js/gitGraphDiagram-WWUBYQGX.js`](js/gitGraphDiagram-WWUBYQGX.js) | `29a7f3941c75` | 生成物，见模块构建说明 |
| [`app/static/js/graphlib.js`](js/graphlib.js) | `6afce81d54f1` | 生成物，见模块构建说明 |
| [`app/static/js/info-A6RAGUB7.js`](js/info-A6RAGUB7.js) | `d2e43a0ee8c5` | 生成物，见模块构建说明 |
| [`app/static/js/infoDiagram-27XIBGKW.js`](js/infoDiagram-27XIBGKW.js) | `9ce08addc4f7` | 生成物，见模块构建说明 |
| [`app/static/js/init.js`](js/init.js) | `cb5c97a434bb` | 生成物，见模块构建说明 |
| [`app/static/js/ishikawaDiagram-5VMMS53U.js`](js/ishikawaDiagram-5VMMS53U.js) | `ae2f80d1be7b` | 生成物，见模块构建说明 |
| [`app/static/js/journeyDiagram-3NMN7TZE.js`](js/journeyDiagram-3NMN7TZE.js) | `05bababb26fe` | 生成物，见模块构建说明 |
| [`app/static/js/kanban-definition-UXKFOSKX.js`](js/kanban-definition-UXKFOSKX.js) | `1a46033d0941` | 生成物，见模块构建说明 |
| [`app/static/js/katex.js`](js/katex.js) | `57d7eb6dbbc3` | 生成物，见模块构建说明 |
| [`app/static/js/line.js`](js/line.js) | `655f21b5e91d` | 生成物，见模块构建说明 |
| [`app/static/js/linear.js`](js/linear.js) | `78c6a0ccd56b` | 生成物，见模块构建说明 |
| [`app/static/js/mermaid-page.js`](js/mermaid-page.js) | `156b35fa8115` | 生成物，见模块构建说明 |
| [`app/static/js/mermaid-parser.core.js`](js/mermaid-parser.core.js) | `7146f3a0e00e` | 生成物，见模块构建说明 |
| [`app/static/js/mermaid.core.js`](js/mermaid.core.js) | `1fb816f0fe83` | 生成物，见模块构建说明 |
| [`app/static/js/mindmap-definition-YA3MSWOX.js`](js/mindmap-definition-YA3MSWOX.js) | `7b724282b151` | 生成物，见模块构建说明 |
| [`app/static/js/mock-pay-page.js`](js/mock-pay-page.js) | `3dd0b4be7370` | 生成物，见模块构建说明 |
| [`app/static/js/ordinal.js`](js/ordinal.js) | `eaef3ecf36db` | 生成物，见模块构建说明 |
| [`app/static/js/packet-AYTQ26CC.js`](js/packet-AYTQ26CC.js) | `604591bbfc70` | 生成物，见模块构建说明 |
| [`app/static/js/path.js`](js/path.js) | `71b62c60fd66` | 生成物，见模块构建说明 |
| [`app/static/js/payments-admin.js`](js/payments-admin.js) | `116e0b0c2872` | 生成物，见模块构建说明 |
| [`app/static/js/pegDiagram-XKGWAZYB.js`](js/pegDiagram-XKGWAZYB.js) | `81e9d6cff794` | 生成物，见模块构建说明 |
| [`app/static/js/pie-WAS4IAKB.js`](js/pie-WAS4IAKB.js) | `1b84c4bfa676` | 生成物，见模块构建说明 |
| [`app/static/js/pieDiagram-E7YTZNPT.js`](js/pieDiagram-E7YTZNPT.js) | `bc6c0614a828` | 生成物，见模块构建说明 |
| [`app/static/js/quadrantDiagram-AXDQQJYC.js`](js/quadrantDiagram-AXDQQJYC.js) | `b3dba4c9ccc6` | 生成物，见模块构建说明 |
| [`app/static/js/radar-RG4KPBEZ.js`](js/radar-RG4KPBEZ.js) | `ea2142fee9a1` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-74A4TZTK.js`](js/railroad-74A4TZTK.js) | `dac2982f2f78` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-abnf-HS5TGJTU.js`](js/railroad-abnf-HS5TGJTU.js) | `fa76fc90ee5c` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-ebnf-LZEXJU2U.js`](js/railroad-ebnf-LZEXJU2U.js) | `dfa9fc39f9cc` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-peg-WCYAUIDC.js`](js/railroad-peg-WCYAUIDC.js) | `aeb91d0f3856` | 生成物，见模块构建说明 |
| [`app/static/js/railroadDiagram-O6MQD6OU.js`](js/railroadDiagram-O6MQD6OU.js) | `8a1c67663b77` | 生成物，见模块构建说明 |
| [`app/static/js/requirementDiagram-BXWQKSXE.js`](js/requirementDiagram-BXWQKSXE.js) | `cb771bd9158d` | 生成物，见模块构建说明 |
| [`app/static/js/rough.esm.js`](js/rough.esm.js) | `ef405c5eaa33` | 生成物，见模块构建说明 |
| [`app/static/js/sankeyDiagram-P5KCCOFB.js`](js/sankeyDiagram-P5KCCOFB.js) | `94031b0096e6` | 生成物，见模块构建说明 |
| [`app/static/js/sequenceDiagram-WJ2MYXX4.js`](js/sequenceDiagram-WJ2MYXX4.js) | `7253da3935a3` | 生成物，见模块构建说明 |
| [`app/static/js/shop-page.js`](js/shop-page.js) | `47c3cb169975` | 生成物，见模块构建说明 |
| [`app/static/js/sizeCapture-INFHLROL.js`](js/sizeCapture-INFHLROL.js) | `1686f2a8163c` | 生成物，见模块构建说明 |
| [`app/static/js/src.js`](js/src.js) | `56404f4d6398` | 生成物，见模块构建说明 |
| [`app/static/js/stateDiagram-D77RDMKH.js`](js/stateDiagram-D77RDMKH.js) | `34bed6ca115b` | 生成物，见模块构建说明 |
| [`app/static/js/stateDiagram-v2-MP3YSRHH.js`](js/stateDiagram-v2-MP3YSRHH.js) | `a53c367355a3` | 生成物，见模块构建说明 |
| [`app/static/js/support-page.js`](js/support-page.js) | `4708cfc724cc` | 生成物，见模块构建说明 |
| [`app/static/js/swimlanes-42K2YHIH.js`](js/swimlanes-42K2YHIH.js) | `24b062312da4` | 生成物，见模块构建说明 |
| [`app/static/js/swimlanesDiagram-VR7AAH4N.js`](js/swimlanesDiagram-VR7AAH4N.js) | `99cd3de777d0` | 生成物，见模块构建说明 |
| [`app/static/js/timeline-definition-24CTP7MA.js`](js/timeline-definition-24CTP7MA.js) | `6f2a01973e9c` | 生成物，见模块构建说明 |
| [`app/static/js/treeView-Q6P3EWNA.js`](js/treeView-Q6P3EWNA.js) | `8abb2a3780c1` | 生成物，见模块构建说明 |
| [`app/static/js/treemap-WGGIJYW6.js`](js/treemap-WGGIJYW6.js) | `7bb0cc7cce0b` | 生成物，见模块构建说明 |
| [`app/static/js/vennDiagram-4TSXK5OY.js`](js/vennDiagram-4TSXK5OY.js) | `84702a938e15` | 生成物，见模块构建说明 |
| [`app/static/js/wardley-WFR3VGLG.js`](js/wardley-WFR3VGLG.js) | `eabfd763cf64` | 生成物，见模块构建说明 |
| [`app/static/js/wardleyDiagram-VM6X3IG4.js`](js/wardleyDiagram-VM6X3IG4.js) | `cba4035cb673` | 生成物，见模块构建说明 |
| [`app/static/js/xychartDiagram-S5SC5T6Z.js`](js/xychartDiagram-S5SC5T6Z.js) | `6faab4aa11db` | 生成物，见模块构建说明 |
| [`app/static/pay_qr.svg`](pay_qr.svg) | `e76dba08c82d` | L1–L17 |
| [`app/static/support.css`](support.css) | `b9404b1b372d` | L1–L15 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

app/static/js/ 是生成目录，归本 README 管理，不逐个解释第三方压缩符号。

## 变更与验证

不得手改压缩 JS；从 app/frontend 修改后 npm run build，检查产物漂移。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

## 2026-09-15 交叉审查增量

本批重新构建对应的shop/support/drawio/er入口；取消轮询、UUID降级/管理员会话、乱序列表及延迟Blob释放在手写源码维护。只改源后构建，不手改生成JS。


## 第十一批产物

payments-admin.js由Vite生成，新增核验状态只读展示/账号清屏；不手改bundle。重复构建和源码/产物两份Node VM检查，真实浏览器另验。

## 第十二批产物

payments-admin.js由新增核验调度表单源码重建；不要直接修改压缩文件。未知body/key恢复、409重新确认、取消及账号切换必须在源码和bundle同时执行测试，并重建比较摘要。


## 2026-09-23 图标、对比度与产物重建（TD-272）

- 新增 `favicon.svg`：纯 `<path>` + `xmlns` 的站点图标，不含脚本/外链/字体引用，符合 CSP `img-src 'self'`。此前没有图标文件，浏览器对每个页面都会额外请求一次并拿到 404。
- `support.css` 的消息时间色由 `#64748b` 改为 `#475569`：11.7px 的小字落在 `#f1f5f9`（客户气泡）上是 4.34:1，低于 WCAG AA；`#475569` 在白底系气泡上为 6.92/6.96:1。
- 产物 `js/auth.js`、`js/drawio-page.js`、`js/mermaid-page.js` 随源码重建提交（`npm run build`，CI 漂移检查会核对）。

- TD-273 重建：`js/auth.js`、`js/drawio-page.js`、`js/support-page.js`（会话到期只提示、不清空编辑内容与草稿）。

## 2026-09-25 客服留言框字体与产物重建（TD-278）

- `support.css`：`#support-body` 改用正文字体族（base 给 textarea 的等宽字体是为 DDL 准备的）；只改 `font-family`，手机 16px 规则照常生效。
- `js/` 下 `auth.js`、`drawio-page.js`、`payments-admin.js`、`shop-page.js`、`support-page.js` 由 `npm run build` 从 `app/frontend` 重建（本批 TD-278 源码改动），`vite.config.mjs` 与产物名不变。

## 2026-09-25 ER 页面产物重建（TD-279）

- `js/er-page.js` 由 `npm run build` 从 `app/frontend/er-page.js` + `er-layout.js` 重建。
- 同时变化的 `js/src.js`（d3 共享分块）与 26 个 Mermaid 分块只是**压缩后的导出名重排**：ER 页新用到 `d3.zoomIdentity`，共享分块的导出集合变了，引用它的分块随之改名。已核实：用旧 `er-page.js` 构建零漂移、新源码重复构建结果一致，依赖版本未变。

## 2026-09-25 Mermaid 按需加载与预加载地址（TD-280）

- `js/mermaid-page.js` 从约 163 KiB（首屏连同静态依赖 16 个文件、约 669 KiB）降到约 3.5 KiB；Mermaid 本体进了新分块 `js/mermaid.core.js`，点「生成类图」时才下载。随之新增/改名的分块（`graphlib.js`、`chunk-*.js` 等）与 Mermaid 各图类型分块的压缩名重排都是这次拆分的结果。
- 预加载助手（在 `js/mermaid-page.js` 里）拼接的地址前缀由 `/` 改为 `/static/js/`（`vite.config.mjs` 的 `base`）。
- `js/auth.js` 随修改密码浮层重建。
