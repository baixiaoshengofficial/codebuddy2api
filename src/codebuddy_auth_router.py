"""Compose CodeBuddy login services at the HTTP boundary."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .authorization_router import create_authorization_router
from .reauthorization_router import create_reauthorization_router
from .credential_login import CredentialLogin, build_codebuddy_credential
from .codebuddy_token_manager import codebuddy_token_manager
from .oauth.client import CODEBUDDY, OAuthLoginClient

router = APIRouter()
codebuddy_login = OAuthLoginClient(CODEBUDDY)
start_codebuddy_auth = codebuddy_login.start
poll_codebuddy_auth_status = codebuddy_login.poll
save_codebuddy_token = CredentialLogin(
    lambda: codebuddy_token_manager, build_codebuddy_credential, "codebuddy"
).save


@router.get("/auth/callback", summary="OAuth2 callback endpoint")
async def oauth_callback(code: str = None, state: str = None, error: str = None):
    """OAuth2回调端点"""
    if error:
        return JSONResponse(
            content={"error": error, "error_description": "授权被拒绝或出现错误"},
            status_code=400,
        )

    return JSONResponse(
        content={"message": "授权成功！请返回应用程序。", "code": code, "state": state}
    )


router.include_router(
    create_authorization_router(
        lambda: start_codebuddy_auth(),
        lambda state: poll_codebuddy_auth_status(state),
        lambda token: save_codebuddy_token(token),
    )
)
router.include_router(
    create_reauthorization_router(
        lambda: codebuddy_token_manager,
        lambda: start_codebuddy_auth(),
        lambda state: poll_codebuddy_auth_status(state),
        build_codebuddy_credential,
    )
)
