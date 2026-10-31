"""v1.9.8 audit regressions. Run through scripts/run_offline_tests.py."""
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest


@pytest.mark.parametrize("endpoint", ["get-link", "resend"])
@pytest.mark.parametrize("client_codes,requested_code,expected_status", [
    (["TEST001"], None, 200),
    (["TEST001", "TEST002"], "TEST002", 200),
    (["TEST001"], "OTHER", 403),
    ([], None, 422),
])
def test_communication_client_scope(endpoint, client_codes, requested_code, expected_status):
    from auth.dependencies import require_auth_payload
    from routers import communication

    app = FastAPI()
    app.include_router(communication.router)
    app.dependency_overrides[require_auth_payload] = lambda: {"client_codes": client_codes}
    body = {"productType": "MANDATE_AUTH", "productRefId": "2026103113239"}
    request_body = dict(body)
    if requested_code is not None:
        request_body["client_code"] = requested_code
    post = AsyncMock(return_value=body)
    with patch("routers.communication._post", post), TestClient(app) as client:
        response = client.post(f"/communication/{endpoint}", json=request_body)
    assert response.status_code == expected_status
    if expected_status == 200:
        expected_url = communication.NSE_GET_LINK_URL if endpoint == "get-link" else communication.NSE_RESEND_COMM_URL
        post.assert_awaited_once_with(expected_url, body)
        assert response.json()["productRefId"] == body["productRefId"]
    else:
        post.assert_not_awaited()


def test_documented_order_success_is_persisted():
    """PDF pp. 9-10: TRXN SUCCESS is an accepted order-entry result."""
    from database import get_db
    from routers.order import router

    db = AsyncMock()
    db.add = MagicMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = None
    db.execute.return_value = query_result

    async def fake_db():
        yield db

    response = MagicMock(status_code=200)
    response.json.return_value = {"transaction_details": [{
        "trxn_order_id": "432160000001", "trxn_status": "TRXN SUCCESS",
        "scheme_code": "AXBDGP-GR", "client_code": "TEST001",
    }]}
    upstream = AsyncMock()
    upstream.__aenter__.return_value = upstream
    upstream.post.return_value = response
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = fake_db
    with patch("routers.order.get_nse_client", return_value=upstream), patch(
        "routers.order.get_auth_headers", return_value={}
    ), TestClient(app) as client:
        result = client.post("/order/entry", json={"transaction_details": [{
            "scheme_code": "AXBDGP-GR", "trxn_type": "P",
            "buy_sell_type": "FRESH", "client_code": "TEST001",
            "demat_physical": "C", "order_amount": "2000",
            "kyc_flag": "Y", "euin_declaration": "N",
        }]})
    assert result.status_code == 200
    assert db.add.call_count == 1, "Documented TRXN SUCCESS was not persisted"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("model_module,model_name", [
    ("schemas.sip", "SIPRegistrationRequest"),
    ("schemas.stp", "STPRegistrationRequest"),
])
def test_registration_rejects_missing_mandatory_fields(model_module, model_name):
    """PDF pp. 21-24 and 26-28 require client, schemes and dates."""
    import importlib

    model = getattr(importlib.import_module(model_module), model_name)
    with pytest.raises(ValidationError):
        model.model_validate({"reg_data": [{}]})


def test_kyc_alias_forwards_documented_keys_and_preserves_link():
    """PDF p. 165: both KYC entry points must speak the same wire contract."""
    from routers.misc_services import router

    app = FastAPI()
    app.include_router(router)
    payload = {
        "amcCode": "B", "panNo": "ABCDE1234F",
        "mobileNo": "9000000000", "invEmail": "offline@example.invalid",
    }
    post = AsyncMock(return_value={"link": "https://example.invalid/kyc", "message": "OK"})
    with patch("routers.misc_services._post", post), TestClient(app) as client:
        response = client.post("/registration/kyc-fresh-register", json=payload)
    assert response.status_code == 200
    assert post.call_args.args[1] == payload
    assert response.json()["link"] == "https://example.invalid/kyc"


def test_master_download_returns_upstream_file_and_caches_scheme_catalogue(monkeypatch):
    from routers import misc_reports

    monkeypatch.setattr(misc_reports, "_master_download_cache", {})

    content = b"SCHEME CODE|SCHEME NAME\nSCHEME1|Sample Scheme\n"
    upstream_response = MagicMock(
        status_code=200,
        headers={"content-type": "text/plain; charset=utf-8"},
    )
    upstream_response.aclose = AsyncMock()

    async def response_chunks():
        yield content[:20]
        yield content[20:]

    upstream_response.aiter_bytes = lambda: response_chunks()
    upstream = MagicMock()
    upstream.build_request.return_value = object()
    upstream.send = AsyncMock(return_value=upstream_response)
    upstream.aclose = AsyncMock()
    app = FastAPI()
    app.include_router(misc_reports.router)

    with patch("routers.misc_reports.get_nse_client", return_value=upstream), patch(
        "routers.misc_reports.get_auth_headers", return_value={}
    ), TestClient(app) as client:
        first_response = client.post("/reports/master-download", json={"file_type": "SCH"})
        cached_response = client.post("/reports/master-download", json={"file_type": "SCH"})

    assert first_response.status_code == 200
    assert first_response.content == content
    assert first_response.headers["content-type"].startswith("text/plain")
    assert first_response.headers["x-master-cache"] == "MISS"
    assert cached_response.status_code == 200
    assert cached_response.content == content
    assert cached_response.headers["x-master-cache"] == "HIT"
    upstream.send.assert_awaited_once_with(upstream.build_request.return_value, stream=True)
    upstream_response.aclose.assert_awaited_once()
    upstream.aclose.assert_awaited_once()


@pytest.mark.parametrize("constant,suffix", [
    ("NSE_2FA_URL", "/reports/2FA"),
    ("NSE_CLIENT_AUTH_URL", "/reports/CLIENT_AUTHORIZATION"),
    ("NSE_ORDER_LIFECYCLE_URL", "/reports/ORDER_LIFECYCLE"),
])
def test_report_url_matches_documented_case(constant, suffix):
    """PDF pp. 118, 119, 126 specify exact upstream path capitalization."""
    from routers import misc_reports

    assert getattr(misc_reports, constant).endswith(suffix)
