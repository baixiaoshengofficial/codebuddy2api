"""Normalize account identity and persist a successful provider login."""

import time
from typing import Callable

from .credential_store import CredentialWriter
from .credential_utils import (
    build_credential_filename,
    decode_jwt_claims,
    first_claim,
    select_claims,
)

USER_FIELDS = (
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
PHONE_FIELDS = ("phone", "mobile", "mobile_phone", "phone_number", "telephone")


def _user_info(claims, extra_fields=()):
    info = select_claims(claims, USER_FIELDS + extra_fields)
    if claims.get("sid") is not None:
        info["session_state"] = claims["sid"]
    return info


def _credential(token, user_id, user_info, **extra):
    data = {
        "bearer_token": token.get("access_token") or token.get("bearer_token"),
        "user_id": user_id,
        "user_info": user_info,
        "created_at": int(time.time()),
        "token_type": token.get("token_type", "Bearer"),
        **{
            key: token.get(key)
            for key in ("expires_in", "refresh_token", "scope", "domain")
        },
        **extra,
    }
    return {key: value for key, value in data.items() if value is not None}


def build_codebuddy_credential(token):
    claims = decode_jwt_claims(
        token.get("access_token") or token.get("bearer_token") or ""
    )
    user_id = (
        first_claim(claims, ("email", "preferred_username", "sub"))
        or token.get("domain")
        or "unknown"
    )
    return _credential(
        token, user_id, _user_info(claims), session_state=token.get("session_state")
    )


def build_workbuddy_credential(token):
    claims = decode_jwt_claims(
        token.get("access_token") or token.get("bearer_token") or ""
    )
    user_id = (
        token.get("account_uid")
        or first_claim(claims, ("preferred_username", "email", "sub"))
        or "unknown"
    )
    info = _user_info(claims, PHONE_FIELDS)
    for source, target in [
        ("account_uid", "uid"),
        ("phone", "phone"),
        ("account_name", "name"),
        ("account_email", "email"),
    ]:
        if token.get(source):
            info[target] = token[source]
    return _credential(
        token, user_id, info or None, phone=token.get("phone"), product="WorkBuddy"
    )


class CredentialLogin:
    def __init__(
        self, get_manager: Callable[[], CredentialWriter], build_credential, product
    ):
        self.get_manager = get_manager
        self.build_credential = build_credential
        self.product = product

    async def save(self, token_data):
        credential = self.build_credential(token_data)
        filename = build_credential_filename(
            self.product, credential["user_id"], credential["created_at"]
        )
        return self.get_manager().add_credential_with_data(
            credential, filename=filename
        )
