import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_url = os.environ["DATABASE_URL"]
# Convert asyncpg URL to psycopg2 format
db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

for path in ["migrations/002_customer_sso_onboarding.sql", "migrations/004_add_nse_sso_sessions.sql"]:
    with open(path) as f:
        sql = f.read()
    try:
        cur.execute(sql)
        print(f"OK: {path}")
    except Exception as e:
        print(f"ERROR in {path}: {e}")

cur.close()
conn.close()
print("Done.")
