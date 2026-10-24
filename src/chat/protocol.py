"""Pure OpenAI-compatible SSE conversion and response aggregation."""

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- 标准响应头 ---
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}

# --- 辅助函数 ---


def format_sse_error(message: str, error_type: str = "stream_error") -> str:
    """格式化SSE错误响应"""
    error_data = {"error": {"message": message, "type": error_type}}
    return f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"


class InsufficientCreditError(Exception):
    """Upstream explicitly reported that this credential has no credits."""

    status_code = 402

    def __init__(self, message: str, can_retry: bool = True):
        super().__init__(message)
        self.can_retry = can_retry


def is_insufficient_credit_error(value: Any) -> bool:
    """Recognize credit errors without treating ordinary rate limits as exhaustion."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            pass

    fields = ("code", "type", "message", "msg", "detail", "error_description")

    def collect_error_parts(item: Any) -> List[str]:
        if isinstance(item, dict):
            parts = [str(item.get(key, "")) for key in fields]
            # CodeBuddy nests quota details under error.data, unlike the
            # top-level shape used by the other supported error responses.
            for key in ("error", "data"):
                if key in item:
                    parts.extend(collect_error_parts(item[key]))
            return parts
        if isinstance(item, list):
            return [part for nested in item for part in collect_error_parts(nested)]
        return [str(item)] if item is not None else []

    message = " ".join(collect_error_parts(value)).lower()
    return any(marker in message for marker in (
        "积分不足", "积分已耗尽", "余额不足", "余额已耗尽", "额度不足",
        "额度已用尽", "额度用尽", "额度已耗尽", "点数不足",
        "insufficient_credit", "insufficient_quota", "insufficient balance",
        "insufficient credits", "insufficient_balance", "not enough credit",
        "credit balance is too low", "credits exhausted", "quota exhausted",
    ))


def headers_for_credential(
    headers: Dict[str, str], credential: Dict[str, Any]
) -> Dict[str, str]:
    updated = dict(headers)
    updated["Authorization"] = f"Bearer {credential['bearer_token']}"
    updated["X-User-Id"] = credential.get("user_id") or updated.get("X-User-Id", "")
    return updated


def has_stream_output(chunk: str) -> bool:
    """Keep metadata chunks buffered until retry is no longer safe."""
    if "[DONE]" in chunk:
        return True
    event = parse_sse_line(chunk.strip())
    if not event:
        return False
    if event.get("error"):
        return True
    return any(
        choice.get("finish_reason")
        or any(
            delta.get(key)
            for key in (
                "content",
                "reasoning_content",
                "tool_calls",
                "function_call",
                "refusal",
            )
        )
        for choice in event.get("choices", [])
        for delta in [choice.get("delta") or {}]
    )


class OpenAICompatibilityConverter:
    """将CodeBuddy格式转换为OpenAI兼容格式"""

    @staticmethod
    def convert_tool_call_id(codebuddy_id: str) -> str:
        """转换工具调用ID格式: tooluse_xxx -> call_xxx"""
        if codebuddy_id.startswith("tooluse_"):
            return f"call_{codebuddy_id[8:]}"
        return codebuddy_id

    @staticmethod
    def convert_sse_chunk_to_openai_format(
        chunk_data: Dict[str, Any], tool_call_index_map: Dict[str, int]
    ) -> Dict[str, Any]:
        """将CodeBuddy SSE块转换为OpenAI格式"""
        if not chunk_data.get("choices"):
            return chunk_data

        converted_chunk = chunk_data.copy()
        converted_choices = []

        for choice in chunk_data.get("choices", []):
            converted_choice = choice.copy()
            delta = (choice.get("delta") or {}).copy()

            function_call = delta.get("function_call")
            if function_call is None or (
                isinstance(function_call, dict)
                and not function_call.get("name")
                and not function_call.get("arguments")
            ):
                delta.pop("function_call", None)

            if delta.get("tool_calls") == []:
                delta.pop("tool_calls", None)
            if delta.get("reasoning_content") in ("", None):
                delta.pop("reasoning_content", None)
            if delta.get("refusal") in ("", None):
                delta.pop("refusal", None)
            if delta.get("extra_fields") is None:
                delta.pop("extra_fields", None)

            if converted_choice.get("finish_reason") == "":
                converted_choice["finish_reason"] = None
            converted_choice["delta"] = delta
            converted_choices.append(converted_choice)

        converted_chunk["choices"] = converted_choices

        choice = converted_chunk["choices"][0]
        delta = choice.get("delta", {})
        tool_calls = delta.get("tool_calls", [])

        if not tool_calls:
            return converted_chunk

        # 转换工具调用格式
        converted_tool_calls = []
        for tc in tool_calls:
            converted_tc = tc.copy()

            # 转换ID格式
            if tc.get("id"):
                original_id = tc["id"]
                converted_id = OpenAICompatibilityConverter.convert_tool_call_id(
                    original_id
                )
                converted_tc["id"] = converted_id

                # 分配新的index
                if original_id not in tool_call_index_map:
                    tool_call_index_map[original_id] = len(tool_call_index_map)

                converted_tc["index"] = tool_call_index_map[original_id]

            # 如果没有ID，使用当前最新的index
            elif tool_call_index_map:
                # 使用最后一个工具调用的index
                converted_tc["index"] = max(tool_call_index_map.values())

            converted_tool_calls.append(converted_tc)

        converted_chunk["choices"][0]["delta"]["tool_calls"] = converted_tool_calls

        return converted_chunk


def parse_sse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行SSE数据"""
    if not line.startswith("data: "):
        return None

    data = line[6:].strip()
    if not data or data == "[DONE]":
        return None

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def validate_and_fix_tool_call_args(args: str) -> str:
    """增强版的工具调用参数验证和修复 - 专门处理多工具调用问题"""
    if not args:
        return "{}"

    args = args.strip()

    # 检查是否是多个JSON对象连接的情况 - 这是多工具调用的主要问题
    if args.count("}{") > 0:
        # 尝试分离多个JSON对象
        json_objects = []
        current_obj = ""
        brace_count = 0

        for i, char in enumerate(args):
            current_obj += char
            if char == "{":
                brace_count += 1
            elif char == "}":
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
        if not args.endswith("}") and args.count("{") > args.count("}"):
            args += "}"

        elif not args.endswith("]") and args.count("[") > args.count("]"):
            args += "]"

        try:
            json.loads(args)

            return args
        except json.JSONDecodeError:
            return "{}"


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
            "system_fingerprint": None,
        }
        # 🔑 关键：使用工具调用ID作为键，因为index都是0会覆盖
        self.tool_call_map = {}  # key: tool_call_id, value: tool_call_data
        self.tool_call_order = []  # 保持工具调用的接收顺序
        self.current_tool_id = None  # 当前正在处理的工具调用ID

    def process_chunk(self, obj: Dict[str, Any]):
        """处理单个响应块"""
        # 聚合基本信息
        self.data["id"] = self.data["id"] or obj.get("id")
        self.data["model"] = self.data["model"] or obj.get("model")
        self.data["system_fingerprint"] = (
            obj.get("system_fingerprint") or self.data["system_fingerprint"]
        )

        if obj.get("usage"):
            self.data["usage"] = obj.get("usage")

        choices = obj.get("choices", [])
        if not choices:
            return

        choice = choices[0]
        if choice.get("finish_reason"):
            self.data["finish_reason"] = choice.get("finish_reason")

        delta = choice.get("delta", {})

        # 聚合内容
        if delta.get("content"):
            self.data["content"] += delta.get("content")

        # 处理工具调用
        if delta.get("tool_calls"):
            self._process_tool_calls(delta.get("tool_calls"))

    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        """处理工具调用 - 修复版：使用工具调用ID，正确处理分块传输"""
        for tc in tool_calls:
            tool_id = tc.get("id")

            # 如果有ID，这是一个新的工具调用
            if tool_id:
                # 新工具调用
                if tool_id not in self.tool_call_map:
                    self.tool_call_map[tool_id] = {
                        "id": tool_id,
                        "type": tc.get("type", "function"),
                        "function": {"name": "", "arguments": ""},
                    }
                    self.tool_call_order.append(tool_id)
                    self.current_tool_id = tool_id
                    logger.info(f"🔧 新工具调用: {tool_id}")
                else:
                    # 更新当前工具调用ID
                    self.current_tool_id = tool_id

                # 更新工具调用信息
                if tc.get("type"):
                    self.tool_call_map[tool_id]["type"] = tc.get("type")

                func = tc.get("function", {})
                if func.get("name"):
                    self.tool_call_map[tool_id]["function"]["name"] = func.get("name")
                if func.get("arguments"):
                    self.tool_call_map[tool_id]["function"]["arguments"] += func.get(
                        "arguments"
                    )

            # 如果没有ID，但有当前工具调用ID，这是增量数据
            elif self.current_tool_id and self.current_tool_id in self.tool_call_map:
                func = tc.get("function", {})
                if func.get("name"):
                    self.tool_call_map[self.current_tool_id]["function"]["name"] = (
                        func.get("name")
                    )
                if func.get("arguments"):
                    self.tool_call_map[self.current_tool_id]["function"][
                        "arguments"
                    ] += func.get("arguments")

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
                    tc["function"]["arguments"] = validate_and_fix_tool_call_args(
                        tc["function"]["arguments"]
                    )
                    self.data["tool_calls"].append(tc)
                    logger.info(f"📋 工具调用: {tool_id} - {tc['function']['name']}")

            logger.info(f"✅ 成功聚合 {len(self.data['tool_calls'])} 个工具调用")

        # 构建最终响应
        final_message = {"role": "assistant", "content": self.data["content"]}
        if self.data["tool_calls"]:
            final_message["tool_calls"] = self.data["tool_calls"]

        finish_reason = (
            "tool_calls"
            if self.data["tool_calls"]
            else (self.data["finish_reason"] or "stop")
        )

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
                    "logprobs": None,
                }
            ],
        }

        if self.data["usage"]:
            final_response["usage"] = self.data["usage"]
        if self.data["system_fingerprint"]:
            final_response["system_fingerprint"] = self.data["system_fingerprint"]

        return final_response
