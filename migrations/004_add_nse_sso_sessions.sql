-- Add persistent NSE SSO session metadata; never stores JWTs or authorization codes.
-- Review existing schema and migration history before applying.
-- Additive only. Do not apply automatically during application startup.
BEGIN;

CREATE TABLE IF NOT EXISTS nse_sso_sessions (
    id                  VARCHAR(36) PRIMARY KEY,
    lendrix_user_uuid   VARCHAR(36) NOT NULL,
    jwt_jti_hash        VARCHAR(64) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ NULL,
    last_used_at        TIMESTAMPTZ NULL,
    CONSTRAINT uq_nse_sso_sessions_jti_hash UNIQUE (jwt_jti_hash)
);

CREATE INDEX IF NOT EXISTS ix_nse_sso_sessions_customer
    ON nse_sso_sessions (lendrix_user_uuid);

CREATE INDEX IF NOT EXISTS ix_nse_sso_sessions_expires_at
    ON nse_sso_sessions (expires_at);

COMMIT;
