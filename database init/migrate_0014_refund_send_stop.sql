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
