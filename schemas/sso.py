"""
Pydantic schemas for SSO endpoints.
No NSE credentials, secrets, or personal data are ever returned to the browser.
"""
from pydantic import BaseModel, field_validator, ConfigDict
from typing import List, Literal, Optional
import re


class SsoExchangeRequest(BaseModel):
    """Body accepted by POST /api/v1/auth/sso/exchange."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    code: str

    @field_validator("code")
    @classmethod
    def code_format(cls, v: str) -> str:
        # Node.js issues 32 random bytes as unpadded base64url.
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", v):
            raise ValueError("Invalid authorization code format")
        return v


class SsoExchangeResponse(BaseModel):
    """Returned by POST /api/v1/auth/sso/exchange on success."""
    authenticated: bool = True
    linking_required: bool
    csrf_token: str
    token_type: str = "cookie"
    # The SSO JWT is set as an HttpOnly cookie; this field confirms success.
    message: str = "SSO session established"
    # Whether the user already has an NSE investor mapping.
    investor_linked: bool
    # Safe display list — never includes secrets or full PAN.
    client_codes: List[str]


class SsoMeResponse(BaseModel):
    """Returned by GET /api/v1/auth/sso/me."""
    authenticated: bool = True
    linking_required: bool
    csrf_token: str
    lendrix_uuid: str
    investor_linked: bool
    client_codes: List[str]


class SsoLogoutResponse(BaseModel):
    message: str = "Logged out"


# ── Internal: shape of the Node.js verify-code response ──────────────────────

class LendrixVerifyCustomer(BaseModel):
    lendrix_uuid: str

    @field_validator("lendrix_uuid")
    @classmethod
    def uuid_format(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", v
        ):
            raise ValueError("lendrix_uuid is not a valid UUID")
        return v


class LendrixVerifyResponse(BaseModel):
    """
    Expected JSON body from POST {LENDRIX_SSO_VERIFY_URL}.
    Only lendrix_uuid is trusted; everything else is informational.
    """
    success: Literal[True]
    customer: LendrixVerifyCustomer
