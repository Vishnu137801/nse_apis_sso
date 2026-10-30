-- Adds lendrix_uuid column to all NSE transaction/registration tables.
BEGIN;

ALTER TABLE ucc_registrations          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE order_transactions         ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE sip_registrations          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE xsip_registrations         ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE switch_transactions        ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE swp_registrations          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE stp_registrations          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE fatca_registrations        ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE fatca_common_registrations ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE mandate_registrations      ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE client_bank_details        ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE order_cancellations        ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE sip_cancellations          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE sip_topup_registrations    ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE sip_amc_pauses             ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE stp_cancellations          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE stp_modifications          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE swp_modifications          ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE xsip_cancellations         ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE daily_xsip_cancellations   ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);
ALTER TABLE utr_updates                ADD COLUMN IF NOT EXISTS lendrix_uuid VARCHAR(36);

-- Indexes for efficient lookup by lendrix_uuid
CREATE INDEX IF NOT EXISTS ix_ucc_registrations_lendrix_uuid          ON ucc_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_order_transactions_lendrix_uuid         ON order_transactions(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_sip_registrations_lendrix_uuid          ON sip_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_xsip_registrations_lendrix_uuid         ON xsip_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_switch_transactions_lendrix_uuid        ON switch_transactions(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_swp_registrations_lendrix_uuid          ON swp_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_stp_registrations_lendrix_uuid          ON stp_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_fatca_registrations_lendrix_uuid        ON fatca_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_fatca_common_registrations_lendrix_uuid ON fatca_common_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_mandate_registrations_lendrix_uuid      ON mandate_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_client_bank_details_lendrix_uuid        ON client_bank_details(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_order_cancellations_lendrix_uuid        ON order_cancellations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_sip_cancellations_lendrix_uuid          ON sip_cancellations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_sip_topup_registrations_lendrix_uuid    ON sip_topup_registrations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_sip_amc_pauses_lendrix_uuid             ON sip_amc_pauses(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_stp_cancellations_lendrix_uuid          ON stp_cancellations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_stp_modifications_lendrix_uuid          ON stp_modifications(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_swp_modifications_lendrix_uuid          ON swp_modifications(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_xsip_cancellations_lendrix_uuid         ON xsip_cancellations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_daily_xsip_cancellations_lendrix_uuid   ON daily_xsip_cancellations(lendrix_uuid);
CREATE INDEX IF NOT EXISTS ix_utr_updates_lendrix_uuid                ON utr_updates(lendrix_uuid);

-- Backfill ucc_registrations from the lendrix_uuid column already present in raw data
UPDATE ucc_registrations u
SET lendrix_uuid = m.lendrix_uuid::uuid
FROM lendrix_user_nse_mapping m
WHERE m.client_code = u.client_code
  AND m.is_active = TRUE
  AND u.lendrix_uuid IS NULL;

COMMIT;
