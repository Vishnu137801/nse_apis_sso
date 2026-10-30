"""
Quick verification: confirms that PK2216 is auto-fetched ONLY because
its UCC reg_status = REG_SUCCESS in the DB.
"""
import asyncio
from sqlalchemy import select, text
from database import AsyncSessionLocal
from models.sso import LendrixUserNseMapping
from models.ucc import UCCRegistration

LENDRIX_UUID = "021bd4fe-f07f-4b39-8e17-b70c786f4ed8"
CLIENT_CODE  = "PK2216"


async def main():
    async with AsyncSessionLocal() as db:

        # 1. Raw mapping row
        r1 = await db.execute(
            text("SELECT client_code, is_active FROM lendrix_user_nse_mapping WHERE lendrix_uuid = :u"),
            {"u": LENDRIX_UUID},
        )
        rows = r1.fetchall()
        print(f"\n[1] lendrix_user_nse_mapping rows for this user:")
        for row in rows:
            print(f"    client_code={row[0]}  is_active={row[1]}")
        if not rows:
            print("    *** NO MAPPING FOUND — PK2216 is not linked to this lendrix_uuid ***")

        # 2. UCC reg_status
        r2 = await db.execute(
            text("SELECT client_code, reg_status FROM ucc_registrations WHERE client_code = :cc"),
            {"cc": CLIENT_CODE},
        )
        ucc_rows = r2.fetchall()
        print(f"\n[2] ucc_registrations for {CLIENT_CODE}:")
        for row in ucc_rows:
            print(f"    client_code={row[0]}  reg_status={row[1]}")
        if not ucc_rows:
            print(f"    *** NO UCC RECORD FOUND for {CLIENT_CODE} ***")

        # 3. Simulate _fetch_verified_client_codes (the new join query)
        r3 = await db.execute(
            select(LendrixUserNseMapping.client_code)
            .join(UCCRegistration, UCCRegistration.client_code == LendrixUserNseMapping.client_code)
            .where(
                LendrixUserNseMapping.lendrix_uuid == LENDRIX_UUID,
                LendrixUserNseMapping.is_active.is_(True),
                UCCRegistration.reg_status.in_(["REG_SUCCESS", "S"]),
            )
        )
        verified = [row[0] for row in r3.fetchall()]
        print(f"\n[3] Auto-fetched verified client_codes (after UCC check): {verified}")

        if verified:
            print("    ✓ PASS — client_code is returned because UCC is REG_SUCCESS")
        else:
            print("    ✗ FAIL — client_code NOT returned (mapping missing or UCC not REG_SUCCESS)")


asyncio.run(main())
