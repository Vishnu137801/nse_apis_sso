from fastapi import APIRouter, Depends, HTTPException, Request, Response
import logging
import uuid
import httpx
from datetime import datetime, timezone, timedelta
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.nse_auth import get_auth_headers
from auth.dependencies import require_auth
from auth.sso_session import create_sso_session, hash_sso_jti, csrf_token_for_session
from config import settings
from database import get_db
from models.sso import LendrixUserNseMapping, NseSsoSession
from models.customer_profile import CustomerProfile
from schemas.auth import LendrixLoginRequest, CustomerLoginResponse, NseMappingInfo
from http_client import get_nse_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=CustomerLoginResponse)
async def login(payload: LendrixLoginRequest, db: AsyncSession = Depends(get_db)):
    """Login via Lendrix, establish NSE SSO session automatically in the backend."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(
                "https://registration.lendrixventech.com/auth/login",
                json={"email": payload.email, "password": payload.password},
                headers={"Content-Type": "application/json"},
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Lendrix login timed out")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach Lendrix: {e}")

    data = resp.json()

    if resp.status_code == 403:
        return CustomerLoginResponse(
            success=False,
            message=data.get("message", "Access denied"),
            code=data.get("code"),
            lendrix_access_token=data.get("access_token"),
            lendrix_refresh_token=data.get("refresh_token"),
            user=data.get("user"),
            resumeKyc=data.get("resumeKyc"),
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=data.get("message", "Login failed"))

    user = data.get("user", {})
    lendrix_uuid = user.get("id")

    # Fetch active NSE client codes
    nse_info = NseMappingInfo()
    client_codes = []
    if lendrix_uuid:
        mapping_result = await db.execute(
            select(LendrixUserNseMapping.client_code)
            .where(
                LendrixUserNseMapping.lendrix_uuid == lendrix_uuid,
                LendrixUserNseMapping.is_active.is_(True),
            )
        )
        client_codes = [row[0] for row in mapping_result.fetchall()]
        nse_info = NseMappingInfo(
            client_codes=client_codes,
            nse_registered=len(client_codes) > 0,
            registration_status="registered" if client_codes else "unlinked",
        )

    # Mint NSE JWT signed with JWT_SECRET — used as Bearer for all NSE endpoints
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    nse_token = jwt.encode(
        {"sub": user.get("email", lendrix_uuid), "exp": expire},
        settings.JWT_SECRET,
        algorithm="HS256",
    )

    # Upsert customer profile
    if lendrix_uuid:
        try:
            existing = await db.execute(
                select(CustomerProfile).where(CustomerProfile.lendrix_uuid == lendrix_uuid)
            )
            profile = existing.scalar_one_or_none()
            profile_data = {
                "email": user.get("email"),
                "first_name": user.get("firstName") or user.get("first_name"),
                "last_name": user.get("lastName") or user.get("last_name"),
                "phone": user.get("phone") or user.get("mobile"),
            }
            if profile:
                for k, v in profile_data.items():
                    setattr(profile, k, v)
            else:
                db.add(CustomerProfile(lendrix_uuid=lendrix_uuid, **profile_data))
            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning("Failed to upsert customer profile for uuid=<redacted>")

    # Automatically establish NSE SSO session backend-to-backend
    sso_token = None
    if lendrix_uuid and settings.SSO_ENABLED:
        try:
            # Step 1: request a one-time code from Lendrix
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, trust_env=False) as client:
                code_resp = await client.post(
                    "https://registration.lendrixventech.com/auth/sso/issue-code",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {settings.LENDRIX_SSO_SERVER_SECRET}",
                    },
                    json={"lendrix_uuid": lendrix_uuid},
                )

            if code_resp.status_code == 200:
                one_time_code = code_resp.json().get("code")

                if one_time_code:
                    # Step 2: verify the code with Lendrix server-to-server
                    async with httpx.AsyncClient(timeout=10, follow_redirects=True, trust_env=False) as client:
                        verify_resp = await client.post(
                            settings.LENDRIX_SSO_VERIFY_URL,
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {settings.LENDRIX_SSO_SERVER_SECRET}",
                            },
                            json={"code": one_time_code},
                        )

                    if verify_resp.status_code == 200:
                        verified_uuid = verify_resp.json().get("customer", {}).get("lendrix_uuid")

                        if verified_uuid == lendrix_uuid:
                            # Step 3: create and persist NSE SSO session
                            session = create_sso_session(lendrix_uuid, client_codes)
                            db.add(NseSsoSession(
                                id=str(uuid.uuid4()),
                                lendrix_user_uuid=lendrix_uuid,
                                jwt_jti_hash=hash_sso_jti(session.jti),
                                expires_at=session.expires_at,
                            ))
                            await db.commit()
                            sso_token = session.token
                            logger.info("SSO session auto-established for uuid=<redacted>")
        except Exception:
            logger.warning("Backend SSO auto-login failed for uuid=<redacted>")

    logger.info(f"Login successful: uuid=<redacted> nse_registered={nse_info.nse_registered}")
    return CustomerLoginResponse(
        success=True,
        message="Login successful",
        lendrix_access_token=data.get("access_token"),
        lendrix_refresh_token=data.get("refresh_token"),
        user=user,
        nse=nse_info,
        nse_token=nse_token,
        sso_token=sso_token,
    )


@router.get("/debug/outbound-ip", tags=["Auth"], include_in_schema=False, dependencies=[Depends(require_auth)])
async def debug_outbound_ip():
    """
    Safe diagnostic endpoint — checks what IP NSE sees from this server.
    Calls a public IP echo service through the same HTTP client used for NSE.
    Never logs or returns credentials.
    """
    headers = get_auth_headers()
    auth_scheme = headers.get("Authorization", "").split(" ")[0]
    member_id_present = "memberId" in headers

    try:
        async with get_nse_client(10) as client:
            ip_response = await client.get("https://api.ipify.org?format=json")
            outbound_ip = ip_response.json().get("ip", "unknown")
    except Exception as e:
        outbound_ip = f"error: {e}"

    logger.info(f"[debug] auth_scheme={auth_scheme} memberId_present={member_id_present} outbound_ip={outbound_ip}")
    logger.info(f"[debug] NSE_BASE_URL={settings.NSE_BASE_URL} NSE_PROXY={'set' if settings.NSE_PROXY else 'NOT SET'}")
    logger.info(f"[debug] NSE_LOGIN_USER_ID={settings.NSE_LOGIN_USER_ID} NSE_MEMBER_ID={settings.NSE_MEMBER_ID}")

    return {
        "outbound_ip": outbound_ip,
        "auth_scheme": auth_scheme,
        "member_id_present": member_id_present,
        "nse_base_url": settings.NSE_BASE_URL,
        "proxy_configured": bool(settings.NSE_PROXY),
        "nse_login_user_id": settings.NSE_LOGIN_USER_ID,
        "nse_member_id": settings.NSE_MEMBER_ID,
    }
