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
