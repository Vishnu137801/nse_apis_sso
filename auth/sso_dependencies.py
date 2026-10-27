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
from auth.sso_session import verify_csrf_token
from auth.sso_session import decode_sso_token, get_sso_token_from_request


async def require_sso_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Validates the customer SSO session cookie.
    Returns the decoded JWT payload containing:
      sub          : lendrix_uuid
      client_codes : list of authorized NSE client codes
      jti          : session identifier (used for revocation)
      exp          : expiry timestamp
    """
    require_sso_enabled()
    token = get_sso_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No SSO session. Please log in via Lendrix.",
        )
    payload = await decode_sso_token(token, db)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        verify_csrf_token(request)
    result = await db.execute(select(LendrixUserNseMapping.client_code).where(
        LendrixUserNseMapping.lendrix_uuid == payload["sub"],
        LendrixUserNseMapping.is_active.is_(True)))
    payload["client_codes"] = [row[0] for row in result.fetchall()]
    return payload


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
