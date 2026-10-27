"""M6 request/response DTO contracts; TODO: add validation schemas."""

from dataclasses import dataclass
from typing import Any


@dataclass
class LoginRequest:
    email: str
    password: str


@dataclass
class TokenResponse:
    access_token: str
    token_type: str
    role: str


@dataclass
class DecisionRequest:
    action: str
    feedback: str | None = None


@dataclass
class StudentListFilters:
    risk_state: str | None = None
    code_module: str | None = None
    code_presentation: str | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class BriefEditRequest:
    content: dict[str, Any]


@dataclass
class BriefResponse:
    brief_id: int
    prediction_id: int
    version: int
    status: str
    content: dict[str, Any]
