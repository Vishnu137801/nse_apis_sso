"""
SSO router — Lendrix → NSE FastAPI single sign-on.

Endpoints:
  POST /api/v1/auth/sso/exchange  — redeem one-time code, establish session
  GET  /api/v1/auth/sso/me        — return current session info
  POST /api/v1/auth/sso/logout    — revoke session, clear cookie

Security model:
  1. The browser never supplies a Lendrix UUID directly.
  2. The one-time code is redeemed server-to-server with a shared secret.
  3. The SSO JWT is stored in an HttpOnly Secure cookie — never in localStorage.
  4. State-changing endpoints require a CSRF double-submit token.
  5. client_code ownership is enforced from the JWT, not from the request body.
  6. NSE exchange credentials are never sent to the browser.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.sso_dependencies import require_sso_auth, require_sso_enabled
from auth.sso_session import (
    create_sso_session,
    decode_sso_token,
    hash_sso_jti,
    csrf_token_for_session, get_sso_token_from_request, verify_sso_origin,
    set_sso_cookie,
    clear_sso_cookie,
    verify_csrf_token,
    revoke_sso_session,
)
from config import settings
from database import get_db
from models.sso import LendrixUserNseMapping, NseSsoSession
from schemas.sso import (
    SsoExchangeRequest,
    SsoExchangeResponse,
    SsoMeResponse,
    SsoLogoutResponse,
    LendrixVerifyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/sso", tags=["SSO"], dependencies=[Depends(require_sso_enabled)])

_VERIFY_TIMEOUT_SECONDS = 10


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _redeem_code_with_lendrix(code: str) -> str:
    """
    Server-to-server call to the Lendrix Node.js verify endpoint.
    Returns the verified lendrix_uuid string.
    Raises HTTPException on any failure — never propagates raw upstream errors.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.LENDRIX_SSO_SERVER_SECRET}",
    }
    body = {"code": code}

    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            resp = await client.post(settings.LENDRIX_SSO_VERIFY_URL, json=body, headers=headers)
    except httpx.TimeoutException:
        logger.error("SSO verify: timeout contacting Lendrix")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")
    except httpx.ConnectError:
        logger.error("SSO verify: connection error contacting Lendrix")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")
    except httpx.RequestError:
        logger.error("SSO verify: unexpected error")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")

    if resp.status_code == 401:
        # Wrong server secret — configuration error, not a user error
        logger.error("SSO verify: Lendrix rejected server secret (401)")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")

    if resp.status_code == 400:
        # Invalid, expired, or already-used code
        logger.warning("SSO verify: Lendrix rejected code (400)")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired authorization code")

    if resp.status_code == 403:
        logger.warning("SSO verify: Lendrix rejected ineligible customer (403)")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lendrix account is not eligible for SSO")

    if resp.status_code == 429:
        logger.warning("SSO verify: Lendrix rate limit reached (429)")
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="SSO service rate limit exceeded")

    if resp.status_code != 200:
        logger.error("SSO verify: unexpected status %s from Lendrix", resp.status_code)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")

    try:
        data = resp.json()
    except ValueError:
        logger.error("SSO verify: Lendrix returned non-JSON body")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")

    try:
        verified = LendrixVerifyResponse.model_validate(data)
    except ValidationError:
        logger.error("SSO verify: Lendrix response failed schema validation")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO service unavailable")

    return verified.customer.lendrix_uuid


async def _get_active_client_codes(lendrix_uuid: str, db: AsyncSession) -> list[str]:
    """Return all active NSE client codes mapped to this Lendrix UUID."""
    result = await db.execute(
        select(LendrixUserNseMapping.client_code)
        .where(
            LendrixUserNseMapping.lendrix_uuid == lendrix_uuid,
            LendrixUserNseMapping.is_active.is_(True),
        )
    )
    return [row[0] for row in result.fetchall()]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/exchange", response_model=SsoExchangeResponse)
async def sso_exchange(
    payload: SsoExchangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Redeem a one-time Lendrix authorization code and establish an NSE SSO session.

    Flow:
      1. Validate code format (Pydantic).
      2. Call Lendrix Node.js verify endpoint server-to-server.
      3. Receive verified lendrix_uuid.
      4. Look up authorized NSE client codes from mapping table.
      5. Mint a customer-specific SSO JWT and persist hashed session metadata.
      6. Set JWT as HttpOnly Secure cookie only after the database commit succeeds.
      7. Return safe session summary (no secrets, no raw JWT in body).
    """
    verify_sso_origin(request)
    # Step 2-3: redeem with Lendrix
    lendrix_uuid = await _redeem_code_with_lendrix(payload.code)

    # Step 4: load investor mapping
    client_codes = await _get_active_client_codes(lendrix_uuid, db)
    investor_linked = len(client_codes) > 0

    # Reuse an active session only when it belongs to the identity just verified.
    token = get_sso_token_from_request(request)
    if token:
        try:
            existing_session = await decode_sso_token(token, db)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_401_UNAUTHORIZED:
                raise
            token = None
        else:
            if existing_session["sub"] != lendrix_uuid:
                token = None

    if token is None:
        # Persist the new session before issuing the browser cookie.
        session = create_sso_session(lendrix_uuid, client_codes)
        db.add(NseSsoSession(
            id=str(uuid.uuid4()),
            lendrix_user_uuid=lendrix_uuid,
            jwt_jti_hash=hash_sso_jti(session.jti),
            expires_at=session.expires_at,
        ))
        await db.commit()
        token = session.token

    # Step 6: build response with cookie
    response_body = SsoExchangeResponse(
        investor_linked=investor_linked,
        linking_required=not investor_linked,
        csrf_token=csrf_token_for_session(token),
        client_codes=client_codes,
    )
    response = JSONResponse(content=response_body.model_dump(), headers={"Cache-Control": "no-store"})
    set_sso_cookie(response, token)

    logger.info("SSO exchange: session established for uuid=<redacted> investor_linked=%s", investor_linked)
    return response


@router.get("/me", response_model=SsoMeResponse)
async def sso_me(
    request: Request,
    response: Response,
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the current SSO session's investor information.
    Re-queries the mapping table so revoked mappings are reflected immediately.
    Never returns NSE credentials or secrets.
    """
    lendrix_uuid: str = sso_payload["sub"]
    client_codes = await _get_active_client_codes(lendrix_uuid, db)

    response.headers["Cache-Control"] = "no-store"
    return SsoMeResponse(
        linking_required=not client_codes,
        csrf_token=csrf_token_for_session(get_sso_token_from_request(request)),
        lendrix_uuid=lendrix_uuid,
        investor_linked=len(client_codes) > 0,
        client_codes=client_codes,
    )


@router.post("/logout", response_model=SsoLogoutResponse)
async def sso_logout(
    request: Request,
    sso_payload: dict = Depends(require_sso_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke the current SSO session and clear the cookie.
    CSRF double-submit check is enforced.
    """
    verify_csrf_token(request)

    jti: str = sso_payload["jti"]
    lendrix_uuid: str = sso_payload["sub"]
    exp_ts: int = sso_payload["exp"]
    expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

    await revoke_sso_session(jti, lendrix_uuid, expires_at, db)

    response = JSONResponse(content=SsoLogoutResponse().model_dump(), headers={"Cache-Control": "no-store"})
    clear_sso_cookie(response)

    logger.info("SSO logout: session revoked for uuid=<redacted>")
    return response
