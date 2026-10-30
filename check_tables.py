import asyncio
from database import engine
from sqlalchemy import text

async def test():
    async with engine.connect() as conn:
        for table in ["customer_ucc_attempts", "lendrix_user_nse_mapping", "nse_sso_sessions", "sso_session_revocations"]:
            result = await conn.execute(text(f"SELECT to_regclass('public.{table}')"))
            val = result.scalar()
            print(f"{table}: {'EXISTS' if val else 'MISSING'}")

asyncio.run(test())
