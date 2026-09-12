# 文档化总结（2026-09-12）

本轮采用 [文档契约](docs/DOCUMENTATION_POLICY.md)，不再沿用初版“100 文件、21 文档、39 路由”的固定结论。

## 可复算结果

- 当前源码/配置/可读资源 **237 文件，13 个 README owner**；其中 99 个声明为生成/锁定内容，不伪造逐函数解释。
- Python **92 模块**；路由 **47 条**（25 条鉴权路由）；登记 Markdown **29 份**。
- 详细符号、依赖边、源码 SHA、行范围由 `docs/site/data/` 本次构建生成；符号包含测试与嵌套定义，不能和旧版仅部分 app 的计数比较。
- 构建同时检查实际输出的本地链接、标题锚点及源码范围。围栏示例不产生假标题，重复标题有唯一 ID；file:// 子页搜索加载本地 JS 索引。

## 复核

```bash
python scripts/check_docs_contract.py
python scripts/build_docs_site.py
python -m pytest tests/test_docs_contract.py tests/test_docs_site.py -q
```

只有读过源码并更新人工说明后才运行 `check_docs_contract.py --write`。正常检查和 CI 不自动刷新过期指纹。
未知扩展名的 UTF-8 文本仍计入归属，不能用新语言后缀绕开 README。密钥形态与符号链接拒绝发布；二进制资源不冒充源代码。

## 未冒充的能力

完整文件清单不等于全语言符号解析；目前只有 Python AST。结构和链接通过也不证明每句历史解释都语义正确。
手写旧记录、性能基准、覆盖率与技术取舍应按日期阅读。用户上传的新旧提案及汇总保留原文，比较意见见契约，实际修复证据见 [当前验收](docs/SECOND_REPAIR_ACCEPTANCE.md)。
