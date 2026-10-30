import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text

DATABASE_URL = "postgresql+asyncpg://dbadmin:MwUJ2C8ny%40MGCnYR@public-primary-pg-inbangalore-189799-1664910.db.onutho.com:5432/LV_NSE"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def test_suggest():
    async with AsyncSessionLocal() as db:
        # Test 1: nextval
        try:
            result = await db.execute(text("SELECT nextval('lvmf_client_code_seq')"))
            seq = result.scalar_one()
            print(f"nextval OK: {seq} -> LVMF{seq:05d}")
        except Exception as e:
            print(f"nextval ERROR: {e}")

        # Test 2: query ucc_registrations for lendrix_uuid
        try:
            from models.ucc import UCCRegistration
            result = await db.execute(
                select(UCCRegistration).where(UCCRegistration.lendrix_uuid == "021bd4fe-f07f-4b39-8e17-b70c786f4ed8")
            )
            rec = result.scalar_one_or_none()
            print(f"UCCRegistration query OK: {rec.client_code if rec else 'None'}")
        except Exception as e:
            print(f"UCCRegistration query ERROR: {e}")

asyncio.run(test_suggest())
