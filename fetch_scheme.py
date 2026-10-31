import asyncio
from auth.nse_auth import get_auth_headers
from config import settings
from http_client import get_nse_client

NSE_MASTER_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/master_download"

async def fetch():
    async with get_nse_client(30) as client:
        resp = await client.post(NSE_MASTER_URL, headers=get_auth_headers(), json={"file_type": "SCH"})
        print("STATUS:", resp.status_code)
        text = resp.text
        for line in text.splitlines():
            if "IIIBHRP" in line.upper():
                print(line)

asyncio.run(fetch())
