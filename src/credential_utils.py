"""Shared helpers for credential identity, paths, and file names.

This module contains no FastAPI or provider-specific behaviour. Routers use it
to interpret an upstream token, while token managers use it for local storage.
"""

import base64
import json
import os
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def resolve_project_path(path: str) -> str:
    """Resolve a relative path from the project root."""
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def decode_jwt_claims(token: str) -> Dict[str, Any]:
    """Decode a JWT payload without signature validation for local display data."""
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".", maxsplit=2)[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def first_claim(claims: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    """Return the first non-empty claim in ``keys`` as text."""
    for key in keys:
        value = claims.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def select_claims(claims: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Return only present claims, keeping credential files compact."""
    return {key: claims[key] for key in keys if claims.get(key) is not None}


def build_credential_filename(product: str, user_id: Any, timestamp: int) -> str:
    """Create a filesystem-safe credential file name."""
    safe_user_id = "".join(
        character for character in str(user_id) if character.isalnum() or character in "._-"
    )[:20]
    return f"{product}_{safe_user_id or 'unknown'}_{timestamp}.json"
