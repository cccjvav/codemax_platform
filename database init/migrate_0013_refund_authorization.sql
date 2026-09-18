-- 0012 -> 0013: stop writers/back up, use maintenance CLI. No backfill or automatic sending.
CREATE TABLE refund_authorization (
    id SERIAL PRIMARY KEY,
    preparation_id INTEGER NOT NULL UNIQUE REFERENCES refund_request(id),
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
