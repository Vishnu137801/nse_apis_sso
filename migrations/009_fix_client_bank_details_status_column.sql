-- Migration: Fix client_bank_details status column length
-- The status column was VARCHAR(2) but NSE returns values like 'FAIL', 'SUCCESS'
BEGIN;

ALTER TABLE client_bank_details
    ALTER COLUMN status TYPE VARCHAR(20);

COMMIT;
