import hashlib
import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.client_code import inject_client_code_and_euin, resolve_client_code, next_sequential_client_code
from auth.sso_dependencies import assert_client_code_authorized, require_sso_auth
from database import get_db
from models.order import OrderTransaction
from models.sso import CustomerUccAttempt, LendrixUserNseMapping
from models.ucc import UCCRegistration
from routers.misc_services import purchase_payment as submit_purchase_payment
from routers.order import order_entry as submit_order
from routers.sip import sip_register as submit_sip
from routers.xsip import xsip_register as submit_xsip
from routers.ucc import (
    _persist_ucc_response,
    _submit_ucc_to_nse,
    _SUCCESS_STATUSES,
)
from schemas.misc_services import (
    PurchasePaymentRequest,
    PurchasePaymentResponse,
    UPIStatusCheckRequest,
    UPIStatusCheckResponse,
)
from schemas.onboarding import OnboardingStatusResponse
from schemas.order import OrderEntryRequest, OrderEntryResponse
from schemas.sip import SIPRegistrationRequest, SIPRegistrationResponse
from schemas.ucc import UCCRegistrationRequest, UCCRegistrationResponse
from schemas.xsip import XSIPRegistrationRequest, XSIPRegistrationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mf", tags=["Mutual Fund Customer"])

_READINESS_UNKNOWN = {
    "lendrix_kyc": "unknown",
    "fatca": "unknown",
    "bank": "unknown",
    "mandate": "unknown",
}
_READINESS_NOTE = (
    "The backend cannot yet verify all onboarding requirements; dashboard "
    "readiness is therefore not confirmed."
)


def _next_action_for_attempt(attempt_status: str) -> str:
    if attempt_status == "registration_in_progress":
        return "await_confirmation"
    if attempt_status == "pending_confirmation":
        return "await_confirmation"
    if attempt_status == "rejected":
        return "correct_registration"
    if attempt_status == "linking_required":
        return "link_existing_account"
    if attempt_status == "registered":
        return "await_confirmation"
    return "await_confirmation"


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def onboarding_status(
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    lendrix_uuid = sso_payload["sub"]
    client_codes = sso_payload["client_codes"]
    latest_attempt_result = await db.execute(
        select(CustomerUccAttempt)
        .where(CustomerUccAttempt.lendrix_uuid == lendrix_uuid)
        .order_by(CustomerUccAttempt.created_at.desc())
        .limit(1)
    )
    latest_attempt = latest_attempt_result.scalar_one_or_none()

    registered_codes: set[str] = set()
    if client_codes:
        registrations = await db.execute(
            select(UCCRegistration.client_code, UCCRegistration.reg_status)
            .where(UCCRegistration.client_code.in_(client_codes))
        )
        registered_codes = {
            code
            for code, reg_status in registrations.fetchall()
            if code and (reg_status or "").strip().upper() in _SUCCESS_STATUSES
        }

    if registered_codes:
        ucc_status = "registered"
        next_action = "await_confirmation"
        onboarding_state = "requirements_unknown"
    elif client_codes:
        ucc_status = "unknown"
        next_action = "await_confirmation"
        onboarding_state = "requirements_unknown"
    elif latest_attempt is not None:
        ucc_status = latest_attempt.status
        next_action = _next_action_for_attempt(latest_attempt.status)
        onboarding_state = latest_attempt.status
    else:
        ucc_status = "not_started"
        next_action = "start_registration"
        onboarding_state = "not_started"

    return OnboardingStatusResponse(
        lendrix_uuid=lendrix_uuid,
        has_nse_account=True if client_codes else None,
        linking_required=not bool(client_codes),
        client_codes=client_codes,
        ucc_status=ucc_status,
        onboarding_status=onboarding_state,
        next_action=next_action,
        readiness_checks=_READINESS_UNKNOWN,
        readiness_note=_READINESS_NOTE if client_codes else None,
    )


@router.post("/ucc/register", response_model=UCCRegistrationResponse)
async def customer_ucc_register(
    payload: UCCRegistrationRequest,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    if not payload.reg_details:
        raise HTTPException(status_code=422, detail="reg_details cannot be empty")
    if len(payload.reg_details) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 records allowed per request")

    lendrix_uuid = sso_payload["sub"]
    idempotency_value = str(idempotency_key)

    # Auto-generate a sequential LVMF##### client code for this customer
    generated_client_code = await next_sequential_client_code(db)
    for record in payload.reg_details:
        record.client_code = generated_client_code
    request_data = payload.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    existing_result = await db.execute(
        select(CustomerUccAttempt).where(
            CustomerUccAttempt.lendrix_uuid == lendrix_uuid,
            CustomerUccAttempt.idempotency_key == idempotency_value,
        )
    )
    existing_attempt = existing_result.scalar_one_or_none()
    if existing_attempt is not None:
        detail = (
            "This idempotency key was already used for a different registration."
            if existing_attempt.request_fingerprint != fingerprint
            else "This registration attempt already exists; check onboarding status before taking further action."
        )
        raise HTTPException(status_code=409, detail=detail)

    attempt = CustomerUccAttempt(
        id=str(uuid4()),
        lendrix_uuid=lendrix_uuid,
        idempotency_key=idempotency_value,
        request_fingerprint=fingerprint,
        status="registration_in_progress",
        nse_statuses=[],
        client_codes=[],
    )
    db.add(attempt)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A registration attempt with this idempotency key already exists.",
        )

    try:
        nse_response = await _submit_ucc_to_nse(payload, db=db)
    except HTTPException as exc:
        attempt.status = "pending_confirmation"
        await db.commit()
        logger.warning(
            "Customer UCC submission outcome uncertain: uuid=<redacted> status=%s",
            exc.status_code,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail="NSE registration outcome is not confirmed; check onboarding status before retrying.",
        )

    records = nse_response.get("reg_details") or []
    statuses = [
        (record.get("reg_status") or "").strip().upper()
        for record in records
    ]
    successful_records = [
        record
        for record, reg_status in zip(records, statuses)
        if reg_status in _SUCCESS_STATUSES
    ]
    successful_codes = {
        (record.get("client_code") or "").strip()
        for record in successful_records
        if (record.get("client_code") or "").strip()
    }
    excluded_codes: set[str] = set()
    already_linked_codes: set[str] = set()
    existing_owner_codes: set[str] = set()
    linking_conflict = False

    for client_code in successful_codes:
        mapping_result = await db.execute(
            select(LendrixUserNseMapping).where(
                LendrixUserNseMapping.client_code == client_code,
                LendrixUserNseMapping.is_active.is_(True),
            )
        )
        existing_mapping = mapping_result.scalar_one_or_none()
        registration_result = await db.execute(
            select(UCCRegistration).where(UCCRegistration.client_code == client_code)
        )
        existing_registration = registration_result.scalar_one_or_none()

        if existing_mapping is not None and existing_mapping.lendrix_uuid != lendrix_uuid:
            linking_conflict = True
            excluded_codes.add(client_code)
        elif existing_registration is not None and existing_mapping is None:
            linking_conflict = True
            excluded_codes.add(client_code)
        elif existing_mapping is not None:
            existing_owner_codes.add(client_code)
            if existing_registration is not None:
                already_linked_codes.add(client_code)

    try:
        await _persist_ucc_response(
            nse_response,
            db,
            excluded_client_codes=excluded_codes,
            commit=False,
        )
        mapped_codes = successful_codes - excluded_codes - existing_owner_codes
        for client_code in mapped_codes:
            db.add(
                LendrixUserNseMapping(
                    lendrix_uuid=lendrix_uuid,
                    client_code=client_code,
                    is_active=True,
                    verification_method="nse_ucc_success",
                    verification_reference=attempt.id,
                )
            )

        attempt.client_codes = sorted(mapped_codes | already_linked_codes | existing_owner_codes)
        attempt.nse_statuses = statuses
        if linking_conflict:
            attempt.status = "linking_required"
        elif (
            records
            and len(successful_records) == len(records)
            and len(attempt.client_codes) == len(records)
        ):
            attempt.status = "registered"
        elif any(status_value not in _SUCCESS_STATUSES for status_value in statuses):
            attempt.status = "unknown"
        else:
            attempt.status = "pending_confirmation"

        await db.commit()
    except IntegrityError:
        await db.rollback()
        attempt_result = await db.execute(
            select(CustomerUccAttempt).where(CustomerUccAttempt.id == attempt.id)
        )
        stored_attempt = attempt_result.scalar_one()
        stored_attempt.status = "linking_required"
        await db.commit()
        logger.warning("Customer UCC mapping conflict: uuid=<redacted>")
        raise HTTPException(
            status_code=409,
            detail="Registration succeeded but account linking requires verification.",
        )
    except Exception as exc:
        await db.rollback()
        logger.error("Customer UCC persistence failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="NSE response received but local registration state could not be saved; do not retry.",
        )

    if linking_conflict:
        raise HTTPException(
            status_code=409,
            detail="Registration succeeded but account linking requires verification.",
        )
    return UCCRegistrationResponse(**nse_response)


@router.post("/order/entry", response_model=OrderEntryResponse)
async def customer_order_entry(
    payload: OrderEntryRequest,
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    client_code = resolve_client_code(sso_payload)
    items = inject_client_code_and_euin(
        [d.model_dump(exclude_none=True) for d in payload.transaction_details],
        client_code,
    )
    from schemas.order import OrderRequest
    enforced = OrderEntryRequest(
        transaction_details=[OrderRequest(**item) for item in items]
    )
    return await submit_order(enforced, db)


@router.post("/sip/register", response_model=SIPRegistrationResponse)
async def customer_sip_register(
    payload: SIPRegistrationRequest,
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    client_code = resolve_client_code(sso_payload)
    items = inject_client_code_and_euin(
        [d.model_dump(exclude_none=True) for d in payload.reg_data],
        client_code,
    )
    from schemas.sip import SIPDetail
    enforced = SIPRegistrationRequest(reg_data=[SIPDetail(**item) for item in items])
    return await submit_sip(enforced, db)


@router.post("/xsip/register", response_model=XSIPRegistrationResponse)
async def customer_xsip_register(
    payload: XSIPRegistrationRequest,
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    client_code = resolve_client_code(sso_payload)
    items = inject_client_code_and_euin(
        [d.model_dump(exclude_none=True) for d in payload.reg_data],
        client_code,
    )
    from schemas.xsip import XSIPDetail
    enforced = XSIPRegistrationRequest(reg_data=[XSIPDetail(**item) for item in items])
    return await submit_xsip(enforced, db)


def _parse_order_ids(order_ids: str) -> list[str]:
    parsed = [part.strip() for part in order_ids.split(",") if part.strip()]
    if not parsed or len(parsed) != len(set(parsed)):
        raise HTTPException(status_code=422, detail="order_ids must contain unique order IDs")
    return parsed


async def _assert_order_ids_authorized(
    order_ids: list[str],
    client_code: str,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(OrderTransaction.trxn_order_id).where(
            OrderTransaction.client_code == client_code,
            OrderTransaction.trxn_order_id.in_(order_ids),
        )
    )
    known_ids = set(result.scalars().all())
    if known_ids != set(order_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="One or more orders are not available to this customer account.",
        )


@router.post("/payments/purchase", response_model=PurchasePaymentResponse)
async def customer_purchase_payment(
    payload: PurchasePaymentRequest,
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    assert_client_code_authorized(sso_payload, payload.client_code)
    await _assert_order_ids_authorized(
        _parse_order_ids(payload.order_ids),
        payload.client_code,
        db,
    )
    return await submit_purchase_payment(payload)


@router.post("/payments/upi-status", response_model=UPIStatusCheckResponse)
async def customer_upi_status_check(
    payload: UPIStatusCheckRequest,
    sso_payload: dict = Depends(require_sso_auth),
):
    assert_client_code_authorized(sso_payload, payload.client_code)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Customer-scoped UPI status lookup is unavailable until the payment "
            "reference can be verified against a customer-owned payment record."
        ),
    )
