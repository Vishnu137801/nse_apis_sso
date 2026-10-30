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


class UCCDetails(BaseModel):
    client_code: Optional[str] = None
    primary_holder_first_name: Optional[str] = None
    primary_holder_middle_name: Optional[str] = None
    primary_holder_last_name: Optional[str] = None
    tax_status: Optional[str] = None
    gender: Optional[str] = None
    primary_holder_dob_incorporation: Optional[str] = None
    occupation_code: Optional[str] = None
    holding_nature: Optional[str] = None
    primary_holder_pan: Optional[str] = None
    email: Optional[str] = None
    indian_mobile_no: Optional[str] = None
    address_1: Optional[str] = None
    address_2: Optional[str] = None
    address_3: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    country: Optional[str] = None
    account_type_1: Optional[str] = None
    account_no_1: Optional[str] = None
    ifsc_code_1: Optional[str] = None
    reg_status: Optional[str] = None


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
    ucc_details: Optional[List[UCCDetails]] = None
