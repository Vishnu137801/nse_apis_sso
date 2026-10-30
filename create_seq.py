import psycopg2
import sys

try:
    conn = psycopg2.connect(
        host="public-primary-pg-inbangalore-189799-1664910.db.onutho.com",
        dbname="LV_NSE",
        user="dbadmin",
        password="MwUJ2C8ny@MGCnYR",
        port=5432,
    )
    print("Connected OK")
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(MAX(CAST(SUBSTRING(client_code FROM 5) AS INT)), 0)
        FROM ucc_registrations
        WHERE client_code LIKE 'LVMF%'
          AND client_code ~ '^LVMF[0-9]+$'
    """)
    max_val = cur.fetchone()[0]
    print(f"Current max LVMF sequence: {max_val}")

    start = max_val + 1
    cur.execute(f"CREATE SEQUENCE IF NOT EXISTS lvmf_client_code_seq START {start} MINVALUE 1 NO MAXVALUE NO CYCLE")
    conn.commit()
    print(f"Sequence created starting at {start}")

    cur.execute("SELECT last_value FROM lvmf_client_code_seq")
    print(f"Sequence last_value: {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    print("Done.")

except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
