# 文档化总结（2026-09-12）

本轮采用 [文档契约](docs/DOCUMENTATION_POLICY.md)，不再沿用初版“100 文件、21 文档、39 路由”的固定结论。

## 可复算结果

- 当前源码/配置/可读资源及 README owner 以构建输出为准；生成/锁定内容单独标明，不伪造逐函数解释。
- 模块、路由、鉴权与文档数量来自当前生成数据，不用历史固定数字替代。
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

## 内容审查而非仅结构检查

本轮重新核对并重写现行模块与架构说明，补全关键函数契约，修正过时业务规则/部署叙述和生成展示；详见 [质量复核](docs/DOCUMENTATION_QUALITY_REVIEW.md)。未取得真实浏览器截图验收，不把 DOM/CSS 检查冒充视觉验证。


## 面向新手的分段精读

从 [复盘指南](docs/CODE_READING_GUIDE.md) 开始，文档站“精读覆盖与缺口”列全部源码状态。部分核心链路的人工解释与源码并排显示，绑定当前 SHA 和连续段界；未补项仍明确待补。本批没有把全量源码定位等同于全量逐行讲解，更不对用户上传的历史原稿做统一语义认证。
