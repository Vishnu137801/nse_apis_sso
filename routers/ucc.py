from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from http_client import get_nse_client, raise_for_nse_error, handle_nse_request_error
from datetime import datetime, timezone, timedelta
from jose import jwt
import logging

from database import get_db
from auth.nse_auth import get_auth_headers
from auth.dependencies import require_auth_payload
from auth.client_code import next_sequential_client_code
from config import settings
from models.ucc import UCCRegistration
from models.sso import LendrixUserNseMapping
from schemas.ucc import UCCRegistrationRequest, UCCRegistrationResponse, UCCDetail, UCCClientCodeSuggestion, UCCFetchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ucc", tags=["UCC Registration"])

NSE_UCC_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/registration/CLIENTCOMMON183"

_KNOWN_FIELDS = set(UCCDetail.model_fields.keys())
_INTEGER_FIELDS = {"nominee_1_applicable", "nominee_2_applicable", "nominee_3_applicable"}
_SUCCESS_STATUSES = {"REG_SUCCESS", "S"}


def _db_value(key, value):
    if value is None or not str(value).strip():
        return None
    if key in _INTEGER_FIELDS:
        return int(str(value).strip())
    return str(value).strip()


async def _submit_ucc_to_nse(payload: UCCRegistrationRequest, db: AsyncSession) -> dict:
    if len(payload.reg_details) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 records allowed per request")

    reg_details_out = []
    for r in payload.reg_details:
        item = {k: v for k, v in r.model_dump().items() if k in _KNOWN_FIELDS}
        # Auto-generate sequential LVMF##### code if frontend did not send one
        if not item.get("client_code"):
            item["client_code"] = await next_sequential_client_code(db)
            logger.info("Auto-generated sequential client_code=%s", item["client_code"])
        reg_details_out.append(item)

    nse_payload = {"reg_details": reg_details_out}
    logger.info("Sending UCC registration to NSE: %s records", len(nse_payload["reg_details"]))

    try:
        async with get_nse_client(30) as client:
            response = await client.post(
                NSE_UCC_URL,
                headers=get_auth_headers(),
                json=nse_payload
            )
    except Exception as e:
        handle_nse_request_error(e)

    logger.info("NSE UCC response status: %s", response.status_code)
    raise_for_nse_error(response)

    try:
        nse_response = response.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"NSE returned invalid JSON: {response.text[:200]}")

    return nse_response


async def _persist_ucc_response(
    nse_response: dict,
    db: AsyncSession,
    excluded_client_codes: set[str] | None = None,
    commit: bool = True,
) -> tuple[int, int]:
    reg_details = nse_response.get("reg_details", [])
    model_columns = {c.name for c in UCCRegistration.__table__.columns}

    excluded_client_codes = excluded_client_codes or set()
    saved, failed = 0, []

    try:
        for record in reg_details:
            client_code = (record.get("client_code") or "").strip()
            reg_status = (record.get("reg_status") or "").strip().upper()

            if reg_status not in _SUCCESS_STATUSES:
                logger.warning("UCC record not saved — reg_status=%s", reg_status)
                failed.append({"reg_status": reg_status, "reg_remark": record.get("reg_remark")})
                continue

            if client_code in excluded_client_codes:
                continue

            db_data = {k: _db_value(k, v) for k, v in record.items() if k in model_columns}

            if not db_data.get("client_code"):
                logger.warning("Skipped DB insert — client_code missing in NSE response record")
                continue

            existing = await db.execute(select(UCCRegistration).where(UCCRegistration.client_code == client_code))
            existing_record = existing.scalar_one_or_none()

            if existing_record:
                for key, value in db_data.items():
                    setattr(existing_record, key, value)
                logger.info("Updated UCC record: client_code=<redacted>")
            else:
                db.add(UCCRegistration(**db_data))
                saved += 1
                logger.info("Inserted UCC record: client_code=<redacted>")

        if commit:
            await db.commit()
            logger.info("DB commit done. saved=%s failed=%s", saved, len(failed))
    except Exception as e:
        await db.rollback()
        logger.error("DB error: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="DB save failed")

    return saved, len(failed)


@router.get("/suggest-client-code", response_model=UCCClientCodeSuggestion)
async def suggest_client_code(
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(require_auth_payload),
):
    """
    Called when the KYC form loads.
    Returns the next sequential LVMF##### code so the frontend can show it to the user.
    If already registered, returns their existing code with already_registered=true.
    """
    lendrix_uuid = token_payload.get("lendrix_uuid")
    if not lendrix_uuid:
        raise HTTPException(status_code=400, detail="Token missing lendrix_uuid")

    # Already registered — return existing code
    existing = await db.execute(
        select(UCCRegistration).where(UCCRegistration.lendrix_uuid == lendrix_uuid)
    )
    existing_record = existing.scalar_one_or_none()
    if existing_record:
        return UCCClientCodeSuggestion(
            client_code=existing_record.client_code,
            lendrix_uuid=lendrix_uuid,
            already_registered=True,
        )

    # Not yet registered — suggest next sequential code
    suggested_code = await next_sequential_client_code(db)
    return UCCClientCodeSuggestion(
        client_code=suggested_code,
        lendrix_uuid=lendrix_uuid,
        already_registered=False,
    )


@router.post("/register", response_model=UCCRegistrationResponse)
async def register_ucc(
    payload: UCCRegistrationRequest,
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(require_auth_payload),
):
    nse_response = await _submit_ucc_to_nse(payload, db=db)
    _, failed_count = await _persist_ucc_response(nse_response, db, commit=False)

    lendrix_uuid = token_payload.get("lendrix_uuid")
    if lendrix_uuid:
        for record in nse_response.get("reg_details", []):
            client_code = (record.get("client_code") or "").strip()
            reg_status = (record.get("reg_status") or "").strip().upper()
            if not client_code or reg_status not in _SUCCESS_STATUSES:
                continue

            existing_mapping = await db.execute(
                select(LendrixUserNseMapping).where(
                    LendrixUserNseMapping.lendrix_uuid == lendrix_uuid,
                    LendrixUserNseMapping.client_code == client_code,
                )
            )
            if not existing_mapping.scalar_one_or_none():
                db.add(LendrixUserNseMapping(
                    lendrix_uuid=lendrix_uuid,
                    client_code=client_code,
                    is_active=True,
                    verification_method="ucc_registration",
                ))
                logger.info("Auto-linked client_code=<redacted> to lendrix_uuid=<redacted>")

            ucc_row = await db.execute(
                select(UCCRegistration).where(UCCRegistration.client_code == client_code)
            )
            ucc_record = ucc_row.scalar_one_or_none()
            if ucc_record and not ucc_record.lendrix_uuid:
                ucc_record.lendrix_uuid = lendrix_uuid

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("DB commit failed after UCC registration: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="DB save failed")

    if failed_count:
        logger.warning("UCC partial failure: %s records failed", failed_count)

    # Mint fresh nse_token with new client_codes so frontend doesn't need to re-login
    fresh_token = None
    if lendrix_uuid:
        all_codes_result = await db.execute(
            select(LendrixUserNseMapping.client_code).where(
                LendrixUserNseMapping.lendrix_uuid == lendrix_uuid,
                LendrixUserNseMapping.is_active.is_(True),
            )
        )
        all_client_codes = [row[0] for row in all_codes_result.fetchall()]
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
        fresh_token = jwt.encode(
            {
                "sub": lendrix_uuid,
                "lendrix_uuid": lendrix_uuid,
                "client_codes": all_client_codes,
                "exp": expire,
            },
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        logger.info("Minted fresh nse_token after UCC registration with %s client_code(s)", len(all_client_codes))

    response = UCCRegistrationResponse(**nse_response)
    response.nse_token = fresh_token
    return response


@router.get("/fetch/{client_code}", response_model=UCCFetchResponse)
async def fetch_ucc(
    client_code: str,
    db: AsyncSession = Depends(get_db),
    token_payload: dict = Depends(require_auth_payload),
):
    """Fetch UCC registration details — only for client_codes owned by the token."""
    from auth.dependencies import assert_token_owns_client_code
    assert_token_owns_client_code(token_payload, client_code)

    # Ownership already verified above — look up by client_code only.
    # lendrix_uuid may be NULL for legacy rows registered before the column was added.
    result = await db.execute(
        select(UCCRegistration).where(UCCRegistration.client_code == client_code)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"No UCC record found for client_code: {client_code}")

    columns = {c.name for c in UCCRegistration.__table__.columns} - {"id", "created_at", "updated_at", "lendrix_uuid"}
    return UCCFetchResponse(**{col: getattr(record, col) for col in columns})
