"""Translate supported OpenAI chat request semantics into CodeBuddy payloads."""
import copy
from typing import Any, Dict

from ..keyword_replacer import (
    apply_keyword_replacement_to_messages,
    apply_keyword_replacement_to_tool_definitions,
)


class ChatRequestError(ValueError):
    """A request cannot be represented by the upstream chat protocol."""


def normalize_tool_choice(payload: Dict[str, Any]) -> None:
    """CodeBuddy accepts string choices; a named choice exposes one required tool."""
    choice = payload.get("tool_choice")
    if choice is None:
        payload.pop("tool_choice", None)
        return
    if isinstance(choice, str):
        if choice not in ("auto", "none", "required"):
            raise ChatRequestError("tool_choice must be auto, none, required, or a named function")
        return
    if not isinstance(choice, dict) or choice.get("type") != "function":
        raise ChatRequestError("Only named function tool_choice objects are supported")
    function = choice.get("function")
    name = function.get("name") if isinstance(function, dict) else None
    if not isinstance(name, str) or not name:
        raise ChatRequestError("tool_choice.function.name must be a non-empty string")
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ChatRequestError("A named tool_choice requires matching tools")
    matching = [
        tool for tool in tools
        if isinstance(tool, dict) and tool.get("type") == "function"
        and isinstance(tool.get("function"), dict)
        and tool["function"].get("name") == name
    ]
    if len(matching) != 1:
        raise ChatRequestError("tool_choice must name exactly one function in tools")
    payload["tools"] = matching
    payload["tool_choice"] = "required"


def prepare_chat_payload(request_body: Dict[str, Any]) -> Dict[str, Any]:
    payload = copy.deepcopy(request_body)
    payload["stream"] = True
    messages = payload.get("messages", [])
    if len(messages) == 1 and messages[0].get("role") == "user":
        payload["messages"] = [
            {"role": "system", "content": "You are a helpful assistant."}
        ] + messages
    normalize_tool_choice(payload)
    payload["messages"] = apply_keyword_replacement_to_messages(payload.get("messages", []))
    if "tools" in payload:
        payload["tools"] = apply_keyword_replacement_to_tool_definitions(payload["tools"])
    return payload
