"""Bind a fresh provider login to an existing credential, without adding accounts."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

from .credential_store import CredentialNotFound, CredentialReplacer

from .credential_utils import decode_jwt_claims
from .device_authorization import AuthorizationError, public_authorization_start


class ReauthorizationError(AuthorizationError):
    def __init__(self, status, message):
        super().__init__(status, "reauthorization_failed", message)


def account_identifiers(data):
    """Compare saved provider identity, never a filename or display name."""
    claims = decode_jwt_claims(data.get("bearer_token", ""))
    sources = [data, data.get("user_info") or {}, claims]
    keys = (
        "user_id",
        "uid",
        "sub",
        "email",
        "preferred_username",
        "phone",
        "mobile",
        "mobile_phone",
        "phone_number",
        "telephone",
    )
    return {
        str(source[key]).strip().casefold()
        for source in sources
        for key in keys
        if source.get(key) not in (None, "", "unknown", data.get("domain"))
    }


@dataclass
class AuthorizationSession:
    filename: str
    original: dict
    deadline: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: dict = None


class CredentialReauthorization:
    def __init__(
        self,
        get_manager: Callable[[], CredentialReplacer],
        start_login,
        poll_login,
        build_credential,
        clock=time.monotonic,
    ):
        self.get_manager = get_manager
        self.start_login = start_login
        self.poll_login = poll_login
        self.build_credential = build_credential
        self.clock = clock
        self.sessions = {}

    def _credential(self, filename):
        try:
            return self.get_manager().get_credential(filename)
        except CredentialNotFound:
            raise ReauthorizationError(404, "凭证不存在或已被删除，请刷新列表")

    async def start(self, filename):
        original = self._credential(filename)
        self.sessions = {
            key: session
            for key, session in self.sessions.items()
            if session.deadline > self.clock()
        }
        result = public_authorization_start(await self.start_login())
        state = result["auth_state"]
        if state in self.sessions:
            raise ReauthorizationError(409, "授权会话重复，请重新开始")
        self.sessions[state] = AuthorizationSession(
            filename,
            original,
            self.clock() + min(int(result.get("expires_in") or 900), 900),
        )
        return {**result, "filename": filename}

    async def poll(self, state):
        session = self.sessions.get(state)
        if session is None or session.deadline <= self.clock():
            self.sessions.pop(state, None)
            raise ReauthorizationError(410, "重新授权会话已过期，请重新开始")
        async with session.lock:
            if session.result is not None:
                return session.result
            current = self._credential(session.filename)
            if current.get("bearer_token") != session.original.get("bearer_token"):
                raise ReauthorizationError(409, "该凭证已更新，请刷新列表后重新授权")
            result = await self.poll_login(state)
            if result.get("status") == "pending":
                return {"pending": True, "message": result.get("message")}
            if result.get("status") != "success":
                raise ReauthorizationError(400, "重新授权失败或已过期，请重新开始")
            token_data = result.get("token_data") or {}
            if not (token_data.get("access_token") or token_data.get("bearer_token")):
                raise ReauthorizationError(502, "上游未返回有效凭证")
            credential = self.build_credential(token_data)
            old_domain, new_domain = current.get("domain"), credential.get("domain")
            if (
                old_domain and new_domain and old_domain != new_domain
            ) or not account_identifiers(current).intersection(
                account_identifiers(credential)
            ):
                raise ReauthorizationError(
                    409, "登录账号与所选凭证不一致，请使用原账号重新授权；原凭证未修改"
                )
            # No await between the final conflict check and the atomic write.
            latest = self._credential(session.filename)
            if latest.get("bearer_token") != current.get("bearer_token"):
                raise ReauthorizationError(409, "该凭证已更新，请刷新列表后重试")
            self.get_manager().replace_credential(session.filename, credential)
            session.result = {
                "saved": True,
                "filename": session.filename,
                "message": "重新授权成功，已更新原凭证",
            }
            return session.result
