"""
SSO authentication dependency.

require_sso_auth() is used on customer-facing SSO endpoints only.
It is SEPARATE from require_auth() which guards the shared application Bearer token.

The two dependency functions must never be treated as equivalent:
  - require_auth()     → shared app token, full NSE API access, no per-user scope
  - require_sso_auth() → customer SSO cookie, scoped to the user's own client_codes
"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from config import settings
from sqlalchemy import select
from models.sso import LendrixUserNseMapping
from models.ucc import UCCRegistration
from auth.sso_session import verify_csrf_token
from auth.sso_session import decode_sso_token, get_sso_token_from_request

_UCC_SUCCESS_STATUSES = {"REG_SUCCESS", "S"}


async def _fetch_verified_client_codes(lendrix_uuid: str, db: AsyncSession) -> list[str]:
    """
    Returns only client_codes that are:
      1. actively mapped to this lendrix_uuid in lendrix_user_nse_mapping
      2. have a successful UCC registration in ucc_registrations
    """
    result = await db.execute(
        select(LendrixUserNseMapping.client_code)
        .join(
            UCCRegistration,
            UCCRegistration.client_code == LendrixUserNseMapping.client_code,
        )
        .where(
            LendrixUserNseMapping.lendrix_uuid == lendrix_uuid,
            LendrixUserNseMapping.is_active.is_(True),
            UCCRegistration.reg_status.in_(_UCC_SUCCESS_STATUSES),
        )
    )
    return [row[0] for row in result.fetchall()]


async def require_sso_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Validates the customer identity via either:
      1. SSO cookie (HttpOnly, set by /auth/sso/exchange or auto-login)
      2. nse_token Bearer header (minted by /auth/login, signed with JWT_SECRET)
    Returns a normalised payload with sub + client_codes.
    """
    from jose import JWTError, jwt as jose_jwt

    # ── Try SSO cookie first ───────────────────────────────────────────────────
    sso_cookie = get_sso_token_from_request(request)
    if sso_cookie:
        require_sso_enabled()
        payload = await decode_sso_token(sso_cookie, db)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            verify_csrf_token(request)
        payload["client_codes"] = await _fetch_verified_client_codes(payload["sub"], db)
        return payload

    # ── Fall back to nse_token Bearer header ──────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = jose_jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )
        lendrix_uuid = payload.get("sub")
        if not lendrix_uuid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject",
            )
        # Always re-query live client_codes from DB — only UCC-verified codes
        payload["client_codes"] = await _fetch_verified_client_codes(lendrix_uuid, db)
        return payload

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No SSO session. Please log in via Lendrix.",
    )


def require_sso_enabled():
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=404, detail="SSO is disabled")



def assert_client_code_authorized(payload: dict, client_code: str) -> None:
    """
    Verify that the SSO session's client_codes list includes the requested client_code.
    Call this inside any SSO-protected business endpoint before forwarding to NSE.
    """
    authorized: list = payload.get("client_codes", [])
    if client_code not in authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this investor account",
        )
