import psycopg2
import traceback

try:
    print("Connecting...")
    conn = psycopg2.connect(
        host="public-primary-pg-inbangalore-189799-1664910.db.onutho.com",
        port=5432,
        user="dbadmin",
        password="MwUJ2C8ny@MGCnYR",
        dbname="LV_NSE",
        connect_timeout=15,
    )
    print("Connected!")
    cur = conn.cursor()

    # Fix LVMF00002 - Deepak Dash
    cur.execute("UPDATE ucc_registrations SET lendrix_uuid = %s WHERE client_code = %s",
        ("c702230d-07f7-4b89-bb06-49987b369b86", "LVMF00002"))
    print(f"LVMF00002 ucc update: {cur.rowcount} row(s)")

    cur.execute("""INSERT INTO lendrix_user_nse_mapping
        (lendrix_uuid, client_code, is_active, verification_method, verification_reference)
        VALUES (%s, %s, true, %s, %s)
        ON CONFLICT (lendrix_uuid, client_code) DO NOTHING""",
        ("c702230d-07f7-4b89-bb06-49987b369b86", "LVMF00002", "manual_backfill", "email_match"))
    print(f"LVMF00002 mapping insert: {cur.rowcount} row(s)")

    # Fix PK2216 - Vishnu Vardhan Reddy
    cur.execute("UPDATE ucc_registrations SET lendrix_uuid = %s WHERE client_code = %s",
        ("62c35b7a-35d5-4e1a-9f1a-339ff0545ac3", "PK2216"))
    print(f"PK2216 ucc update: {cur.rowcount} row(s)")

    cur.execute("""INSERT INTO lendrix_user_nse_mapping
        (lendrix_uuid, client_code, is_active, verification_method, verification_reference)
        VALUES (%s, %s, true, %s, %s)
        ON CONFLICT (lendrix_uuid, client_code) DO NOTHING""",
        ("62c35b7a-35d5-4e1a-9f1a-339ff0545ac3", "PK2216", "manual_backfill", "email_match"))
    print(f"PK2216 mapping insert: {cur.rowcount} row(s)")

    # Ensure LVMF00001 - Poonam Dash
    cur.execute("""INSERT INTO lendrix_user_nse_mapping
        (lendrix_uuid, client_code, is_active, verification_method, verification_reference)
        VALUES (%s, %s, true, %s, %s)
        ON CONFLICT (lendrix_uuid, client_code) DO NOTHING""",
        ("5aa07c9b-33b0-4e5d-8e20-997e062e8052", "LVMF00001", "manual_backfill", "email_match"))
    print(f"LVMF00001 mapping insert: {cur.rowcount} row(s)")

    conn.commit()
    print("Committed!")

    # Verify
    cur.execute("SELECT client_code, lendrix_uuid FROM ucc_registrations ORDER BY client_code")
    print("\nucc_registrations:")
    for r in cur.fetchall():
        print(f"  client_code={r[0]}  lendrix_uuid={r[1]}")

    cur.execute("SELECT lendrix_uuid, client_code, is_active FROM lendrix_user_nse_mapping ORDER BY client_code")
    print("\nlendrix_user_nse_mapping:")
    for r in cur.fetchall():
        print(f"  {r}")

    conn.close()
    print("\nAll done successfully.")

except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    print(traceback.format_exc())
