import asyncio
from database import get_db
from sqlalchemy import select
from models.sip import SIPRegistration


async def fetch():
    async for db in get_db():
        r = await db.execute(
            select(SIPRegistration)
            .where(SIPRegistration.client_code == "PK2216")
            .order_by(SIPRegistration.created_at.desc())
        )
        records = r.scalars().all()

        if not records:
            print("No SIP registrations found for PK2216")
        else:
            for rec in records:
                print("=== SIP RECORD ===")
                for col in SIPRegistration.__table__.columns:
                    print(f"{col.name:<30}: {getattr(rec, col.name)}")
                print()
        break


asyncio.run(fetch())
