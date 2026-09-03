-- ============================================================
-- 迁移 0005：sys_user 增加 role（TD-138 管理员角色）
--
-- 为什么需要：抓取 + 解析（`app/tools/crawler.py` + `app/tools/extract.py`）
-- 此前只是服务层函数，没有 HTTP 端点，只能从代码/测试里调用。要把它开放成
-- 「后台一键抓取」，前提是能区分管理员 —— 否则等于给任何人一个
-- 「让服务器去抓任意 URL + 烧 LLM token」的入口（既是 SSRF 面，也是烧钱面）。
--
-- 做法：加一个 role 列。0=普通用户，1=管理员。
--
-- ⚠️ 已有行统一填 0（普通用户）。**本次迁移不会让任何现存用户变成管理员**，
--    这是刻意的：提权必须由运维显式执行，不能靠迁移脚本顺带发生。
--
-- ⚠️ 角色**不写进 JWT**。`get_current_user` 每个请求都从库里读用户
--    （本来就要读 status 和 password_changed_at），所以改角色立刻生效，
--    不必等 token 过期，也不必另造一个失效时间戳。
--
-- ⚠️ 本脚本针对**已有数据的生产库**。全新部署直接跑 full_init.sql 即可
--    （那边已含 role 列，两者由 tests/test_schema_sync.py 钉住不许跑偏）。
--
-- 用法：
--   psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 -f "database init/migrate_0005_user_role.sql"
-- 幂等：重复执行安全。
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sys_user' AND column_name = 'role'
    ) THEN
        ALTER TABLE sys_user ADD COLUMN role SMALLINT NOT NULL DEFAULT 0;
        RAISE NOTICE '已添加 sys_user.role，现存用户全部为 0（普通用户）';
    ELSE
        RAISE NOTICE 'sys_user.role 已存在，跳过';
    END IF;
END
$$;

-- 校验：不应存在 NULL 或既不是 0 也不是 1 的值
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'sys_user' AND column_name = 'role';

SELECT count(*) AS rows_with_bad_role FROM sys_user WHERE role IS NULL OR role NOT IN (0, 1);

-- ------------------------------------------------------------
-- 提权（**需要人工执行**，本脚本刻意不做）：
--
--   UPDATE sys_user SET role = 1 WHERE username = '你的管理员账号';
--
-- 降权同理改回 0。改完**立即生效**，该用户不必重新登录（见上面的说明）。
-- ------------------------------------------------------------
