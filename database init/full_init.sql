-- 1. 创建数据库
CREATE DATABASE codemax_db;

-- (请切换连接到 codemax_db 数据库后再执行以下建表语句)

-- 2. 删除已存在的表（如果存在）
DROP TABLE IF EXISTS sys_user CASCADE;

-- 3. 创建用户表
CREATE TABLE sys_user (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    nickname VARCHAR(50),
    avatar VARCHAR(255),
    status SMALLINT DEFAULT 1, -- 状态：1正常，0禁用
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 插入测试账号 (密码明文假设为 123456，这里填入的是 bcrypt 加密后的哈希值)
INSERT INTO sys_user (username, password, nickname) 
VALUES ('admin', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQ68Yqsh', '管理员');