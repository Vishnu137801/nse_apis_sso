-- SSO customer mapping integrity: one active NSE client code per Lendrix UUID.
-- Review duplicate active mappings and existing index definitions before applying.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_nse_client_code
    ON lendrix_user_nse_mapping (client_code)
    WHERE is_active = TRUE;

COMMIT;
