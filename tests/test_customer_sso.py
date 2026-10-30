from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from routers import mf
from models.sso import LendrixUserNseMapping
from schemas.misc_services import PurchasePaymentRequest, UPIStatusCheckRequest
from schemas.onboarding import OnboardingStatusResponse
from schemas.order import OrderEntryRequest
from schemas.ucc import UCCRegistrationRequest


LENDRIX_UUID = "f0a6e627-16de-40b5-aede-879e0d9b9246"


def _result(scalar=None, rows=None, scalars=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.fetchall.return_value = rows or []
    result.scalars.return_value.all.return_value = scalars or []
    return result


@pytest.mark.asyncio
async def test_onboarding_status_does_not_equate_mapping_with_readiness():
    db = AsyncMock()
    db.execute.side_effect = [
        _result(),
        _result(rows=[("OWN001", "REG_SUCCESS")]),
    ]

    result = await mf.onboarding_status(
        {"sub": LENDRIX_UUID, "client_codes": ["OWN001"]},
        db,
    )

    assert isinstance(result, OnboardingStatusResponse)
    assert result.ucc_status == "registered"
    assert result.onboarding_status == "requirements_unknown"
    assert result.next_action != "open_dashboard"
    assert set(result.readiness_checks.values()) == {"unknown"}


@pytest.mark.asyncio
async def test_new_customer_status_starts_registration():
    db = AsyncMock()
    db.execute.return_value = _result()

    result = await mf.onboarding_status(
        {"sub": LENDRIX_UUID, "client_codes": []},
        db,
    )

    assert result.has_nse_account is None
    assert result.linking_required
    assert result.ucc_status == "not_started"
    assert result.next_action == "start_registration"


@pytest.mark.asyncio
async def test_customer_ucc_rejects_browser_supplied_existing_client_code():
    """Frontend-supplied client_code is silently overwritten by the auto-generated one."""
    db = AsyncMock()
    db.execute.return_value = _result()
    payload = UCCRegistrationRequest(
        reg_details=[{"client_code": "OTHER001", "primary_holder_first_name": "Example"}]
    )
    response = {
        "reg_details": [{"client_code": "GENERATED", "reg_status": "REG_SUCCESS", "primary_holder_first_name": "Example"}],
        "response_status": "SUCCESS",
    }

    with patch("routers.mf._submit_ucc_to_nse", AsyncMock(return_value=response)) as submit:
        await mf.customer_ucc_register(
            payload,
            uuid4(),
            {"sub": LENDRIX_UUID, "client_codes": []},
            db,
        )
    # The payload sent to NSE must NOT contain the browser-supplied code
    submitted = submit.call_args[0][0]
    assert submitted.reg_details[0].client_code != "OTHER001"


@pytest.mark.asyncio
async def test_customer_ucc_success_maps_only_nse_confirmed_client_code():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [
        _result(),  # No matching idempotency attempt
        _result(),  # No mapping for NSE-returned client code
        _result(),  # No existing UCC record
        _result(),  # Persistence lookup
    ]
    payload = UCCRegistrationRequest(
        reg_details=[{"primary_holder_first_name": "Example"}]
    )
    response = {
        "reg_details": [{
            "client_code": "NEW001",
            "reg_status": "REG_SUCCESS",
            "primary_holder_first_name": "Example",
        }],
        "response_status": "SUCCESS",
    }

    with patch("routers.mf._submit_ucc_to_nse", AsyncMock(return_value=response)):
        result = await mf.customer_ucc_register(
            payload,
            uuid4(),
            {"sub": LENDRIX_UUID, "client_codes": []},
            db,
        )

    assert result.reg_details[0].client_code == "NEW001"
    mapping = next(
        item for item in db.add.call_args_list
        if isinstance(item.args[0], LendrixUserNseMapping)
        and getattr(item.args[0], "client_code", None) == "NEW001"
    ).args[0]
    assert mapping.lendrix_uuid == LENDRIX_UUID
    assert mapping.verification_method == "nse_ucc_success"
    assert mapping.verification_reference
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_customer_ucc_unknown_exchange_status_is_not_marked_registered():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result()
    payload = UCCRegistrationRequest(
        reg_details=[{"primary_holder_first_name": "Example"}]
    )
    response = {
        "reg_details": [{"reg_status": "WAITING"}],
        "response_status": "SUCCESS",
    }

    with patch("routers.mf._submit_ucc_to_nse", AsyncMock(return_value=response)):
        await mf.customer_ucc_register(
            payload,
            uuid4(),
            {"sub": LENDRIX_UUID, "client_codes": []},
            db,
        )

    attempt = next(
        item for item in db.add.call_args_list
        if hasattr(item.args[0], "request_fingerprint")
    ).args[0]
    assert attempt.status == "unknown"
    assert attempt.client_codes == []


@pytest.mark.asyncio
async def test_customer_ucc_timeout_persists_pending_state_and_does_not_retry():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result()
    payload = UCCRegistrationRequest(
        reg_details=[{"primary_holder_first_name": "Example"}]
    )

    with patch(
        "routers.mf._submit_ucc_to_nse",
        AsyncMock(side_effect=HTTPException(status_code=504, detail="timeout")),
    ):
        with pytest.raises(HTTPException) as error:
            await mf.customer_ucc_register(
                payload,
                uuid4(),
                {"sub": LENDRIX_UUID, "client_codes": []},
                db,
            )

    attempt = next(
        item for item in db.add.call_args_list
        if hasattr(item.args[0], "request_fingerprint")
    ).args[0]
    assert error.value.status_code == 504
    assert attempt.status == "pending_confirmation"
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_customer_ucc_does_not_claim_a_client_code_already_linked_elsewhere():
    db = AsyncMock()
    db.add = MagicMock()
    other_owner = SimpleNamespace(lendrix_uuid="another-customer")
    db.execute.side_effect = [
        _result(),  # No matching idempotency attempt
        _result(scalar=other_owner),  # NSE code belongs to another UUID
        _result(),  # Existing UCC lookup
    ]
    payload = UCCRegistrationRequest(
        reg_details=[{"primary_holder_first_name": "Example"}]
    )
    response = {"reg_details": [{"client_code": "CONFLICT1", "reg_status": "S"}]}

    with patch("routers.mf._submit_ucc_to_nse", AsyncMock(return_value=response)):
        with pytest.raises(HTTPException) as error:
            await mf.customer_ucc_register(
                payload,
                uuid4(),
                {"sub": LENDRIX_UUID, "client_codes": []},
                db,
            )

    attempt = next(
        item for item in db.add.call_args_list
        if hasattr(item.args[0], "request_fingerprint")
    ).args[0]
    assert error.value.status_code == 409
    assert attempt.status == "linking_required"
    assert attempt.client_codes == []
    assert all(
        not isinstance(item.args[0], LendrixUserNseMapping)
        for item in db.add.call_args_list
    )


@pytest.mark.asyncio
async def test_customer_order_rejects_mixed_owner_batch_before_submission():
    """All items get the resolved client code — mixed batches are not possible."""
    db = AsyncMock()
    payload = OrderEntryRequest(
        transaction_details=[
            {
                "scheme_code": "SCHEME1",
                "trxn_type": "P",
                "buy_sell_type": "FRESH",
                "client_code": code,
                "demat_physical": "P",
                "order_amount": "1000",
                "kyc_flag": "Y",
                "euin_declaration": "N",
            }
            for code in ("OWN001", "OTHER001")
        ]
    )

    captured_codes = []

    async def fake_submit(enforced_payload, db):
        captured_codes.extend(
            t.client_code for t in enforced_payload.transaction_details
        )
        return MagicMock(transaction_details=[])

    with patch("routers.mf.submit_order", fake_submit):
        await mf.customer_order_entry(
            payload,
            {"sub": LENDRIX_UUID, "client_codes": ["OWN001"]},
            db,
        )

    # All items must have the single resolved code — no OTHER001 allowed through
    assert all(c == "OWN001" for c in captured_codes)
    assert "OTHER001" not in captured_codes


@pytest.mark.asyncio
async def test_purchase_payment_requires_customer_owned_persisted_order():
    db = AsyncMock()
    db.execute.return_value = _result(scalars=["OWN_ORDER"])
    payload = PurchasePaymentRequest(
        payment_mode="UPI",
        client_code="OWN001",
        order_ids="OWN_ORDER,OTHER_ORDER",
    )

    with patch("routers.mf.submit_purchase_payment", AsyncMock()) as submit:
        with pytest.raises(HTTPException) as error:
            await mf.customer_purchase_payment(
                payload,
                {"client_codes": ["OWN001"]},
                db,
            )

    assert error.value.status_code == 403
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_purchase_payment_for_owned_order_calls_existing_payment_handler():
    db = AsyncMock()
    db.execute.return_value = _result(scalars=["OWN_ORDER"])
    payload = PurchasePaymentRequest(
        payment_mode="UPI",
        client_code="OWN001",
        order_ids="OWN_ORDER",
    )
    expected = object()

    with patch(
        "routers.mf.submit_purchase_payment",
        AsyncMock(return_value=expected),
    ) as submit:
        result = await mf.customer_purchase_payment(
            payload,
            {"client_codes": ["OWN001"]},
            db,
        )

    assert result is expected
    submit.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_purchase_payment_rejects_unowned_client_code_before_database_or_nse():
    db = AsyncMock()
    payload = PurchasePaymentRequest(
        payment_mode="UPI",
        client_code="OTHER001",
        order_ids="ORDER1",
    )

    with patch("routers.mf.submit_purchase_payment", AsyncMock()) as submit:
        with pytest.raises(HTTPException) as error:
            await mf.customer_purchase_payment(
                payload,
                {"client_codes": ["OWN001"]},
                db,
            )

    assert error.value.status_code == 403
    db.execute.assert_not_awaited()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_upi_status_rejects_unowned_client_code():
    payload = UPIStatusCheckRequest(
        nse_upi_ref_no="REFERENCE1",
        client_code="OTHER001",
    )

    with pytest.raises(HTTPException) as error:
        await mf.customer_upi_status_check(payload, {"client_codes": ["OWN001"]})

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_upi_status_is_disabled_until_reference_ownership_is_recorded():
    payload = UPIStatusCheckRequest(
        nse_upi_ref_no="REFERENCE1",
        client_code="OWN001",
    )

    with pytest.raises(HTTPException) as error:
        await mf.customer_upi_status_check(payload, {"client_codes": ["OWN001"]})

    assert error.value.status_code == 501
