# 文档导航

> 第一次在 Windows 操作？请从 [Windows 新手逐步验收](../Windows新手逐步验收.md) 开始：VS Code 集成 CMD＋Conda＋系统 Node，顺序命令、预期结果与失败恢复在一篇中完成。

[项目总览](../总览.md) · [架构讲解](ARCHITECTURE_GUIDE.md) · [文档策略](DOCUMENTATION_POLICY.md)

## 模块职责

文档总入口：保留目录内 README，构建单一离线文档站。

## 精读入口与数据

[从零复盘指南](CODE_READING_GUIDE.md) 提供学习顺序和功能链路；构建后的 reading.html 展示全部源码的精读状态和待补项。
`code_reading_notes.json` 是权威讲解数据：version、files、path、源码 sha256、method 和 blocks 的 end/title/explanation。起点按前段末尾推导，覆盖整文件。
- `manual` 为人工分段说明（首批省略 method 时兼容此值）。
- `guided` 为人工整理功能契约＋AST 语句导读；语句导读只陈述当前赋值/分支/异常/断言等事实，不自动推断设计理由。
- 仅这个 JSON 本身列为 `note_data`：文件不能在自身存入自己的有效 SHA。格式、来源、维护规则由本节和[指南第 10 节](CODE_READING_GUIDE.md#10-讲解数据的格式来源与维护)解释；本目录自动表和外部 code-manifest 仍记录其真实指纹。其他 JSON 无此例外。
- `scripts/code_reading.py` 校验并在源码旁转义渲染；构建要求全部非空非生成源码有解释，新增漏项/过期摘要/坏范围会失败，不自动刷新 notes 摘要。

当前计数由构建输出及覆盖页生成；生成物、空包文件和讲解数据单列。它不认证任意历史 Markdown 的业务含义，也不认证讲解正确性。

## 现行指南与历史材料

当前阅读：根 README → ARCHITECTURE_GUIDE → 对应目录 README → DEPLOY。

- Windows 本地：[conda 运行/维护/测试](WINDOWS_CONDA.md)，[venv 备选与常见问题](WINDOWS_LOCAL_RUN.md)。
- 签收步骤：[浏览器、文件、客服、支付、模型与上线验收](ACCEPTANCE_GUIDE.md)，明确执行环境和通过标准；未执行步骤不是通过记录。
DOCUMENTATION_QUALITY_REVIEW 记录本轮内容审核；SECOND_REPAIR_ACCEPTANCE 是第二批实现验收快照。
TECH_DECISIONS、ROADMAP 与原始审查报告按日期理解，不能用其旧状态覆盖现行模块契约。
`documentation_policy.json` 定义有理由的生成例外，不存放函数业务解释。

## 文件与入口

下表为可复算清单；生成区以外的职责解释由维护者负责。

<!-- doc-contract:files:start -->

| 文件（源码） | SHA-256 前 12 位 | 定位范围 |
| --- | --- | --- |
| [`docs/code_reading_notes.json`](code_reading_notes.json) | `0f0bb651916b` | L1–L10849 |
| [`docs/documentation_policy.json`](documentation_policy.json) | `4201a25e6404` | L1–L7 |

完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。
其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。

<!-- doc-contract:files:end -->

## 数据流与约束

人工解释与机器结构证据分离；规则和新旧方案比较见 DOCUMENTATION_POLICY.md。

## 变更与验证

先读实际源码，再刷新指纹；构建站点会执行同一门禁，不以文件存在冒充语义正确。
源码变更必须复核本目录说明后执行 `python scripts/check_docs_contract.py --write`（仓库根目录）。
只刷新指纹不是语义审查；评审时必须核对人工说明。

管理员实际操作见[订单与收款工作台](PAYMENTS_ADMIN_GUIDE.md)，不需要打开生产API文档。


当前新增第十一批只读核验队列；操作见PAYMENTS_ADMIN_GUIDE，单独进程/0015部署见DEPLOY，边界与证据见review/RELEASE_BLOCKERS_PHASE11.md。没有自动转款/结算权限。
