-- ============================================================
-- codemax_db 数据库初始化（PostgreSQL）
-- 本文件由 db_init.py 自动执行（数据库本身由 db_init.py 自动创建）
-- 如需手动创建数据库：
--   psql -U postgres -c "CREATE DATABASE codemax_db;"
--   psql -U postgres -d codemax_db -f full_init.sql
-- 注意：表结构需与 app/models.py 保持一致
-- ============================================================

-- 1. 删除已存在的表（如果存在，便于重复执行）
DROP TABLE IF EXISTS sys_article CASCADE;
DROP TABLE IF EXISTS sys_diagram CASCADE;
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
    role        SMALLINT DEFAULT 0, -- 角色：0普通，1管理员（TD-138 抓取入库端点）
    password_changed_at TIMESTAMPTZ, -- 最近改密码时刻，JWT 校验用（TD-70）
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 3. 订单表（状态机：pending -> paid -> downloaded，阶段三实现支付流程）
CREATE TABLE sys_order (
    id           SERIAL PRIMARY KEY,
    order_no     VARCHAR(32) UNIQUE NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES sys_user(id),
    product_name VARCHAR(100) NOT NULL,
    amount       INTEGER NOT NULL,          -- 金额，单位：分
    status       VARCHAR(20) DEFAULT 'pending',
    code_url     VARCHAR(512),              -- NATIVE 下单返回的二维码链接
    transaction_id VARCHAR(64),             -- 微信支付订单号（回调解出）
    paid_at      TIMESTAMPTZ,
    create_time  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    update_time  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
-- **同一用户同一时刻最多一张待支付单**（TD-199）。
-- 必须由数据库兜底：「先查 pending 再新建」在并发下必然漏（asyncio 交错时
-- 5 个 SELECT 会全部跑完才开始 INSERT），而应用层锁在多实例部署下各算各的。
-- 用**部分**唯一索引：paid / closed / downloaded 的历史单可以有多张，
-- 只有 pending 需要唯一。存量库升级见 migrate_0006_order_single_pending.sql。
CREATE UNIQUE INDEX uq_sys_order_user_pending ON sys_order (user_id) WHERE status = 'pending';

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
    expires_at   TIMESTAMPTZ NOT NULL,
    used         BOOLEAN DEFAULT FALSE,
    create_time  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 8. 种子客户端（演示用；明文密钥仅存于本注释与 README，库内只存 bcrypt 哈希）
--    tools: client_secret = codemax-tools-secret
--    shop:  client_secret = codemax-shop-secret
INSERT INTO oauth_client (client_id, client_secret_hash, name, redirect_uri) VALUES
('tools', '$2b$12$YRBHm2yQf9N6Yfq56wg/MeCUl0iPCYEkqAxtD815g/WrKP05s6nuS', '工具平台', 'https://tools.codemax.top/callback'),
('shop',  '$2b$12$GUioADBiOgOS7Akgme1/5e/r6B5BIxZ/PmVlBSNRVi8LG4cfSlmQK', '商业平台', 'https://shop.codemax.top/callback');

-- 9. Drawio 流程图表（S2-01-3，用户私有资产；需与 app/models.py 的 SysDiagram 保持一致）
CREATE TABLE sys_diagram (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES sys_user(id),
    name        VARCHAR(100) NOT NULL,
    content     TEXT NOT NULL,
    deleted_at  TIMESTAMPTZ,   -- 软删除（TD-64）：NULL = 存活
    version     INTEGER NOT NULL DEFAULT 1,  -- 乐观锁（TD-65）：每次保存 +1
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
-- 列表与配额统计都是「某个用户的存活行」，所以把 deleted_at 并进索引
CREATE INDEX idx_sys_diagram_user ON sys_diagram (user_id, deleted_at);

-- 7. 文章表（S4-01-4 内容冷启动：爬虫 + LLM 解析后入库）
CREATE TABLE sys_article (
    id           SERIAL PRIMARY KEY,
    url          VARCHAR(500) UNIQUE NOT NULL,   -- 同一篇不重复入库
    title        VARCHAR(300) NOT NULL,
    author       VARCHAR(100),
    published_at VARCHAR(50),                    -- 源站原文，格式各异，不强行解析（TD-137）
    content      TEXT NOT NULL,
    source_site  VARCHAR(200),                   -- 域名，便于按站分组
    create_time  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_article_site ON sys_article(source_site);
