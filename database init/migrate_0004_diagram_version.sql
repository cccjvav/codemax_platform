-- ============================================================
-- 迁移 0004：sys_diagram 增加 version（TD-65 乐观锁）
--
-- 为什么需要：保存流程图是直接覆盖 content 的。用户开两个标签页（或手机和电脑
-- 同时开着）编辑同一张图，后保存的会**静默覆盖**先保存的，先改的那份一个字都
-- 不留 —— 而且用户完全不知道。这和「删除不可恢复」（TD-64）是同一类数据丢失。
--
-- 做法：加一个 version 列，每次保存 +1。客户端把它当 ETag 拿着，保存时用
-- If-Match 带回来；服务端用**原子 CAS**（UPDATE ... WHERE version = 期望值）
-- 判定，对不上就 412 且一个字都不写。
--
-- ⚠️ 已有行统一填 1：DEFAULT 1 NOT NULL 会把现存所有行都填成 1，
--    这正是想要的（它们都算「第 1 版」，客户端第一次 GET 就会拿到 "1"）。
--
-- ⚠️ 本脚本针对**已有数据的生产库**。全新部署直接跑 full_init.sql 即可。
--
-- 用法：
--   psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 -f "database init/migrate_0004_diagram_version.sql"
-- 幂等：重复执行安全。
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sys_diagram' AND column_name = 'version'
    ) THEN
        ALTER TABLE sys_diagram ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        RAISE NOTICE '已添加 sys_diagram.version，现存行全部填为 1';
    ELSE
        RAISE NOTICE 'sys_diagram.version 已存在，跳过';
    END IF;
END
$$;

-- 校验：不应存在 NULL 或 <1 的行
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'sys_diagram' AND column_name = 'version';

SELECT count(*) AS rows_with_bad_version FROM sys_diagram WHERE version IS NULL OR version < 1;
