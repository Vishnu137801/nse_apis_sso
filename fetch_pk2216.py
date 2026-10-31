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
            print("EMAIL       :", rec.email)
            print("MOBILE      :", rec.indian_mobile_no)
            print("ACCOUNT_NO_1:", rec.account_no_1)
            print("IFSC_CODE_1 :", rec.ifsc_code_1)
            print("ACCOUNT_TYPE:", rec.account_type_1)
        else:
            print("PK2216 not found in ucc_registrations")

        if mrec:
            print("MANDATE_ID  :", mrec.member_mandate_no)
            print("MANDATE_STATUS:", mrec.reg_status)
        else:
            print("MANDATE_ID  : NOT FOUND")
        break


asyncio.run(fetch())
