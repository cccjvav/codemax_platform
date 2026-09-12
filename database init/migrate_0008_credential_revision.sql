-- Back up first. All legacy JWTs (without ver) and outstanding authorization codes expire.
BEGIN;
ALTER TABLE sys_user ADD COLUMN IF NOT EXISTS credential_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE oauth_code ADD COLUMN IF NOT EXISTS credential_version INTEGER NOT NULL DEFAULT 0;
DELETE FROM oauth_code;
COMMIT;
