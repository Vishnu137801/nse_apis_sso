from pydantic import BaseModel, Field
from typing import Optional


class GetLinkRequest(BaseModel):
    productType: str
    productRefId: str
    client_code: Optional[str] = Field(default=None, exclude=True)


class GetLinkResponse(BaseModel):
    productType: Optional[str] = None
    productRefId: Optional[str] = None
    firstHolderLink: Optional[str] = None
    secondHolderLink: Optional[str] = None
    thirdHolderLink: Optional[str] = None
    errorMessage: Optional[str] = None


class ResendCommRequest(BaseModel):
    productType: str
    productRefId: str
    client_code: Optional[str] = Field(default=None, exclude=True)


class ResendCommResponse(BaseModel):
    productType: Optional[str] = None
    productRefId: Optional[str] = None
    response_status: Optional[str] = None
    error_remark: Optional[str] = None
