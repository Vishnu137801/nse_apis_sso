-- Adds verification_method and verification_reference to lendrix_user_nse_mapping.
BEGIN;

ALTER TABLE lendrix_user_nse_mapping
    ADD COLUMN IF NOT EXISTS verification_method    VARCHAR(40),
    ADD COLUMN IF NOT EXISTS verification_reference VARCHAR(36);

COMMIT;
