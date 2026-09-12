# 开发与文档构建脚本

## 模块职责

`check_docs_contract.py` 检查归属和文档结构，`build_docs_site.py` 提取代码并渲染离线站，`check_schema_pg.mjs` 提供可选 PGlite 建表体检。文档构建不是可有可无的旁路，现有 CI 会执行，相关函数也有 pytest 覆盖。

## 文件与入口

### check_docs_contract.py

| 函数 | 输入 → 输出 | 约束 |
| --- | --- | --- |
| `repository_files(root)` | Git 工作树 → 文件列表 | 包含 tracked 与未忽略的新文件；不扫描 node_modules/虚拟环境等忽略内容；需要 Git 环境 |
| `symbols(path)` | Python 文件 → 限定名、签名、返回注解、docstring、起止行 | AST 只读解析，不执行模块；方法与嵌套函数不能只用短名；其他语言不冒充完整 AST 支持 |
| `inventory(root)` | 清单 + policy → 文件元数据、错误列表 | 每个文件有 README owner 和 SHA；未知后缀可读文本也覆盖；生成例外要写 owner/reason；拒绝密钥形态文件及符号链接 |
| `file_table(owner, rows)` | 模块条目 → 自动 Markdown 表 | 只有文件、定位和指纹，不生成业务解释；空文件明确没有源码行 |
| `check(root, write=False)` | 校验结果 rows/errors | 检查四章节、单一自动区、指纹；剔除自动区后检查正文，乱码和纯占位章节拒绝；write 只更新文件表，不代写说明 |
| `main` | CLI → 退出码 | 存在错误非零；CI 不运行 --write 掩盖过期说明 |

### build_docs_site.py

| 阶段 / 函数 | 做什么、返回什么 | 不代表什么 |
| --- | --- | --- |
| `collect_python` / `module_name` / `package_of` | 从同一 Git 清单确定模块身份 | 不是只扫 app 或某几个示例函数 |
| `build_import_graph` / `_layer_of` | AST 静态导入和层级 → 节点、边 | 动态 import、运行时分支和实际调用关系不能全由 import 图证明 |
| `build_routes` | 装饰器及站点清单注册 → HTTP 方法/路径/处理函数/权限元数据 | 源码提取不等于运行时执行；另有集合对比测试兜底 |
| `build_symbols` / `_doc_for` | 限定符号 → 源码位置与归属说明 | 没有 docstring 时明确显示缺失，不凭函数名编解释 |
| `build_manifest` / `DOC_GROUPS` | 登记 Markdown 的分组和元数据 | 历史报告、新旧提案只是参考材料，不自动转为当前产品承诺 |
| `_md` / `_headings` / `_inject_heading_ids` / `_toc_of` | 同一渲染规则产生正文、唯一标题 ID、目录和搜索锚点 | 围栏内标题不算真正章节；重复标题不能共享 ID |
| `_postprocess` / `_normalize` / `_rebase_nav` | 源码、Markdown、范围与导航链接转为站内相对位置 | 不依赖 GitHub 才能定位代码；外部站点连通性没有因此验证 |
| `render_site` / `_shell` / `_render_*` | 文档页、源码页、图表与索引 HTML | 只清理生成 d/s 目录；不能删除手写 JS/CSS/README；文档中的 Mermaid 示例显示代码，不假称渲染图像 |
| `_write` / `esc` / `slug` | 写 UTF-8 文件、HTML 转义、稳定页面名 | 转义源码/说明时不能把 docstring 当成可执行 HTML |
| `validate_site` | 扫生成 HTML → 错误列表 | 检查本地目标、fragment、源码范围、script/img 等引用；不证明视觉正常，也不做第三方链接联网探测 |
| `main` | 契约检查 → 提取数据 → 写站点 → 验证 | 任一门禁失败应退出非零；生成物不提交 Git |

### check_schema_pg.mjs

`loadPGlite(spec)` 接受已安装的包名或目录并解析模块入口。主流程建立临时 WASM PostgreSQL、执行 full_init 两遍，检查表/外键/索引并做写读冒烟。
它执行的是破坏性初始化 SQL，必须只在测试实例中运行。PGlite 不是生产 PostgreSQL 服务，不能替代真实多会话并发与驱动联调。该可选依赖没有自动加入项目生产依赖。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`scripts/build_docs_site.py`](build_docs_site.py) | `fa9e53008bc7` | L1–L1041 |
| [`scripts/check_docs_contract.py`](check_docs_contract.py) | `2cf72c0d91d0` | L1–L154 |
| [`scripts/check_schema_pg.mjs`](check_schema_pg.mjs) | `0246b7b3475a` | L1–L68 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

人工 README 是解释来源；源码 AST/指纹是结构证据；HTML 是可重建产物。不要反过来编辑生成 HTML 并期望修复会保留。
不把“存在文件”“带 docstring”“输出 JSON”“全部链接通”当作语义质量证明。源码签名和说明在符号页展开查看，默认隐藏测试符号以降低噪声，勾选后仍能查全。

## 变更与验证

从仓库根运行：

```bash
python scripts/check_docs_contract.py
python scripts/build_docs_site.py
python -m pytest tests/test_docs_contract.py tests/test_docs_site.py -q
```

源码变动且已复核解释后才执行 `python scripts/check_docs_contract.py --write`。新增 Markdown 同时登记 DOC_GROUPS；变更排版要检查窄屏、长签名、表格横向滚动及键盘焦点。
