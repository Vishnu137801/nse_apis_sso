from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from http_client import get_nse_client
import logging

from database import get_db
from auth.nse_auth import get_auth_headers
from auth.dependencies import require_auth_payload, assert_token_owns_client_code
from config import settings
from models.stp_cancellation import STPCancellation
from schemas.stp_cancellation import STPCancellationRequest, STPCancellationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stp-cancellation", tags=["STP Cancellation"])

NSE_STP_CAN_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/cancellation/STP_CAN"


@router.post("/cancel", response_model=STPCancellationResponse)
async def cancel_stp(payload: STPCancellationRequest, db: AsyncSession = Depends(get_db), token_payload: dict = Depends(require_auth_payload)):
    if len(payload.can_data) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 STPs allowed per request")

    auto_code = (token_payload.get("client_codes") or [None])[0]
    for item in payload.can_data:
        if not item.client_code:
            if not auto_code:
                raise HTTPException(status_code=422, detail="client_code is required")
            item.client_code = auto_code
        assert_token_owns_client_code(token_payload, item.client_code)

    nse_payload = {
        "can_data": [
            {k: v for k, v in d.model_dump(exclude_none=True).items() if v != ""}
            for d in payload.can_data
        ]
    }

    logger.info(f"Sending to NSE URL: {NSE_STP_CAN_URL}")

    try:
        async with get_nse_client(60) as client:
            response = await client.post(
                NSE_STP_CAN_URL,
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

    # NSE returns reg_data key in response (not can_data)
    can_data = nse_response.get("reg_data", [])
    model_columns = {c.name for c in STPCancellation.__table__.columns}
    lendrix_uuid = token_payload.get("lendrix_uuid")

    try:
        for record in can_data:
            db_data = {
                k: str(v).strip() if v is not None and str(v).strip() else None
                for k, v in record.items()
                if k in model_columns
            }
            if lendrix_uuid:
                db_data["lendrix_uuid"] = lendrix_uuid
            db.add(STPCancellation(**db_data))

        await db.commit()
        logger.info(f"Saved {len(can_data)} STP cancellation(s) to DB")
    except Exception as e:
        await db.rollback()
        logger.error(f"DB error: {e}")
        raise HTTPException(status_code=500, detail=f"DB save failed: {e}")

    return nse_response
