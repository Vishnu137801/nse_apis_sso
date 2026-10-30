"""
Offline tests for Client Code resolution and EUIN enforcement.

Covers:
  - Customer A / B isolation
  - Missing mapping (no client code)
  - Multiple client codes (conflict)
  - Client code resolution from SSO payload
  - EUIN injection on order, SIP, XSIP
  - Backend rejection of wrong client code on order entry
  - Registration status includes EUIN only when registered
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from auth.client_code import (
    enforce_euin,
    get_distributor_euin,
    inject_client_code_and_euin,
    resolve_client_code,
)
from schemas.order import OrderEntryRequest, OrderRequest
from schemas.sip import SIPDetail, SIPRegistrationRequest
from schemas.xsip import XSIPDetail, XSIPRegistrationRequest


CUSTOMER_A_UUID = "f0a6e627-16de-40b5-aede-879e0d9b9246"
CUSTOMER_B_UUID = "9831b3f3-52cf-4cb0-96d4-e9f1e4ff8e16"
CLIENT_CODE_A = "VERIFIED1"
CLIENT_CODE_B = "OTHER001"
EUIN = "E656590"


# ── resolve_client_code ───────────────────────────────────────────────────────

def test_resolve_client_code_returns_single_code():
    payload = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A]}
    assert resolve_client_code(payload) == CLIENT_CODE_A


def test_resolve_client_code_raises_403_when_no_mapping():
    payload = {"sub": CUSTOMER_A_UUID, "client_codes": []}
    with pytest.raises(HTTPException) as exc:
        resolve_client_code(payload)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "NSE_REGISTRATION_REQUIRED"


def test_resolve_client_code_raises_409_when_multiple_codes():
    payload = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A, CLIENT_CODE_B]}
    with pytest.raises(HTTPException) as exc:
        resolve_client_code(payload)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "MULTIPLE_CLIENT_CODES"


def test_customer_a_and_b_resolve_different_codes():
    payload_a = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A]}
    payload_b = {"sub": CUSTOMER_B_UUID, "client_codes": [CLIENT_CODE_B]}
    assert resolve_client_code(payload_a) == CLIENT_CODE_A
    assert resolve_client_code(payload_b) == CLIENT_CODE_B
    assert resolve_client_code(payload_a) != resolve_client_code(payload_b)


# ── EUIN enforcement ──────────────────────────────────────────────────────────

def test_get_distributor_euin_returns_configured_value():
    with patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        assert get_distributor_euin() == EUIN


def test_enforce_euin_sets_euin_number_and_declaration():
    with patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        item = {"scheme_code": "SCH001"}
        result = enforce_euin(item)
    assert result["euin_number"] == EUIN
    assert result["euin_declaration"] == "Y"


def test_enforce_euin_overwrites_frontend_supplied_euin():
    with patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        item = {"euin_number": "WRONG_EUIN", "euin_declaration": "N"}
        result = enforce_euin(item)
    assert result["euin_number"] == EUIN
    assert result["euin_declaration"] == "Y"


def test_inject_client_code_and_euin_sets_all_fields():
    with patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        items = [{"scheme_code": "SCH001"}, {"scheme_code": "SCH002"}]
        result = inject_client_code_and_euin(items, CLIENT_CODE_A)
    for item in result:
        assert item["client_code"] == CLIENT_CODE_A
        assert item["euin_number"] == EUIN
        assert item["euin_declaration"] == "Y"


def test_inject_does_not_allow_different_client_code_per_item():
    """All items must get the same resolved client code regardless of input."""
    with patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        items = [
            {"scheme_code": "SCH001", "client_code": CLIENT_CODE_B},
            {"scheme_code": "SCH002", "client_code": "HACKER"},
        ]
        result = inject_client_code_and_euin(items, CLIENT_CODE_A)
    for item in result:
        assert item["client_code"] == CLIENT_CODE_A


# ── Customer order entry isolation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_order_entry_injects_resolved_client_code_not_frontend_value():
    from routers import mf

    db = AsyncMock()
    payload = OrderEntryRequest(
        transaction_details=[
            OrderRequest(
                scheme_code="SCH001",
                trxn_type="P",
                buy_sell_type="FRESH",
                client_code=CLIENT_CODE_B,   # frontend tries Customer B's code
                demat_physical="P",
                order_amount="1000",
                kyc_flag="Y",
                euin_declaration="N",
            )
        ]
    )
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A]}

    captured = {}

    async def fake_submit(enforced_payload, db):
        captured["client_code"] = enforced_payload.transaction_details[0].client_code
        captured["euin_number"] = enforced_payload.transaction_details[0].euin_number
        captured["euin_declaration"] = enforced_payload.transaction_details[0].euin_declaration
        return MagicMock(transaction_details=[])

    with patch("routers.mf.submit_order", fake_submit), \
         patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        await mf.customer_order_entry(payload, sso_payload, db)

    assert captured["client_code"] == CLIENT_CODE_A
    assert captured["euin_number"] == EUIN
    assert captured["euin_declaration"] == "Y"


@pytest.mark.asyncio
async def test_customer_order_entry_rejects_unregistered_customer():
    from routers import mf

    db = AsyncMock()
    payload = OrderEntryRequest(
        transaction_details=[
            OrderRequest(
                scheme_code="SCH001",
                trxn_type="P",
                buy_sell_type="FRESH",
                client_code="",
                demat_physical="P",
                order_amount="1000",
                kyc_flag="Y",
                euin_declaration="N",
            )
        ]
    )
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": []}

    with patch("routers.mf.submit_order", AsyncMock()) as submit:
        with pytest.raises(HTTPException) as exc:
            await mf.customer_order_entry(payload, sso_payload, db)

    assert exc.value.status_code == 403
    submit.assert_not_awaited()


# ── Customer SIP isolation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_sip_register_injects_client_code_and_euin():
    from routers import mf

    db = AsyncMock()
    payload = SIPRegistrationRequest(
        reg_data=[SIPDetail(sch_code="SCH001", client_code=CLIENT_CODE_B)]
    )
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A]}

    captured = {}

    async def fake_submit(enforced_payload, db):
        captured["client_code"] = enforced_payload.reg_data[0].client_code
        captured["euin_number"] = enforced_payload.reg_data[0].euin_number
        return MagicMock(reg_data=[])

    with patch("routers.mf.submit_sip", fake_submit), \
         patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        await mf.customer_sip_register(payload, sso_payload, db)

    assert captured["client_code"] == CLIENT_CODE_A
    assert captured["euin_number"] == EUIN


@pytest.mark.asyncio
async def test_customer_sip_rejects_unregistered_customer():
    from routers import mf

    db = AsyncMock()
    payload = SIPRegistrationRequest(reg_data=[SIPDetail(sch_code="SCH001")])
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": []}

    with patch("routers.mf.submit_sip", AsyncMock()) as submit:
        with pytest.raises(HTTPException) as exc:
            await mf.customer_sip_register(payload, sso_payload, db)

    assert exc.value.status_code == 403
    submit.assert_not_awaited()


# ── Customer XSIP isolation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_xsip_register_injects_client_code_and_euin():
    from routers import mf

    db = AsyncMock()
    payload = XSIPRegistrationRequest(
        reg_data=[XSIPDetail(sch_code="SCH001", client_code=CLIENT_CODE_B)]
    )
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A]}

    captured = {}

    async def fake_submit(enforced_payload, db):
        captured["client_code"] = enforced_payload.reg_data[0].client_code
        captured["euin_number"] = enforced_payload.reg_data[0].euin_number
        return MagicMock(reg_data=[])

    with patch("routers.mf.submit_xsip", fake_submit), \
         patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        await mf.customer_xsip_register(payload, sso_payload, db)

    assert captured["client_code"] == CLIENT_CODE_A
    assert captured["euin_number"] == EUIN


@pytest.mark.asyncio
async def test_customer_xsip_rejects_unregistered_customer():
    from routers import mf

    db = AsyncMock()
    payload = XSIPRegistrationRequest(reg_data=[XSIPDetail(sch_code="SCH001")])
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": []}

    with patch("routers.mf.submit_xsip", AsyncMock()) as submit:
        with pytest.raises(HTTPException) as exc:
            await mf.customer_xsip_register(payload, sso_payload, db)

    assert exc.value.status_code == 403
    submit.assert_not_awaited()


# ── Registration status EUIN ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registration_status_includes_euin_when_registered():
    from routers.customer import customer_registration_status

    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": [CLIENT_CODE_A]}

    with patch("auth.client_code.settings") as s:
        s.NSE_DISTRIBUTOR_EUIN = EUIN
        result = await customer_registration_status(sso_payload, db)

    assert result.euin == EUIN
    assert result.registration_status == "registered"


@pytest.mark.asyncio
async def test_registration_status_excludes_euin_when_unregistered():
    from routers.customer import customer_registration_status

    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    sso_payload = {"sub": CUSTOMER_A_UUID, "client_codes": []}

    result = await customer_registration_status(sso_payload, db)

    assert result.euin is None
    assert result.registration_status == "unlinked"
