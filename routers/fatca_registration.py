from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from http_client import get_nse_client
import logging

from database import get_db
from auth.nse_auth import get_auth_headers
from auth.dependencies import require_auth_payload, assert_token_owns_client_code
from config import settings
from models.fatca_registration import FATCARegistration
from models.ucc import UCCRegistration
from schemas.fatca_registration import FATCAUploadRequest, FATCAUploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fatca", tags=["FATCA"])

NSE_FATCA_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/registration/FATCA"


@router.post("/upload", response_model=FATCAUploadResponse)
async def fatca_upload(
    payload: FATCAUploadRequest,
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(require_auth_payload),
):
    if len(payload.reg_details) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 FATCA records allowed per request")

    # Verify each PAN belongs to the token's user via ucc_registrations
    lendrix_uuid = token_payload.get("lendrix_uuid")
    if not lendrix_uuid:
        raise HTTPException(status_code=403, detail="Token missing lendrix_uuid")
    owned_pans_result = await db.execute(
        select(UCCRegistration.primary_holder_pan).where(
            UCCRegistration.lendrix_uuid == lendrix_uuid
        )
    )
    owned_pans = {row[0] for row in owned_pans_result.fetchall() if row[0]}
    for item in payload.reg_details:
        if item.pan_rp and item.pan_rp not in owned_pans:
            raise HTTPException(status_code=403, detail="Token is not authorized for this PAN")

    nse_payload = {
        "reg_details": [
            {k: v for k, v in d.model_dump(exclude_none=True).items() if v != ""}
            for d in payload.reg_details
        ]
    }

    logger.info(f"Sending to NSE URL: {NSE_FATCA_URL}")

    try:
        async with get_nse_client(60) as client:
            response = await client.post(
                NSE_FATCA_URL,
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
    logger.info(f"NSE Response body: {response.text[:500]}")

    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="NSE authentication failed")

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    try:
        nse_response = response.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"NSE returned invalid JSON: {response.text[:500]}")

    reg_details = nse_response.get("reg_details", [])
    model_columns = {c.name for c in FATCARegistration.__table__.columns}
    lendrix_uuid = token_payload.get("lendrix_uuid")

    try:
        for record in reg_details:
            db_data = {
                k: str(v).strip() if v is not None and str(v).strip() else None
                for k, v in record.items()
                if k in model_columns
            }
            if lendrix_uuid:
                db_data["lendrix_uuid"] = lendrix_uuid
            db.add(FATCARegistration(**db_data))

        await db.commit()
        logger.info(f"Saved {len(reg_details)} FATCA registration(s) to DB")
    except Exception as e:
        await db.rollback()
        logger.error(f"DB error: {e}")
        raise HTTPException(status_code=500, detail=f"DB save failed: {e}")

    return nse_response
