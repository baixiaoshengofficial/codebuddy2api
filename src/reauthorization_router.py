"""Shared HTTP adapter for credential-specific provider authorization."""

from fastapi import APIRouter, Depends

from .auth import authenticate
from .authorization_router import authorization_response
from .device_authorization import AuthorizationError
from .credential_reauthorization import CredentialReauthorization
from .models import ReauthorizationStart, ReauthorizationPoll


def create_reauthorization_router(
    get_manager, start_login, poll_login, build_credential
):
    service = CredentialReauthorization(
        get_manager, start_login, poll_login, build_credential
    )
    router = APIRouter(dependencies=[Depends(authenticate)])

    @router.post("/auth/reauthorize")
    async def start(command: ReauthorizationStart):
        return await authorization_response(service.start(command.filename))

    @router.post("/auth/reauthorize/poll")
    async def poll(command: ReauthorizationPoll):
        async def action():
            result = await service.poll(command.auth_state)
            if result.get("pending"):
                raise AuthorizationError(
                    400, "authorization_pending", "等待用户登录授权"
                )
            return result

        return await authorization_response(action())

    return router
