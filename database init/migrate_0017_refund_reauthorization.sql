-- 0016 -> 0017. Stop ALL writers, back up and use maintenance CLI. No money movement/backfill.
-- Old stops and authorizations remain immutable. Only never-started, stopped leaves may be replaced.
ALTER TABLE refund_authorization DROP CONSTRAINT refund_authorization_preparation_id_key;
ALTER TABLE refund_authorization ADD COLUMN supersedes_id INTEGER UNIQUE REFERENCES refund_authorization(id);
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
