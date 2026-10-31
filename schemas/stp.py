from pydantic import BaseModel, field_validator
from typing import Optional, List


class STPDetail(BaseModel):
    client_code: Optional[str] = None
    from_scheme_code: Optional[str] = None
    to_scheme_code: Optional[str] = None
    buy_sell_type: Optional[str] = None

    @field_validator("buy_sell_type")
    @classmethod
    def validate_buy_sell_type(cls, v):
        if v is not None and v.upper() != "ADDITIONAL":
            raise ValueError("buy_sell_type must be 'ADDITIONAL' for STP")
        return v.upper() if v else v
    transaction_mode: Optional[str] = None
    folio_no: Optional[str] = None
    internal_ref_number: Optional[str] = None
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
    no_of_transfers: Optional[str] = None
    to_date: Optional[str] = None
    installment_amount: Optional[str] = None
    installment_units: Optional[str] = None
    first_order_today: Optional[str] = None
    sub_broker_code: Optional[str] = None
    euin_declaration: Optional[str] = None
    euin_number: Optional[str] = None
    remarks: Optional[str] = None
    sub_broker_arn_code: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None


class STPRegistrationRequest(BaseModel):
    reg_data: List[STPDetail]


class STPRegistrationResponse(BaseModel):
    reg_data: Optional[List[STPDetail]] = None

    model_config = {"extra": "allow"}
