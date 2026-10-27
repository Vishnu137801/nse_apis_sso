from typing import Literal

from pydantic import BaseModel, ConfigDict


UccStatus = Literal[
    "not_started",
    "registration_in_progress",
    "pending_confirmation",
    "registered",
    "rejected",
    "linking_required",
    "unknown",
]
NextAction = Literal[
    "start_registration",
    "resume_registration",
    "await_confirmation",
    "correct_registration",
    "link_existing_account",
    "open_dashboard",
]


class OnboardingStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True] = True
    lendrix_uuid: str
    has_nse_account: bool | None
    linking_required: bool
    client_codes: list[str]
    ucc_status: UccStatus
    onboarding_status: str
    next_action: NextAction
    readiness_checks: dict[str, str]
    readiness_note: str | None = None
