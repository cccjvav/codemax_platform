-- ============================================================
-- 迁移 0001：时间列 TIMESTAMP -> TIMESTAMPTZ（TD-146）
--
-- 为什么需要：改类型之前，本仓库有**三种**互不一致的时钟基准写在同一批
-- 无时区 TIMESTAMP 列里：
--   1) create_time / update_time —— server_default=CURRENT_TIMESTAMP，
--      即**数据库服务器本地时间**
--   2) paid_at                   —— 应用侧 datetime.now()，即**应用服务器本地时间**
--   3) oauth_code.expires_at     —— 应用侧 UTC 但抹掉了 tzinfo，即**裸 UTC**
-- 裸值之间无法正确比较：数据库不在 UTC 时区时，expires_at 会比 create_time
-- 早若干小时，paid_at 也可能早于同表 create_time。
--
-- 改完之后统一为：列是 TIMESTAMPTZ（存绝对时刻），应用侧一律写带时区的 UTC。
--
-- ⚠️ 本脚本针对**已有数据的生产库**。全新部署直接跑 full_init.sql 即可，
--    不需要本脚本（db_init.py 执行的 full_init.sql 已是 TIMESTAMPTZ）。
--
-- 用法：
--   psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 -f "database init/migrate_0001_timestamptz.sql"
-- 幂等：重复执行安全（已经是 timestamptz 的列会走 DO 块里的判断直接跳过）。
-- ============================================================

BEGIN;

-- 逐列转换。**关键在 USING 子句**：它告诉 PostgreSQL「这个裸值原本该按哪个
-- 时区解读」，解读错了就会把历史数据整体平移若干小时。
DO $$
DECLARE
    r record;
BEGIN
    -- 1) create_time / update_time：原本是数据库服务器本地时间
    FOR r IN
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.column_name IN ('create_time', 'update_time')
          AND c.data_type = 'timestamp without time zone'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ '
            'USING %I AT TIME ZONE current_setting(''TimeZone'')',
            r.table_name, r.column_name, r.column_name
        );
        RAISE NOTICE '已转换 %.%（按数据库时区解读）', r.table_name, r.column_name;
    END LOOP;

    -- 2) oauth_code.expires_at：原本就是 UTC，只是被抹掉了 tzinfo
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'oauth_code'
          AND column_name = 'expires_at'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE oauth_code
            ALTER COLUMN expires_at TYPE TIMESTAMPTZ USING expires_at AT TIME ZONE 'UTC';
        RAISE NOTICE '已转换 oauth_code.expires_at（按 UTC 解读）';
    END IF;

    -- 3) sys_order.paid_at：原本是**应用服务器**本地时间。
    --    单机部署（应用与数据库同区）时按数据库时区解读即为正确；
    --    若两者不同区，这批历史值在改类型之前就已经是错的，
    --    **无法从数据本身恢复**——只能按当时应用所在时区手工修正。
    --    这里按数据库时区解读，并在下面输出一行提示。
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'sys_order'
          AND column_name = 'paid_at'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE sys_order
            ALTER COLUMN paid_at TYPE TIMESTAMPTZ
            USING paid_at AT TIME ZONE current_setting('TimeZone');
        RAISE NOTICE '已转换 sys_order.paid_at（按数据库时区解读）';
        RAISE WARNING '若历史上应用与数据库不在同一时区，paid_at 的既有值在改类型前就已偏差，需手工核对';
    END IF;
END
$$;

COMMIT;

-- 验证：应全部为 timestamp with time zone
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name IN ('create_time', 'update_time', 'paid_at', 'expires_at')
ORDER BY table_name, column_name;
