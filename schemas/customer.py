from typing import Literal

from pydantic import BaseModel, ConfigDict


class CustomerRegistrationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True] = True
    registration_status: Literal[
        "registered",
        "pending",
        "failed",
        "unlinked",
        "linking_required",
        "unknown",
    ]
    client_codes: list[str]
    attempt_status: str | None
