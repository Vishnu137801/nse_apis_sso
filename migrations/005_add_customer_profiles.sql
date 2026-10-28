-- Creates customer_profiles table to store Lendrix customer details locally on login.
BEGIN;

CREATE TABLE IF NOT EXISTS customer_profiles (
    lendrix_uuid  VARCHAR(36)  PRIMARY KEY,
    email         VARCHAR(100),
    first_name    VARCHAR(100),
    last_name     VARCHAR(100),
    phone         VARCHAR(20),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMIT;
