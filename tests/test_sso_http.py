"""Offline HTTP security and compatibility checks; lifespan never runs."""
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from main import app
from config import settings
from database import get_db
from auth.dependencies import require_auth
from auth.sso_dependencies import require_sso_auth
from auth.sso_session import create_sso_token, hash_sso_jti
from models.sso import NseSsoSession, SsoSessionRevocation
from jose import jwt

UUID = "f0a6e627-16de-40b5-aede-879e0d9b9246"
ORIGIN = "https://mutualfund.lendrixventech.com"

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "SSO_ALLOWED_ORIGINS",
        "https://lendrixventech.com,https://mutualfund.lendrixventech.com",
    )
    mapping_result = MagicMock()
    mapping_result.fetchall.return_value = [("CLIENT1",), ("CLIENT2",)]
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = None
    revocation_result = MagicMock()
    revocation_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    session_record = None

    async def execute(statement):
        nonlocal session_record
        sql = str(statement)
        if sql.startswith("UPDATE nse_sso_sessions"):
            if session_record is not None:
                session_record.revoked_at = datetime.now(timezone.utc)
            return MagicMock()
        if "nse_sso_sessions" in sql:
            session_result.scalar_one_or_none.return_value = session_record
            return session_result
        if "sso_session_revocations" in sql:
            return revocation_result
        if "lendrix_user_nse_mapping" in sql:
            return mapping_result
        raise AssertionError(f"Unexpected database query: {sql}")

    def add(record):
        nonlocal session_record
        if isinstance(record, NseSsoSession):
            session_record = record
        else:
            assert isinstance(record, SsoSessionRevocation)

    db.execute = AsyncMock(side_effect=execute)
    db.add = MagicMock(side_effect=add)
    async def database():
        yield db
    app.dependency_overrides[get_db] = database
    c = TestClient(app, base_url="https://nse.lendrixventech.com", raise_server_exceptions=False)
    yield c, db, mapping_result
    c.close()
    app.dependency_overrides.clear()


def exchange(c):
    with patch("routers.sso._redeem_code_with_lendrix", AsyncMock(return_value=UUID)):
        return c.post("/api/v1/auth/sso/exchange", json={"code": "a" * 43}, headers={"Origin": ORIGIN})


def test_exchange_me_logout_replay(client):
    c, db, result = client
    r = exchange(c)
    assert r.status_code == 200
    assert r.json()["client_codes"] == ["CLIENT1", "CLIENT2"]
    assert r.json()["authenticated"]
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Domain=" not in cookie
    token = c.cookies.get(settings.SSO_COOKIE_NAME)
    assert token not in r.text
    jti = jwt.get_unverified_claims(token)["jti"]
    session = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], NseSsoSession))
    assert session.lendrix_user_uuid == UUID
    assert session.jwt_jti_hash == hash_sso_jti(jti)
    assert session.jwt_jti_hash not in token
    assert session.expires_at > datetime.now(timezone.utc)
    me = c.get("/api/v1/auth/sso/me")
    assert me.json()["lendrix_uuid"] == UUID
    assert me.headers["cache-control"] == "no-store"
    assert c.post("/api/v1/auth/sso/logout", headers={"Origin": ORIGIN}).status_code == 403
    r = c.post("/api/v1/auth/sso/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": me.json()["csrf_token"]})
    assert r.status_code == 200
    assert db.commit.await_count == 2
    assert "Max-Age=0" in r.headers["set-cookie"]
    c.cookies.set(settings.SSO_COOKIE_NAME, token)
    assert c.get("/api/v1/auth/sso/me").status_code == 401


def test_exchange_reuses_existing_session_for_same_verified_customer(client):
    c, db, _ = client
    first = exchange(c)
    assert first.status_code == 200
    original_token = c.cookies.get(settings.SSO_COOKIE_NAME)

    second = exchange(c)

    assert second.status_code == 200
    assert c.cookies.get(settings.SSO_COOKIE_NAME) == original_token
    assert db.add.call_count == 1
    assert db.commit.await_count == 1


def test_missing_and_removed_mapping(client):
    c, _, result = client
    result.fetchall.return_value = []
    assert exchange(c).json()["linking_required"]
    assert c.get("/api/v1/auth/sso/me").json()["client_codes"] == []


def test_disabled_and_exchange_csrf(client, monkeypatch):
    c, _, _ = client
    with patch("routers.sso._redeem_code_with_lendrix", AsyncMock()) as redeem:
        assert c.post("/api/v1/auth/sso/exchange", json={"code": "a"*43}).status_code == 403
        redeem.assert_not_called()
        monkeypatch.setattr(settings, "SSO_ENABLED", False)
        assert c.post("/api/v1/auth/sso/exchange", json={"code": "a"*43}, headers={"Origin": ORIGIN}).status_code == 404
        redeem.assert_not_called()


def test_production_origin_allowed_and_untrusted_origin_rejected(client, caplog):
    c, _, _ = client
    with patch("routers.sso._redeem_code_with_lendrix", AsyncMock(return_value=UUID)) as redeem:
        accepted = c.post(
            "/api/v1/auth/sso/exchange",
            json={"code": "a" * 43},
            headers={"Origin": "https://lendrixventech.com"},
        )
        rejected = c.post(
            "/api/v1/auth/sso/exchange",
            json={"code": "a" * 43},
            headers={"Origin": "https://untrusted.example"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "Origin not allowed"
    assert redeem.await_count == 1
    assert "Origin is not in SSO_ALLOWED_ORIGINS" in caplog.text


def test_local_development_requires_explicit_https_origin(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setattr(settings, "SSO_ALLOWED_ORIGINS", "https://localhost:8081")
    with patch("routers.sso._redeem_code_with_lendrix", AsyncMock(return_value=UUID)) as redeem:
        accepted = c.post(
            "/api/v1/auth/sso/exchange",
            json={"code": "a" * 43},
            headers={"Origin": "https://localhost:8081"},
        )
        rejected = c.post(
            "/api/v1/auth/sso/exchange",
            json={"code": "a" * 43},
            headers={"Origin": "http://localhost:8081"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 403
    assert redeem.await_count == 1


def test_default_cors_fallback_excludes_insecure_local_origins():
    # The fallback list in main.py must always include the production origins
    # and must never include plain-HTTP localhost entries.
    # We test the fallback constant directly rather than the runtime list,
    # because the runtime list is overridden by ALLOWED_ORIGINS in .env.
    import main as _main
    import ast, inspect
    source = inspect.getsource(_main)
    # Extract the fallback list literal from the source
    tree = ast.parse(source)
    fallback = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "allowed_origins" for t in node.targets)
            and isinstance(node.value, ast.IfExp)
        ):
            if isinstance(node.value.orelse, ast.List):
                fallback = [elt.s for elt in node.value.orelse.elts if isinstance(elt, ast.Constant)]
            break
    assert fallback is not None, "Could not locate fallback origins list in main.py"
    assert "https://lendrixventech.com" in fallback
    assert "https://mutualfund.lendrixventech.com" in fallback
    assert not any(o.startswith("http://localhost") for o in fallback)


def test_exchange_does_not_issue_cookie_when_session_commit_fails(client):
    c, db, _ = client
    db.commit.side_effect = RuntimeError("database unavailable")
    with patch("routers.sso._redeem_code_with_lendrix", AsyncMock(return_value=UUID)):
        response = c.post(
            "/api/v1/auth/sso/exchange",
            json={"code": "a" * 43},
            headers={"Origin": ORIGIN},
        )

    assert response.status_code == 500
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("status,body,expected", [
    (400, {"reason": "expired"}, 400), (400, {"reason": "reused"}, 400),
    (400, {"reason": "invalid"}, 400), (503, {}, 503), (302, {}, 503),
    (403, {"success": False, "code": "SSO_NOT_ELIGIBLE"}, 403),
    (429, {"success": False}, 429),
    (200, {"success": True, "customer": {"lendrix_uuid": "invalid"}}, 503),
    (200, [], 503),
    (200, {"success": True, "customer": {"lendrix_uuid": UUID}}, 200),
])
def test_verification_contract(client, status, body, expected):
    c, _, _ = client
    response = MagicMock(status_code=status)
    response.json.return_value = body
    http = AsyncMock()
    http.__aenter__.return_value = http
    http.post.return_value = response
    with patch("routers.sso.httpx.AsyncClient", return_value=http) as factory:
        r = c.post("/api/v1/auth/sso/exchange", json={"code": "a"*43}, headers={"Origin": ORIGIN})
    assert r.status_code == expected
    assert factory.call_args.kwargs["follow_redirects"] is False
    request = http.post.call_args
    assert request.args[0] == settings.LENDRIX_SSO_VERIFY_URL
    assert request.kwargs["json"] == {"code": "a" * 43}
    assert request.kwargs["headers"]["Authorization"] == f"Bearer {settings.LENDRIX_SSO_SERVER_SECRET}"


def test_all_business_routes_deny_customer_cookie_and_bearer(client):
    c, _, _ = client
    token = create_sso_token(UUID, ["CLIENT1"])
    c.cookies.set(settings.SSO_COOKIE_NAME, token)
    checked = 0
    assert c.get("/api/v1/mf/onboarding/status").status_code == 404
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith("/api/v1/auth/sso") or route.path.endswith("/login") or route.path == "/health":
            continue
        if route.path.startswith("/api/v1/customer/"):
            assert any(d.call is require_sso_auth for d in route.dependant.dependencies), route.path
            continue
        assert any(d.call is require_auth for d in route.dependant.dependencies), route.path
        for method in route.methods:
            for headers in ({}, {"Authorization": "Bearer " + token}):
                r = c.request(method, route.path, headers=headers, json={"pan": "OTHERPAN", "client_code": "OTHER", "transaction_details": [{"client_code": "OTHER"}]})
                assert r.status_code in (401, 403), (route.path, r.status_code)
            checked += 1
    assert checked > 40


def test_existing_login_aliases(client):
    c, _, _ = client
    for path in ("/auth/login", "/api/v1/auth/login"):
        r = c.post(path, json={"username": settings.APP_USERNAME, "password": settings.APP_PASSWORD})
        assert r.status_code == 200
        from fastapi.security import HTTPAuthorizationCredentials
        assert require_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=r.json()["access_token"]))


def test_invalid_code_never_redeemed(client):
    c, _, _ = client
    with patch("routers.sso._redeem_code_with_lendrix", AsyncMock()) as redeem:
        assert c.post("/api/v1/auth/sso/exchange", json={"code": "invalid"}, headers={"Origin": ORIGIN}).status_code == 422
        redeem.assert_not_called()

@pytest.mark.parametrize("field,value", [("LENDRIX_SSO_VERIFY_URL", "http://unsafe.invalid/verify"), ("SSO_ALLOWED_ORIGINS", "*"), ("SSO_ALLOWED_ORIGINS", ""), ("ALLOWED_ORIGINS", "*"), ("SSO_JWT_SECRET", "short"), ("SSO_SESSION_EXPIRE_MINUTES", 0)])
def test_enabled_configuration_rejects_unsafe_values(field, value):
    from config import Settings
    from pydantic import ValidationError
    values = settings.model_dump()
    values.update(SSO_ENABLED=True)
    values[field] = value
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)

@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "jti", "iss", "aud"])
def test_required_session_claims(client, claim):
    from jose import jwt
    c, _, _ = client
    token = create_sso_token(UUID, [])
    payload = jwt.get_unverified_claims(token)
    del payload[claim]
    c.cookies.set(settings.SSO_COOKIE_NAME, jwt.encode(payload, settings.SSO_JWT_SECRET, algorithm="HS256"))
    assert c.get("/api/v1/auth/sso/me").status_code == 401
