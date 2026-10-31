import asyncio
from database import get_db
from sqlalchemy import select
from models.ucc import UCCRegistration
from models.mandate_registration import MandateRegistration


async def fetch():
    async for db in get_db():
        # UCC details
        r = await db.execute(select(UCCRegistration).where(UCCRegistration.client_code == "PK2216"))
        rec = r.scalar_one_or_none()

        # Mandate details
        m = await db.execute(
            select(MandateRegistration)
            .where(MandateRegistration.client_code == "PK2216")
            .order_by(MandateRegistration.created_at.desc())
            .limit(1)
        )
        mrec = m.scalar_one_or_none()

        if rec:
            print("=== UCC DETAILS ===")
            print("client_code         :", rec.client_code)
            print("first_name          :", rec.primary_holder_first_name)
            print("middle_name         :", rec.primary_holder_middle_name)
            print("last_name           :", rec.primary_holder_last_name)
            print("email               :", rec.email)
            print("mobile              :", rec.indian_mobile_no)
            print("pan                 :", rec.primary_holder_pan)
            print("dob                 :", rec.primary_holder_dob_incorporation)
            print("tax_status          :", rec.tax_status)
            print("holding_nature      :", rec.holding_nature)
            print("kyc_type            :", rec.primary_holder_kyc_type)
            print("account_no_1        :", rec.account_no_1)
            print("account_type_1      :", rec.account_type_1)
            print("ifsc_code_1         :", rec.ifsc_code_1)
            print("micr_no_1           :", rec.micr_no_1)
            print("default_bank_flag_1 :", rec.default_bank_flag_1)
            print("city                :", rec.city)
            print("state               :", rec.state)
            print("pincode             :", rec.pincode)
            print("country             :", rec.country)
            print("reg_status          :", rec.reg_status)
            print("nomination_opt      :", rec.nomination_opt)
            print("nominee_1_name      :", rec.nominee_1_name)
            print("paperless_flag      :", rec.paperless_flag)
            print("communication_mode  :", rec.communication_mode)
        else:
            print("PK2216 NOT FOUND in ucc_registrations")

        print()
        print("=== MANDATE DETAILS ===")
        if mrec:
            print("member_mandate_no   :", mrec.member_mandate_no)
            print("mandate_type        :", mrec.mandate_type)
            print("account_no          :", mrec.account_no)
            print("ac_type             :", mrec.ac_type)
            print("ifsc_code           :", mrec.ifsc_code)
            print("amount              :", mrec.amount)
            print("start_date          :", mrec.start_date)
            print("end_date            :", mrec.end_date)
            print("reg_status          :", mrec.reg_status)
        else:
            print("No mandate found for PK2216")

        break


asyncio.run(fetch())
