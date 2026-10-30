-- Creates a PostgreSQL sequence for LVMF client codes.
-- Starts from the current max so existing codes are never reused.

DO $$
DECLARE
    current_max INT;
BEGIN
    SELECT COALESCE(MAX(CAST(SUBSTRING(client_code FROM 5) AS INT)), 0)
    INTO current_max
    FROM ucc_registrations
    WHERE client_code LIKE 'LVMF%'
      AND client_code ~ '^LVMF[0-9]{5}$';

    EXECUTE format(
        'CREATE SEQUENCE IF NOT EXISTS lvmf_client_code_seq START %s MINVALUE 1 NO MAXVALUE NO CYCLE',
        current_max + 1
    );
END;
$$;
