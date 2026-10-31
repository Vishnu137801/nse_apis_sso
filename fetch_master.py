import asyncio
from auth.nse_auth import get_auth_headers
from config import settings
from http_client import get_nse_client

NSE_MASTER_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/MASTER_DOWNLOAD"


async def fetch():
    async with get_nse_client(120) as client:
        resp = await client.post(NSE_MASTER_URL, headers=get_auth_headers(), json={"file_type": "SCH"})
        print("STATUS:", resp.status_code)
        if resp.status_code != 200:
            print("ERROR:", resp.text[:500])
            return
        lines = resp.text.splitlines()
        print("TOTAL LINES:", len(lines))
        print("HEADER:", lines[0] if lines else "NO HEADER")
        print()
        # Find 360 ONE schemes
        for line in lines[1:]:
            if "360" in line.upper() or "IIIBHR" in line.upper():
                print(line)

asyncio.run(fetch())
