import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.client_code import get_distributor_euin
from auth.nse_auth import get_auth_headers
from auth.sso_dependencies import require_sso_auth
from config import settings
from database import get_db
from http_client import get_nse_client
from models.sso import CustomerUccAttempt
from routers.misc_reports import _download_master
from schemas.customer import CustomerRegistrationStatusResponse, MFOnboardingStatusResponse
from schemas.misc_reports import MasterDownloadRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customer", tags=["Customer"])


@router.get("/schemes")
async def customer_scheme_catalogue(
    _sso_payload: dict = Depends(require_sso_auth),
):
    """Return NSE's scheme-master file to an SSO customer without an investor mapping."""
    try:
        result = await _download_master(MasterDownloadRequest(file_type="SCH"))
    except HTTPException as exc:
        logger.warning("Customer scheme catalogue upstream failure: status=%s", exc.status_code)
        if exc.status_code == 504:
            raise HTTPException(status_code=504, detail="NSE scheme catalogue timed out")
        raise HTTPException(status_code=502, detail="NSE scheme catalogue is unavailable")
    except Exception as exc:
        logger.error("Customer scheme catalogue failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="NSE scheme catalogue is unavailable")

    if not isinstance(result, Response) or result.media_type != "text/plain":
        logger.warning("Customer scheme catalogue returned an unexpected response type")
        raise HTTPException(status_code=502, detail="NSE scheme catalogue returned an unsupported response")

    return Response(
        content=result.body,
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/registration/status",
    response_model=CustomerRegistrationStatusResponse,
)
async def customer_registration_status(
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    """Report mapping and latest registration-attempt state for the authenticated customer."""
    lendrix_uuid = sso_payload["sub"]
    client_codes = sso_payload["client_codes"]
    result = await db.execute(
        select(CustomerUccAttempt)
        .where(CustomerUccAttempt.lendrix_uuid == lendrix_uuid)
        .order_by(CustomerUccAttempt.created_at.desc())
        .limit(1)
    )
    latest_attempt = result.scalar_one_or_none()
    attempt_status = latest_attempt.status if latest_attempt is not None else None

    if client_codes:
        registration_status = "registered"
    elif attempt_status in {"registration_in_progress", "pending_confirmation"}:
        registration_status = "pending"
    elif attempt_status == "rejected":
        registration_status = "failed"
    elif attempt_status == "linking_required":
        registration_status = "linking_required"
    elif attempt_status is not None:
        registration_status = "unknown"
    else:
        registration_status = "unlinked"

    return CustomerRegistrationStatusResponse(
        registration_status=registration_status,
        client_codes=client_codes,
        attempt_status=attempt_status,
        euin=get_distributor_euin() if client_codes else None,
    )


# ── Onboarding status cache: {lendrix_uuid: (expires_at, response_dict)} ──────
_ONBOARDING_CACHE_TTL = 60
_onboarding_cache: dict[str, tuple[float, dict]] = {}

NSE_KYC_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/CLIENT_KYC_REPORT"
NSE_AOF_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/AOF_IMAGE_UPLOAD_REPORT"
NSE_ELOG_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/ELOG_UPLOAD_REPORT"
NSE_MANDATE_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/MANDATE_STATUS"
NSE_CLIENT_MASTER_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/client_master_report"


async def _nse_post(url: str, payload: dict) -> dict | None:
    """POST to NSE and return parsed JSON, or None on any error."""
    try:
        async with get_nse_client(30) as client:
            resp = await client.post(url, headers=get_auth_headers(), json=payload)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


async def _check_ucc(client_codes: list[str]) -> dict:
    if client_codes:
        return {"done": True, "status": "registered", "client_codes": client_codes}
    return {"done": False, "status": "unregistered", "client_codes": []}


async def _check_ekyc(client_code: str) -> dict:
    data = await _nse_post(NSE_KYC_URL, {"client_code": client_code})
    if data is None:
        return {"done": False, "status": "check_failed", "verified": False}
    records = data.get("report_data") or []
    for r in records:
        if str(r.get("kyc_status", "")).upper() == "SUCCESS" and \
                "KYC VALIDATED" in str(r.get("status_remark", "")).upper():
            return {"done": True, "status": "KYC VALIDATED", "verified": True}
    status = records[0].get("status_remark", "not_verified") if records else "not_verified"
    return {"done": False, "status": status, "verified": False}


async def _check_aof(client_code: str) -> dict:
    data = await _nse_post(NSE_AOF_REPORT_URL, {"client_code": client_code})
    if data is None:
        return {"done": False, "status": "check_failed", "records_found": 0}
    records = data.get("report_data") or []
    count = len(records)
    success_keywords = ("ready to invest", "uploaded", "success")
    done = any(
        any(kw in str(r.get("cams_status", "")).lower() or
            kw in str(r.get("kfin_status", "")).lower() for kw in success_keywords)
        for r in records
    )
    return {"done": done, "status": "submitted" if done else "not_submitted", "records_found": count}


async def _check_elog(client_code: str) -> dict:
    data = await _nse_post(NSE_ELOG_REPORT_URL, {"client_code": client_code})
    if data is None:
        return {"done": False, "status": "check_failed", "records_found": 0}
    records = data.get("report_data") or []
    count = len(records)
    success_keywords = ("ready to invest", "uploaded", "success")
    done = any(
        any(kw in str(r.get("cams_status", "")).lower() or
            kw in str(r.get("kfin_status", "")).lower() for kw in success_keywords)
        for r in records
    )
    return {"done": done, "status": "submitted" if done else "not_submitted", "records_found": count}


async def _check_mandate(client_code: str) -> dict:
    data = await _nse_post(NSE_MANDATE_REPORT_URL, {"client_code": client_code})
    if data is None:
        return {"done": False, "status": "check_failed", "mandate_count": 0}
    records = data.get("report_data") or []
    count = len(records)
    active_statuses = {"approved", "active"}
    done = any(str(r.get("status", "")).lower() in active_statuses for r in records)
    return {"done": done, "status": "active" if done else "not_registered", "mandate_count": count}


async def _check_nominees(client_code: str) -> dict:
    data = await _nse_post(NSE_CLIENT_MASTER_URL, {"client_code": client_code})
    if data is None:
        return {"done": False, "status": "check_failed", "nominee_count": 0}
    records = data.get("report_data") or []
    nominee_count = 0
    for r in records:
        for i in range(1, 4):
            if str(r.get(f"nominee_{i}_name", "")).strip():
                nominee_count += 1
    done = nominee_count > 0
    return {"done": done, "status": "added" if done else "not_added", "nominee_count": nominee_count}


@router.get("/onboarding/status", response_model=MFOnboardingStatusResponse)
async def mf_onboarding_status(
    sso_payload: dict = Depends(require_sso_auth),
):
    """Return real-time completion status for all 6 MF onboarding steps."""
    lendrix_uuid: str = sso_payload["sub"]
    client_codes: list[str] = sso_payload["client_codes"]

    cached = _onboarding_cache.get(lendrix_uuid)
    if cached and time.monotonic() < cached[0]:
        return cached[1]

    client_code = client_codes[0] if client_codes else ""

    async def _no_cc(fallback: dict) -> dict:
        return fallback

    ucc_result, ekyc_result, aof_result, elog_result, mandate_result, nominees_result = \
        await asyncio.gather(
            _check_ucc(client_codes),
            _check_ekyc(client_code) if client_code else _no_cc({"done": False, "status": "no_client_code", "verified": False}),
            _check_aof(client_code) if client_code else _no_cc({"done": False, "status": "no_client_code", "records_found": 0}),
            _check_elog(client_code) if client_code else _no_cc({"done": False, "status": "no_client_code", "records_found": 0}),
            _check_mandate(client_code) if client_code else _no_cc({"done": False, "status": "no_client_code", "mandate_count": 0}),
            _check_nominees(client_code) if client_code else _no_cc({"done": False, "status": "no_client_code", "nominee_count": 0}),
        )

    steps = {
        "ucc": ucc_result,
        "ekyc": ekyc_result,
        "aof": aof_result,
        "elog": elog_result,
        "mandate": mandate_result,
        "nominees": nominees_result,
    }
    response = {
        "authenticated": True,
        "onboarding_complete": all(s["done"] for s in steps.values()),
        "steps": steps,
    }

    _onboarding_cache[lendrix_uuid] = (time.monotonic() + _ONBOARDING_CACHE_TTL, response)
    return response
