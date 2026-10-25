"""Shared authenticated HTTP boundary for provider login results."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .auth import authenticate
from .device_authorization import AuthorizationError, DeviceAuthorization
from .models import AuthorizationPollCommand

logger = logging.getLogger(__name__)


async def authorization_response(action):
    try:
        return await action
    except AuthorizationError as exc:
        return JSONResponse(
            {"error": exc.error, "error_description": str(exc)},
            status_code=exc.status,
        )
    except OSError:
        logger.exception("Unable to persist authorization result")
        return JSONResponse(
            {
                "error": "save_failed",
                "error_description": "保存凭证失败，请重新尝试授权",
            },
            status_code=500,
        )


def create_authorization_router(start_login, poll_login, save_token):
    service = DeviceAuthorization(start_login, poll_login, save_token)
    router = APIRouter(dependencies=[Depends(authenticate)])

    @router.get("/auth/start")
    async def start():
        return await authorization_response(service.start())

    @router.post("/auth/poll")
    async def poll(command: AuthorizationPollCommand):
        return await authorization_response(service.poll(command.auth_state))

    return router
