"""Keep CodeBuddy chat credentials active before the upstream session expires."""

import asyncio
import logging
import time
from typing import Any, Dict

import httpx

from config import get_codebuddy_api_endpoint
from .codebuddy_token_manager import codebuddy_token_manager
from .credential_utils import decode_jwt_claims


logger = logging.getLogger(__name__)


class SessionExpiredError(RuntimeError):
    """The upstream no longer accepts an account's offline session."""


class CodeBuddyCredentialRefresher:
    REFRESH_INTERVAL = 20 * 60 * 60
    REFRESH_PATH = "/v2/plugin/auth/token/refresh"
    REALM_HOSTS = {"www.codebuddy.cn", "www.codebuddy.ai", "www.workbuddy.ai"}

    def __init__(self, manager=None):
        self.manager = manager or codebuddy_token_manager
        self._locks: Dict[str, asyncio.Lock] = {}
        self._task = None

    def _current(self, filename: str) -> Dict[str, Any]:
        for item in self.manager.credentials:
            if item["file_path"].endswith("/" + filename):
                return item["data"]
        raise SessionExpiredError("CodeBuddy credential was removed")

    async def ensure_fresh(self, selected: Dict[str, Any]) -> Dict[str, Any]:
        filename = selected["_credential_filename"]
        lock = self._locks.setdefault(filename, asyncio.Lock())
        async with lock:
            current = self._current(filename)
            if current.get("session_dead_at"):
                raise SessionExpiredError("CodeBuddy session expired")
            expired = self.manager.is_token_expired(current)
            if expired and not current.get("refresh_token"):
                raise SessionExpiredError("CodeBuddy token expired")
            refreshed_at = current.get("last_refresh_at") or current.get("created_at") or 0
            try:
                age = time.time() - int(refreshed_at)
            except (TypeError, ValueError):
                age = self.REFRESH_INTERVAL
            if current.get("refresh_token") and (expired or age >= self.REFRESH_INTERVAL):
                try:
                    current = await self._refresh(filename, current)
                except SessionExpiredError:
                    self.manager.mark_session_dead(filename)
                    raise
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("CodeBuddy keepalive failed for %s: %s", filename, exc)
            return {**current, **{
                key: value for key, value in selected.items() if key.startswith("_credential_")
            }}

    async def _refresh(self, filename: str, credential: Dict[str, Any]) -> Dict[str, Any]:
        domain = str(credential.get("domain") or "")
        realm = (
            "https://" + domain
            if domain in self.REALM_HOSTS
            else get_codebuddy_api_endpoint().rstrip("/")
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": realm,
            "Referer": realm + "/",
            "User-Agent": "CodeBuddy/5.2.5",
            "X-Refresh-Token": credential["refresh_token"],
            "X-Auth-Refresh-Source": "codebuddy",
        }
        enterprise_id = credential.get("enterprise_id") or credential.get("enterpriseId")
        if enterprise_id:
            headers["X-Enterprise-Id"] = str(enterprise_id)
        timeout = httpx.Timeout(10.0, connect=6.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(2):
                try:
                    response = await client.post(
                        realm + self.REFRESH_PATH, headers=headers, json={}
                    )
                    break
                except httpx.TransportError:
                    if attempt:
                        raise
                    await asyncio.sleep(1)
        if response.status_code in (401, 403):
            raise SessionExpiredError("CodeBuddy offline session rejected")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid CodeBuddy refresh response")
        if payload.get("code") == 12153:
            raise SessionExpiredError("CodeBuddy offline session not found")
        if payload.get("code") != 0:
            raise ValueError("CodeBuddy refresh rejected: code %s" % payload.get("code"))
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise ValueError("CodeBuddy refresh did not return an access token")
        now = int(time.time())
        new_token = data["accessToken"]
        expires_in = data.get("expiresIn")
        if not expires_in:
            exp = decode_jwt_claims(new_token).get("exp")
            expires_in = max(0, int(exp) - now) if exp else None
        updates = {
            "bearer_token": new_token,
            "created_at": now,
            "last_refresh_at": now,
        }
        if data.get("refreshToken"):
            updates["refresh_token"] = data["refreshToken"]
        if expires_in:
            updates["expires_in"] = int(expires_in)
        if data.get("domain"):
            updates["domain"] = data["domain"]
        refreshed = self.manager.update_credential(filename, updates)
        logger.info("Refreshed CodeBuddy credential: %s", filename)
        return refreshed

    async def _keepalive_loop(self) -> None:
        try:
            delay = 60
            while True:
                await asyncio.sleep(delay)
                delay = 3600
                for item in list(self.manager.credentials):
                    filename = item["file_path"].rsplit("/", 1)[-1]
                    try:
                        await self.ensure_fresh({
                            **item["data"], "_credential_filename": filename
                        })
                    except SessionExpiredError:
                        logger.warning("CodeBuddy account requires reauthorization: %s", filename)
                    except Exception:
                        logger.exception("CodeBuddy keepalive failed for %s", filename)
        except asyncio.CancelledError:
            pass

    async def startup(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._keepalive_loop(), name="codebuddy-keepalive")

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
            await self._task
            self._task = None


codebuddy_credential_refresher = CodeBuddyCredentialRefresher()
