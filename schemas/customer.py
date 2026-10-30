from typing import Any, Literal

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
    euin: str | None = None


class MFOnboardingStatusResponse(BaseModel):
    authenticated: Literal[True] = True
    onboarding_complete: bool
    steps: dict[str, Any]
