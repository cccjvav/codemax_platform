-- 0015 -> 0016. Back up/stop writers. No historical backfill or outgoing refund.
ALTER TABLE refund_receipt ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE refund_receipt ADD COLUMN verification_event_id INTEGER UNIQUE REFERENCES payment_event(id);
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
