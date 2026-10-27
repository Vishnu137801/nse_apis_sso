-- Customer SSO onboarding state and exclusive active client-code ownership.
-- Apply explicitly after reviewing the live schema and duplicate mappings.
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT client_code
        FROM lendrix_user_nse_mapping
        WHERE is_active = TRUE
        GROUP BY client_code
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Duplicate active NSE client_code mappings exist; resolve them before migration';
    END IF;
END
$$;

ALTER TABLE lendrix_user_nse_mapping
    ADD COLUMN IF NOT EXISTS verification_method VARCHAR(40);
ALTER TABLE lendrix_user_nse_mapping
    ADD COLUMN IF NOT EXISTS verification_reference VARCHAR(36);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_nse_client_code
    ON lendrix_user_nse_mapping (client_code)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS customer_ucc_attempts (
    id                  VARCHAR(36) PRIMARY KEY,
    lendrix_uuid        VARCHAR(36) NOT NULL,
    idempotency_key     VARCHAR(36) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    status              VARCHAR(40) NOT NULL,
    nse_statuses        JSON,
    client_codes        JSON,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_customer_ucc_attempt_idempotency
        UNIQUE (lendrix_uuid, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_customer_ucc_attempt_lendrix_uuid
    ON customer_ucc_attempts (lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_customer_ucc_attempt_uuid_created
    ON customer_ucc_attempts (lendrix_uuid, created_at DESC);

COMMIT;
