-- ============================================================
-- codemax_db 数据库初始化（PostgreSQL）
-- 本文件由 db_init.py 自动执行（数据库本身由 db_init.py 自动创建）
-- 如需手动创建数据库：
--   psql -U postgres -c "CREATE DATABASE codemax_db;"
--   psql -U postgres -d codemax_db -f full_init.sql
-- ============================================================

-- 1. 删除已存在的表（如果存在，便于重复执行）
DROP TABLE IF EXISTS sys_user CASCADE;

-- 2. 创建用户表
CREATE TABLE sys_user (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50)  UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,
    nickname    VARCHAR(50),
    avatar      VARCHAR(255),
    status      SMALLINT DEFAULT 1, -- 状态：1正常，0禁用
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 插入测试账号 (密码为 123456，此处填入 bcrypt(rounds=12) 加密后的哈希)
INSERT INTO sys_user (username, password, nickname)
VALUES ('admin', '$2b$12$toA/MNcYjF.wehRbK3g9IuWPOWO.7IGreBqiEMFabdxbiTecJTI3a', '管理员');
