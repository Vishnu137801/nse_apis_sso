import psycopg2

conn = psycopg2.connect(
    host='public-primary-pg-inbangalore-189799-1664910.db.onutho.com',
    port=5432,
    dbname='LV_NSE',
    user='dbadmin',
    password='MwUJ2C8ny@MGCnYR',
    sslmode='disable'
)
cur = conn.cursor()

with open('migrations/007_add_verification_columns_to_mapping.sql', 'r') as f:
    sql = f.read()

cur.execute(sql)
conn.commit()
print("Migration 007 applied.")

# Verify
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='lendrix_user_nse_mapping' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("Columns:", cols)

cur.close()
conn.close()
