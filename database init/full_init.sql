-- ============================================================
-- codemax_db 数据库初始化（PostgreSQL）
-- 本文件由 db_init.py 自动执行（数据库本身由 db_init.py 自动创建）
-- 如需手动创建数据库：
--   psql -U postgres -c "CREATE DATABASE codemax_db;"
--   psql -U postgres -d codemax_db -f full_init.sql
-- 注意：表结构需与 app/models.py 保持一致
-- ============================================================

-- 1. 删除已存在的表（如果存在，便于重复执行）
DROP TABLE IF EXISTS oauth_code CASCADE;
DROP TABLE IF EXISTS oauth_client CASCADE;
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

-- 6. OAuth2 客户端表（SSO 接入方：工具平台 / 商业平台）
CREATE TABLE oauth_client (
    id                 SERIAL PRIMARY KEY,
    client_id          VARCHAR(64)  UNIQUE NOT NULL,
    client_secret_hash VARCHAR(255) NOT NULL,
    name               VARCHAR(100) NOT NULL,
    redirect_uri       VARCHAR(255) NOT NULL,
    status             SMALLINT DEFAULT 1
);

-- 7. 一次性授权码表（绑定用户与客户端，短时有效）
CREATE TABLE oauth_code (
    id           SERIAL PRIMARY KEY,
    code         VARCHAR(64) UNIQUE NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES sys_user(id),
    client_id    INTEGER NOT NULL REFERENCES oauth_client(id),
    redirect_uri VARCHAR(255) NOT NULL,
    expires_at   TIMESTAMP NOT NULL,
    used         BOOLEAN DEFAULT FALSE,
    create_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. 种子客户端（演示用；明文密钥仅存于本注释与 README，库内只存 bcrypt 哈希）
--    tools: client_secret = codemax-tools-secret
--    shop:  client_secret = codemax-shop-secret
INSERT INTO oauth_client (client_id, client_secret_hash, name, redirect_uri) VALUES
('tools', '$2b$12$YRBHm2yQf9N6Yfq56wg/MeCUl0iPCYEkqAxtD815g/WrKP05s6nuS', '工具平台', 'https://tools.codemax.top/callback'),
('shop',  '$2b$12$GUioADBiOgOS7Akgme1/5e/r6B5BIxZ/PmVlBSNRVi8LG4cfSlmQK', '商业平台', 'https://shop.codemax.top/callback');
