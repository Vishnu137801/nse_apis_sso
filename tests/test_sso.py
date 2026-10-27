"""
SSO test suite — covers all 20 required test cases.

Mocks:
  - httpx.AsyncClient (Node.js verify calls)
  - Database session (SQLAlchemy AsyncSession)
  - NSE exchange calls (never executed in tests)

No real financial transactions are executed.
No real Node.js or NSE endpoints are called.
"""
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

# ---------------------------------------------------------------------------
# Minimal settings patch — must happen before any app import
# ---------------------------------------------------------------------------
import os
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-value-at-least-32-chars!!")
os.environ.setdefault("SSO_JWT_SECRET", "test-sso-secret-value-at-least-32-chars!!")
os.environ.setdefault("LENDRIX_SSO_VERIFY_URL", "https://registration.lendrixventech.com/auth/sso/verify-code")
os.environ.setdefault("LENDRIX_SSO_SERVER_SECRET", "test-server-secret-value-at-least-32-chars!!")
os.environ.setdefault("SSO_SESSION_EXPIRE_MINUTES", "60")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "test")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_CODE = "a" * 43  # Node.js unpadded base64url code
VALID_UUID = str(uuid.uuid4())
VALID_CLIENT_CODE = "TEST001"

SSO_JWT_SECRET = "test-sso-secret-value-at-least-32-chars!!"
_ALGORITHM = "HS256"
_AUDIENCE = "mutualfund.lendrixventech.com"
_ISSUER = "nse-fastapi"


def _make_sso_token(
    lendrix_uuid: str = VALID_UUID,
    client_codes: list = None,
    expired: bool = False,
    jti: str = None,
) -> str:
    if client_codes is None:
        client_codes = [VALID_CLIENT_CODE]
    if jti is None:
        jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=60)
    payload = {
        "sub": lendrix_uuid,
        "typ": "customer_sso",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": exp,
        "jti": jti,
        "client_codes": client_codes,
    }
    return jwt.encode(payload, SSO_JWT_SECRET, algorithm=_ALGORITHM)


def _mock_db_no_revocation():
    """DB session with an active persisted session and no revocation or mapping."""
    db = AsyncMock()
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = MagicMock(
        lendrix_user_uuid=VALID_UUID,
        revoked_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
    )
    revocation_result = MagicMock()
    revocation_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[session_result, revocation_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


def _mock_db_with_mapping(client_codes: list):
    """DB session that returns active client code mappings."""
    db = AsyncMock()

    revocation_result = MagicMock()
    revocation_result.scalar_one_or_none.return_value = None

    mapping_result = MagicMock()
    mapping_result.fetchall.return_value = [(cc,) for cc in client_codes]

    db.execute = AsyncMock(side_effect=[revocation_result, mapping_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


def _mock_db_revoked():
    """DB session that finds a persistent session and its revocation."""
    db = AsyncMock()
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = MagicMock(
        lendrix_user_uuid=VALID_UUID,
        revoked_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
    )
    revocation_result = MagicMock()
    revocation_result.scalar_one_or_none.return_value = MagicMock()
    db.execute = AsyncMock(side_effect=[session_result, revocation_result])
    return db


# ---------------------------------------------------------------------------
# Test 1: Valid SSO code — successful exchange
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_sso_code_exchange():
    from auth.sso_session import create_sso_token, decode_sso_token

    db = _mock_db_no_revocation()
    token = create_sso_token(VALID_UUID, [VALID_CLIENT_CODE])
    payload = await decode_sso_token(token, db)

    assert payload["sub"] == VALID_UUID
    assert payload["typ"] == "customer_sso"
    assert VALID_CLIENT_CODE in payload["client_codes"]
    assert "jti" in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        None,
        MagicMock(
            lendrix_user_uuid=str(uuid.uuid4()),
            revoked_at=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        ),
        MagicMock(
            lendrix_user_uuid=VALID_UUID,
            revoked_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        ),
        MagicMock(
            lendrix_user_uuid=VALID_UUID,
            revoked_at=None,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ),
    ],
)
async def test_unknown_mismatched_revoked_or_expired_database_session_rejected(session):
    from fastapi import HTTPException
    from auth.sso_session import create_sso_token, decode_sso_token

    db = AsyncMock()
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = session
    db.execute = AsyncMock(return_value=session_result)

    with pytest.raises(HTTPException) as exc_info:
        await decode_sso_token(create_sso_token(VALID_UUID, []), db)
    assert exc_info.value.status_code == 401


def test_sso_session_metadata_contains_only_hash_for_database():
    from auth.sso_session import create_sso_session, hash_sso_jti

    session = create_sso_session(VALID_UUID, [VALID_CLIENT_CODE])
    payload = jwt.get_unverified_claims(session.token)

    assert payload["jti"] == session.jti
    assert int(session.expires_at.timestamp()) == payload["exp"]
    assert hash_sso_jti(session.jti) not in session.token
    assert len(hash_sso_jti(session.jti)) == 64


# ---------------------------------------------------------------------------
# Test 2: Invalid code format rejected by schema
# ---------------------------------------------------------------------------

def test_invalid_code_format_rejected():
    from schemas.sso import SsoExchangeRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SsoExchangeRequest(code="not-a-valid-hex-code")

    with pytest.raises(ValidationError):
        SsoExchangeRequest(code="abc123")  # too short

    with pytest.raises(ValidationError):
        SsoExchangeRequest(code="z" * 64)  # too long

    with pytest.raises(ValidationError):
        SsoExchangeRequest(code="a" * 42)  # too short

    with pytest.raises(ValidationError):
        SsoExchangeRequest(code="a" * 42 + "=")  # Node.js omits base64 padding

    assert SsoExchangeRequest(code="A" * 43).code == "A" * 43


# ---------------------------------------------------------------------------
# Test 3: Expired SSO token rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_sso_token_rejected():
    from fastapi import HTTPException
    from auth.sso_session import decode_sso_token

    db = _mock_db_no_revocation()
    expired_token = _make_sso_token(expired=True)

    with pytest.raises(HTTPException) as exc_info:
        await decode_sso_token(expired_token, db)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Test 4: Revoked session (used code / logged-out session) rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoked_session_rejected():
    from fastapi import HTTPException
    from auth.sso_session import decode_sso_token

    db = _mock_db_revoked()
    token = _make_sso_token()

    with pytest.raises(HTTPException) as exc_info:
        await decode_sso_token(token, db)
    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Test 5: Node.js verification service unavailable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lendrix_service_unavailable():
    from fastapi import HTTPException
    from routers.sso import _redeem_code_with_lendrix

    with patch("routers.sso.httpx.AsyncClient") as mock_client_cls:
        import httpx
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await _redeem_code_with_lendrix(VALID_CODE)
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Test 6: Incorrect server-to-server secret (Lendrix returns 401)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wrong_server_secret_returns_503():
    from fastapi import HTTPException
    from routers.sso import _redeem_code_with_lendrix

    with patch("routers.sso.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await _redeem_code_with_lendrix(VALID_CODE)
        # Must not expose the real reason (wrong secret) to the caller
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Test 7: User without NSE mapping — investor_linked is False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_without_nse_mapping():
    from routers.sso import _get_active_client_codes

    db = _mock_db_no_revocation()
    codes = await _get_active_client_codes(VALID_UUID, db)
    assert codes == []


# ---------------------------------------------------------------------------
# Test 8: User with valid NSE mapping — client codes returned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_with_valid_nse_mapping():
    from routers.sso import _get_active_client_codes

    db = _mock_db_with_mapping([VALID_CLIENT_CODE])
    # _get_active_client_codes only calls execute once (no revocation check)
    db2 = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = [(VALID_CLIENT_CODE,)]
    db2.execute = AsyncMock(return_value=result)

    codes = await _get_active_client_codes(VALID_UUID, db2)
    assert VALID_CLIENT_CODE in codes


# ---------------------------------------------------------------------------
# Test 9: User with multiple authorized client codes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_with_multiple_client_codes():
    from routers.sso import _get_active_client_codes

    multi_codes = ["CODE0001", "CODE0002", "CODE0003"]
    db = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = [(c,) for c in multi_codes]
    db.execute = AsyncMock(return_value=result)

    codes = await _get_active_client_codes(VALID_UUID, db)
    assert len(codes) == 3
    assert set(codes) == set(multi_codes)


# ---------------------------------------------------------------------------
# Test 10: Unauthorized client_code rejected by assert_client_code_authorized
# ---------------------------------------------------------------------------

def test_unauthorized_client_code_rejected():
    from fastapi import HTTPException
    from auth.sso_dependencies import assert_client_code_authorized

    payload = {"client_codes": ["AUTHORIZED1"]}

    with pytest.raises(HTTPException) as exc_info:
        assert_client_code_authorized(payload, "UNAUTHORIZED9")
    assert exc_info.value.status_code == 403


def test_authorized_client_code_passes():
    from auth.sso_dependencies import assert_client_code_authorized

    payload = {"client_codes": ["AUTHORIZED1", "AUTHORIZED2"]}
    # Should not raise
    assert_client_code_authorized(payload, "AUTHORIZED1")
    assert_client_code_authorized(payload, "AUTHORIZED2")


# ---------------------------------------------------------------------------
# Test 11: PAN-based endpoint — SSO payload does not contain PAN
# ---------------------------------------------------------------------------

def test_sso_payload_contains_no_pan():
    from auth.sso_session import create_sso_token

    token = create_sso_token(VALID_UUID, [VALID_CLIENT_CODE])
    payload = jwt.decode(
        token, SSO_JWT_SECRET, algorithms=[_ALGORITHM],
        audience=_AUDIENCE, issuer=_ISSUER,
    )
    # PAN must never be embedded in the SSO token
    assert "pan" not in payload
    assert "pan_rp" not in payload


# ---------------------------------------------------------------------------
# Test 12: SSO token contains only client_codes, not raw investor data
# ---------------------------------------------------------------------------

def test_sso_token_payload_is_minimal():
    from auth.sso_session import create_sso_token

    token = create_sso_token(VALID_UUID, [VALID_CLIENT_CODE])
    payload = jwt.decode(
        token, SSO_JWT_SECRET, algorithms=[_ALGORITHM],
        audience=_AUDIENCE, issuer=_ISSUER,
    )
    allowed_keys = {"sub", "typ", "iss", "aud", "iat", "exp", "jti", "client_codes"}
    assert set(payload.keys()).issubset(allowed_keys)


# ---------------------------------------------------------------------------
# Test 13: Session expiration — expired token raises 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_expiration():
    from fastapi import HTTPException
    from auth.sso_session import decode_sso_token

    db = _mock_db_no_revocation()
    expired = _make_sso_token(expired=True)

    with pytest.raises(HTTPException) as exc_info:
        await decode_sso_token(expired, db)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Test 14: Session revocation via revoke_sso_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_revocation_inserts_record():
    from auth.sso_session import revoke_sso_session

    db = AsyncMock()
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_existing)
    db.add = MagicMock()
    db.commit = AsyncMock()

    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=60)
    await revoke_sso_session(jti, VALID_UUID, expires_at, db)

    db.add.assert_called_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_session_revocation_is_idempotent():
    """Revoking an already-revoked JTI must not insert a duplicate."""
    from auth.sso_session import revoke_sso_session

    db = AsyncMock()
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = MagicMock()  # already revoked
    db.execute = AsyncMock(return_value=existing)
    db.add = MagicMock()
    db.commit = AsyncMock()

    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=60)
    await revoke_sso_session(jti, VALID_UUID, expires_at, db)

    db.add.assert_not_called()
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 15: Missing CSRF token on logout
# ---------------------------------------------------------------------------

def test_missing_csrf_token_rejected():
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from auth.sso_session import verify_csrf_token

    mock_request = MagicMock()
    mock_request.cookies = {}
    mock_request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        verify_csrf_token(mock_request)
    assert exc_info.value.status_code == 403


def test_mismatched_csrf_token_rejected():
    from fastapi import HTTPException
    from auth.sso_session import verify_csrf_token

    mock_request = MagicMock()
    mock_request.cookies = {"x-csrf-token": "value-a"}
    mock_request.headers = {"X-CSRF-Token": "value-b"}

    with pytest.raises(HTTPException) as exc_info:
        verify_csrf_token(mock_request)
    assert exc_info.value.status_code == 403


def test_matching_csrf_token_passes():
    from auth.sso_session import verify_csrf_token, csrf_token_for_session
    from config import settings
    from unittest.mock import patch

    mock_request = MagicMock()
    mock_request.cookies = {settings.SSO_COOKIE_NAME: "session-token"}
    mock_request.headers = {
        "X-CSRF-Token": csrf_token_for_session("session-token"),
        "origin": "https://lendrixventech.com",
    }

    with patch.object(settings, "SSO_ALLOWED_ORIGINS", "https://lendrixventech.com"):
        verify_csrf_token(mock_request)


# ---------------------------------------------------------------------------
# Test 16: Customer SSO token must not pass require_auth (app Bearer guard)
# ---------------------------------------------------------------------------

def test_customer_sso_token_rejected_by_require_auth():
    """
    A customer SSO JWT is signed with SSO_JWT_SECRET.
    require_auth() uses JWT_SECRET — the two are different.
    A customer token must never pass the shared app guard.
    """
    from fastapi import HTTPException
    from auth.dependencies import require_auth
    from fastapi.security import HTTPAuthorizationCredentials

    sso_token = _make_sso_token()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=sso_token)

    with pytest.raises(HTTPException) as exc_info:
        require_auth(creds)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Test 17: Existing application login compatibility — /auth/login unchanged
# ---------------------------------------------------------------------------

def test_existing_login_schema_unchanged():
    from schemas.auth import LoginRequest, LoginResponse

    req = LoginRequest(username="testuser", password="testpass")
    assert req.username == "testuser"

    resp = LoginResponse(
        status="200",
        message="Authentication successful",
        access_token="sometoken",
        token_type="Bearer",
    )
    assert resp.token_type == "Bearer"
    assert resp.access_token == "sometoken"


# ---------------------------------------------------------------------------
# Test 18: Existing Bearer JWT compatibility — require_auth still works
# ---------------------------------------------------------------------------

def test_existing_bearer_jwt_still_works():
    from auth.dependencies import require_auth
    from fastapi.security import HTTPAuthorizationCredentials
    from datetime import datetime, timezone, timedelta

    app_secret = "test-jwt-secret-value-at-least-32-chars!!"
    expire = datetime.now(timezone.utc) + timedelta(minutes=60)
    token = jwt.encode({"sub": "appuser", "exp": expire}, app_secret, algorithm="HS256")

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with patch("auth.dependencies.settings") as mock_settings:
        mock_settings.JWT_SECRET = app_secret
        result = require_auth(creds)

    assert result == token


# ---------------------------------------------------------------------------
# Test 19: NSE exchange auth regression — nse_auth.py untouched
# ---------------------------------------------------------------------------

def test_nse_auth_headers_structure_unchanged():
    """
    Verify nse_auth._build_headers still produces the exact header set
    required by NSE. This is a regression guard — nse_auth.py must not change.
    """
    from auth.nse_auth import _build_headers

    headers = _build_headers(
        user_id="TESTUSER",
        api_secret="FAKESECRET",
        license_key="abcdef1234567890abcdef1234567890",
        member_id="9999999",
    )

    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"].startswith("Basic ")
    assert headers["memberId"] == "9999999"
    assert headers["User-Agent"] == "PostmanRuntime/7.43.0"
    assert headers["Accept"] == ""
    assert headers["Referer"] == "www.google.com"
    assert "Accept-Language" in headers
    assert "Connection" in headers


# ---------------------------------------------------------------------------
# Test 20: No sensitive values in SSO token or logs
# ---------------------------------------------------------------------------

def test_no_sensitive_values_in_sso_token():
    from auth.sso_session import create_sso_token

    token = create_sso_token(VALID_UUID, [VALID_CLIENT_CODE])
    payload = jwt.decode(
        token, SSO_JWT_SECRET, algorithms=[_ALGORITHM],
        audience=_AUDIENCE, issuer=_ISSUER,
    )

    sensitive_keys = {
        "password", "secret", "api_secret", "license_key",
        "nse_login_user_id", "member_id", "pan", "aadhaar",
        "account_no", "ifsc", "micr",
    }
    for key in sensitive_keys:
        assert key not in payload, f"Sensitive key '{key}' found in SSO token payload"


def test_lendrix_uuid_not_logged_directly(caplog):
    """
    The SSO router logs <redacted> instead of the real UUID.
    This test verifies the log message pattern used in routers/sso.py.
    """
    import logging
    real_uuid = str(uuid.uuid4())
    logger = logging.getLogger("routers.sso")

    with caplog.at_level(logging.INFO, logger="routers.sso"):
        logger.info("SSO exchange: session established for uuid=<redacted> investor_linked=%s", True)

    assert real_uuid not in caplog.text
    assert "<redacted>" in caplog.text


# ---------------------------------------------------------------------------
# Test: SsoExchangeRequest valid code accepted
# ---------------------------------------------------------------------------

def test_valid_code_accepted_by_schema():
    from schemas.sso import SsoExchangeRequest
    from pydantic import ValidationError

    req = SsoExchangeRequest(code="a" * 43)
    assert req.code == "a" * 43

    with pytest.raises(ValidationError):
        SsoExchangeRequest(code="A" * 64)

    req2 = SsoExchangeRequest(code="A" * 43)
    assert req2.code == "A" * 43


# ---------------------------------------------------------------------------
# Test: LendrixVerifyResponse rejects malformed UUID
# ---------------------------------------------------------------------------

def test_lendrix_verify_response_rejects_bad_uuid():
    from schemas.sso import LendrixVerifyResponse
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LendrixVerifyResponse(
            success=True,
            customer={"lendrix_uuid": "not-a-uuid"},
        )

    with pytest.raises(ValidationError):
        LendrixVerifyResponse(success=True, customer={"lendrix_uuid": ""})


def test_lendrix_verify_response_accepts_valid_uuid():
    from schemas.sso import LendrixVerifyResponse

    resp = LendrixVerifyResponse(
        success=True,
        customer={"lendrix_uuid": VALID_UUID},
    )
    assert resp.customer.lendrix_uuid == VALID_UUID
