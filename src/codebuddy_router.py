"""
CodeBuddy API Router - 兼容CodeBuddy官方API格式
重构版本 - 优化了代码结构、错误处理和资源管理
"""
import json
import time
import uuid
import logging
import asyncio
import hashlib
import re
from typing import Optional, Dict, Any, List

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request, Header
from fastapi.responses import StreamingResponse

from .auth import authenticate
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_model_manager import codebuddy_model_manager
from .codebuddy_token_manager import codebuddy_token_manager
from .codebuddy_refresh import (
    CredentialRefreshUnavailable,
    SessionExpiredError,
    codebuddy_credential_refresher,
)
from .usage_stats_manager import usage_stats_manager
from .console.diagnostics import DiagnosticsRepository
from .chat.observation import AttemptObserver
from .chat.service import CodeBuddyStreamService
from .chat.request import ChatRequestError, prepare_chat_payload
from .chat.protocol import SSE_HEADERS, parse_sse_line

from .request_log_manager import (
    build_request_preview,
    build_response_preview,
    detect_client,
    get_client_detail,
    request_log_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter()

def get_codebuddy_api_url() -> str:
    """获取当前 CodeBuddy API URL，支持后台热更新站点配置。"""
    from config import get_codebuddy_api_endpoint
    return f"{get_codebuddy_api_endpoint()}/v2/chat/completions"

# --- HTTP 客户端配置 ---
HTTP_CLIENT_CONFIG = {
    "timeout": httpx.Timeout(300.0, connect=8.0, read=300.0),
    "limits": httpx.Limits(max_keepalive_connections=20, max_connections=100)
}

# --- 异步安全的 HTTP 客户端池 ---
_http_client_pool: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

async def get_http_client() -> httpx.AsyncClient:
    """获取全局 HTTP 客户端池 - 异步安全"""
    global _http_client_pool
    if _http_client_pool is None:
        async with _client_lock:
            # 双重检查锁定模式 - 异步版本
            if _http_client_pool is None:
                _http_client_pool = httpx.AsyncClient(**HTTP_CLIENT_CONFIG)
    return _http_client_pool

async def close_http_client():
    """关闭全局 HTTP 客户端池 - 异步安全"""
    global _http_client_pool
    async with _client_lock:
        if _http_client_pool is not None:
            await _http_client_pool.aclose()
            _http_client_pool = None

# --- 应用生命周期管理 ---
class AppLifecycleManager:
    """应用生命周期管理器 - 处理资源清理"""
    
    @staticmethod
    async def startup():
        """应用启动时的初始化"""
        logger.info("CodeBuddy Router 启动中...")
        # 预热连接池
        await get_http_client()
        logger.info("HTTP 连接池已初始化")
    
    @staticmethod
    async def shutdown():
        """应用关闭时的清理"""
        logger.info("CodeBuddy Router 关闭中...")
        await close_http_client()
        logger.info("资源清理完成")

# 导出生命周期管理器供主应用使用
lifecycle_manager = AppLifecycleManager()

async def next_credential_after_exhaustion(index: Optional[int]) -> Optional[Dict[str, Any]]:
    if index is None:
        return None
    codebuddy_token_manager.mark_credential_exhausted(index)
    try:
        return await get_ready_credential()
    except HTTPException:
        return None



def create_chat_service(credential=None, observer=None):
    """Composition boundary: bind upstream execution to application resources."""
    return CodeBuddyStreamService(
        client_factory=get_http_client, api_url_factory=get_codebuddy_api_url,
        credentials=codebuddy_token_manager,
        select_after_exhaustion=next_credential_after_exhaustion,
        credential=credential, observer=observer,
    )


def resolve_credential_selector(body: Dict[str, Any]) -> Optional[int]:
    """Resolve an explicit admin test target before forwarding the payload."""
    index = body.pop("_credential_index", None)
    filename = body.pop("_credential_filename", None)
    if index is not None and filename is not None:
        raise HTTPException(422, "Specify only one credential selector")
    if filename is not None:
        if not isinstance(filename, str):
            raise HTTPException(422, "_credential_filename must be a string")
        index = next((row['index'] for row in codebuddy_token_manager.get_credentials_info()
                      if row['filename'] == filename), None)
        if index is None:
            raise HTTPException(409, "凭证列表已变更，请刷新后重试")
    if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
        raise HTTPException(422, "_credential_index must be an integer")
    return index


class RequestProcessor:
    """请求预处理器 - 线程安全的请求处理"""
    
    @staticmethod
    def prepare_payload(request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Map domain validation failures to the public HTTP request boundary."""
        try:
            return prepare_chat_payload(request_body)
        except ChatRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @staticmethod
    def validate_request(request_body: Dict[str, Any]) -> None:
        """验证请求参数"""
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        messages = request_body.get("messages")
        if not messages or not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="Messages field is required and must be an array")
        
        if not messages:
            raise HTTPException(status_code=400, detail="At least one message is required")
        
        # 验证消息格式
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise HTTPException(status_code=400, detail=f"Message {i} must be an object")
            if "role" not in msg or "content" not in msg:
                raise HTTPException(status_code=400, detail=f"Message {i} must have 'role' and 'content' fields")

class CredentialManager:
    """凭证管理器 - 线程安全的凭证获取"""
    
    @staticmethod
    def get_valid_credential(credential_index: Optional[int] = None) -> Dict[str, Any]:
        """获取有效凭证，包含错误处理"""
        try:
            if credential_index is None:
                credential = codebuddy_token_manager.get_next_credential()
            else:
                credential = codebuddy_token_manager.get_credential_by_index(credential_index)
            if not credential:
                if (credential_index is None
                        and codebuddy_token_manager.all_available_credentials_exhausted()):
                    raise HTTPException(status_code=402, detail="所有可用凭证积分不足")
                raise HTTPException(status_code=401, detail="没有可用的CodeBuddy凭证")
            
            bearer_token = credential.get('bearer_token')
            if not bearer_token:
                raise HTTPException(status_code=401, detail="无效的CodeBuddy凭证")
            
            return credential
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取凭证失败: {e}")
            raise HTTPException(status_code=401, detail="凭证获取失败")


async def get_ready_credential(credential_index: Optional[int] = None) -> Dict[str, Any]:
    """Refresh old sessions and move past revoked accounts in auto mode."""
    attempts = 1 if credential_index is not None else max(1, len(codebuddy_token_manager.credentials))
    for _ in range(attempts):
        credential = CredentialManager.get_valid_credential(credential_index)
        try:
            return await codebuddy_credential_refresher.ensure_fresh(credential)
        except CredentialRefreshUnavailable:
            raise HTTPException(status_code=503, detail="CodeBuddy 凭证续期暂时失败，请稍后重试")
        except SessionExpiredError:
            if credential_index is not None:
                break
    raise HTTPException(status_code=401, detail="CodeBuddy 凭证已失效，请重新授权")

def create_endpoint_probe(endpoint: str) -> Dict[str, Any]:
    """Return a small unauthenticated probe response for endpoint speed checks."""
    return {
        "object": "endpoint",
        "endpoint": endpoint,
        "status": "ok",
        "service": "codebuddy2api",
    }


def estimate_request_tokens(value: Any) -> int:
    """Estimate tokens when the upstream does not return usage metadata."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(value)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", serialized))
    non_cjk_count = len(re.sub(r"[\u4e00-\u9fff]", "", serialized))
    return max(1, cjk_count + non_cjk_count // 4)


def start_request_audit(
    request: Request,
    request_body: Dict[str, Any],
    endpoint: str,
    model: str,
    credential: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    conversation_id = request.headers.get("x-conversation-id")
    canonical_body = json.dumps(
        request_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    credential = credential or {}
    try:
        record_id = request_log_manager.start_request(
            request_id=request_id,
            conversation_id=conversation_id,
            endpoint=endpoint,
            model=model or "unknown",
            client=detect_client(request.headers, request_body),
            client_detail=get_client_detail(request.headers),
            client_host=request.client.host if request.client else None,
            credential=credential.get("_credential_filename"),
            credential_user_id=credential.get("user_id"),
            request_preview=build_request_preview(request_body),
            request_hash=hashlib.sha256(canonical_body.encode("utf-8")).hexdigest(),
            is_streaming=bool(request_body.get("stream", False)),
        )
    except Exception as exc:
        logger.error(f"创建请求明细失败: {exc}")
        record_id = None
    return {
        "record_id": record_id,
        "request_id": request_id,
        "started_at": time.perf_counter(),
        "estimated_input_tokens": estimate_request_tokens(request_body),
    }


def finish_request_audit(
    audit: Dict[str, Any],
    status_code: int,
    usage: Optional[Dict[str, Any]] = None,
    output_value: Any = None,
    upstream_attempts: int = 1,
    error: Optional[str] = None,
    credential: Optional[Dict[str, Any]] = None,
) -> None:
    if audit.get("record_id") is None:
        return
    latency_ms = max(0, int((time.perf_counter() - audit["started_at"]) * 1000))
    estimated_output_tokens = None
    if output_value not in (None, ""):
        estimated_output_tokens = estimate_request_tokens(output_value)
    try:
        request_log_manager.finish_request(
            record_id=audit["record_id"],
            status_code=status_code,
            latency_ms=latency_ms,
            usage=usage,
            upstream_attempts=upstream_attempts,
            estimated_input_tokens=audit.get("estimated_input_tokens"),
            estimated_output_tokens=estimated_output_tokens,
            response_preview=build_response_preview(output_value),
            error=error,
            credential=(credential or {}).get("_credential_filename"),
            credential_user_id=(credential or {}).get("user_id"),
        )
    except Exception as exc:
        logger.error(f"完成请求明细失败: {exc}")


def extract_stream_metrics(chunk: Any, metrics: Dict[str, Any]) -> None:
    """Collect usage/output from an OpenAI-compatible SSE chunk."""
    if isinstance(chunk, bytes):
        text = chunk.decode("utf-8", errors="ignore")
    else:
        text = str(chunk)
    for line in text.splitlines():
        if line.strip() == "data: [DONE]":
            metrics["complete"] = True
        obj = parse_sse_line(line)
        if not obj:
            continue
        if obj.get("usage"):
            metrics["usage"] = obj["usage"]
        if obj.get("error"):
            error_value = obj["error"]
            metrics["error"] = error_value.get("message") if isinstance(error_value, dict) else str(error_value)
        for choice in obj.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                metrics["output"].append(str(delta["content"]))


def attach_stream_audit(
    response: StreamingResponse, audit: Dict[str, Any], service: CodeBuddyStreamService
) -> StreamingResponse:
    original_iterator = response.body_iterator

    async def audited_iterator():
        metrics: Dict[str, Any] = {
            "usage": None,
            "output": [],
            "error": None,
            "complete": False,
        }
        status_code = response.status_code or 200
        error = None
        try:
            async for chunk in original_iterator:
                extract_stream_metrics(chunk, metrics)
                yield chunk
        except asyncio.CancelledError:
            if not metrics["complete"]:
                status_code = 499
                error = "Client disconnected"
            raise
        except Exception as exc:
            status_code = 500
            error = str(exc)
            raise
        finally:
            if metrics["error"] and not error:
                status_code = 502
                error = metrics["error"]
            finish_request_audit(
                audit,
                status_code=status_code,
                usage=metrics["usage"],
                output_value="".join(metrics["output"]),
                upstream_attempts=service.attempt_count,
                error=error,
                credential=service.credential_for_audit,
            )

    response.body_iterator = audited_iterator()
    return response


def get_chat_output_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")

def responses_input_to_messages(request_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert a minimal OpenAI Responses input into chat messages."""
    input_value = request_body.get("input")
    instructions = request_body.get("instructions")
    messages: List[Dict[str, Any]] = []

    if instructions:
        messages.append({"role": "system", "content": instructions})

    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
    elif isinstance(input_value, list):
        for item in input_value:
            if not isinstance(item, dict):
                continue

            role = item.get("role") or "user"
            content = item.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text") or part.get("content")
                        if text:
                            text_parts.append(str(text))
                    elif part:
                        text_parts.append(str(part))
                content = "\n".join(text_parts)

            messages.append({"role": role, "content": content})

    if not messages:
        raise HTTPException(
            status_code=400,
            detail="Responses request requires a non-empty 'input' field",
        )

    return messages

def responses_request_to_chat_request(request_body: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a minimal Responses API request to Chat Completions format."""
    chat_request = {
        "model": request_body.get("model"),
        "messages": responses_input_to_messages(request_body),
        "stream": False,
    }

    if not chat_request["model"]:
        raise HTTPException(status_code=400, detail="Model field is required")

    for key in ("temperature", "top_p", "max_tokens", "tools", "tool_choice"):
        if key in request_body:
            chat_request[key] = request_body[key]

    if "max_output_tokens" in request_body:
        chat_request["max_tokens"] = request_body["max_output_tokens"]

    return chat_request

def chat_response_to_responses_response(
    chat_response: Dict[str, Any],
    response_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a Chat Completions response to a minimal Responses API response."""
    choice = (chat_response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    output_id = f"msg_{uuid.uuid4().hex}"

    return {
        "id": response_id or f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": chat_response.get("created", int(time.time())),
        "status": "completed",
        "model": chat_response.get("model", "unknown"),
        "output": [
            {
                "id": output_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": content,
        "usage": chat_response.get("usage"),
    }

# --- API Endpoints ---

@router.get("/v1")
async def openai_v1_root():
    """轻量 OpenAI 兼容根路径探测，供 cc-switch 等工具测速。"""
    return create_endpoint_probe("/codebuddy/v1")

@router.get("/v1/chat/completions")
async def chat_completions_probe():
    """轻量 Chat Completions 端点探测，供 cc-switch 等工具测速。"""
    return create_endpoint_probe("/codebuddy/v1/chat/completions")

@router.get("/v1/responses")
async def responses_probe():
    """轻量 Responses 端点探测，供 cc-switch/Codex 等工具测速。"""
    return create_endpoint_probe("/codebuddy/v1/responses")

@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    x_conversation_request_id: Optional[str] = Header(None, alias="X-Conversation-Request-ID"),
    x_conversation_message_id: Optional[str] = Header(None, alias="X-Conversation-Message-ID"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    _token: str = Depends(authenticate)
):
    """CodeBuddy V1 聊天完成API - 重构后的简洁版本"""
    try:
        # 解析和验证请求体
        try:
            request_body = await request.json()
        except Exception as e:
            logger.error(f"解析请求体失败: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON request body: {str(e)}")
        
        # 验证请求参数
        RequestProcessor.validate_request(request_body)

        # The management UI can test one credential without changing the
        # currently selected credential or the rotation mode.  This field is
        # intentionally removed before forwarding the request upstream.
        credential_index = resolve_credential_selector(request_body)

        # 获取有效凭证
        credential = await get_ready_credential(credential_index)
        
        # 预处理请求并创建持久化请求明细
        payload = RequestProcessor.prepare_payload(request_body)
        audit = start_request_audit(
            request,
            request_body,
            endpoint="/codebuddy/v1/chat/completions",
            model=payload.get("model", "unknown"),
            credential=credential,
        )

        # 生成请求头
        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=credential.get('bearer_token'),
            user_id=credential.get('user_id'),
            conversation_id=x_conversation_id,
            conversation_request_id=x_conversation_request_id,
            conversation_message_id=x_conversation_message_id,
            request_id=x_request_id or audit["request_id"]
        )
        usage_stats_manager.record_model_usage(payload.get("model", "unknown"))
        
        # 使用服务类处理请求
        service = create_chat_service(credential, AttemptObserver(
            DiagnosticsRepository(request_log_manager.connect), audit.get("record_id")
        ))
        client_wants_stream = request_body.get("stream", False)
        
        try:
            cred_idx = credential.get("_credential_index")
            retry_on_exhaustion = credential_index is None and codebuddy_token_manager.mode == 'auto'
            if client_wants_stream:
                response = await service.handle_stream_response(
                    payload, headers, cred_idx, retry_on_exhaustion=retry_on_exhaustion,
                )
                return attach_stream_audit(response, audit, service)

            response = await service.handle_non_stream_response(
                payload, headers, cred_idx, retry_on_exhaustion=retry_on_exhaustion,
            )
            finish_request_audit(
                audit,
                status_code=200,
                usage=response.get("usage"),
                output_value=get_chat_output_text(response),
                upstream_attempts=service.attempt_count,
                credential=service.credential_for_audit,
            )
            return response
        except HTTPException as exc:
            finish_request_audit(
                audit, exc.status_code, upstream_attempts=service.attempt_count,
                error=str(exc.detail), credential=service.credential_for_audit,
            )
            raise
        except Exception as exc:
            finish_request_audit(
                audit, 500, upstream_attempts=service.attempt_count, error=str(exc),
                credential=service.credential_for_audit,
            )
            raise
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CodeBuddy V1 API错误: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")

@router.post("/v1/responses")
async def responses(
    request: Request,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    x_conversation_request_id: Optional[str] = Header(None, alias="X-Conversation-Request-ID"),
    x_conversation_message_id: Optional[str] = Header(None, alias="X-Conversation-Message-ID"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    _token: str = Depends(authenticate)
):
    """Minimal Responses API adapter for connectivity checks and simple Codex probes."""
    try:
        try:
            request_body = await request.json()
        except Exception as e:
            logger.error(f"解析Responses请求体失败: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON request body: {str(e)}")

        response_id = f"resp_{uuid.uuid4().hex}"
        client_wants_stream = request_body.get("stream", False)
        credential_index = resolve_credential_selector(request_body)
        chat_request = responses_request_to_chat_request(request_body)
        RequestProcessor.validate_request(chat_request)

        credential = await get_ready_credential(credential_index)
        payload = RequestProcessor.prepare_payload(chat_request)
        audit = start_request_audit(
            request,
            request_body,
            endpoint="/codebuddy/v1/responses",
            model=payload.get("model", "unknown"),
            credential=credential,
        )
        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=credential.get('bearer_token'),
            user_id=credential.get('user_id'),
            conversation_id=x_conversation_id,
            conversation_request_id=x_conversation_request_id,
            conversation_message_id=x_conversation_message_id,
            request_id=x_request_id or audit["request_id"]
        )
        usage_stats_manager.record_model_usage(payload.get("model", "unknown"))

        service = create_chat_service(credential, AttemptObserver(
            DiagnosticsRepository(request_log_manager.connect), audit.get("record_id")
        ))
        try:
            chat_response = await service.handle_non_stream_response(
                payload, headers, credential.get("_credential_index"),
                retry_on_exhaustion=credential_index is None and codebuddy_token_manager.mode == 'auto',
            )
        except HTTPException as exc:
            finish_request_audit(
                audit, exc.status_code, upstream_attempts=service.attempt_count,
                error=str(exc.detail), credential=service.credential_for_audit,
            )
            raise
        except Exception as exc:
            finish_request_audit(
                audit, 500, upstream_attempts=service.attempt_count, error=str(exc),
                credential=service.credential_for_audit,
            )
            raise

        response_payload = chat_response_to_responses_response(chat_response, response_id)
        finish_request_audit(
            audit,
            status_code=200,
            usage=chat_response.get("usage"),
            output_value=response_payload.get("output_text"),
            upstream_attempts=service.attempt_count,
            credential=service.credential_for_audit,
        )

        if not client_wants_stream:
            return response_payload

        async def stream_response():
            yield (
                "event: response.created\n"
                f"data: {json.dumps({'type': 'response.created', 'response': response_payload}, ensure_ascii=False)}\n\n"
            )
            output_text = response_payload.get("output_text") or ""
            if output_text:
                yield (
                    "event: response.output_text.delta\n"
                    f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': output_text, 'output_index': 0, 'content_index': 0}, ensure_ascii=False)}\n\n"
                )
            yield (
                "event: response.completed\n"
                f"data: {json.dumps({'type': 'response.completed', 'response': response_payload}, ensure_ascii=False)}\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_response(), media_type="text/event-stream", headers=SSE_HEADERS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Responses API错误: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")

@router.get("/v1/models")
async def list_v1_models():
    """获取CodeBuddy V1模型列表"""
    try:
        models = list(dict.fromkeys(await codebuddy_model_manager.get_models()))
        return {
            "object": "list",
            "data": [{
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "capabilities": ["chat.completions", "responses"],
                "owned_by": "codebuddy"
            } for model in models]
        }
        
    except Exception as e:
        logger.error(f"获取V1模型列表错误: {e}")
        raise HTTPException(status_code=502, detail=f"获取模型列表失败: {str(e)}")

@router.get("/v1/credentials", summary="List all available credentials")
async def list_credentials(_token: str = Depends(authenticate)):
    """列出所有可用凭证的详细信息，包括过期状态"""
    try:
        credentials_info = codebuddy_token_manager.get_credentials_info()
        safe_credentials = []
        
        credentials = codebuddy_token_manager.get_all_credentials()
        
        for info in credentials_info:
            bearer_token = credentials[info['index']].get("bearer_token", "") if info['index'] < len(credentials) else ""
            
            # 格式化时间显示
            if info['time_remaining'] is not None and info['time_remaining'] > 0:
                days, remainder = divmod(info['time_remaining'], 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes = remainder // 60
                time_remaining_str = f"{days}d {hours}h" if days > 0 else f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            else:
                time_remaining_str = "Expired" if info['time_remaining'] is not None else "Unknown"
            
            safe_credentials.append({
                **info,  # 展开所有原始信息
                "time_remaining_str": time_remaining_str,
                "has_token": bool(bearer_token),
                "token_preview": f"{bearer_token[:10]}...{bearer_token[-4:]}" if len(bearer_token) > 14 else "Invalid Token"
            })
        
        return {"credentials": safe_credentials}
        
    except Exception as e:
        logger.error(f"获取凭证列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/set-mode", summary="Set credential mode (auto/manual)")
async def set_credential_mode(request: Request, _token: str = Depends(authenticate)):
    """设置凭证模式: auto=积分不足时自动切换, manual=锁定指定凭证(index 必填)"""
    try:
        data = await request.json()
        mode = data.get("mode")
        index = data.get("index")
        if mode not in ("auto", "manual"):
            raise HTTPException(status_code=422, detail="mode must be 'auto' or 'manual'")
        if mode == "manual" and index is None:
            raise HTTPException(status_code=422, detail="index is required for manual mode")

        success = codebuddy_token_manager.set_mode(mode, index)
        if not success:
            raise HTTPException(status_code=400, detail="Invalid mode or credential index")

        return {
            "message": f"Switched to {mode} mode",
            "mode": mode,
            "manual_selected_index": codebuddy_token_manager.manual_selected_index
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置凭证模式失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v1/credentials/current", summary="Get current credential info")
async def get_current_credential(_token: str = Depends(authenticate)):
    """获取当前使用的凭证信息"""
    try:
        info = codebuddy_token_manager.get_current_credential_info()
        return info

    except Exception as e:
        logger.error(f"获取当前凭证信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/delete", summary="Delete a credential by index")
async def delete_credential(request: Request, _token: str = Depends(authenticate)):
    """删除一个凭证文件（通过索引）并从列表中移除"""
    try:
        data = await request.json()
        index = data.get("index")
        if index is None or not isinstance(index, int):
            raise HTTPException(status_code=422, detail="Valid integer index is required")

        if not codebuddy_token_manager.delete_credential_by_index(index):
            raise HTTPException(status_code=400, detail="Invalid index or failed to delete credential")

        return {"message": f"Credential #{index + 1} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
