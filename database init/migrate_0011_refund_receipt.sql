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
    actor_id INTEGER NOT NULL REFERENCES sys_user(id),
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
