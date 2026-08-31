-- ============================================================
-- codemax_db 数据库初始化（PostgreSQL）
-- 本文件由 db_init.py 自动执行（数据库本身由 db_init.py 自动创建）
-- 如需手动创建数据库：
--   psql -U postgres -c "CREATE DATABASE codemax_db;"
--   psql -U postgres -d codemax_db -f full_init.sql
-- 注意：表结构需与 app/models.py 保持一致
-- ============================================================

-- 1. 删除已存在的表（如果存在，便于重复执行）
DROP TABLE IF EXISTS sys_order CASCADE;
DROP TABLE IF EXISTS sys_config CASCADE;
DROP TABLE IF EXISTS sys_user CASCADE;

-- 2. 用户表
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

-- 3. 订单表（状态机：pending -> paid -> downloaded，阶段三实现支付流程）
CREATE TABLE sys_order (
    id           SERIAL PRIMARY KEY,
    order_no     VARCHAR(32) UNIQUE NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES sys_user(id),
    product_name VARCHAR(100) NOT NULL,
    amount       INTEGER NOT NULL,          -- 金额，单位：分
    status       VARCHAR(20) DEFAULT 'pending',
    paid_at      TIMESTAMP,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 系统配置表
CREATE TABLE sys_config (
    id           SERIAL PRIMARY KEY,
    config_key   VARCHAR(64) UNIQUE NOT NULL,
    config_value TEXT NOT NULL,
    remark       VARCHAR(255)
);

-- 5. 插入测试账号 (密码为 123456，此处填入 bcrypt(rounds=12) 加密后的哈希)
INSERT INTO sys_user (username, password, nickname)
VALUES ('admin', '$2b$12$toA/MNcYjF.wehRbK3g9IuWPOWO.7IGreBqiEMFabdxbiTecJTI3a', '管理员');
