-- 由db_init.py migrate在同一事务/迁移锁下执行。保留账号及其业务数据，只停用已知公开凭据。
-- 仅匹配原始种子哈希；曾用同一弱密码重新哈希的身份由生产启动检查额外拦截。
UPDATE sys_user SET status=0, credential_version=credential_version+1
WHERE password='$2b$12$toA/MNcYjF.wehRbK3g9IuWPOWO.7IGreBqiEMFabdxbiTecJTI3a';
UPDATE oauth_client SET status=0
WHERE client_id IN ('tools','shop') AND client_secret_hash IN (
    '$2b$12$YRBHm2yQf9N6Yfq56wg/MeCUl0iPCYEkqAxtD815g/WrKP05s6nuS',
    '$2b$12$GUioADBiOgOS7Akgme1/5e/r6B5BIxZ/PmVlBSNRVi8LG4cfSlmQK'
);
DELETE FROM oauth_code WHERE user_id IN (SELECT id FROM sys_user WHERE status=0)
    OR client_id IN (SELECT id FROM oauth_client WHERE status=0);
