"""
关键词替换工具模块 - 统一处理关键词替换逻辑
防止CodeBuddy检测到竞争对手关键词
"""
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

REPLACEMENTS = [
    (
        "https://github.com/anthropics/claude-code/issues",
        "https://cnb.cool/codebuddy/codebuddy-code/-/issues",
    ),
    ("Anthropic's official CLI for Claude", "Tencent's official CLI for CodeBuddy"),
    ("Claude Code", "CodeBuddy Code"),
    ("claude-code", "codebuddy-code"),
    ("Anthropic", "Tencent"),
    ("Claude", "CodeBuddy"),
]

TEXT_FIELDS = {
    "content",
    "text",
    "description",
    "instructions",
    "prompt",
    "system",
    "suffix",
    "prefix",
}


def apply_keyword_replacement(text: str) -> str:
    """
    统一的关键词替换函数

    Args:
        text: 需要处理的文本内容

    Returns:
        str: 替换后的文本内容
    """
    if not isinstance(text, str):
        return text

    original_text = text

    # 应用所有替换规则
    for old_keyword, new_keyword in REPLACEMENTS:
        text = re.sub(re.escape(old_keyword), new_keyword, text, flags=re.IGNORECASE)

    # 记录替换日志（仅在调试模式下）
    if text != original_text:
        logger.debug(f"[KEYWORD_REPLACE] Applied keyword replacements, original length: {len(original_text)}, new length: {len(text)}")

    return text


def apply_keyword_replacement_to_value(value: Any) -> Any:
    """递归处理消息内容中的字符串值。"""
    if isinstance(value, str):
        return apply_keyword_replacement(value)

    if isinstance(value, list):
        return [apply_keyword_replacement_to_value(item) for item in value]

    if isinstance(value, dict):
        return {
            key: apply_keyword_replacement_to_value(item)
            for key, item in value.items()
        }

    return value


def apply_keyword_replacement_to_system_message(content) -> str:
    """
    专门用于处理系统消息的关键词替换
    支持字符串和复杂结构的content

    Args:
        content: 消息内容，可能是字符串或列表结构

    Returns:
        str: 处理后的内容
    """
    return apply_keyword_replacement_to_value(content)


def apply_keyword_replacement_to_messages(messages: Any) -> Any:
    """处理 OpenAI 消息数组中的所有文本内容。"""
    if not isinstance(messages, list):
        return messages

    processed_messages = []
    for message in messages:
        if not isinstance(message, dict):
            processed_messages.append(message)
            continue

        processed_message = message.copy()
        if "content" in processed_message:
            processed_message["content"] = apply_keyword_replacement_to_value(
                processed_message.get("content")
            )
        if "tool_calls" in processed_message:
            processed_message["tool_calls"] = apply_keyword_replacement_to_value(
                processed_message.get("tool_calls")
            )
        processed_messages.append(processed_message)

    return processed_messages


def apply_keyword_replacement_to_tool_definitions(tools: Any) -> Any:
    """仅处理工具定义中的描述性文本，避免修改工具名等协议字段。"""
    if not isinstance(tools, list):
        return tools

    return [_replace_tool_text_fields(tool) for tool in tools]


def _replace_tool_text_fields(value: Any, key: str = "") -> Any:
    if isinstance(value, str):
        return apply_keyword_replacement(value) if key in TEXT_FIELDS else value

    if isinstance(value, list):
        return [_replace_tool_text_fields(item, key) for item in value]

    if isinstance(value, dict):
        return {
            item_key: _replace_tool_text_fields(item, item_key)
            for item_key, item in value.items()
        }

    return value
