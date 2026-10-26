"""Headers for CodeBuddy model calls; conversation identifiers are preserved."""

import secrets
import uuid
from typing import Dict, Optional


def chat_request_headers(
    bearer_token: str,
    user_id: str = None,
    conversation_id: Optional[str] = None,
    conversation_request_id: Optional[str] = None,
    conversation_message_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    生成CodeBuddy API所需的完整请求头。
    优先使用传入的会话ID，如果未提供则随机生成。
    """
    from config import get_codebuddy_api_host

    api_host = get_codebuddy_api_host()

    headers = {
        "Host": api_host,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "x-stainless-arch": "x64",
        "x-stainless-lang": "js",
        "x-stainless-os": "Windows",
        "x-stainless-package-version": "5.10.1",
        "x-stainless-retry-count": "0",
        "x-stainless-runtime": "node",
        "x-stainless-runtime-version": "v22.13.1",
        "X-Conversation-ID": conversation_id or str(uuid.uuid4()),
        "X-Conversation-Request-ID": conversation_request_id or secrets.token_hex(16),
        "X-Conversation-Message-ID": conversation_message_id
        or str(uuid.uuid4()).replace("-", ""),
        "X-Request-ID": request_id or str(uuid.uuid4()).replace("-", ""),
        "X-Agent-Intent": "craft",
        "X-IDE-Type": "CLI",
        "X-IDE-Name": "CLI",
        "X-IDE-Version": "1.0.7",
        "Authorization": f"Bearer {bearer_token}",
        "X-Domain": api_host,
        "User-Agent": "CLI/1.0.7 CodeBuddy/1.0.7",
        "X-Product": "SaaS",
        "X-User-Id": user_id or "b5be3a67-237e-4ee6-9b9a-0b9ecd7b454b",
    }
    return headers
