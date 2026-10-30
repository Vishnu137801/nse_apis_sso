from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db

bearer_scheme = HTTPBearer()

_UCC_SUCCESS_STATUSES = {"REG_SUCCESS", "S"}


def _decode_bearer(credentials: HTTPAuthorizationCredentials) -> dict:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization token")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        if payload.get("typ") not in (None, "app_bearer"):
            raise JWTError("Wrong identity type")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return payload


def require_auth(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """Validates the JWT signature and expiry on every protected request."""
    _decode_bearer(credentials)
    return credentials.credentials


async def require_auth_payload(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Decodes the Bearer token and always re-fetches client_codes live from DB.
    This ensures the client code is always current regardless of token age.
    """
    from models.sso import LendrixUserNseMapping
    from models.ucc import UCCRegistration

    payload = _decode_bearer(credentials)
    lendrix_uuid = payload.get("lendrix_uuid") or payload.get("sub")
    if lendrix_uuid:
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
        payload["client_codes"] = [row[0] for row in result.fetchall()]
    return payload


def assert_token_owns_client_code(payload: dict, client_code: str) -> None:
    """
    Verify the live-fetched client_codes includes the requested client_code.
    """
    token_codes: list = payload.get("client_codes") or []
    if not token_codes or client_code not in token_codes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is not authorized for this client code",
        )
