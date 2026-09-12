# 静态资源与前端产物

`support.css` 是站内客服布局样式；支付码图片是公开素材，不是到账凭证。人工支付必须由管理员实际核验到账。

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
| [`app/static/js/abnfDiagram-VCTEODGH.js`](js/abnfDiagram-VCTEODGH.js) | `22e93e1e23f8` | 生成物，见模块构建说明 |
| [`app/static/js/arc.js`](js/arc.js) | `fd7da114edcf` | 生成物，见模块构建说明 |
| [`app/static/js/architecture-7GRP2DOG.js`](js/architecture-7GRP2DOG.js) | `509d093d6593` | 生成物，见模块构建说明 |
| [`app/static/js/architectureDiagram-5GKGNRK7.js`](js/architectureDiagram-5GKGNRK7.js) | `198519b0d7f0` | 生成物，见模块构建说明 |
| [`app/static/js/array.js`](js/array.js) | `63126646dbf6` | 生成物，见模块构建说明 |
| [`app/static/js/auth.js`](js/auth.js) | `f0046a8742fb` | 生成物，见模块构建说明 |
| [`app/static/js/blockDiagram-I7D4REHJ.js`](js/blockDiagram-I7D4REHJ.js) | `cd411aa15cda` | 生成物，见模块构建说明 |
| [`app/static/js/c4Diagram-7LVT6UL2.js`](js/c4Diagram-7LVT6UL2.js) | `1ff47f09c4ed` | 生成物，见模块构建说明 |
| [`app/static/js/channel.js`](js/channel.js) | `46376a0454cf` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-2Q5K7J3B.js`](js/chunk-2Q5K7J3B.js) | `a2970e1985d8` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-4HAMMTFA.js`](js/chunk-4HAMMTFA.js) | `a04a712822a7` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-5VM5RSS4.js`](js/chunk-5VM5RSS4.js) | `233a2a3d3d97` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-75Z2AOVW.js`](js/chunk-75Z2AOVW.js) | `b1d4973d0adb` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-DU6HZSFF.js`](js/chunk-DU6HZSFF.js) | `ec41d13ec65b` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-F27PBJKO.js`](js/chunk-F27PBJKO.js) | `e07bba8cb358` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-FOHPRMQF.js`](js/chunk-FOHPRMQF.js) | `6883597ec9cf` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-GMAD6QVW.js`](js/chunk-GMAD6QVW.js) | `4adc2d43b2a6` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-GVQU2GXP.js`](js/chunk-GVQU2GXP.js) | `73876d221c3b` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-IMKFNOWR.js`](js/chunk-IMKFNOWR.js) | `82fe944f57f3` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-JWPE2WC7.js`](js/chunk-JWPE2WC7.js) | `2173f1e31f5c` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-P2QGCYS3.js`](js/chunk-P2QGCYS3.js) | `a7166951da7e` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-POPQ4Y6H.js`](js/chunk-POPQ4Y6H.js) | `5497ff512135` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-PWAF6VOD.js`](js/chunk-PWAF6VOD.js) | `7c0d1931188b` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-SHT3W25Y.js`](js/chunk-SHT3W25Y.js) | `882421fdd1a8` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-SVP7TREG.js`](js/chunk-SVP7TREG.js) | `847366b7172b` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-TICWLB2K.js`](js/chunk-TICWLB2K.js) | `94e3d3256094` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-X3CZISLH.js`](js/chunk-X3CZISLH.js) | `64c6b905dff6` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-XXDRQBXY.js`](js/chunk-XXDRQBXY.js) | `c7d03b7608aa` | 生成物，见模块构建说明 |
| [`app/static/js/chunk-Y2CYZVJY.js`](js/chunk-Y2CYZVJY.js) | `a74e3669f356` | 生成物，见模块构建说明 |
| [`app/static/js/classDiagram-ZZMXUADV.js`](js/classDiagram-ZZMXUADV.js) | `801caae69eef` | 生成物，见模块构建说明 |
| [`app/static/js/classDiagram-v2-VYDZK3BY.js`](js/classDiagram-v2-VYDZK3BY.js) | `801caae69eef` | 生成物，见模块构建说明 |
| [`app/static/js/cose-bilkent-JH36ORCC.js`](js/cose-bilkent-JH36ORCC.js) | `0ac4d27d4db1` | 生成物，见模块构建说明 |
| [`app/static/js/cynefin-OW5HDTMX.js`](js/cynefin-OW5HDTMX.js) | `8d8f21a01e95` | 生成物，见模块构建说明 |
| [`app/static/js/cynefinDiagram-5FMLGOSQ.js`](js/cynefinDiagram-5FMLGOSQ.js) | `78844bcbc3e7` | 生成物，见模块构建说明 |
| [`app/static/js/cytoscape.esm.js`](js/cytoscape.esm.js) | `bd28566dcfa2` | 生成物，见模块构建说明 |
| [`app/static/js/dagre-GXQ25YYZ.js`](js/dagre-GXQ25YYZ.js) | `625fa91e2526` | 生成物，见模块构建说明 |
| [`app/static/js/dagre.js`](js/dagre.js) | `1a831655f522` | 生成物，见模块构建说明 |
| [`app/static/js/defaultLocale.js`](js/defaultLocale.js) | `098cc4b380fb` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-S7CK7UJ4.js`](js/diagram-S7CK7UJ4.js) | `af95e2a00e2d` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-UQ7AKVKN.js`](js/diagram-UQ7AKVKN.js) | `8ad17acde898` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-VSXAHHWV.js`](js/diagram-VSXAHHWV.js) | `70d782a125ee` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-VX7I27RA.js`](js/diagram-VX7I27RA.js) | `bd9b31ce6bed` | 生成物，见模块构建说明 |
| [`app/static/js/diagram-Z3DM3KII.js`](js/diagram-Z3DM3KII.js) | `aa8b75627d07` | 生成物，见模块构建说明 |
| [`app/static/js/dist.js`](js/dist.js) | `7b1e508a3391` | 生成物，见模块构建说明 |
| [`app/static/js/drawio-page.js`](js/drawio-page.js) | `24213b835fd8` | 生成物，见模块构建说明 |
| [`app/static/js/ebnfDiagram-PWID7BFC.js`](js/ebnfDiagram-PWID7BFC.js) | `2fdef19157c2` | 生成物，见模块构建说明 |
| [`app/static/js/er-page.js`](js/er-page.js) | `02e92ef138f2` | 生成物，见模块构建说明 |
| [`app/static/js/erDiagram-RLTQ6QDP.js`](js/erDiagram-RLTQ6QDP.js) | `1e225335524d` | 生成物，见模块构建说明 |
| [`app/static/js/eventmodeling-NTZA5JFV.js`](js/eventmodeling-NTZA5JFV.js) | `6d880858817b` | 生成物，见模块构建说明 |
| [`app/static/js/flowDiagram-HODETNUW.js`](js/flowDiagram-HODETNUW.js) | `d7d9dae9f4bc` | 生成物，见模块构建说明 |
| [`app/static/js/ganttDiagram-EL5Y4UJY.js`](js/ganttDiagram-EL5Y4UJY.js) | `1fcf68c6943b` | 生成物，见模块构建说明 |
| [`app/static/js/gitGraph-4MIJSDKK.js`](js/gitGraph-4MIJSDKK.js) | `7c0174a3e844` | 生成物，见模块构建说明 |
| [`app/static/js/gitGraphDiagram-WWUBYQGX.js`](js/gitGraphDiagram-WWUBYQGX.js) | `9413c66e4fd8` | 生成物，见模块构建说明 |
| [`app/static/js/info-A6RAGUB7.js`](js/info-A6RAGUB7.js) | `d2e43a0ee8c5` | 生成物，见模块构建说明 |
| [`app/static/js/infoDiagram-27XIBGKW.js`](js/infoDiagram-27XIBGKW.js) | `60bb3bb92ff4` | 生成物，见模块构建说明 |
| [`app/static/js/init.js`](js/init.js) | `cb5c97a434bb` | 生成物，见模块构建说明 |
| [`app/static/js/ishikawaDiagram-5VMMS53U.js`](js/ishikawaDiagram-5VMMS53U.js) | `5c99245ad85e` | 生成物，见模块构建说明 |
| [`app/static/js/journeyDiagram-3NMN7TZE.js`](js/journeyDiagram-3NMN7TZE.js) | `c745a2fd25e2` | 生成物，见模块构建说明 |
| [`app/static/js/kanban-definition-UXKFOSKX.js`](js/kanban-definition-UXKFOSKX.js) | `89109b81f2d4` | 生成物，见模块构建说明 |
| [`app/static/js/katex.js`](js/katex.js) | `57d7eb6dbbc3` | 生成物，见模块构建说明 |
| [`app/static/js/line.js`](js/line.js) | `655f21b5e91d` | 生成物，见模块构建说明 |
| [`app/static/js/linear.js`](js/linear.js) | `e397ec34a46d` | 生成物，见模块构建说明 |
| [`app/static/js/mermaid-page.js`](js/mermaid-page.js) | `5bcacc2989ca` | 生成物，见模块构建说明 |
| [`app/static/js/mermaid-parser.core.js`](js/mermaid-parser.core.js) | `9520a3f6428b` | 生成物，见模块构建说明 |
| [`app/static/js/mindmap-definition-YA3MSWOX.js`](js/mindmap-definition-YA3MSWOX.js) | `681c78495038` | 生成物，见模块构建说明 |
| [`app/static/js/mock-pay-page.js`](js/mock-pay-page.js) | `f8cfae180477` | 生成物，见模块构建说明 |
| [`app/static/js/ordinal.js`](js/ordinal.js) | `eaef3ecf36db` | 生成物，见模块构建说明 |
| [`app/static/js/packet-AYTQ26CC.js`](js/packet-AYTQ26CC.js) | `604591bbfc70` | 生成物，见模块构建说明 |
| [`app/static/js/path.js`](js/path.js) | `71b62c60fd66` | 生成物，见模块构建说明 |
| [`app/static/js/pegDiagram-XKGWAZYB.js`](js/pegDiagram-XKGWAZYB.js) | `e7a30edbfb21` | 生成物，见模块构建说明 |
| [`app/static/js/pie-WAS4IAKB.js`](js/pie-WAS4IAKB.js) | `1b84c4bfa676` | 生成物，见模块构建说明 |
| [`app/static/js/pieDiagram-E7YTZNPT.js`](js/pieDiagram-E7YTZNPT.js) | `637c1b861223` | 生成物，见模块构建说明 |
| [`app/static/js/quadrantDiagram-AXDQQJYC.js`](js/quadrantDiagram-AXDQQJYC.js) | `b6ced8111eca` | 生成物，见模块构建说明 |
| [`app/static/js/radar-RG4KPBEZ.js`](js/radar-RG4KPBEZ.js) | `ea2142fee9a1` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-74A4TZTK.js`](js/railroad-74A4TZTK.js) | `dac2982f2f78` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-abnf-HS5TGJTU.js`](js/railroad-abnf-HS5TGJTU.js) | `fa76fc90ee5c` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-ebnf-LZEXJU2U.js`](js/railroad-ebnf-LZEXJU2U.js) | `dfa9fc39f9cc` | 生成物，见模块构建说明 |
| [`app/static/js/railroad-peg-WCYAUIDC.js`](js/railroad-peg-WCYAUIDC.js) | `aeb91d0f3856` | 生成物，见模块构建说明 |
| [`app/static/js/railroadDiagram-O6MQD6OU.js`](js/railroadDiagram-O6MQD6OU.js) | `6b69bdedf416` | 生成物，见模块构建说明 |
| [`app/static/js/requirementDiagram-BXWQKSXE.js`](js/requirementDiagram-BXWQKSXE.js) | `399b51e57a62` | 生成物，见模块构建说明 |
| [`app/static/js/rough.esm.js`](js/rough.esm.js) | `ef405c5eaa33` | 生成物，见模块构建说明 |
| [`app/static/js/sankeyDiagram-P5KCCOFB.js`](js/sankeyDiagram-P5KCCOFB.js) | `85d173a4a0d0` | 生成物，见模块构建说明 |
| [`app/static/js/sequenceDiagram-WJ2MYXX4.js`](js/sequenceDiagram-WJ2MYXX4.js) | `1b5c908c5705` | 生成物，见模块构建说明 |
| [`app/static/js/shop-page.js`](js/shop-page.js) | `37d4a76abef4` | 生成物，见模块构建说明 |
| [`app/static/js/sizeCapture-INFHLROL.js`](js/sizeCapture-INFHLROL.js) | `1686f2a8163c` | 生成物，见模块构建说明 |
| [`app/static/js/src.js`](js/src.js) | `150172a4838f` | 生成物，见模块构建说明 |
| [`app/static/js/stateDiagram-D77RDMKH.js`](js/stateDiagram-D77RDMKH.js) | `28780fcb0898` | 生成物，见模块构建说明 |
| [`app/static/js/stateDiagram-v2-MP3YSRHH.js`](js/stateDiagram-v2-MP3YSRHH.js) | `0b7e352156f4` | 生成物，见模块构建说明 |
| [`app/static/js/support-page.js`](js/support-page.js) | `3f626a807042` | 生成物，见模块构建说明 |
| [`app/static/js/swimlanes-42K2YHIH.js`](js/swimlanes-42K2YHIH.js) | `c2ceefb41216` | 生成物，见模块构建说明 |
| [`app/static/js/swimlanesDiagram-VR7AAH4N.js`](js/swimlanesDiagram-VR7AAH4N.js) | `b74e03e6b933` | 生成物，见模块构建说明 |
| [`app/static/js/timeline-definition-24CTP7MA.js`](js/timeline-definition-24CTP7MA.js) | `32c8e2e90cc3` | 生成物，见模块构建说明 |
| [`app/static/js/treeView-Q6P3EWNA.js`](js/treeView-Q6P3EWNA.js) | `8abb2a3780c1` | 生成物，见模块构建说明 |
| [`app/static/js/treemap-WGGIJYW6.js`](js/treemap-WGGIJYW6.js) | `7bb0cc7cce0b` | 生成物，见模块构建说明 |
| [`app/static/js/vennDiagram-4TSXK5OY.js`](js/vennDiagram-4TSXK5OY.js) | `3a138d9a3a6f` | 生成物，见模块构建说明 |
| [`app/static/js/wardley-WFR3VGLG.js`](js/wardley-WFR3VGLG.js) | `eabfd763cf64` | 生成物，见模块构建说明 |
| [`app/static/js/wardleyDiagram-VM6X3IG4.js`](js/wardleyDiagram-VM6X3IG4.js) | `c8f3a7cb403f` | 生成物，见模块构建说明 |
| [`app/static/js/xychartDiagram-S5SC5T6Z.js`](js/xychartDiagram-S5SC5T6Z.js) | `48d7bbc18667` | 生成物，见模块构建说明 |
| [`app/static/pay_qr.svg`](pay_qr.svg) | `e76dba08c82d` | L1–L17 |
| [`app/static/support.css`](support.css) | `15df7d6e48e3` | L1–L10 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

app/static/js/ 是生成目录，归本 README 管理，不逐个解释第三方压缩符号。

## 变更与验证

不得手改压缩 JS；从 app/frontend 修改后 npm run build，检查产物漂移。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。
