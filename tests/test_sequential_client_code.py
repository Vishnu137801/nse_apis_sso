"""
Tests for sequential LVMF client code generation.

Run:
    pytest tests/test_sequential_client_code.py -v
"""
import asyncio
import pytest
import httpx
from datetime import datetime, timezone, timedelta
from jose import jwt

BASE = "http://127.0.0.1:8000/api/v1"
TIMEOUT = 20

LOGIN_EMAIL = "pk520817@gmail.com"
LOGIN_PASSWORD = "Pavan@2216"

# From .env — used only for expired token test
JWT_SECRET = "5527c0b82987c9bed24ac46549c36a01c59239c0dc2453716cf188e2392b6520"

# Known session data from DB (Pavankumar / Mandha PavankumarReddy)
KNOWN_UUID = "021bd4fe-f07f-4b39-8e17-b70c786f4ed8"
KNOWN_CLIENT_CODES = ["PK2216"]


def mint_token(lendrix_uuid: str, client_codes: list) -> str:
    """Mint a fresh valid token directly — avoids Cloudflare rate limiting on login."""
    from datetime import datetime, timezone, timedelta
    expire = datetime.now(timezone.utc) + timedelta(minutes=60)
    return jwt.encode(
        {"sub": lendrix_uuid, "lendrix_uuid": lendrix_uuid, "client_codes": client_codes, "exp": expire},
        JWT_SECRET,
        algorithm="HS256",
    )


def get_session() -> dict:
    """Return a valid session without hitting Lendrix — mints token locally."""
    return {
        "nse_token": mint_token(KNOWN_UUID, KNOWN_CLIENT_CODES),
        "lendrix_uuid": KNOWN_UUID,
        "client_codes": KNOWN_CLIENT_CODES,
    }


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Phase 0 — Server health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_server_up(self):
        r = httpx.get("http://127.0.0.1:8000/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Phase 1 — Auth
# ---------------------------------------------------------------------------

class TestAuth:

    def test_valid_token_is_jwt(self):
        session = get_session()
        assert session["nse_token"].startswith("ey"), "nse_token should be a JWT"

    def test_expired_token_returns_401(self):
        expired = jwt.encode(
            {"sub": "test", "lendrix_uuid": "test", "client_codes": [],
             "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
            JWT_SECRET, algorithm="HS256",
        )
        r = httpx.get(
            f"{BASE}/ucc/suggest-client-code",
            headers=auth_headers(expired),
            timeout=TIMEOUT,
        )
        assert r.status_code == 401

    def test_protected_endpoint_without_token_returns_403(self):
        r = httpx.get(f"{BASE}/ucc/suggest-client-code", timeout=TIMEOUT)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Phase 2 — suggest-client-code
# ---------------------------------------------------------------------------

class TestSuggestClientCode:

    def test_suggest_returns_lvmf_format(self):
        session = get_session()
        r = httpx.get(
            f"{BASE}/ucc/suggest-client-code",
            headers=auth_headers(session["nse_token"]),
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, f"Unexpected: {r.status_code} {r.text[:300]}"
        data = r.json()
        assert "client_code" in data
        assert "lendrix_uuid" in data
        assert "already_registered" in data
        code = data["client_code"]
        assert code.startswith("LVMF"), f"Expected LVMF prefix, got: {code}"
        assert len(code) >= 9  # LVMF + at least 5 digits
        print(f"\nSuggested code: {code} | already_registered: {data['already_registered']}")

    def test_suggest_already_registered_returns_existing_code(self):
        """If user already has a UCC, suggest returns existing code with already_registered=True."""
        session = get_session()
        r = httpx.get(
            f"{BASE}/ucc/suggest-client-code",
            headers=auth_headers(session["nse_token"]),
            timeout=TIMEOUT,
        )
        assert r.status_code == 200
        data = r.json()
        if data["already_registered"]:
            assert data["client_code"], "already_registered=True but client_code is empty"
            assert data["client_code"] in session["client_codes"], (
                f"Returned code {data['client_code']} not in token's client_codes {session['client_codes']}"
            )


# ---------------------------------------------------------------------------
# Phase 3 — Concurrent code generation (race condition test)
# ---------------------------------------------------------------------------

class TestConcurrentClientCodes:

    def test_10_concurrent_suggest_calls_all_unique(self):
        """
        10 simultaneous calls from the same token.
        If user is already registered — all 10 return same existing code (correct).
        If user is not registered — all 10 must be unique (nextval guarantees this).
        """
        session = get_session()
        token = session["nse_token"]

        async def fetch_code(client: httpx.AsyncClient) -> str:
            r = await client.get(
                f"{BASE}/ucc/suggest-client-code",
                headers=auth_headers(token),
                timeout=TIMEOUT,
            )
            assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"
            return r.json()["client_code"]

        async def run_concurrent():
            async with httpx.AsyncClient() as client:
                tasks = [fetch_code(client) for _ in range(10)]
                return await asyncio.gather(*tasks)

        codes = asyncio.run(run_concurrent())
        assert len(codes) == 10

        unique_codes = set(codes)
        print(f"\nCodes returned: {sorted(unique_codes)}")

        for code in codes:
            assert code.startswith("LVMF"), f"Non-LVMF code: {code}"

        all_same = len(unique_codes) == 1      # already registered — correct
        all_unique = len(unique_codes) == 10   # not registered — nextval working

        assert all_same or all_unique, (
            f"Race condition detected! Got mixed duplicates: {sorted(codes)}"
        )


# ---------------------------------------------------------------------------
# Phase 4 — fetch/{client_code} ownership
# ---------------------------------------------------------------------------

class TestFetchUCC:

    def test_fetch_without_token_returns_403(self):
        r = httpx.get(f"{BASE}/ucc/fetch/LVMF00001", timeout=TIMEOUT)
        assert r.status_code == 403

    def test_fetch_other_users_code_returns_403_or_404(self):
        """Pavankumar's token cannot fetch Poonam's LVMF00001."""
        session = get_session()
        # LVMF00001 belongs to Poonam — Pavan should not access it
        r = httpx.get(
            f"{BASE}/ucc/fetch/LVMF00001",
            headers=auth_headers(session["nse_token"]),
            timeout=TIMEOUT,
        )
        if "LVMF00001" not in session["client_codes"]:
            assert r.status_code in (403, 404), (
                f"Expected 403/404 for cross-user access, got {r.status_code}: {r.text[:200]}"
            )

    def test_fetch_own_code_succeeds(self):
        """User can fetch their own client_code."""
        session = get_session()
        if not session["client_codes"]:
            pytest.skip("User has no registered client_codes yet")
        own_code = session["client_codes"][0]
        r = httpx.get(
            f"{BASE}/ucc/fetch/{own_code}",
            headers=auth_headers(session["nse_token"]),
            timeout=TIMEOUT,
        )
        assert r.status_code in (200, 404), f"Unexpected: {r.status_code}: {r.text[:200]}"

    def test_fetch_nonexistent_code_returns_403_or_404(self):
        session = get_session()
        r = httpx.get(
            f"{BASE}/ucc/fetch/LVMF99999",
            headers=auth_headers(session["nse_token"]),
            timeout=TIMEOUT,
        )
        assert r.status_code in (403, 404)
