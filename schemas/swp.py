from pydantic import BaseModel, field_validator
from typing import Optional, List


class SWPDetail(BaseModel):
    client_code: Optional[str] = None
    scheme_code: Optional[str] = None
    trans_mode: Optional[str] = None
    folio_no: Optional[str] = None
    internal_ref_no: Optional[str] = None
    start_date: Optional[str] = None

    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, v):
        if v:
            try:
                day = int(v.split("/")[0])
            except (ValueError, IndexError):
                raise ValueError("start_date must be in DD/MM/YYYY format")
            if day not in (1, 7, 14, 21):
                raise ValueError("Day of start_date must be 1, 7, 14, or 21")
        return v
    frequency_type: Optional[str] = None
    no_of_withdrawals: Optional[str] = None
    end_date: Optional[str] = None
    installment_amount: Optional[str] = None
    installment_units: Optional[str] = None
    first_order_today: Optional[str] = None
    sub_broker_code: Optional[str] = None
    euin_declaration: Optional[str] = None
    euin_number: Optional[str] = None
    remarks: Optional[str] = None
    sub_broker_arn: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    account_no: Optional[str] = None


class SWPRegistrationRequest(BaseModel):
    reg_data: List[SWPDetail]


class SWPRegistrationResponse(BaseModel):
    reg_data: Optional[List[SWPDetail]] = None

    model_config = {"extra": "allow"}
