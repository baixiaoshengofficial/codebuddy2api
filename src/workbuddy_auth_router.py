"""WorkBuddy authentication for daily check-in credentials."""

import base64
import json
import logging
import secrets
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from config import (
    get_codebuddy_api_endpoint,
    get_codebuddy_api_host,
    get_codebuddy_site,
    get_codebuddy_ssl_verify,
)
from .auth import authenticate
from .workbuddy_token_manager import workbuddy_token_manager

logger = logging.getLogger(__name__)
router = APIRouter()

WORKBUDDY_CLIENT_VERSION = "5.2.5"
WORKBUDDY_PLATFORM = "workbuddy"
_last_auth_state: Optional[str] = None


def _base_url() -> str:
    return get_codebuddy_api_endpoint().rstrip("/")


def _auth_state_endpoint() -> str:
    return f"{_base_url()}/v2/plugin/auth/state"


def _auth_token_endpoint() -> str:
    return f"{_base_url()}/v2/plugin/auth/token"


def _login_account_endpoint() -> str:
    return f"{_base_url()}/v2/plugin/login/account"


def _auth_headers() -> Dict[str, str]:
    request_id = str(uuid.uuid4()).replace("-", "")
    api_host = get_codebuddy_api_host()
    return {
        "Host": api_host,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "close",
        "X-Requested-With": "XMLHttpRequest",
        "X-Domain": api_host,
        "X-No-Authorization": "true",
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
        "User-Agent": f"WorkBuddy/{WORKBUDDY_CLIENT_VERSION}",
        # Desktop login interceptor still uses deploymentType SaaS during auth.
        "X-Product": "SaaS",
        "X-Request-ID": request_id,
    }


def _poll_headers() -> Dict[str, str]:
    headers = _auth_headers()
    request_id = headers["X-Request-ID"]
    span_id = secrets.token_hex(8)
    headers.update({
        "b3": f"{request_id}-{span_id}-1-",
        "X-B3-TraceId": request_id,
        "X-B3-ParentSpanId": "",
        "X-B3-SpanId": span_id,
        "X-B3-Sampled": "1",
    })
    return headers


def _decode_jwt_user(bearer_token: str) -> Dict[str, Any]:
    user_id = "unknown"
    user_info: Dict[str, Any] = {}
    try:
        if not bearer_token or "." not in bearer_token:
            return {"user_id": user_id, "user_info": user_info}
        parts = bearer_token.split(".")
        if len(parts) < 2:
            return {"user_id": user_id, "user_info": user_info}
        payload_part = parts[1]
        missing_padding = len(payload_part) % 4
        if missing_padding:
            payload_part += "=" * (4 - missing_padding)
        payload = base64.urlsafe_b64decode(payload_part)
        jwt_data = json.loads(payload.decode("utf-8"))
        user_id = (
            jwt_data.get("preferred_username")
            or jwt_data.get("email")
            or jwt_data.get("sub")
            or "unknown"
        )
        user_info = {
            key: jwt_data.get(key)
            for key in (
                "sub",
                "email",
                "preferred_username",
                "name",
                "given_name",
                "family_name",
                "exp",
                "iat",
                "scope",
            )
            if jwt_data.get(key) is not None
        }
        if jwt_data.get("sid"):
            user_info["session_state"] = jwt_data["sid"]
    except Exception as exc:
        logger.warning("Failed to decode WorkBuddy JWT: %s", exc)
    return {"user_id": user_id, "user_info": user_info}


async def _fetch_account_uid(client: httpx.AsyncClient, auth_state: str, access_token: str) -> Optional[str]:
    try:
        response = await client.get(
            f"{_login_account_endpoint()}?state={auth_state}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": f"WorkBuddy/{WORKBUDDY_CLIENT_VERSION}",
                "X-Product": "WorkBuddy",
                "X-Domain": get_codebuddy_api_host(),
            },
            timeout=30,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        for key in ("uid", "userId", "user_id", "id"):
            value = data.get(key)
            if value:
                return str(value)
        account = data.get("account")
        if isinstance(account, dict):
            for key in ("uid", "userId", "user_id", "id"):
                value = account.get(key)
                if value:
                    return str(value)
    except Exception as exc:
        logger.debug("WorkBuddy account lookup failed: %s", exc)
    return None


async def start_workbuddy_auth() -> Dict[str, Any]:
    try:
        headers = _auth_headers()
        async with httpx.AsyncClient(verify=get_codebuddy_ssl_verify()) as client:
            nonce = secrets.token_hex(8)
            state_url = f"{_auth_state_endpoint()}?platform={WORKBUDDY_PLATFORM}&nonce={nonce}"
            response = await client.post(state_url, json={"nonce": nonce}, headers=headers, timeout=30)
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": "auth_start_failed",
                    "message": f"API 请求失败，状态码: {response.status_code}",
                    "response_text": response.text[:500],
                }

            result = response.json()
            if result.get("code") != 0 or not result.get("data"):
                return {
                    "success": False,
                    "error": "auth_start_failed",
                    "message": result.get("msg") or "启动 WorkBuddy 授权失败",
                    "response": result,
                }

            data = result["data"]
            auth_state = data.get("state")
            auth_url = data.get("authUrl")
            if not auth_state or not auth_url:
                return {
                    "success": False,
                    "error": "auth_start_failed",
                    "message": "上游未返回 state 或 authUrl",
                    "response": result,
                }

            global _last_auth_state
            if _last_auth_state and auth_state == _last_auth_state:
                nonce2 = secrets.token_hex(8)
                state_url2 = f"{_auth_state_endpoint()}?platform={WORKBUDDY_PLATFORM}&nonce={nonce2}"
                response2 = await client.post(state_url2, json={"nonce": nonce2}, headers=headers, timeout=30)
                if response2.status_code == 200:
                    result2 = response2.json()
                    data2 = (result2 or {}).get("data") or {}
                    if data2.get("state") and data2.get("authUrl") and data2["state"] != auth_state:
                        auth_state = data2["state"]
                        auth_url = data2["authUrl"]

            _last_auth_state = auth_state
            logger.info(
                "WorkBuddy auth started (site=%s, platform=%s)",
                get_codebuddy_site(),
                WORKBUDDY_PLATFORM,
            )
            return {
                "success": True,
                "method": "workbuddy_auth",
                "auth_state": auth_state,
                "verification_uri_complete": auth_url,
                "platform": WORKBUDDY_PLATFORM,
                "status": "awaiting_login",
                "message": "请在浏览器完成 WorkBuddy 登录授权",
            }
    except Exception as exc:
        logger.error("WorkBuddy auth start failed: %s", exc)
        return {
            "success": False,
            "error": "auth_start_failed",
            "message": f"认证启动失败: {exc}",
        }


async def poll_workbuddy_auth_status(auth_state: str) -> Dict[str, Any]:
    try:
        headers = _poll_headers()
        url = f"{_auth_token_endpoint()}?state={auth_state}"
        async with httpx.AsyncClient(verify=get_codebuddy_ssl_verify()) as client:
            response = await client.get(url, headers=headers, timeout=30)
            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"API 请求失败，状态码: {response.status_code}",
                    "response_text": response.text[:500],
                }

            result = response.json()
            if result.get("code") == 11217:
                return {
                    "status": "pending",
                    "message": result.get("msg", "等待登录..."),
                    "code": result.get("code"),
                }
            if result.get("code") == 0 and result.get("data", {}).get("accessToken"):
                data = result["data"]
                access_token = data.get("accessToken")
                account_uid = await _fetch_account_uid(client, auth_state, access_token)
                return {
                    "status": "success",
                    "message": "认证成功",
                    "token_data": {
                        "access_token": access_token,
                        "bearer_token": access_token,
                        "token_type": data.get("tokenType", "Bearer"),
                        "expires_in": data.get("expiresIn"),
                        "refresh_token": data.get("refreshToken"),
                        "scope": data.get("scope"),
                        "domain": data.get("domain"),
                        "account_uid": account_uid,
                        "full_response": result,
                    },
                }
            return {
                "status": "unknown",
                "message": result.get("msg", "未知状态"),
                "code": result.get("code"),
                "response": result,
            }
    except Exception as exc:
        logger.error("WorkBuddy auth poll failed: %s", exc)
        return {"status": "error", "message": f"轮询失败: {exc}"}


async def save_workbuddy_token(token_data: Dict[str, Any]) -> bool:
    try:
        bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
        decoded = _decode_jwt_user(bearer_token or "")
        user_id = token_data.get("account_uid") or decoded["user_id"] or token_data.get("domain") or "unknown"
        user_info = dict(decoded.get("user_info") or {})
        if token_data.get("account_uid"):
            user_info["uid"] = token_data["account_uid"]

        credential_data = {
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": int(time.time()),
            "expires_in": token_data.get("expires_in"),
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "Bearer"),
            "scope": token_data.get("scope"),
            "domain": token_data.get("domain"),
            "product": "WorkBuddy",
            "user_info": user_info or None,
        }
        credential_data = {key: value for key, value in credential_data.items() if value is not None}

        timestamp = int(time.time())
        safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:20]
        filename = f"workbuddy_{safe_user_id}_{timestamp}.json"
        return workbuddy_token_manager.add_credential_with_data(credential_data, filename=filename)
    except Exception as exc:
        logger.error("Failed to save WorkBuddy token: %s", exc)
        return False


@router.get("/auth/start", summary="Start WorkBuddy authentication for check-in")
async def start_device_auth(_token: str = Depends(authenticate)):
    result = await start_workbuddy_auth()
    if not result.get("success"):
        return JSONResponse(content=result, status_code=400)
    return result


@router.post("/auth/poll", summary="Poll WorkBuddy OAuth token")
async def poll_for_token(
    auth_state: str = Body(None, embed=True),
    _token: str = Depends(authenticate),
):
    if not auth_state:
        return JSONResponse(
            content={"error": "missing_parameters", "error_description": "缺少必要的参数：auth_state"},
            status_code=400,
        )

    poll_result = await poll_workbuddy_auth_status(auth_state)
    if poll_result.get("status") == "success":
        token_data = poll_result.get("token_data") or {}
        bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
        if not bearer_token:
            return JSONResponse(
                content={
                    "error": "invalid_token_response",
                    "error_description": "API 返回的响应中没有找到 token",
                },
                status_code=400,
            )
        saved = await save_workbuddy_token(token_data)
        if not saved:
            return JSONResponse(
                content={
                    "error": "save_failed",
                    "error_description": "Token 获取成功，但写入 WorkBuddy 凭证目录失败",
                    "access_token": bearer_token,
                    "saved": False,
                },
                status_code=500,
            )
        return {
            "access_token": bearer_token,
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_in": token_data.get("expires_in"),
            "refresh_token": token_data.get("refresh_token"),
            "scope": token_data.get("scope"),
            "saved": saved,
            "message": "WorkBuddy 授权成功",
            "domain": token_data.get("domain"),
            "user_id": token_data.get("account_uid"),
        }
    if poll_result.get("status") == "pending":
        return JSONResponse(
            content={
                "error": "authorization_pending",
                "error_description": poll_result.get("message", "等待用户登录..."),
                "code": poll_result.get("code"),
            },
            status_code=400,
        )
    return JSONResponse(
        content={
            "error": "auth_error",
            "error_description": poll_result.get("message", "认证过程发生错误"),
            "details": poll_result,
        },
        status_code=400,
    )


@router.get("/credentials", summary="List WorkBuddy check-in credentials")
async def list_credentials(_token: str = Depends(authenticate)):
    workbuddy_token_manager.load_all_tokens()
    return {"credentials": workbuddy_token_manager.get_credentials_info()}


@router.post("/credentials/delete", summary="Delete a WorkBuddy credential by index")
async def delete_credential(
    index: int = Body(..., embed=True),
    _token: str = Depends(authenticate),
):
    success = workbuddy_token_manager.delete_credential_by_index(index)
    if not success:
        return JSONResponse(
            content={"success": False, "message": "删除失败，索引无效或文件不存在"},
            status_code=400,
        )
    return {"success": True, "message": "已删除 WorkBuddy 凭证"}
