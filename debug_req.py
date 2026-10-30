import httpx
from datetime import datetime, timezone, timedelta
from jose import jwt

JWT_SECRET = "5527c0b82987c9bed24ac46549c36a01c59239c0dc2453716cf188e2392b6520"
expire = datetime.now(timezone.utc) + timedelta(minutes=60)
token = jwt.encode(
    {"sub": "021bd4fe-f07f-4b39-8e17-b70c786f4ed8",
     "lendrix_uuid": "021bd4fe-f07f-4b39-8e17-b70c786f4ed8",
     "client_codes": ["PK2216"], "exp": expire},
    JWT_SECRET, algorithm="HS256"
)

r = httpx.get(
    "http://127.0.0.1:8000/api/v1/ucc/suggest-client-code",
    headers={"Authorization": f"Bearer {token}"},
    timeout=20
)

with open("debug_out.txt", "w") as f:
    f.write(f"STATUS: {r.status_code}\n")
    f.write(f"BODY: {r.text}\n")

r2 = httpx.get(
    "http://127.0.0.1:8000/api/v1/ucc/fetch/PK2216",
    headers={"Authorization": f"Bearer {token}"},
    timeout=20
)
with open("debug_out.txt", "a") as f:
    f.write(f"\nFETCH STATUS: {r2.status_code}\n")
    f.write(f"FETCH BODY: {r2.text}\n")
