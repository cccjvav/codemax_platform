-- ============================================================
-- 迁移 0002：sys_user 增加 password_changed_at（TD-70）
--
-- 为什么需要：改密码之后，此前签发的 JWT 仍然有效，直到自然过期。
-- 若用户是因为密码泄露才改的密码，这段窗口正好是攻击者还能用的时间。
--
-- 做法是**令牌版本化**而不是 jti 黑名单：把「改密码的时刻」写进 JWT 声明，
-- 校验时与库里当前值比对，对不上就拒。好处是不用建黑名单表、不用每次登录写库，
-- 而且一次就能吊销该用户**所有**旧 token（黑名单得先把它们枚举出来）。
--
-- 语义约定：NULL 表示「从未改过密码」。此时任何不带 pwd 声明的旧 token 仍然有效 ——
-- 这是刻意的，否则本次上线会让全站已登录用户瞬间掉线。
--
-- ⚠️ 本脚本针对**已有数据的生产库**。全新部署直接跑 full_init.sql 即可。
--
-- 用法：
--   psql -U postgres -d codemax_db -v ON_ERROR_STOP=1 -f "database init/migrate_0002_password_changed_at.sql"
-- 幂等：重复执行安全。
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sys_user' AND column_name = 'password_changed_at'
    ) THEN
        ALTER TABLE sys_user ADD COLUMN password_changed_at TIMESTAMPTZ;
        RAISE NOTICE '已添加 sys_user.password_changed_at';
    ELSE
        RAISE NOTICE 'sys_user.password_changed_at 已存在，跳过';
    END IF;
END
$$;

-- 校验
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'sys_user' AND column_name = 'password_changed_at';
