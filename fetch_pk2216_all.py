import asyncio
from database import get_db
from sqlalchemy import select
from models.ucc import UCCRegistration
from models.mandate_registration import MandateRegistration


async def fetch():
    async for db in get_db():
        r = await db.execute(select(UCCRegistration).where(UCCRegistration.client_code == "PK2216"))
        rec = r.scalar_one_or_none()

        m = await db.execute(
            select(MandateRegistration)
            .where(MandateRegistration.client_code == "PK2216")
            .order_by(MandateRegistration.created_at.desc())
            .limit(1)
        )
        mrec = m.scalar_one_or_none()

        if rec:
            print("=== FULL UCC DETAILS ===")
            for col in UCCRegistration.__table__.columns:
                print(f"{col.name:<45}: {getattr(rec, col.name)}")

        print()
        print("=== FULL MANDATE DETAILS ===")
        if mrec:
            for col in MandateRegistration.__table__.columns:
                print(f"{col.name:<45}: {getattr(mrec, col.name)}")
        else:
            print("No mandate found")

        break


asyncio.run(fetch())
