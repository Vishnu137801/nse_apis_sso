from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from http_client import get_nse_client
from datetime import datetime, timedelta
import logging

from database import get_db
from auth.nse_auth import get_auth_headers
from auth.dependencies import require_auth_payload, assert_token_owns_client_code
from config import settings
from models.sip import SIPRegistration
from schemas.sip import SIPRegistrationRequest, SIPRegistrationResponse, SIPDetail, SIPRegistrationResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip", tags=["SIP Registration"])

NSE_SIP_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/registration/product/SIP"

_STEP_UP_FREQ_MONTHS = {"MONTHLY": 1, "QUARTERLY": 3, "SEMI-ANNUAL": 6, "ANNUAL": 12}
_FREQ_MONTHS = {"DAILY": 0, "WEEKLY": 0, "MONTHLY": 1, "QUARTERLY": 3, "SEMI-ANNUAL": 6, "ANNUAL": 12}


def _parse_date(d: str) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(d.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _validate_sip_item(item: SIPDetail) -> list[str]:
    errors = []
    freq = (item.frequency_type or "").upper()

    # status is mandatory
    if not item.status:
        errors.append("status is required")

    # start_date must be provided and day must be 1-28
    start_dt = _parse_date(item.start_date or "")
    if not start_dt:
        errors.append("start_date is required in DD/MM/YYYY format")
    else:
        if start_dt.day > 28:
            errors.append(f"start_date day must be between 1-28, got {start_dt.day}")
        if start_dt <= datetime.now() + timedelta(days=8):
            errors.append(f"start_date must be at least 9 days from today (use {(datetime.now() + timedelta(days=9)).strftime('%d/%m/%Y')} or later)")

    # end_date only allowed for DAILY SIP
    if item.end_date and item.end_date.strip() and freq != "DAILY":
        errors.append("end_date is only allowed for DAILY frequency SIP, remove it for MONTHLY/QUARTERLY")

    # step_up validation
    if (item.step_up_required or "").upper() == "Y":
        if not item.step_up_amount or not item.step_up_amount.strip():
            errors.append("step_up_amount is required when step_up_required is Y")
        if not item.step_up_frequency or not item.step_up_frequency.strip():
            errors.append("step_up_frequency is required when step_up_required is Y")

        step_freq = (item.step_up_frequency or "").upper()
        step_freq_months = _STEP_UP_FREQ_MONTHS.get(step_freq, 0)
        sip_freq_months = _FREQ_MONTHS.get(freq, 1)
        installment_no = int(item.installment_no or 0)
        sip_duration_months = installment_no * sip_freq_months if sip_freq_months else 0

        step_start_dt = _parse_date(item.step_up_start_date or "")
        step_end_dt = _parse_date(item.step_up_end_date or "")

        if not step_start_dt:
            errors.append("step_up_start_date is required in DD/MM/YYYY format when step_up_required is Y")
        elif start_dt:
            # step_up_start_date must be exactly N months after start_date (where N = step_up_frequency months)
            expected_step_start = start_dt + timedelta(days=step_freq_months * 30)
            diff_days = abs((step_start_dt - expected_step_start).days)
            if diff_days > 5:  # allow 5 days tolerance
                errors.append(
                    f"step_up_start_date must be ~{step_freq_months} months after start_date. "
                    f"Expected around {expected_step_start.strftime('%d/%m/%Y')}, got {step_start_dt.strftime('%d/%m/%Y')}"
                )

        if step_end_dt and start_dt and sip_duration_months:
            sip_end_dt = start_dt + timedelta(days=sip_duration_months * 30)
            if step_end_dt >= sip_end_dt:
                errors.append(
                    f"step_up_end_date ({step_end_dt.strftime('%d/%m/%Y')}) must be before SIP end "
                    f"({sip_end_dt.strftime('%d/%m/%Y')}). Increase installment_no or reduce step_up_end_date"
                )
            if step_start_dt and step_end_dt <= step_start_dt:
                errors.append("step_up_end_date must be after step_up_start_date")

        # need at least 2 step-up cycles within SIP duration
        if sip_duration_months and step_freq_months:
            if sip_duration_months < step_freq_months * 2:
                errors.append(
                    f"SIP duration ({sip_duration_months} months) too short for SEMI-ANNUAL step-up. "
                    f"Use installment_no >= {step_freq_months * 2 // sip_freq_months} for at least 2 step-up cycles"
                )

    return errors


@router.post("/register", response_model=SIPRegistrationResponse)
async def sip_register(payload: SIPRegistrationRequest, db: AsyncSession = Depends(get_db), token_payload: dict = Depends(require_auth_payload)):
    if len(payload.reg_data) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 orders allowed per request")

    auto_code = (token_payload.get("client_codes") or [None])[0]
    for item in payload.reg_data:
        if not item.client_code:
            item.client_code = auto_code
        if item.client_code:
            assert_token_owns_client_code(token_payload, item.client_code)

    # Pre-validate all items before sending to NSE
    all_errors = {}
    for i, item in enumerate(payload.reg_data):
        errs = _validate_sip_item(item)
        if errs:
            all_errors[f"reg_data[{i}]"] = errs
    if all_errors:
        raise HTTPException(status_code=422, detail=all_errors)

    nse_payload = {
        "reg_data": [
            {k: v for k, v in d.model_dump(exclude_none=True).items() if v != ""}
            for d in payload.reg_data
        ]
    }

    logger.info(f"Sending to NSE URL: {NSE_SIP_URL}")

    try:
        async with get_nse_client(60) as client:
            response = await client.post(
                NSE_SIP_URL,
                headers=get_auth_headers(),
                json=nse_payload
            )
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to NSE: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="NSE request timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NSE request failed: {e}")

    logger.info(f"NSE Response status: {response.status_code}")
    logger.info(f"NSE Response body: {response.text}")

    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="NSE authentication failed")

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    try:
        nse_response = response.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"NSE returned invalid JSON: {response.text[:500]}")

    reg_data = nse_response.get("reg_data", [])
    model_columns = {c.name for c in SIPRegistration.__table__.columns}
    lendrix_uuid = token_payload.get("lendrix_uuid")

    try:
        for record in reg_data:
            db_data = {
                k: str(v).strip() if v is not None and str(v).strip() else None
                for k, v in record.items()
                if k in model_columns
            }
            if lendrix_uuid:
                db_data["lendrix_uuid"] = lendrix_uuid
            db.add(SIPRegistration(**db_data))

        await db.commit()
        logger.info(f"Saved {len(reg_data)} SIP registration(s) to DB")
    except Exception as e:
        await db.rollback()
        logger.error(f"DB error: {e}")
        raise HTTPException(status_code=500, detail=f"DB save failed: {e}")

    return nse_response


@router.get("/registrations/{client_code}", response_model=SIPRegistrationResponse)
async def get_sip_registrations(client_code: str, db: AsyncSession = Depends(get_db), token_payload: dict = Depends(require_auth_payload)):
    assert_token_owns_client_code(token_payload, client_code)

    result = await db.execute(
        select(SIPRegistration).where(SIPRegistration.client_code == client_code)
    )
    records = result.scalars().all()

    if not records:
        raise HTTPException(status_code=404, detail=f"No SIP registrations found for client_code: {client_code}")

    reg_data = [
        SIPRegistrationResult(
            amc_code=r.amc_code,
            sch_code=r.sch_code,
            client_code=r.client_code,
            internal_ref_no=r.internal_ref_no,
            trans_mode=r.trans_mode,
            dp_txn_mode=r.dp_txn_mode,
            start_date=r.start_date,
            frequency_type=r.frequency_type,
            frequency_allowed=r.frequency_allowed,
            installment_amount=r.installment_amount,
            folio_no=r.folio_no,
            sip_remarks=r.sip_remarks,
            installment_no=r.installment_no,
            sip_mandate_id=r.sip_mandate_id,
            sub_broker_code=r.sub_broker_code,
            euin_number=r.euin_number,
            euin_declaration=r.euin_declaration,
            dpc_flag=r.dpc_flag,
            first_order_today=r.first_order_today,
            sub_broker_arn=r.sub_broker_arn,
            end_date=r.end_date,
            primary_holder_mobile=r.primary_holder_mobile,
            primary_holder_email=r.primary_holder_email,
            step_up_required=r.step_up_required,
            step_up_start_date=r.step_up_start_date,
            step_up_end_date=r.step_up_end_date,
            step_up_frequency=r.step_up_frequency,
            step_up_amount=r.step_up_amount,
            goal_based_sip=r.goal_based_sip,
            goal_type=r.goal_type,
            goal_name=r.goal_name,
            goal_amount=r.goal_amount,
            filler_1=r.filler_1,
            filler_2=r.filler_2,
            filler_3=r.filler_3,
            filler_4=r.filler_4,
            filler_5=r.filler_5,
            reg_id=r.reg_id,
            reg_status=r.reg_status,
            reg_remark=r.reg_remark,
            member_unique_id=r.member_unique_id,
        )
        for r in records
    ]

    return SIPRegistrationResponse(reg_data=reg_data)
