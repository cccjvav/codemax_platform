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
