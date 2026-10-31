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
            .limit(1)
        )
        rec = r.scalar_one_or_none()
        if rec:
            print("reg_status    :", rec.reg_status)
            print("reg_remark    :", rec.reg_remark)
            print("reg_id        :", rec.reg_id)
            print("sip_mandate_id:", rec.sip_mandate_id)
            print("amc_code      :", rec.amc_code)
            print("created_at    :", rec.created_at)
        break


asyncio.run(fetch())
