-- ============================================================
-- codemax_db 数据库初始化（PostgreSQL）
-- 只初始化已预建的空库；使用 db_init.py init，拒绝覆盖任何已有表。
-- 如需手动创建数据库：
--   psql -U postgres -c "CREATE DATABASE codemax_db;"
--   python "database init/db_init.py" init --confirm-database codemax_db
-- 注意：表结构需与 app/models.py 保持一致
-- ============================================================

-- 1. 空库保护；没有任何 DROP。正常入口是 db_init.py init，并由其持锁/事务包裹。
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')) THEN
        RAISE EXCEPTION 'Initialization requires an empty public schema; use migrations for existing data';
    END IF;
END $$;

-- 2. 用户表
CREATE TABLE sys_user (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50)  UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,
    nickname    VARCHAR(50),
    avatar      VARCHAR(255),
    status      SMALLINT DEFAULT 1, -- 状态：1正常，0禁用
    role        SMALLINT DEFAULT 0, -- 角色：0普通，1管理员（TD-138 抓取入库端点）
    credential_version INTEGER NOT NULL DEFAULT 0,
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
    payment_mode VARCHAR(16),
    merchant_id VARCHAR(64),
    app_id VARCHAR(64),
    currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
    delivery_key VARCHAR(512),
    delivery_digest VARCHAR(64),
    delivery_size BIGINT,
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
    credential_version INTEGER NOT NULL DEFAULT 0,
    used         BOOLEAN DEFAULT FALSE,
    create_time  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

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

-- 站内客户/管理员留言
CREATE TABLE IF NOT EXISTS support_message (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES sys_user(id),
    sender_id INTEGER NOT NULL REFERENCES sys_user(id),
    sender_role SMALLINT NOT NULL,
    body TEXT NOT NULL,
    client_nonce VARCHAR(36) NOT NULL,
    create_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_support_sender_nonce UNIQUE (sender_id, client_nonce)
);
CREATE INDEX IF NOT EXISTS idx_support_customer_id ON support_message(customer_id, id);

-- 迁移账本由CLI在同一事务内记录。原始SQL不伪造已经执行的迁移记录。
CREATE TABLE schema_migration (
    version VARCHAR(4) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Settlement evidence and attempt journal; no historical receipts are invented.
CREATE TABLE payment_receipt (
	id SERIAL NOT NULL,
	order_id INTEGER NOT NULL,
	source VARCHAR(16) NOT NULL,
	transaction_id VARCHAR(64) NOT NULL,
	amount INTEGER NOT NULL,
	currency VARCHAR(3) NOT NULL,
	merchant_id VARCHAR(64),
	app_id VARCHAR(64),
	actor_id INTEGER,
	actor_name VARCHAR(50),
	evidence VARCHAR(500),
	paid_at TIMESTAMPTZ,
	received_at TIMESTAMPTZ DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payment_source_transaction UNIQUE (source, transaction_id),
	UNIQUE (order_id),
	FOREIGN KEY(order_id) REFERENCES sys_order (id),
	FOREIGN KEY(actor_id) REFERENCES sys_user (id)
);

CREATE TABLE payment_event (
	id SERIAL NOT NULL,
	order_id INTEGER NOT NULL,
	attempt_id VARCHAR(32) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	actor_id INTEGER,
	actor_name VARCHAR(50),
	evidence VARCHAR(500),
	create_time TIMESTAMPTZ DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payment_attempt_kind UNIQUE (attempt_id, kind),
	FOREIGN KEY(order_id) REFERENCES sys_order (id),
	FOREIGN KEY(actor_id) REFERENCES sys_user (id)
);
CREATE INDEX idx_payment_event_order ON payment_event (order_id, id);

ALTER TABLE payment_receipt ADD CONSTRAINT ck_receipt_amount_currency CHECK (amount > 0 AND currency = 'CNY');
ALTER TABLE payment_receipt ADD CONSTRAINT ck_receipt_source CHECK (source IN ('wechat', 'manual', 'mock'));

ALTER TABLE payment_receipt ADD CONSTRAINT ck_receipt_manual_evidence
CHECK (source != 'manual' OR (actor_id IS NOT NULL AND actor_name IS NOT NULL AND evidence IS NOT NULL));

CREATE FUNCTION codemax_freeze_order_contract() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.order_no, NEW.user_id, NEW.product_name, NEW.amount, NEW.currency)
       IS DISTINCT FROM ROW(OLD.order_no, OLD.user_id, OLD.product_name, OLD.amount, OLD.currency)
       OR (OLD.payment_mode IS NOT NULL AND
           ROW(NEW.payment_mode, NEW.merchant_id, NEW.app_id, NEW.delivery_key, NEW.delivery_digest, NEW.delivery_size)
           IS DISTINCT FROM
           ROW(OLD.payment_mode, OLD.merchant_id, OLD.app_id, OLD.delivery_key, OLD.delivery_digest, OLD.delivery_size)) THEN
        RAISE EXCEPTION 'Order contract is immutable; use explicit correction workflow, not overwrite';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER freeze_order_contract BEFORE UPDATE ON sys_order
FOR EACH ROW EXECUTE FUNCTION codemax_freeze_order_contract();

CREATE FUNCTION codemax_append_only_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Payment evidence is append only';
END $$;
CREATE TRIGGER freeze_payment_receipt BEFORE UPDATE OR DELETE ON payment_receipt
FOR EACH ROW EXECUTE FUNCTION codemax_append_only_evidence();
CREATE TRIGGER freeze_payment_event BEFORE UPDATE OR DELETE ON payment_event
FOR EACH ROW EXECUTE FUNCTION codemax_append_only_evidence();
-- 0010 -> 0011. Back up and stop writers; use maintenance CLI/checksum ledger.
-- No invented historical refunds. Application rollout invalidates key-only download links;
-- unrefunded owners can reissue order-bound links. This migration is not a refund API.
CREATE TABLE refund_receipt (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES sys_order(id),
    payment_receipt_id INTEGER NOT NULL UNIQUE REFERENCES payment_receipt(id),
    source VARCHAR(16) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    refund_id VARCHAR(64) NOT NULL,
    out_refund_no VARCHAR(64) NOT NULL,
    amount INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL,
    actor_id INTEGER REFERENCES sys_user(id),
    verification_event_id INTEGER UNIQUE REFERENCES payment_event(id),
    actor_name VARCHAR(50) NOT NULL,
    evidence VARCHAR(500) NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_refund_source_id UNIQUE (source, refund_id),
    CONSTRAINT uq_refund_merchant_reference UNIQUE (source, merchant_id, out_refund_no),
    CONSTRAINT ck_refund_amount_currency CHECK (amount > 0 AND currency = 'CNY'),
    CONSTRAINT ck_refund_source CHECK (source IN ('wechat', 'manual'))
);
CREATE FUNCTION codemax_check_full_refund() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM sys_order WHERE id = NEW.order_id FOR NO KEY UPDATE;
    IF NOT EXISTS (
        SELECT 1 FROM payment_receipt r JOIN sys_order o ON o.id = r.order_id
        WHERE r.id = NEW.payment_receipt_id AND r.order_id = NEW.order_id
          AND r.source = NEW.source AND r.amount = NEW.amount AND r.currency = NEW.currency
          AND coalesce(r.merchant_id, '') = NEW.merchant_id
          AND o.payment_mode = r.source AND o.status IN ('paid', 'downloaded')
          AND o.amount = r.amount AND o.currency = r.currency AND o.transaction_id = r.transaction_id
          AND o.merchant_id IS NOT DISTINCT FROM r.merchant_id AND o.app_id IS NOT DISTINCT FROM r.app_id
          AND (r.paid_at IS NULL OR NEW.completed_at >= r.paid_at)
    ) THEN
        RAISE EXCEPTION 'Full refund must match the original payment receipt';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_full_refund BEFORE INSERT ON refund_receipt
FOR EACH ROW EXECUTE FUNCTION codemax_check_full_refund();
CREATE TRIGGER freeze_refund_receipt BEFORE UPDATE OR DELETE ON refund_receipt
FOR EACH ROW EXECUTE FUNCTION codemax_append_only_evidence();
-- 0011 -> 0012. Back up/stop writers and use the checksum-aware maintenance CLI.
-- Local FULL WeChat refund preparation only; no backfill, money movement or approval to send.
CREATE TABLE refund_request (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES sys_order(id),
    payment_receipt_id INTEGER NOT NULL UNIQUE REFERENCES payment_receipt(id),
    request_id VARCHAR(32) NOT NULL UNIQUE,
    out_refund_no VARCHAR(64) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    app_id VARCHAR(64) NOT NULL,
    amount INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES sys_user(id),
    actor_name VARCHAR(50) NOT NULL,
    evidence VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_request_merchant_reference UNIQUE (merchant_id, out_refund_no),
    CONSTRAINT ck_request_amount_currency CHECK (amount > 0 AND currency = 'CNY')
);
CREATE FUNCTION codemax_check_refund_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Same user-before-order lock order as application finance writes.
    PERFORM 1 FROM sys_user WHERE id = NEW.actor_id FOR NO KEY UPDATE;
    PERFORM 1 FROM sys_order WHERE id = NEW.order_id FOR NO KEY UPDATE;
    IF NEW.request_id !~ '^[0-9a-f]{32}$' OR NEW.out_refund_no !~ '^CMR[0-9a-f]{32}$'
       OR length(btrim(NEW.evidence)) < 3 OR NEW.evidence ~ '[[:cntrl:]]'
       OR NOT EXISTS (SELECT 1 FROM sys_user WHERE id = NEW.actor_id AND role = 1 AND status = 1 AND username = NEW.actor_name)
       OR NOT EXISTS (
        SELECT 1 FROM payment_receipt r JOIN sys_order o ON o.id = r.order_id
        WHERE r.id = NEW.payment_receipt_id AND r.order_id = NEW.order_id AND r.source = 'wechat'
          AND r.amount = NEW.amount AND r.currency = NEW.currency
          AND r.merchant_id = NEW.merchant_id AND length(NEW.merchant_id) > 0
          AND r.app_id = NEW.app_id AND length(NEW.app_id) > 0
          AND o.payment_mode = r.source AND o.status IN ('paid', 'downloaded')
          AND o.amount = r.amount AND o.currency = r.currency AND o.transaction_id = r.transaction_id
          AND o.merchant_id = r.merchant_id AND o.app_id = r.app_id
       ) OR EXISTS (SELECT 1 FROM refund_receipt WHERE order_id = NEW.order_id)
       OR EXISTS (SELECT 1 FROM payment_event WHERE order_id = NEW.order_id
                  AND kind IN ('refund_notify_signal', 'refund_query_started')) THEN
        RAISE EXCEPTION 'Refund preparation requires an unmatched original WeChat payment and active administrator';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_refund_request BEFORE INSERT ON refund_request
FOR EACH ROW EXECUTE FUNCTION codemax_check_refund_request();
CREATE TRIGGER freeze_refund_request BEFORE UPDATE OR DELETE ON refund_request
FOR EACH ROW EXECUTE FUNCTION codemax_append_only_evidence();
-- 0012 -> 0013: stop writers/back up, use maintenance CLI. No backfill or automatic sending.
CREATE TABLE refund_authorization (
    id SERIAL PRIMARY KEY,
    preparation_id INTEGER NOT NULL REFERENCES refund_request(id),
    supersedes_id INTEGER UNIQUE REFERENCES refund_authorization(id),
    request_id VARCHAR(32) NOT NULL UNIQUE,
    actor_id INTEGER NOT NULL REFERENCES sys_user(id),
    actor_name VARCHAR(50) NOT NULL,
    evidence VARCHAR(160) NOT NULL,
    body TEXT NOT NULL,
    digest VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION codemax_check_refund_authorization() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p refund_request%ROWTYPE; r payment_receipt%ROWTYPE; b jsonb;
BEGIN
    PERFORM 1 FROM sys_user WHERE id=NEW.actor_id FOR NO KEY UPDATE;
    SELECT * INTO p FROM refund_request WHERE id=NEW.preparation_id;
    PERFORM 1 FROM sys_order WHERE id=p.order_id FOR NO KEY UPDATE;
    SELECT * INTO r FROM payment_receipt WHERE id=p.payment_receipt_id;
    b := NEW.body::jsonb;
    IF p.id IS NULL OR r.id IS NULL OR NEW.request_id !~ '^[0-9a-f]{32}$'
       OR NOT EXISTS (SELECT 1 FROM sys_user WHERE id=NEW.actor_id AND role=1 AND status=1 AND username=NEW.actor_name)
       OR length(btrim(NEW.evidence)) < 3 OR NEW.evidence ~ '[[:cntrl:]]'
       OR NEW.digest <> encode(sha256(convert_to(NEW.body,'UTF8')),'hex')
       OR jsonb_typeof(b) IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(b)) <> 5
       OR b->>'transaction_id' IS DISTINCT FROM r.transaction_id
       OR b->>'out_refund_no' IS DISTINCT FROM p.out_refund_no
       OR b->'amount' IS DISTINCT FROM jsonb_build_object('refund',p.amount,'total',r.amount,'currency','CNY')
       OR jsonb_typeof(b->'reason') IS DISTINCT FROM 'string'
       OR octet_length(b->>'reason') NOT BETWEEN 1 AND 80 OR b->>'reason' ~ '[[:cntrl:]]'
       OR b->>'reason' IS DISTINCT FROM btrim(b->>'reason')
       OR jsonb_typeof(b->'notify_url') IS DISTINCT FROM 'string'
       OR octet_length(b->>'notify_url') > 256
       OR b->>'notify_url' !~ '^https://[^/@?#[:space:]]+/shop/refunds/notify$'
       OR EXISTS (SELECT 1 FROM refund_receipt WHERE order_id=p.order_id)
       OR EXISTS (SELECT 1 FROM payment_event WHERE order_id=p.order_id AND kind IN ('refund_query_started','refund_notify_signal')) THEN
        RAISE EXCEPTION 'Invalid full refund authorization contract';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_refund_authorization BEFORE INSERT ON refund_authorization
FOR EACH ROW EXECUTE FUNCTION codemax_check_refund_authorization();
CREATE TRIGGER freeze_refund_authorization BEFORE UPDATE OR DELETE ON refund_authorization
FOR EACH ROW EXECUTE FUNCTION codemax_append_only_evidence();
-- 0013 -> 0014: back up/stop writers, use maintenance CLI. No channel cancellation/backfill.
CREATE TABLE refund_send_stop (
    id SERIAL PRIMARY KEY,
    authorization_id INTEGER NOT NULL UNIQUE REFERENCES refund_authorization(id),
    request_id VARCHAR(32) NOT NULL UNIQUE,
    actor_id INTEGER NOT NULL REFERENCES sys_user(id),
    actor_name VARCHAR(50) NOT NULL,
    evidence VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION codemax_check_refund_send_stop() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE oid integer;
BEGIN
    PERFORM 1 FROM sys_user WHERE id=NEW.actor_id FOR NO KEY UPDATE;
    SELECT p.order_id INTO oid FROM refund_authorization a
      JOIN refund_request p ON p.id=a.preparation_id WHERE a.id=NEW.authorization_id;
    PERFORM 1 FROM sys_order WHERE id=oid FOR NO KEY UPDATE;
    IF oid IS NULL OR NEW.request_id !~ '^[0-9a-f]{32}$'
       OR length(btrim(NEW.evidence)) < 3 OR NEW.evidence ~ '[[:cntrl:]]'
       OR NOT EXISTS (SELECT 1 FROM sys_user WHERE id=NEW.actor_id AND role=1 AND status=1 AND username=NEW.actor_name) THEN
        RAISE EXCEPTION 'Local refund stop requires an existing authorization and active administrator';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_refund_send_stop BEFORE INSERT ON refund_send_stop
FOR EACH ROW EXECUTE FUNCTION codemax_check_refund_send_stop();
CREATE TRIGGER freeze_refund_send_stop BEFORE UPDATE OR DELETE ON refund_send_stop
FOR EACH ROW EXECUTE FUNCTION codemax_append_only_evidence();
-- 0014 -> 0015. Back up/stop writers, then maintenance CLI migrate. No money or entitlement changes.
-- No synchronous backfill: an explicitly enabled worker repairs missing jobs in bounded batches.
CREATE TABLE refund_verification_job (
    id SERIAL PRIMARY KEY,
    notice_event_id INTEGER NOT NULL UNIQUE REFERENCES payment_event(id),
    order_id INTEGER NOT NULL REFERENCES sys_order(id),
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    token VARCHAR(32),
    lease_until TIMESTAMPTZ,
    next_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome VARCHAR(32) NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_verify_state CHECK (state IN ('pending','running','retry','verified','attention')),
    CONSTRAINT ck_verify_attempts CHECK (attempts >= 0 AND attempts <= 8),
    CONSTRAINT ck_verify_lease CHECK (
        (state = 'running' AND token IS NOT NULL AND lease_until IS NOT NULL) OR
        (state <> 'running' AND token IS NULL AND lease_until IS NULL))
);
CREATE INDEX ix_verify_due ON refund_verification_job(state, next_at);
CREATE FUNCTION codemax_check_refund_verification_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Keep refund verification history';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.notice_event_id, NEW.order_id, NEW.created_at)
        IS DISTINCT FROM (OLD.notice_event_id, OLD.order_id, OLD.created_at) THEN
        RAISE EXCEPTION 'Verification origin is immutable';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM payment_event WHERE id=NEW.notice_event_id
        AND order_id=NEW.order_id AND kind='refund_notify_signal' AND actor_id IS NULL AND actor_name IS NULL)
        OR (NEW.token IS NOT NULL AND NEW.token !~ '^[0-9a-f]{32}$') THEN
        RAISE EXCEPTION 'Verification requires original system refund notice';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_refund_verification_job BEFORE INSERT OR UPDATE OR DELETE ON refund_verification_job
FOR EACH ROW EXECUTE FUNCTION codemax_check_refund_verification_job();

ALTER TABLE refund_receipt ADD CONSTRAINT ck_refund_authority CHECK (
    (actor_id IS NOT NULL AND verification_event_id IS NULL) OR
    (actor_id IS NULL AND verification_event_id IS NOT NULL AND source='wechat' AND actor_name='system:refund-verifier')
);
CREATE FUNCTION codemax_check_system_refund_actor() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.verification_event_id IS NOT NULL THEN
        PERFORM 1 FROM sys_order WHERE id=NEW.order_id FOR NO KEY UPDATE;
        PERFORM 1 FROM refund_verification_job j JOIN payment_event e ON e.attempt_id=j.token
            AND e.kind='refund_verify_started' WHERE e.id=NEW.verification_event_id FOR UPDATE OF j;
        IF NOT EXISTS (
            SELECT 1 FROM refund_verification_job j JOIN payment_event e ON e.attempt_id=j.token
            WHERE e.id=NEW.verification_event_id AND e.order_id=NEW.order_id
              AND e.kind='refund_verify_started' AND e.actor_id IS NULL AND e.actor_name IS NULL
              AND j.order_id=NEW.order_id AND j.state='running' AND j.lease_until > clock_timestamp()
        ) THEN
            RAISE EXCEPTION 'System refund requires a live matching verification claim';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_system_refund_actor BEFORE INSERT ON refund_receipt
FOR EACH ROW EXECUTE FUNCTION codemax_check_system_refund_actor();

-- 0017: immutable authorization successors, never an in-flight request correction.
CREATE UNIQUE INDEX uq_refund_authorization_root ON refund_authorization(preparation_id) WHERE supersedes_id IS NULL;
CREATE OR REPLACE FUNCTION codemax_check_refund_authorization() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p refund_request%ROWTYPE; r payment_receipt%ROWTYPE; b jsonb;
BEGIN
    PERFORM 1 FROM sys_user WHERE id=NEW.actor_id FOR NO KEY UPDATE;
    SELECT * INTO p FROM refund_request WHERE id=NEW.preparation_id;
    PERFORM 1 FROM sys_order WHERE id=p.order_id FOR NO KEY UPDATE;
    SELECT * INTO r FROM payment_receipt WHERE id=p.payment_receipt_id;
    b := NEW.body::jsonb;
    IF p.id IS NULL OR r.id IS NULL OR NEW.request_id !~ '^[0-9a-f]{32}$'
       OR NOT EXISTS (SELECT 1 FROM sys_user WHERE id=NEW.actor_id AND role=1 AND status=1 AND username=NEW.actor_name)
       OR length(btrim(NEW.evidence)) < 3 OR NEW.evidence ~ '[[:cntrl:]]'
       OR NEW.digest <> encode(sha256(convert_to(NEW.body,'UTF8')),'hex')
       OR jsonb_typeof(b) IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(b)) <> 5
       OR b->>'transaction_id' IS DISTINCT FROM r.transaction_id
       OR b->>'out_refund_no' IS DISTINCT FROM p.out_refund_no
       OR b->'amount' IS DISTINCT FROM jsonb_build_object('refund',p.amount,'total',r.amount,'currency','CNY')
       OR jsonb_typeof(b->'reason') IS DISTINCT FROM 'string'
       OR octet_length(b->>'reason') NOT BETWEEN 1 AND 80 OR b->>'reason' ~ '[[:cntrl:]]'
       OR b->>'reason' IS DISTINCT FROM btrim(b->>'reason')
       OR jsonb_typeof(b->'notify_url') IS DISTINCT FROM 'string'
       OR octet_length(b->>'notify_url') > 256
       OR b->>'notify_url' !~ '^https://[^/@?#[:space:]]+/shop/refunds/notify$'
       OR EXISTS (SELECT 1 FROM refund_receipt WHERE order_id=p.order_id)
       OR EXISTS (SELECT 1 FROM payment_event WHERE order_id=p.order_id AND kind IN ('refund_query_started','refund_notify_signal')) THEN
        RAISE EXCEPTION 'Invalid full refund authorization contract';
    END IF;
    IF EXISTS (SELECT 1 FROM payment_event WHERE order_id=p.order_id AND (kind IN ('refund_send_started','refund_send_observed')
        OR kind LIKE 'refund_query_%' OR kind LIKE 'refund_verify_%')) THEN
        RAISE EXCEPTION 'Started refund requests cannot be reauthorized';
    END IF;
    IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM refund_authorization a JOIN refund_send_stop s ON s.authorization_id=a.id
        WHERE a.id=NEW.supersedes_id AND a.preparation_id=NEW.preparation_id
          AND NOT EXISTS (SELECT 1 FROM refund_authorization child WHERE child.supersedes_id=a.id)
    ) THEN
        RAISE EXCEPTION 'Reauthorization requires the stopped leaf of the same preparation';
    END IF;
    RETURN NEW;
END $$;
