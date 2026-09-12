-- Existing databases: back up first. Transactional and repeatable; no data deletion.
BEGIN;
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
COMMIT;
