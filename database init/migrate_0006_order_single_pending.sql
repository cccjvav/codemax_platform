-- migrate_0006_order_single_pending.sql —— TD-199
--
-- 给 sys_order 加「同一用户最多一张待支付单」的部分唯一索引。
--
-- 为什么需要它：POST /shop/orders 的语义是「已有未支付订单就复用同一单」，
-- 但实现是「先查 pending，没有才新建」。并发下多个请求的 SELECT 会全部跑完
-- 才开始 INSERT，于是每个都判定「没有可复用的单」，建出多张 pending 单。
-- 实测 5 个并发请求落出 5 张（SQLite 单连接下也复现，不需要真并行）。
-- 应用层加锁救不了多实例部署（与限流 TD-141 同一个道理），必须数据库兜底。
--
-- ⚠️ 执行前请先清理存量重复：同一用户若已有 ≥2 张 pending 单，建索引会失败。
--    下面这条查询先列出冲突，人工确认后再决定关单还是保留哪一张：
--      SELECT user_id, count(*) FROM sys_order
--       WHERE status = 'pending' GROUP BY user_id HAVING count(*) > 1;
--    批量关掉多余单（保留 id 最大的那张，即最新下的单）：
--      UPDATE sys_order o SET status = 'closed', update_time = CURRENT_TIMESTAMP
--       WHERE o.status = 'pending'
--         AND o.id < (SELECT max(id) FROM sys_order
--                      WHERE user_id = o.user_id AND status = 'pending');

CREATE UNIQUE INDEX IF NOT EXISTS uq_sys_order_user_pending
    ON sys_order (user_id) WHERE status = 'pending';
