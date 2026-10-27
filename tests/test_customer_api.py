from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt
from starlette.responses import Response

from auth.sso_session import create_sso_token, hash_sso_jti
from config import settings
from database import get_db
from main import app
from models.sso import NseSsoSession

CUSTOMER_A = "f0a6e627-16de-40b5-aede-879e0d9b9246"
CUSTOMER_B = "9831b3f3-52cf-4cb0-96d4-e9f1e4ff8e16"


def _result(scalar=None, rows=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.fetchall.return_value = rows or []
    return result


class CustomerDatabase:
    def __init__(self, mappings=None, attempts=None):
        self.mappings = mappings or {}
        self.attempts = attempts or {}
        self.sessions = {}

    async def execute(self, statement):
        sql = str(statement)
        if "nse_sso_sessions" in sql:
            session_hash = next(iter(statement.compile().params.values()))
            return _result(scalar=self.sessions.get(session_hash))
        if "sso_session_revocations" in sql:
            return _result()
        if "lendrix_user_nse_mapping" in sql:
            params = statement.compile().params.values()
            customer_uuid = next(value for value in params if value in self.mappings)
            return _result(rows=[(code,) for code in self.mappings[customer_uuid]])
        if "customer_ucc_attempts" in sql:
            customer_uuid = next(value for value in statement.compile().params.values() if value in self.attempts)
            return _result(scalar=self.attempts[customer_uuid])
        raise AssertionError(f"Unexpected database query: {sql}")


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    db = CustomerDatabase(
        mappings={CUSTOMER_A: ["VERIFIED1"], CUSTOMER_B: ["OTHER001"]},
        attempts={
            CUSTOMER_A: None,
            CUSTOMER_B: None,
        },
    )

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    client = TestClient(app, base_url="https://nse.lendrixventech.com")
    client.sso_database = db
    yield client, db
    client.close()
    app.dependency_overrides.clear()


def _set_customer(client, customer_uuid, expired=False):
    token = create_sso_token(customer_uuid, [])
    if expired:
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": customer_uuid,
                "typ": "customer_sso",
                "iss": "nse-fastapi",
                "aud": "mutualfund.lendrixventech.com",
                "iat": int((now - timedelta(minutes=10)).timestamp()),
                "exp": int((now - timedelta(minutes=5)).timestamp()),
                "jti": str(uuid4()),
                "client_codes": [],
            },
            settings.SSO_JWT_SECRET,
            algorithm="HS256",
        )
    client.cookies.set(settings.SSO_COOKIE_NAME, token)
    if not expired:
        payload = jwt.decode(
            token,
            settings.SSO_JWT_SECRET,
            algorithms=["HS256"],
            audience="mutualfund.lendrixventech.com",
            issuer="nse-fastapi",
        )
        client.sso_database.sessions[hash_sso_jti(payload["jti"])] = NseSsoSession(
            id=str(uuid4()),
            lendrix_user_uuid=customer_uuid,
            jwt_jti_hash=hash_sso_jti(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], timezone.utc),
        )


def test_scheme_catalogue_requires_sso_but_not_client_mapping(api_client):
    client, _ = api_client
    body = "SCHEME CODE|SCHEME NAME|AMC CODE\nSCHEME1|Sample Scheme|AMC_MF"
    with patch(
        "routers.customer._download_master",
        AsyncMock(return_value=Response(body, media_type="text/plain")),
    ) as download:
        response = client.get("/api/v1/customer/schemes")
        assert response.status_code == 401
        assert client.get("/api/v1/customer/registration/status").status_code == 401
        download.assert_not_awaited()

        _set_customer(client, CUSTOMER_A)
        response = client.get("/api/v1/customer/schemes")

    assert response.status_code == 200
    assert response.text == body
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    download.assert_awaited_once()


def test_scheme_catalogue_rejects_expired_sso_session(api_client):
    client, _ = api_client
    _set_customer(client, CUSTOMER_A, expired=True)
    with patch("routers.customer._download_master", AsyncMock()) as download:
        response = client.get("/api/v1/customer/schemes")
    assert response.status_code == 401
    download.assert_not_awaited()


@pytest.mark.parametrize(
    "upstream_status,expected_status,expected_detail",
    [
        (403, 502, "NSE scheme catalogue is unavailable"),
        (502, 502, "NSE scheme catalogue is unavailable"),
        (504, 504, "NSE scheme catalogue timed out"),
    ],
)
def test_scheme_catalogue_sanitizes_upstream_errors(
    api_client, upstream_status, expected_status, expected_detail
):
    client, _ = api_client
    _set_customer(client, CUSTOMER_A)
    with patch(
        "routers.customer._download_master",
        AsyncMock(
            side_effect=HTTPException(
                status_code=upstream_status,
                detail="secret upstream response",
            )
        ),
    ):
        response = client.get("/api/v1/customer/schemes")
    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert "secret upstream response" not in response.text


@pytest.mark.parametrize(
    "attempt_status,expected",
    [
        ("registration_in_progress", "pending"),
        ("pending_confirmation", "pending"),
        ("rejected", "failed"),
        ("linking_required", "linking_required"),
        ("unknown", "unknown"),
        ("registered", "unknown"),
    ],
)
def test_registration_status_reports_customer_attempt_state(api_client, attempt_status, expected):
    client, db = api_client
    db.mappings[CUSTOMER_A] = []
    db.attempts[CUSTOMER_A] = MagicMock(status=attempt_status)
    _set_customer(client, CUSTOMER_A)
    response = client.get("/api/v1/customer/registration/status")
    assert response.status_code == 200
    assert response.json()["registration_status"] == expected
    assert response.json()["attempt_status"] == attempt_status
    assert response.json()["client_codes"] == []


def test_registration_status_returns_only_authenticated_customers_mapping(api_client):
    client, _ = api_client
    _set_customer(client, CUSTOMER_A)
    response = client.get("/api/v1/customer/registration/status")
    assert response.status_code == 200
    assert response.json()["registration_status"] == "registered"
    assert response.json()["client_codes"] == ["VERIFIED1"]
    assert "OTHER001" not in response.text

    _set_customer(client, CUSTOMER_B)
    response = client.get("/api/v1/customer/registration/status")
    assert response.status_code == 200
    assert response.json()["client_codes"] == ["OTHER001"]
    assert "VERIFIED1" not in response.text


def test_registration_status_reports_unlinked_customer(api_client):
    client, db = api_client
    db.mappings[CUSTOMER_A] = []
    db.attempts[CUSTOMER_A] = None
    _set_customer(client, CUSTOMER_A)
    response = client.get("/api/v1/customer/registration/status")
    assert response.status_code == 200
    assert response.json()["registration_status"] == "unlinked"
    assert response.json()["client_codes"] == []


def test_customer_routes_reject_legacy_bearer_as_ss_auth(api_client):
    client, _ = api_client
    with patch("routers.customer._download_master", AsyncMock()) as download:
        response = client.get(
            "/api/v1/customer/schemes",
            headers={"Authorization": "Bearer not-an-sso-session"},
        )
    assert response.status_code == 401
    download.assert_not_awaited()


def test_customer_routes_use_sso_cookie_in_openapi(api_client):
    client, _ = api_client
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/customer/schemes"]["get"]["security"] == [
        {"SsoCookie": []}
    ]
    assert schema["paths"]["/api/v1/customer/registration/status"]["get"]["security"] == [
        {"SsoCookie": []}
    ]
