-- Review and apply explicitly; application startup does not create SSO tables.
BEGIN;
-- =============================================================================
-- Migration: Add SSO tables
-- File     : migrations/001_add_sso_tables.sql
-- Type     : ADDITIVE ONLY — no existing tables are modified or dropped
-- Review   : inspect pre-existing SSO tables/indexes and target schema first
-- DO NOT   : run automatically against production without an approved backup
-- =============================================================================

-- Table: lendrix_user_nse_mapping
-- Maps a verified Lendrix UUID to one or more authorized NSE client codes.
-- Populated manually or via a verified admin process — never auto-linked.
CREATE TABLE IF NOT EXISTS lendrix_user_nse_mapping (
    id            SERIAL PRIMARY KEY,
    lendrix_uuid  VARCHAR(36)  NOT NULL,
    client_code   VARCHAR(10)  NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lendrix_uuid_client_code UNIQUE (lendrix_uuid, client_code)
);

CREATE INDEX IF NOT EXISTS ix_lendrix_uuid_nse_mapping
    ON lendrix_user_nse_mapping (lendrix_uuid);

CREATE INDEX IF NOT EXISTS ix_client_code_nse_mapping
    ON lendrix_user_nse_mapping (client_code);

-- Table: sso_session_revocations
-- Stores revoked SSO session JTIs for logout and forced invalidation.
-- Rows with expires_at < NOW() can be pruned safely.
CREATE TABLE IF NOT EXISTS sso_session_revocations (
    id            SERIAL PRIMARY KEY,
    jti           VARCHAR(36)  NOT NULL UNIQUE,
    lendrix_uuid  VARCHAR(36)  NOT NULL,
    revoked_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_sso_jti
    ON sso_session_revocations (jti);

-- Optional periodic cleanup (safe to run anytime):
-- DELETE FROM sso_session_revocations WHERE expires_at < NOW();

COMMIT;
