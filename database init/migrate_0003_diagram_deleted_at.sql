-- ============================================================
-- 迁移 0003：sys_diagram 增加 deleted_at + 改索引（TD-64）
--
-- 为什么需要：流程图原本没有配额也没有软删除 —— 任何人都能无限建图把库刷满，
-- 而删除是硬删（DELETE 一行），用户手滑删掉一张图就再也找不回来。
--
-- 做法：
--   1) 加 deleted_at（NULL = 存活）。删除改成打时间戳，可用
--      POST /diagrams/{id}/restore 恢复。
--   2) 索引从 (user_id) 改成 (user_id, deleted_at)。列表与配额统计现在都是
--      「某个用户的存活行」，把过滤列并进索引才不用回表再筛一遍。
--
-- ⚠️ 应用侧的配额上限是 settings.DIAGRAM_QUOTA（默认 50），不在数据库里；
--    本脚本只负责把列和索引准备好。
--
-- ⚠️ 本脚本针对**已有数据的生产库**。全新部署直接跑 full_init.sql 即可。
--
-- 用法：
--   psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 -f "database init/migrate_0003_diagram_deleted_at.sql"
-- 幂等：重复执行安全。
-- ============================================================

-- 1) 软删除列
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sys_diagram' AND column_name = 'deleted_at'
    ) THEN
        ALTER TABLE sys_diagram ADD COLUMN deleted_at TIMESTAMPTZ;
        RAISE NOTICE '已添加 sys_diagram.deleted_at';
    ELSE
        RAISE NOTICE 'sys_diagram.deleted_at 已存在，跳过';
    END IF;
END
$$;

-- 2) 索引补上 deleted_at
--    判断依据是索引定义里有没有这一列，而不是索引存不存在 ——
--    旧库上 idx_sys_diagram_user 是存在的（只含 user_id），只判存在就会跳过升级。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_sys_diagram_user' AND indexdef ILIKE '%deleted_at%'
    ) THEN
        DROP INDEX IF EXISTS idx_sys_diagram_user;
        CREATE INDEX idx_sys_diagram_user ON sys_diagram (user_id, deleted_at);
        RAISE NOTICE '已把 idx_sys_diagram_user 升级为 (user_id, deleted_at)';
    ELSE
        RAISE NOTICE 'idx_sys_diagram_user 已含 deleted_at，跳过';
    END IF;
END
$$;

-- 校验
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'sys_diagram' AND column_name = 'deleted_at';

SELECT indexname, indexdef FROM pg_indexes WHERE indexname = 'idx_sys_diagram_user';
