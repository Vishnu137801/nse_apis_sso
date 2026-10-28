from pydantic import BaseModel
from typing import Optional, List


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    status: Optional[str] = None
    message: Optional[str] = None
    access_token: Optional[str] = None
    token_type: Optional[str] = None


class LendrixLoginRequest(BaseModel):
    email: str
    password: str


class NseMappingInfo(BaseModel):
    client_codes: List[str] = []
    nse_registered: bool = False
    registration_status: Optional[str] = None
    attempt_status: Optional[str] = None


class CustomerLoginResponse(BaseModel):
    success: bool
    message: str
    lendrix_access_token: Optional[str] = None
    lendrix_refresh_token: Optional[str] = None
    user: Optional[dict] = None
    nse: Optional[NseMappingInfo] = None
    nse_token: Optional[str] = None
    sso_token: Optional[str] = None
    code: Optional[str] = None
    resumeKyc: Optional[bool] = None
