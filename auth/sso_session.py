"""
SSO session utilities.

Handles customer-specific JWT minting and verification.
These tokens are SEPARATE from the shared application Bearer token
issued by POST /api/v1/auth/login.

Token type claim distinguishes the two:
  - Shared app token : typ = "app_bearer"
  - Customer SSO     : typ = "customer_sso"
"""
import uuid
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from config import settings
from models.sso import NseSsoSession, SsoSessionRevocation

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_TYPE = "customer_sso"
_ISSUER = "nse-fastapi"
_AUDIENCE = "mutualfund.lendrixventech.com"


@dataclass(frozen=True)
class CreatedSsoSession:
    token: str
    jti: str
    expires_at: datetime


# ── Token minting ─────────────────────────────────────────────────────────────

def create_sso_session(lendrix_uuid: str, client_codes: List[str]) -> CreatedSsoSession:
    """
    Mint a customer-specific JWT and return the metadata required for persistence.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.SSO_SESSION_EXPIRE_MINUTES)
    jti = str(uuid.uuid4())

    payload = {
        "sub": lendrix_uuid,
        "typ": _TOKEN_TYPE,
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": expire,
        "jti": jti,
        "client_codes": client_codes,
    }
    return CreatedSsoSession(
        token=jwt.encode(payload, settings.SSO_JWT_SECRET, algorithm=_ALGORITHM),
        jti=jti,
        expires_at=expire,
    )


def create_sso_token(lendrix_uuid: str, client_codes: List[str]) -> str:
    """Mint a customer-specific SSO JWT without exposing its persistence metadata."""
    return create_sso_session(lendrix_uuid, client_codes).token


def hash_sso_jti(jti: str) -> str:
    """Hash the JWT identifier before storing it in PostgreSQL."""
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


# ── Token verification ────────────────────────────────────────────────────────

async def decode_sso_token(token: str, db: AsyncSession) -> dict:
    """
    Decode and fully validate a customer SSO JWT.
    Raises HTTP 401 on any failure — never leaks reason details to the caller.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SSO_JWT_SECRET,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
            issuer=_ISSUER,
            options={"require_exp": True, "require_iat": True, "require_sub": True,
                     "require_jti": True, "require_aud": True, "require_iss": True},
        )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired SSO session")

    if payload.get("typ") != _TOKEN_TYPE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired SSO session")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired SSO session")

    try:
        uuid.UUID(payload["sub"])
        uuid.UUID(jti)
        if type(payload["iat"]) is not int or type(payload["exp"]) is not int:
            raise ValueError()
        if payload["iat"] > datetime.now(timezone.utc).timestamp() or payload["exp"] <= payload["iat"]:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired SSO session")

    # A JWT is valid only while its matching persistent session is active.
    result = await db.execute(
        select(NseSsoSession).where(NseSsoSession.jwt_jti_hash == hash_sso_jti(jti))
    )
    session = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        session is None
        or session.lendrix_user_uuid != payload["sub"]
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired SSO session")

    # Preserve revocation records created by the previous session implementation.
    result = await db.execute(
        select(SsoSessionRevocation).where(SsoSessionRevocation.jti == jti)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has been revoked")

    return payload


# ── Cookie helpers ────────────────────────────────────────────────────────────

def set_sso_cookie(response, token: str) -> None:
    """Attach the SSO JWT as a Secure HttpOnly SameSite=Lax cookie."""
    response.set_cookie(
        key=settings.SSO_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",   # same-site HTTPS subdomains: NSE backend ↔ MF frontend on different subdomains
        max_age=settings.SSO_SESSION_EXPIRE_MINUTES * 60,
        path="/",
    )


def clear_sso_cookie(response) -> None:
    response.delete_cookie(
        key=settings.SSO_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def get_sso_token_from_request(request: Request) -> Optional[str]:
    """Extract the SSO token from the HttpOnly cookie."""
    return request.cookies.get(settings.SSO_COOKIE_NAME)


# ── CSRF helpers ──────────────────────────────────────────────────────────────

def csrf_token_for_session(token: str) -> str:
    """Return a session-bound CSRF token for the separate frontend origin."""
    return hmac.new(settings.SSO_JWT_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def verify_sso_origin(request: Request) -> None:
    allowed = {o.strip() for o in settings.SSO_ALLOWED_ORIGINS.split(",") if o.strip()}
    if request.headers.get("origin") not in allowed:
        logger.warning("SSO request rejected: Origin is not in SSO_ALLOWED_ORIGINS")
        raise HTTPException(status_code=403, detail="Origin not allowed")


def verify_csrf_token(request: Request) -> None:
    verify_sso_origin(request)
    token = get_sso_token_from_request(request)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not token or not hmac.compare_digest(supplied.encode(), csrf_token_for_session(token).encode()):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


async def revoke_sso_session(jti: str, lendrix_uuid: str, expires_at: datetime, db: AsyncSession) -> None:
    """Mark a persistent session revoked and retain the legacy revocation record."""
    await db.execute(
        update(NseSsoSession)
        .where(
            NseSsoSession.jwt_jti_hash == hash_sso_jti(jti),
            NseSsoSession.lendrix_user_uuid == lendrix_uuid,
        )
        .values(revoked_at=func.coalesce(NseSsoSession.revoked_at, func.now()))
    )
    existing = await db.execute(
        select(SsoSessionRevocation).where(SsoSessionRevocation.jti == jti)
    )
    already_revoked = existing.scalar_one_or_none() is not None
    if not already_revoked:
        db.add(SsoSessionRevocation(
            jti=jti,
            lendrix_uuid=lendrix_uuid,
            expires_at=expires_at,
        ))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if already_revoked:
            raise
        # A concurrent logout may already have inserted the same JTI.
        existing = await db.execute(select(SsoSessionRevocation).where(SsoSessionRevocation.jti == jti))
        if existing.scalar_one_or_none() is None:
            raise
