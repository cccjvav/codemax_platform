-- 0009 -> 0010; offline maintenance transaction only. Back up DB and storage together.
-- Historical payment/delivery fields remain NULL for explicit operator review.
ALTER TABLE sys_order ADD COLUMN payment_mode VARCHAR(16);
ALTER TABLE sys_order ADD COLUMN merchant_id VARCHAR(64);
ALTER TABLE sys_order ADD COLUMN app_id VARCHAR(64);
ALTER TABLE sys_order ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'CNY';
ALTER TABLE sys_order ADD COLUMN delivery_key VARCHAR(512);
ALTER TABLE sys_order ADD COLUMN delivery_digest VARCHAR(64);
ALTER TABLE sys_order ADD COLUMN delivery_size BIGINT;
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
