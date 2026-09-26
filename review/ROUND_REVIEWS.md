# 每轮复审记录

用户 2026-09-27 要求：每轮任务之后，必要时做一次复审；当轮不做或发现不在当轮处理的，都要记录下来，之后统一回头处理。本文件只存复审证据与结论；**搁置项的待办状态只在 [ROADMAP](../ROADMAP.md) 的 R 组维护**，这里不另立第二套剩余项清单。

每轮复审固定检查：
1. 复看本轮 diff：行为是否与 TD 描述一致，有没有漏改的调用方和文档。
2. 在真 PostgreSQL（本机 pgserver 一次性库，或同一 SHA 的 CI job）上跑受影响的测试；SQLite 通过不代表 PG 通过。
3. 等最终 SHA 的六个 CI job 全部完成。被新提交取消的运行不算，汇报里写明 SHA 与结论。
4. 发现的问题：当轮能小改就修（有测试、有 TD），否则登记到 ROADMAP R 组并写明理由。

## 2026-09-27：TD-291～TD-295（用户批准的五项跟进）

范围：导读维护脚本、客服 reason 不外泄、控制字符统一、OAuth 令牌端点错误格式、客服页「先问智能助手」。复审修复登记为 TD-296。

| 编号 | 发现 | 处理 |
|---|---|---|
| RR-01 | **CI 真 PostgreSQL job 挂死到超时**。TD-292 新用例 `test_llm_failure_detail_goes_to_logs_not_to_the_public_reason` 同时用了 `db` 与 `client` 两个夹具；第 3 组参数里 `answer()` 的查询让 `db` 会话的事务一直持有表锁，`client` 先拆除时 `drop_all` 要排他锁，永远等不到（`db` 夹具更晚拆除）。SQLite 没有表级锁，本地和 SQLite job 都通过。本机 pgserver 复现：`-v` 停在该用例第 3 组。 | 已修（TD-296）：直接调用后 `await db.rollback()` 再走 HTTP。本机 PG 全量 1955 passed。断言未改。 |
| RR-02 | **流程缺口**：TD-291～294 的中间提交 CI 都被后续推送取消（工作流开启了取消进行中的运行），TD-295 汇报时最终 SHA 的 PG job 仍在运行，所以 RR-01 没被发现；违反 ROADMAP H-03 与 AGENTS 工作循环第 8 步。 | 已改流程：AGENTS 工作循环新增第 9 步，汇报前必须等最终 SHA 全部 CI 完成，并在本机 PG 上跑受影响测试。 |
| RR-03 | TD-295：回答显示后切换成管理员登录，「把这个问题留言给管理员」仍显示，点击无反应（按钮显隐只在显示回答时计算）。 | 已修（TD-296）：记住 `lastEscalated`，`onUser` 按新角色重算显隐；Node 场景新增断言，修复前源码与产物都失败。 |
| RR-04 | TD-294：为了缺参数时返回 400 `invalid_request`，令牌端点表单参数改为空串默认值，OpenAPI（/docs）里这些参数显示为可选。 | 接受，不改：RFC 语义优先；参数检查在端点内完成并有测试。仅记录。 |
| RR-05 | TD-295：转人工的固定答案「请登录站内客服页发送留言」，用户在客服页上读到时略显多余。该文案同时通过 `/support/ask` 返回给其他调用方。 | 搁置 → ROADMAP R-03。 |
| RR-06 | 导读笔记中既有、非本轮引入的生成结果不一致：`app/routers/shop.py` 6 块（手写文件）、`tests/test_manual_pay.py::test_admin_confirm_is_idempotent`、`tests/test_second_review_regressions.py::test_pg_serializes_quota_and_message_retries`、`app/tools/llm.py::LLMClient._call`（旧模板变体）。 | 搁置 → ROADMAP R-04（笔记可读性，不影响门禁）。 |

复核过、没有发现问题的点：TD-293 共享常量覆盖全部 9 个字段（自省测试守住）、已存复核事件解码不追溯；TD-294 授权页错误格式未变、未知客户端仍跑假哈希、429 仍为 `detail`；TD-292 日志不含用户问题原文以外的新敏感字段；TD-291 脚本只改指定文件的条目。
