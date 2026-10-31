import asyncio
import asyncpg
import json

async def main():
    conn = await asyncpg.connect(
        host="public-primary-pg-inbangalore-189799-1664910.db.onutho.com",
        port=5432,
        user="dbadmin",
        password="MwUJ2C8ny@MGCnYR",
        database="LV_NSE"
    )
    rows = await conn.fetch("SELECT * FROM ucc_registrations WHERE client_code = 'PK2216' LIMIT 1")
    for r in rows:
        print(json.dumps(dict(r), default=str, indent=2))
    await conn.close()

asyncio.run(main())
