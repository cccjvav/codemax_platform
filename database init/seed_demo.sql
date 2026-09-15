-- 仅显式开发命令使用；生产不加载，禁止覆盖已有用户/客户端。
DO $$ BEGIN
    IF current_setting('codemax.allow_demo', true) IS DISTINCT FROM 'yes'
       OR EXISTS (SELECT 1 FROM sys_user) OR EXISTS (SELECT 1 FROM oauth_client) THEN
        RAISE EXCEPTION 'Demo seed requires explicit opt-in and empty identity tables';
    END IF;
END $$;

-- 5. 插入测试账号 (密码为 123456，此处填入 bcrypt(rounds=12) 加密后的哈希)
--    role 必须**显式写 1**：该列默认是 0（普通用户），而 app/deps.py 的
--    require_admin 要求 role == 1。漏写这一列的后果是「昵称叫管理员的账号
--    进不了任何管理端点，一律 403」—— 而测试发现不了，因为
--    tests/test_admin_ingest.py 每个用例都显式调用 _set_role(..., 1)。
INSERT INTO sys_user (username, password, nickname, role)
VALUES ('admin', '$2b$12$toA/MNcYjF.wehRbK3g9IuWPOWO.7IGreBqiEMFabdxbiTecJTI3a', '管理员', 1);

-- 8. 种子客户端（演示用；明文密钥仅存于本注释与 README，库内只存 bcrypt 哈希）
--    tools: client_secret = codemax-tools-secret
--    shop:  client_secret = codemax-shop-secret
INSERT INTO oauth_client (client_id, client_secret_hash, name, redirect_uri) VALUES
('tools', '$2b$12$YRBHm2yQf9N6Yfq56wg/MeCUl0iPCYEkqAxtD815g/WrKP05s6nuS', '工具平台', 'https://tools.codemax.top/callback'),
('shop',  '$2b$12$GUioADBiOgOS7Akgme1/5e/r6B5BIxZ/PmVlBSNRVi8LG4cfSlmQK', '商业平台', 'https://shop.codemax.top/callback');

