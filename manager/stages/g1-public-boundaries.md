# G1：公开接口资源与边界（第十八批）

范围、验收标准与顺序只在 [ROADMAP](../../ROADMAP.md) G1 维护；本页只记录本阶段的证据、阻塞与下一步，不复制队列。首发定位为演示/免费工具（TD-259），G1 的意义是匿名可达入口在演示站同样暴露。

## 证据

| 项 | 状态 | 证据与边界 |
| --- | --- | --- |
| 前置文本批 | 已提交 | 提交信息 "batch 18a"；全量 pytest 1687 passed / 7 skipped；精确 SHA 的六项 CI 见交付消息 |
| A-01 请求体预算 | 已实现（本批交付；提交 SHA 与六项 CI 见交付消息，不在参与自身哈希的文档里写入） | `app/middleware.py::RequestBodyBudgetMiddleware` + `main.py` 422 处理器 + 四个配置项；`tests/test_request_body_budget.py` 17 条（受控变异：拆中间件注册 / 摘 422 处理器 / 默认档放大到 32 MiB / 去掉 `Connection: close` 分别使 7 / 3 / 4 / 3 条转红，合计 12 条不同用例；源码已恢复并核对 SHA）；本地 uvicorn：50 MB 声明 → 413/0 字节，50 MB 分块 → 413 并关连接，2 秒时限慢体 → 408；F-01 探针从 `audit_handoff_probes.py` 退役。全量套件暴露既有 `tests/test_perf.py::test_rejects_oversized_ddl_before_parsing` 的单条 422 断言与新预算冲突（160 张表 DDL 的 JSON 转义后 133 386 字节 > 128 KiB），已改成两条独立证据：超 20000 字符但仍在预算内 → 422 且不回显；转义后超预算 → 413；断言只增不减。未做：真实代理/生产网络（L-01） |
| A-05 embedding index 严格整型 | 已完成（本批交付；提交 SHA 与六项 CI 见交付消息） | `app/tools/llm.py` 排序前要求 `type(index) is int`（拒绝 bool/float/字符串）；缺失/重复/越界仍为 `LLMError`。`tests/test_faq_semantic.py` 新增保护性回归：bool/float 四例在旧实现下全部被接受（受控变异改回 `isinstance` 后 bool 两例转红），字符串/None/容器的锁定例与缺失/重复/越界例保持绿；F-05 探针退役，诊断文件现余四项。未做：真实提供方模型能力（L-05） |
| A-02 LLM 客户端边界 | 待做 | 探针 LLM 大无关字段 / 深嵌套 JSON 仍复现；慢读需本地真实 HTTP 服务 |
| A-03 同单预支付并发/限流 | 待做 | 探针同单双预支付仍复现；方案先评审（PaymentEvent 追加租约，无 schema） |
| A-04 登录来源策略 | 待做 | 需用户在 Windows 真实浏览器做 HTTPS 双来源 PoC 后定 Origin/Fetch-Metadata 组合 |

## 阻塞与说明

- 真实反向代理、Windows/浏览器、商户联调不在沙箱内；相关项只记录"未执行"，不推断。
- 探针 PASS 表示复现待修行为；每修一项就把对应探针换成默认套件里的保护性回归并在此登记。

## 下一步

按 ROADMAP 顺序做 A-05；每项：方案 → 先写红测试 → 最小实现 → 受控变异 → 文档/精读/TD → 选择性提交 → 推送 → 精确 SHA 六项 CI。
