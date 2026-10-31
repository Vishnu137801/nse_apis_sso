-- Migration: Fix client_bank_details column sizes
-- action_type VARCHAR(2->10), account_type VARCHAR(2->5), default_bank_flag VARCHAR(1->5), status VARCHAR(2->20)
BEGIN;

ALTER TABLE client_bank_details ALTER COLUMN action_type TYPE VARCHAR(10);
ALTER TABLE client_bank_details ALTER COLUMN account_type TYPE VARCHAR(5);
ALTER TABLE client_bank_details ALTER COLUMN default_bank_flag TYPE VARCHAR(5);
ALTER TABLE client_bank_details ALTER COLUMN status TYPE VARCHAR(20);

COMMIT;
