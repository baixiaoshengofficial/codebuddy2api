"""WorkBuddy credential commands and login composition."""

import logging
from fastapi import APIRouter, Depends, HTTPException

from .auth import authenticate
from .models import CredentialDeleteCommand
from .authorization_router import create_authorization_router
from .reauthorization_router import create_reauthorization_router
from .credential_login import CredentialLogin, build_workbuddy_credential
from .workbuddy_token_manager import workbuddy_token_manager
from .oauth.client import WORKBUDDY, OAuthLoginClient
from .oauth.account import lookup_workbuddy_account

logger = logging.getLogger(__name__)
router = APIRouter()
workbuddy_login = OAuthLoginClient(WORKBUDDY, account_lookup=lookup_workbuddy_account)
start_workbuddy_auth = workbuddy_login.start
poll_workbuddy_auth_status = workbuddy_login.poll
save_workbuddy_token = CredentialLogin(
    lambda: workbuddy_token_manager, build_workbuddy_credential, "workbuddy"
).save


@router.get("/credentials", summary="List WorkBuddy check-in credentials")
async def list_credentials(_token: str = Depends(authenticate)):
    workbuddy_token_manager.load_all_tokens()
    return {"credentials": workbuddy_token_manager.get_credentials_info()}


def _delete_credential(filename):
    try:
        workbuddy_token_manager.delete_credential(filename)
    except FileNotFoundError:
        raise HTTPException(404, "凭证不存在，请刷新列表")
    except OSError:
        logger.exception("Unable to delete WorkBuddy credential")
        raise HTTPException(500, "删除凭证失败，请稍后重试")
    return {"success": True, "message": "已删除 WorkBuddy 凭证"}


@router.delete(
    "/credentials/{filename}", summary="Delete a WorkBuddy credential by filename"
)
async def delete_credential(filename: str, _token: str = Depends(authenticate)):
    return _delete_credential(filename)


@router.post("/credentials/delete", deprecated=True)
async def delete_credential_legacy(
    command: CredentialDeleteCommand, _token: str = Depends(authenticate)
):
    if not command.filename:
        raise HTTPException(409, "按序号删除已停用，请刷新页面后按具体凭证删除")
    return _delete_credential(command.filename)


router.include_router(
    create_authorization_router(
        lambda: start_workbuddy_auth(),
        lambda state: poll_workbuddy_auth_status(state),
        lambda token: save_workbuddy_token(token),
    )
)
router.include_router(
    create_reauthorization_router(
        lambda: workbuddy_token_manager,
        lambda: start_workbuddy_auth(),
        lambda state: poll_workbuddy_auth_status(state),
        build_workbuddy_credential,
    )
)
