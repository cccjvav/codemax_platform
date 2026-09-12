# 文档导航

[项目总览](../总览.md) · [架构讲解](ARCHITECTURE_GUIDE.md) · [文档策略](DOCUMENTATION_POLICY.md)

## 模块职责

文档总入口：保留目录内 README，构建单一离线文档站。

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
