"""
CodeBuddy API Router - 兼容CodeBuddy官方API格式
重构版本 - 优化了代码结构、错误处理和资源管理
"""
import json
import time
import uuid
import logging
import asyncio
import copy
import hashlib
import re
from typing import Optional, Dict, Any, List, AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request, Header
from fastapi.responses import StreamingResponse

from .auth import authenticate
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_model_manager import codebuddy_model_manager
from .codebuddy_token_manager import codebuddy_token_manager
from .usage_stats_manager import usage_stats_manager
from .request_log_manager import (
    build_request_preview,
    build_response_preview,
    detect_client,
    get_client_detail,
    request_log_manager,
)
from .keyword_replacer import (
    apply_keyword_replacement_to_messages,
    apply_keyword_replacement_to_tool_definitions,
)
logger = logging.getLogger(__name__)

router = APIRouter()

def get_codebuddy_api_url() -> str:
    """获取当前 CodeBuddy API URL，支持后台热更新站点配置。"""
    from config import get_codebuddy_api_endpoint
    return f"{get_codebuddy_api_endpoint()}/v2/chat/completions"

# --- HTTP 客户端配置 ---
HTTP_CLIENT_CONFIG = {
    "timeout": httpx.Timeout(300.0, connect=30.0, read=300.0),
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

# --- 标准响应头 ---
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*"
}

# --- 辅助函数 ---

def format_sse_error(message: str, error_type: str = "stream_error") -> str:
    """格式化SSE错误响应"""
    error_data = {
        "error": {
            "message": message,
            "type": error_type
        }
    }
    return f'data: {json.dumps(error_data, ensure_ascii=False)}\n\n'

class OpenAICompatibilityConverter:
    """将CodeBuddy格式转换为OpenAI兼容格式"""
    
    @staticmethod
    def convert_tool_call_id(codebuddy_id: str) -> str:
        """转换工具调用ID格式: tooluse_xxx -> call_xxx"""
        if codebuddy_id.startswith('tooluse_'):
            return f"call_{codebuddy_id[8:]}"
        return codebuddy_id
    
    @staticmethod
    def convert_sse_chunk_to_openai_format(chunk_data: Dict[str, Any], tool_call_index_map: Dict[str, int]) -> Dict[str, Any]:
        """将CodeBuddy SSE块转换为OpenAI格式"""
        if not chunk_data.get('choices'):
            return chunk_data

        converted_chunk = chunk_data.copy()
        converted_choices = []

        for choice in chunk_data.get('choices', []):
            converted_choice = choice.copy()
            delta = (choice.get('delta') or {}).copy()

            function_call = delta.get('function_call')
            if (
                function_call is None
                or (
                    isinstance(function_call, dict)
                    and not function_call.get('name')
                    and not function_call.get('arguments')
                )
            ):
                delta.pop('function_call', None)

            if delta.get('tool_calls') == []:
                delta.pop('tool_calls', None)
            if delta.get('reasoning_content') in ("", None):
                delta.pop('reasoning_content', None)
            if delta.get('refusal') in ("", None):
                delta.pop('refusal', None)
            if delta.get('extra_fields') is None:
                delta.pop('extra_fields', None)

            if converted_choice.get('finish_reason') == "":
                converted_choice['finish_reason'] = None
            converted_choice['delta'] = delta
            converted_choices.append(converted_choice)

        converted_chunk['choices'] = converted_choices
        
        choice = converted_chunk['choices'][0]
        delta = choice.get('delta', {})
        tool_calls = delta.get('tool_calls', [])
        
        if not tool_calls:
            return converted_chunk
        
        # 转换工具调用格式
        converted_tool_calls = []
        for tc in tool_calls:
            converted_tc = tc.copy()
            
            # 转换ID格式
            if tc.get('id'):
                original_id = tc['id']
                converted_id = OpenAICompatibilityConverter.convert_tool_call_id(original_id)
                converted_tc['id'] = converted_id
                
                # 分配新的index
                if original_id not in tool_call_index_map:
                    tool_call_index_map[original_id] = len(tool_call_index_map)
                
                converted_tc['index'] = tool_call_index_map[original_id]
            
            # 如果没有ID，使用当前最新的index
            elif tool_call_index_map:
                # 使用最后一个工具调用的index
                converted_tc['index'] = max(tool_call_index_map.values())
            
            converted_tool_calls.append(converted_tc)
        
        converted_chunk['choices'][0]['delta']['tool_calls'] = converted_tool_calls
        
        return converted_chunk

def parse_sse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行SSE数据"""
    if not line.startswith('data: '):
        return None
    
    data = line[6:].strip()
    if not data or data == '[DONE]':
        return None
    
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None

def validate_and_fix_tool_call_args(args: str) -> str:
    """增强版的工具调用参数验证和修复 - 专门处理多工具调用问题"""
    if not args:
        return '{}'
    
    args = args.strip()
    
    # 检查是否是多个JSON对象连接的情况 - 这是多工具调用的主要问题
    if args.count('}{') > 0:
        # 尝试分离多个JSON对象
        json_objects = []
        current_obj = ""
        brace_count = 0
        
        for i, char in enumerate(args):
            current_obj += char
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and current_obj.strip():
                    # 完成了一个JSON对象
                    try:
                        parsed = json.loads(current_obj.strip())
                        json_objects.append(parsed)
                        current_obj = ""
                    except json.JSONDecodeError:
                        current_obj = ""
        
        if json_objects:
            return json.dumps(json_objects[0], ensure_ascii=False)
    
    # 原有的修复逻辑
    try:
        json.loads(args)
        return args
    except json.JSONDecodeError as e:
        
        
        # 尝试修复常见的JSON问题
        original_args = args
        if not args.endswith('}') and args.count('{') > args.count('}'):
            args += '}'
            
        elif not args.endswith(']') and args.count('[') > args.count(']'):
            args += ']'
            
        
        try:
            json.loads(args)
            
            return args
        except json.JSONDecodeError:
            return '{}'

class SSEConnectionManager:
    """SSE 连接管理器，包含重连逻辑"""
    
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    async def stream_with_retry(self, stream_func, *args, **kwargs):
        """带重连的流式处理"""
        for attempt in range(self.max_retries + 1):
            try:
                async for chunk in stream_func(*args, **kwargs):
                    yield chunk
                break  # 成功完成，退出重试循环
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)  # 指数退避: 1s, 2s, 4s
                    logger.warning(f"连接失败，{wait_time}秒后重试 (第{attempt + 1}次): {e}")
                    yield format_sse_error(f"Connection lost, retrying in {wait_time}s... (attempt {attempt + 1})", "connection_retry")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"重连失败，已达到最大重试次数: {e}")
                    yield format_sse_error(f"Connection failed after {self.max_retries} retries: {str(e)}", "connection_failed")
                    raise
            except Exception as e:
                # 其他异常不重试，直接抛出
                logger.error(f"流式处理异常: {e}")
                yield format_sse_error(f"Stream error: {str(e)}", "stream_error")
                raise

class StreamResponseAggregator:
    """流式响应聚合器 - 修复多工具调用问题：使用工具调用ID作为键"""
    
    def __init__(self):
        self.data = {
            "id": None,
            "model": None,
            "content": "",
            "tool_calls": [],
            "finish_reason": None,
            "usage": None,
            "system_fingerprint": None
        }
        # 🔑 关键：使用工具调用ID作为键，因为index都是0会覆盖
        self.tool_call_map = {}  # key: tool_call_id, value: tool_call_data
        self.tool_call_order = []  # 保持工具调用的接收顺序
        self.current_tool_id = None  # 当前正在处理的工具调用ID
    
    def process_chunk(self, obj: Dict[str, Any]):
        """处理单个响应块"""
        # 聚合基本信息
        self.data["id"] = self.data["id"] or obj.get('id')
        self.data["model"] = self.data["model"] or obj.get('model')
        self.data["system_fingerprint"] = obj.get('system_fingerprint') or self.data["system_fingerprint"]
        
        if obj.get('usage'):
            self.data["usage"] = obj.get('usage')
        
        choices = obj.get('choices', [])
        if not choices:
            return
        
        choice = choices[0]
        if choice.get('finish_reason'):
            self.data["finish_reason"] = choice.get('finish_reason')
        
        delta = choice.get('delta', {})
        
        # 聚合内容
        if delta.get('content'):
            self.data["content"] += delta.get('content')
        
        # 处理工具调用
        if delta.get('tool_calls'):
            self._process_tool_calls(delta.get('tool_calls'))
    
    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        """处理工具调用 - 修复版：使用工具调用ID，正确处理分块传输"""
        for tc in tool_calls:
            tool_id = tc.get('id')
            
            # 如果有ID，这是一个新的工具调用
            if tool_id:
                # 新工具调用
                if tool_id not in self.tool_call_map:
                    self.tool_call_map[tool_id] = {
                        'id': tool_id,
                        'type': tc.get('type', 'function'),
                        'function': {
                            'name': '',
                            'arguments': ''
                        }
                    }
                    self.tool_call_order.append(tool_id)
                    self.current_tool_id = tool_id
                    logger.info(f"🔧 新工具调用: {tool_id}")
                else:
                    # 更新当前工具调用ID
                    self.current_tool_id = tool_id
                
                # 更新工具调用信息
                if tc.get('type'):
                    self.tool_call_map[tool_id]['type'] = tc.get('type')
                
                func = tc.get('function', {})
                if func.get('name'):
                    self.tool_call_map[tool_id]['function']['name'] = func.get('name')
                if func.get('arguments'):
                    self.tool_call_map[tool_id]['function']['arguments'] += func.get('arguments')
            
            # 如果没有ID，但有当前工具调用ID，这是增量数据
            elif self.current_tool_id and self.current_tool_id in self.tool_call_map:
                func = tc.get('function', {})
                if func.get('name'):
                    self.tool_call_map[self.current_tool_id]['function']['name'] = func.get('name')
                if func.get('arguments'):
                    self.tool_call_map[self.current_tool_id]['function']['arguments'] += func.get('arguments')
            
            else:
                # 没有ID且没有当前工具调用，跳过
                logger.warning("⚠️ 工具调用缺少ID且无当前工具调用上下文，跳过处理")
    
    def finalize(self) -> Dict[str, Any]:
        """完成聚合并返回最终响应"""
        # 按接收顺序构建工具调用列表
        if self.tool_call_map:
            self.data["tool_calls"] = []
            for tool_id in self.tool_call_order:
                if tool_id in self.tool_call_map:
                    tc = self.tool_call_map[tool_id]
                    # 验证和修复工具调用参数
                    tc['function']['arguments'] = validate_and_fix_tool_call_args(
                        tc['function']['arguments']
                    )
                    self.data["tool_calls"].append(tc)
                    logger.info(f"📋 工具调用: {tool_id} - {tc['function']['name']}")
            
            logger.info(f"✅ 成功聚合 {len(self.data['tool_calls'])} 个工具调用")
        
        # 构建最终响应
        final_message = {"role": "assistant", "content": self.data["content"]}
        if self.data["tool_calls"]:
            final_message["tool_calls"] = self.data["tool_calls"]
        
        finish_reason = "tool_calls" if self.data["tool_calls"] else (self.data["finish_reason"] or "stop")
        
        final_response = {
            "id": self.data["id"] or str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.data["model"] or "unknown",
            "choices": [
                {
                    "index": 0,
                    "message": final_message,
                    "finish_reason": finish_reason,
                    "logprobs": None
                }
            ]
        }
        
        if self.data["usage"]:
            final_response["usage"] = self.data["usage"]
        if self.data["system_fingerprint"]:
            final_response["system_fingerprint"] = self.data["system_fingerprint"]
        
        return final_response

class CodeBuddyStreamService:
    """CodeBuddy 流式服务类 - 职责分离，使用连接池优化"""
    
    def __init__(self):
        self.connection_manager = SSEConnectionManager(max_retries=3, retry_delay=1.0)
    
    def _handle_api_error(self, status_code: int, error_msg: str) -> None:
        """统一的API错误处理 - 直接抛出异常"""
        logger.error(f"CodeBuddy API错误: {status_code} - {error_msg}")
        
        if status_code == 401:
            raise HTTPException(status_code=401, detail="CodeBuddy API authentication failed")
        elif status_code == 429:
            raise HTTPException(status_code=429, detail="CodeBuddy API rate limit exceeded")
        elif status_code >= 500:
            raise HTTPException(status_code=502, detail="CodeBuddy API server error")
        else:
            raise HTTPException(status_code=status_code, detail=f"CodeBuddy API error: {error_msg}")
    
    async def handle_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str]) -> StreamingResponse:
        """处理流式响应 - 使用OpenAI兼容性转换器修复格式问题"""
        async def stream_core():
            client = await get_http_client()
            async with client.stream("POST", get_codebuddy_api_url(), json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_msg = error_text.decode('utf-8', errors='ignore')
                    yield format_sse_error(f"CodeBuddy API error: {response.status_code} - {error_msg}", "api_error")
                    return
                
                buffer = ""
                tool_call_index_map = {}  # 用于跟踪工具调用ID到index的映射
                
                
                async for chunk in response.aiter_text(chunk_size=8192):
                    if not chunk:
                        continue
                    
                    buffer += chunk
                    
                    # 处理完整的SSE行
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        
                        # 跳过空行和注释行
                        if not line.strip() or line.startswith(':'):
                            continue
                        
                        # 检查是否结束
                        if '[DONE]' in line:
                            yield 'data: [DONE]\n\n'
                            return
                        
                        # 解析SSE数据
                        chunk_data = parse_sse_line(line)
                        if chunk_data:
                            # 🔑 关键修改：使用OpenAI兼容性转换器
                            converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                                chunk_data, tool_call_index_map
                            )
                            
                            # 重新格式化为SSE格式并发送
                            converted_line = f"data: {json.dumps(converted_chunk, ensure_ascii=False)}"
                            yield converted_line + '\n\n'
                        else:
                            # 非数据行直接转发
                            yield line + '\n\n'
                
                # 处理缓冲区中剩余的数据
                if buffer.strip():
                    chunk_data = parse_sse_line(buffer.strip())
                    if chunk_data:
                        converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                            chunk_data, tool_call_index_map
                        )
                        converted_line = f"data: {json.dumps(converted_chunk, ensure_ascii=False)}"
                        yield converted_line + '\n\n'
                    else:
                        yield buffer + '\n\n'
        
        async def stream_with_retry():
            async for chunk in self.connection_manager.stream_with_retry(stream_core):
                yield chunk
        
        return StreamingResponse(stream_with_retry(), media_type="text/event-stream", headers=SSE_HEADERS)
    
    async def handle_non_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        """处理非流式响应 - 使用修复后的聚合器，支持多工具调用"""
        try:
            client = await get_http_client()
            response = await client.post(get_codebuddy_api_url(), json=payload, headers=headers)
            
            if response.status_code != 200:
                error_msg = response.text
                self._handle_api_error(response.status_code, error_msg)
            
            aggregator = StreamResponseAggregator()
            buffer = ""
            
            async for chunk in response.aiter_text():
                if not chunk:
                    continue
                buffer += chunk
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    obj = parse_sse_line(line)
                    if obj:
                        aggregator.process_chunk(obj)
            
            if buffer.strip():
                obj = parse_sse_line(buffer.strip())
                if obj:
                    aggregator.process_chunk(obj)
            
            return aggregator.finalize()
            
        except httpx.TimeoutException:
            logger.error("CodeBuddy API 超时")
            raise HTTPException(status_code=504, detail="CodeBuddy API timeout")
        except httpx.NetworkError as e:
            logger.error(f"网络错误: {e}")
            raise HTTPException(status_code=502, detail=f"Network error: {str(e)}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"请求异常: {e}")
            raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

class RequestProcessor:
    """请求预处理器 - 线程安全的请求处理"""
    
    @staticmethod
    def prepare_payload(request_body: Dict[str, Any]) -> Dict[str, Any]:
        """准备请求载荷"""
        payload = copy.deepcopy(request_body)
        payload["stream"] = True  # CodeBuddy 只支持流式请求
        
        # 处理消息长度要求：CodeBuddy要求至少2条消息
        messages = payload.get("messages", [])
        if len(messages) == 1 and messages[0].get("role") == "user":
            system_msg = {"role": "system", "content": "You are a helpful assistant."}
            payload["messages"] = [system_msg] + messages
        
        # 应用关键词替换，避免上游把 cc-switch/Codex 的兼容提示误判为敏感内容。
        payload["messages"] = apply_keyword_replacement_to_messages(payload.get("messages", []))
        if "tools" in payload:
            payload["tools"] = apply_keyword_replacement_to_tool_definitions(payload.get("tools"))
        
        return payload
    
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
    def get_valid_credential() -> Dict[str, Any]:
        """获取有效凭证，包含错误处理"""
        try:
            credential = codebuddy_token_manager.get_next_credential()
            if not credential:
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
        obj = parse_sse_line(line)
        if not obj:
            continue
        if obj.get("usage"):
            metrics["usage"] = obj["usage"]
        if obj.get("error"):
            error_value = obj["error"]
            error_type = error_value.get("type") if isinstance(error_value, dict) else None
            if error_type == "connection_retry":
                metrics["upstream_attempts"] += 1
            else:
                metrics["error"] = error_value.get("message") if isinstance(error_value, dict) else str(error_value)
        for choice in obj.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                metrics["output"].append(str(delta["content"]))


def attach_stream_audit(response: StreamingResponse, audit: Dict[str, Any]) -> StreamingResponse:
    original_iterator = response.body_iterator

    async def audited_iterator():
        metrics: Dict[str, Any] = {
            "usage": None,
            "output": [],
            "error": None,
            "upstream_attempts": 1,
        }
        status_code = response.status_code or 200
        error = None
        try:
            async for chunk in original_iterator:
                extract_stream_metrics(chunk, metrics)
                yield chunk
        except asyncio.CancelledError:
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
                upstream_attempts=metrics["upstream_attempts"],
                error=error,
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
        
        # 获取有效凭证
        credential = CredentialManager.get_valid_credential()
        
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
        service = CodeBuddyStreamService()
        client_wants_stream = request_body.get("stream", False)
        
        try:
            if client_wants_stream:
                response = await service.handle_stream_response(payload, headers)
                return attach_stream_audit(response, audit)

            response = await service.handle_non_stream_response(payload, headers)
            finish_request_audit(
                audit,
                status_code=200,
                usage=response.get("usage"),
                output_value=get_chat_output_text(response),
            )
            return response
        except HTTPException as exc:
            finish_request_audit(audit, exc.status_code, error=str(exc.detail))
            raise
        except Exception as exc:
            finish_request_audit(audit, 500, error=str(exc))
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
        chat_request = responses_request_to_chat_request(request_body)
        RequestProcessor.validate_request(chat_request)

        credential = CredentialManager.get_valid_credential()
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

        service = CodeBuddyStreamService()
        try:
            chat_response = await service.handle_non_stream_response(payload, headers)
        except HTTPException as exc:
            finish_request_audit(audit, exc.status_code, error=str(exc.detail))
            raise
        except Exception as exc:
            finish_request_audit(audit, 500, error=str(exc))
            raise

        response_payload = chat_response_to_responses_response(chat_response, response_id)
        finish_request_audit(
            audit,
            status_code=200,
            usage=chat_response.get("usage"),
            output_value=response_payload.get("output_text"),
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


@router.post("/v1/credentials", summary="Add a new credential")
async def add_credential(
    request: Request,
    _token: str = Depends(authenticate)
):
    """添加一个新的认证凭证"""
    try:
        data = await request.json()
        if not data.get("bearer_token"):
            raise HTTPException(status_code=422, detail="bearer_token is required")

        success = codebuddy_token_manager.add_credential(
            data.get("bearer_token"),
            data.get("user_id"),
            data.get("filename")
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save credential file")
        
        return {"message": "Credential added successfully"}

    except Exception as e:
        logger.error(f"添加凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/select", summary="Manually select a credential")
async def select_credential(
    request: Request,
    _token: str = Depends(authenticate)
):
    """手动选择指定的凭证"""
    try:
        data = await request.json()
        index = data.get("index")
        if index is None:
            raise HTTPException(status_code=422, detail="index is required")

        if not codebuddy_token_manager.set_manual_credential(index):
            raise HTTPException(status_code=400, detail="Invalid credential index")
        
        return {"message": f"Credential #{index + 1} selected successfully"}

    except Exception as e:
        logger.error(f"选择凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/auto", summary="Resume automatic credential rotation")
async def resume_auto_rotation(_token: str = Depends(authenticate)):
    """恢复自动凭证轮换"""
    try:
        codebuddy_token_manager.clear_manual_selection()
        return {"message": "Resumed automatic credential rotation"}

    except Exception as e:
        logger.error(f"恢复自动轮换失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/toggle-rotation", summary="Toggle automatic credential rotation")
async def toggle_auto_rotation(_token: str = Depends(authenticate)):
    """切换自动轮换开关"""
    try:
        is_enabled = codebuddy_token_manager.toggle_auto_rotation()
        status = "enabled" if is_enabled else "disabled"
        message = f"Auto rotation {status}"
        return {
            "message": message,
            "auto_rotation_enabled": is_enabled
        }

    except Exception as e:
        logger.error(f"切换自动轮换失败: {e}")
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
