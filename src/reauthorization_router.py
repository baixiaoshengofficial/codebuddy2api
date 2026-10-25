"""Shared HTTP adapter for credential-specific provider authorization."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import authenticate
from .credential_reauthorization import CredentialReauthorization, ReauthorizationError

logger = logging.getLogger(__name__)


class ReauthorizationStart(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)


class ReauthorizationPoll(BaseModel):
    auth_state: str = Field(..., min_length=1, max_length=2048)


def create_reauthorization_router(
    get_manager, start_login, poll_login, build_credential
):
    service = CredentialReauthorization(
        get_manager, start_login, poll_login, build_credential
    )
    router = APIRouter(dependencies=[Depends(authenticate)])

    async def execute(action):
        try:
            return await action
        except ReauthorizationError as exc:
            raise HTTPException(exc.status, str(exc))
        except OSError:
            logger.exception("Credential reauthorization persistence failed")
            raise HTTPException(500, "保存凭证失败，请重试")

    @router.post("/auth/reauthorize")
    async def start(command: ReauthorizationStart):
        return await execute(service.start(command.filename))

    @router.post("/auth/reauthorize/poll")
    async def poll(command: ReauthorizationPoll):
        result = await execute(service.poll(command.auth_state))
        if result.get("pending"):
            return JSONResponse(
                {
                    "error": "authorization_pending",
                    "error_description": result.get("message") or "等待登录",
                },
                status_code=400,
            )
        return result

    return router
