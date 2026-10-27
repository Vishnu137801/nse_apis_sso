import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.sso_dependencies import require_sso_auth
from database import get_db
from models.sso import CustomerUccAttempt
from routers.misc_reports import _download_master
from schemas.customer import CustomerRegistrationStatusResponse
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
    )
