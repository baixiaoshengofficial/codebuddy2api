"""HTTP command schemas used by the management routes."""

from typing import Optional
from pydantic import BaseModel, Field


class AuthorizationPollCommand(BaseModel):
    auth_state: Optional[str] = Field(None, min_length=1, max_length=2048)


class CredentialDeleteCommand(BaseModel):
    # Legacy index-only requests are rejected explicitly by the HTTP boundary.
    filename: Optional[str] = Field(None, min_length=1, max_length=255)


class ReauthorizationStart(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)


class ReauthorizationPoll(BaseModel):
    auth_state: str = Field(..., min_length=1, max_length=2048)
