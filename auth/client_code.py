"""
Client code and EUIN resolution helpers.

resolve_client_code() extracts the single verified NSE client code from the
SSO session payload.  It never accepts a client code from the request body.

enforce_euin() overwrites euin_number and euin_declaration on every item in
an order / SIP / XSIP payload so the frontend cannot supply a different EUIN.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from config import settings


def resolve_client_code(sso_payload: dict) -> str:
    codes: list[str] = sso_payload.get("client_codes", [])
    if not codes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "NSE_REGISTRATION_REQUIRED",
                "message": "No verified NSE client code found. Please complete NSE registration.",
            },
        )
    if len(codes) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MULTIPLE_CLIENT_CODES",
                "message": "Multiple active NSE client codes found. Contact support.",
            },
        )
    return codes[0]


def get_distributor_euin() -> str:
    return settings.NSE_DISTRIBUTOR_EUIN


def enforce_euin(item: dict) -> dict:
    euin = get_distributor_euin()
    if euin:
        item["euin_number"] = euin
        item["euin_declaration"] = "Y"
    return item


def format_client_code(sequence: int) -> str:
    return f"LVMF{sequence:05d}"


def generate_client_code(member_code: str, lendrix_uuid: str) -> str:
    """Generate a deterministic client code from member_code + lendrix_uuid."""
    import hashlib
    digest = hashlib.sha256(f"{member_code}:{lendrix_uuid}".encode()).hexdigest()[:5].upper()
    return f"LV{digest}"


async def next_sequential_client_code(db: AsyncSession) -> str:
    """
    Returns the next LVMF##### code using a PostgreSQL sequence.
    Concurrent-safe — nextval() is atomic, no two callers get the same value.
    """
    from sqlalchemy import text

    result = await db.execute(text("SELECT nextval('lvmf_client_code_seq')"))
    seq = result.scalar_one()
    return format_client_code(seq)


def inject_client_code_and_euin(items: list[dict], client_code: str) -> list[dict]:
    for item in items:
        item["client_code"] = client_code
        enforce_euin(item)
    return items
