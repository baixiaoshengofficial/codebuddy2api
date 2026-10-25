"""
CodeBuddy Authentication Router
基于真实CodeBuddy API的认证实现
"""
import secrets
import httpx
import uuid
import time
from typing import Dict, Any, Optional
from fastapi.responses import JSONResponse
from fastapi import APIRouter

from config import (
    get_codebuddy_api_endpoint,
    get_codebuddy_api_host,
    get_codebuddy_site,
)
import logging

from .authorization_router import create_authorization_router
from .reauthorization_router import create_reauthorization_router
from .codebuddy_token_manager import codebuddy_token_manager
from .credential_utils import build_credential_filename, decode_jwt_claims, first_claim, select_claims

logger = logging.getLogger(__name__)

# --- Runtime endpoint helpers ---
def get_codebuddy_base_url() -> str:
    return get_codebuddy_api_endpoint()

def get_codebuddy_auth_token_endpoint() -> str:
    return f'{get_codebuddy_base_url()}/v2/plugin/auth/token'

def get_codebuddy_auth_state_endpoint() -> str:
    return f'{get_codebuddy_base_url()}/v2/plugin/auth/state'

_last_auth_state: Optional[str] = None

router = APIRouter()

def get_auth_start_headers() -> Dict[str, str]:
    """生成启动认证(/state)所需的请求头"""
    request_id = str(uuid.uuid4()).replace('-', '')
    api_host = get_codebuddy_api_host()
    return {
        'Host': api_host,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Domain': api_host,
        'X-No-Authorization': 'true',
        'X-No-User-Id': 'true',
        'X-No-Enterprise-Id': 'true',
        'X-No-Department-Info': 'true',
        'User-Agent': 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'SaaS',
        'X-Request-ID': request_id,
    }

def get_auth_poll_headers() -> Dict[str, str]:
    """生成轮询认证(/token)所需的请求头"""
    request_id = str(uuid.uuid4()).replace('-', '')
    span_id = secrets.token_hex(8)
    api_host = get_codebuddy_api_host()
    return {
        'Host': api_host,
        'Accept': 'application/json, text/plain, */*',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Request-ID': request_id,
        'b3': f'{request_id}-{span_id}-1-',
        'X-B3-TraceId': request_id,
        'X-B3-ParentSpanId': '',
        'X-B3-SpanId': span_id,
        'X-B3-Sampled': '1',
        'X-No-Authorization': 'true',
        'X-No-User-Id': 'true',
        'X-No-Enterprise-Id': 'true',
        'X-No-Department-Info': 'true',
        'X-Domain': api_host,
        'User-Agent': 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'SaaS',
    }

async def start_codebuddy_auth() -> Dict[str, Any]:
    """启动CodeBuddy认证流程"""
    try:
        base_url = get_codebuddy_base_url()
        auth_state_endpoint = get_codebuddy_auth_state_endpoint()
        auth_token_endpoint = get_codebuddy_auth_token_endpoint()
        logger.info(f"启动CodeBuddy认证流程，站点: {get_codebuddy_site()}，端点: {base_url}")
        
        headers = get_auth_start_headers()
        
        # 调用 /v2/plugin/auth/state 获取认证状态和URL
        async with httpx.AsyncClient() as client:
            # 为避免上游/中间层缓存，添加随机nonce参数，确保每次请求唯一
            nonce = secrets.token_hex(8)
            state_url = f"{auth_state_endpoint}?platform=CLI&nonce={nonce}"
            payload = {"nonce": nonce}
            
            response = await client.post(state_url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0 and result.get('data'):
                    data = result['data']
                    auth_state = data.get('state')
                    auth_url = data.get('authUrl')
                    
                    if auth_state and auth_url:
                        global _last_auth_state
                        if _last_auth_state and auth_state == _last_auth_state:
                            logger.warning("上游返回的state与上一次相同，尝试重新获取新的state...")
                            try:
                                nonce2 = secrets.token_hex(8)
                                state_url2 = f"{auth_state_endpoint}?platform=CLI&nonce={nonce2}"
                                payload2 = {"nonce": nonce2}
                                async with httpx.AsyncClient() as client2:
                                    response2 = await client2.post(state_url2, json=payload2, headers=headers, timeout=30)
                                if response2.status_code == 200:
                                    result2 = response2.json()
                                    if result2.get('code') == 0 and result2.get('data'):
                                        data2 = result2['data']
                                        ns = data2.get('state')
                                        nu = data2.get('authUrl')
                                        if ns and nu and ns != auth_state:
                                            auth_state = ns
                                            auth_url = nu
                            except Exception:
                                pass
                        token_endpoint = f"{auth_token_endpoint}?state={auth_state}"
                        _last_auth_state = auth_state
                        
                        return {
                            "success": True,
                            "method": "codebuddy_real_auth",
                            "auth_state": auth_state,
                            "verification_uri_complete": auth_url,
                            "verification_uri": base_url,
                            "token_endpoint": token_endpoint,
                            "expires_in": 1800,
                            "interval": 5,
                            "status": "awaiting_login",
                            "instructions": "请点击链接完成CodeBuddy登录",
                            "message": "请使用提供的链接登录CodeBuddy",
                            "platform": "CLI"
                        }
                        
        return {
            "success": False,
            "error": "auth_start_failed",
            "message": "无法启动认证流程"
        }
        
    except Exception as e:
        logger.error(f"启动CodeBuddy认证失败: {e}")
        return {
            "success": False,
            "error": "auth_start_failed", 
            "message": f"认证启动失败: {str(e)}"
        }

async def poll_codebuddy_auth_status(auth_state: str) -> Dict[str, Any]:
    """轮询CodeBuddy认证状态"""
    try:
        headers = get_auth_poll_headers()
        url = f"{get_codebuddy_auth_token_endpoint()}?state={auth_state}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get('code') == 11217:
                    # 仍在等待登录
                    return {
                        "status": "pending",
                        "message": result.get('msg', 'login ing...'),
                        "code": result.get('code')
                    }
                elif result.get('code') == 0 and result.get('data') and result.get('data', {}).get('accessToken'):
                    # 认证成功，获得token
                    data = result.get('data', {})
                    return {
                        "status": "success",
                        "message": "认证成功！",
                        "token_data": {
                            "access_token": data.get('accessToken'),
                            "bearer_token": data.get('accessToken'),
                            "token_type": data.get('tokenType', 'Bearer'),
                            "expires_in": data.get('expiresIn'),
                            "refresh_token": data.get('refreshToken'),
                            "session_state": data.get('sessionState'),
                            "scope": data.get('scope'),
                            "domain": data.get('domain'),
                            "full_response": result
                        }
                    }
                else:
                    # 其他状态码
                    return {
                        "status": "unknown",
                        "message": result.get('msg', 'Unknown status'),
                        "code": result.get('code'),
                        "response": result
                    }
            else:
                return {
                    "status": "error",
                    "message": f"API请求失败，状态码: {response.status_code}",
                    "response_text": response.text
                }
                
    except Exception as e:
        logger.error(f"轮询认证状态失败: {e}")
        return {
            "status": "error",
            "message": f"轮询失败: {str(e)}"
        }

def build_codebuddy_credential(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an upstream login independently from persistence."""
    # Parse display metadata only; token verification remains upstream's job.
    bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
    claims = decode_jwt_claims(bearer_token or "")
    user_id = first_claim(claims, ("email", "preferred_username", "sub"))
    user_id = user_id or token_data.get("domain") or "unknown"
    user_info = select_claims(
        claims,
        (
            "sub",
            "email",
            "preferred_username",
            "name",
            "given_name",
            "family_name",
            "exp",
            "iat",
            "scope",
        ),
    )
    if claims.get("sid") is not None:
        user_info["session_state"] = claims["sid"]

    # 构建完整的凭证数据
    credential_data = {
        "bearer_token": bearer_token,
        "user_id": user_id,
        "created_at": int(time.time()),
        "expires_in": token_data.get("expires_in"),
        "refresh_token": token_data.get("refresh_token"),
        "token_type": token_data.get("token_type", "Bearer"),
        "scope": token_data.get("scope"),
        "domain": token_data.get("domain"),
        "session_state": token_data.get("session_state"),
        "user_info": user_info,
        "full_response": token_data,  # 保存完整的原始响应
    }

    # 移除None值，保持文件整洁
    credential_data = {k: v for k, v in credential_data.items() if v is not None}
    return credential_data


async def save_codebuddy_token(token_data: Dict[str, Any]) -> bool:
    try:
        credential_data = build_codebuddy_credential(token_data)
        timestamp = int(time.time())
        filename = build_credential_filename("codebuddy", credential_data["user_id"], timestamp)
        
        # 使用token管理器保存
        success = codebuddy_token_manager.add_credential_with_data(
            credential_data=credential_data,
            filename=filename
        )
        
        if success:
            logger.info(f"成功保存CodeBuddy token，用户: {credential_data['user_id']}，文件: {filename}")
        
        return success
        
    except Exception as e:
        logger.error(f"保存CodeBuddy token失败: {e}")
        return False

# --- API Endpoints ---
@router.get("/auth/callback", summary="OAuth2 callback endpoint")
async def oauth_callback(code: str = None, state: str = None, error: str = None):
    """OAuth2回调端点"""
    if error:
        return JSONResponse(
            content={"error": error, "error_description": "授权被拒绝或出现错误"},
            status_code=400
        )
    
    return JSONResponse(
        content={
            "message": "授权成功！请返回应用程序。",
            "code": code,
            "state": state
        }
    )


router.include_router(
    create_authorization_router(
        lambda: start_codebuddy_auth(),
        lambda state: poll_codebuddy_auth_status(state),
        lambda token: save_codebuddy_token(token),
    )
)


# Targeted renewal uses the same provider login and token normalization.

router.include_router(
    create_reauthorization_router(
        lambda: codebuddy_token_manager,
        lambda: start_codebuddy_auth(),
        lambda state: poll_codebuddy_auth_status(state),
        build_codebuddy_credential,
    )
)
