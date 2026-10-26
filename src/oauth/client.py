"""HTTP adapter for the shared device login protocol."""

import asyncio
import logging
import secrets
import uuid
from dataclasses import dataclass

import httpx

from config import get_codebuddy_api_endpoint, get_codebuddy_api_host

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OAuthProduct:
    platform: str
    user_agent: str
    method: str
    expires_in: int = 900
    interval: int = 3
    poll_content_type: bool = True


CODEBUDDY = OAuthProduct(
    "CLI", "CLI/1.0.8 CodeBuddy/1.0.8", "codebuddy_real_auth", 1800, 5, False
)
WORKBUDDY = OAuthProduct("workbuddy", "WorkBuddy/5.2.5", "workbuddy_auth")


class OAuthLoginClient:
    def __init__(
        self,
        product,
        client_factory=None,
        account_lookup=None,
        endpoint=get_codebuddy_api_endpoint,
        host=get_codebuddy_api_host,
    ):
        self.product = product
        self.client_factory = client_factory
        self.account_lookup = account_lookup
        self.endpoint = endpoint
        self.host = host
        self._last_state = None
        self._start_lock = asyncio.Lock()

    def _client(self):
        return (self.client_factory or httpx.AsyncClient)()

    def _headers(self, poll=False):
        request_id = uuid.uuid4().hex
        headers = {
            "Host": self.host(),
            "Accept": "application/json, text/plain, */*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "close",
            "X-Requested-With": "XMLHttpRequest",
            "X-Domain": self.host(),
            "X-No-Authorization": "true",
            "X-No-User-Id": "true",
            "X-No-Enterprise-Id": "true",
            "X-No-Department-Info": "true",
            "User-Agent": self.product.user_agent,
            "X-Product": "SaaS",
            "X-Request-ID": request_id,
        }
        if not poll or self.product.poll_content_type:
            headers["Content-Type"] = "application/json"
        if poll:
            span = secrets.token_hex(8)
            headers.update(
                {
                    "b3": f"{request_id}-{span}-1-",
                    "X-B3-TraceId": request_id,
                    "X-B3-ParentSpanId": "",
                    "X-B3-SpanId": span,
                    "X-B3-Sampled": "1",
                }
            )
        return headers

    @staticmethod
    def _payload(response):
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid login response")
        return payload

    async def start(self):
        try:
            async with self._start_lock:
                base = self.endpoint().rstrip("/")
                async with self._client() as client:
                    for _ in range(2):
                        nonce = secrets.token_hex(8)
                        response = await client.post(
                            base + "/v2/plugin/auth/state",
                            params={"platform": self.product.platform, "nonce": nonce},
                            json={"nonce": nonce},
                            headers=self._headers(),
                            timeout=30,
                        )
                        payload = self._payload(response)
                        data = payload.get("data") or {}
                        if payload.get("code") != 0 or not isinstance(data, dict):
                            break
                        state, url = data.get("state"), data.get("authUrl")
                        if (
                            not isinstance(state, str)
                            or not isinstance(url, str)
                            or not state
                            or not url
                        ):
                            break
                        if state == self._last_state:
                            continue
                        self._last_state = state
                        return {
                            "success": True,
                            "method": self.product.method,
                            "auth_state": state,
                            "verification_uri_complete": url,
                            "verification_uri": base,
                            "platform": self.product.platform,
                            "expires_in": self.product.expires_in,
                            "interval": self.product.interval,
                            "status": "awaiting_login",
                        }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning(
                "Device login start failed (%s): %s",
                self.product.platform,
                type(exc).__name__,
            )
        return {
            "success": False,
            "error": "auth_start_failed",
            "message": "无法启动授权，请重新尝试",
        }

    async def poll(self, auth_state):
        try:
            base = self.endpoint().rstrip("/")
            async with self._client() as client:
                response = await client.get(
                    base + "/v2/plugin/auth/token",
                    params={"state": auth_state},
                    headers=self._headers(poll=True),
                    timeout=30,
                )
                payload = self._payload(response)
                if payload.get("code") == 11217:
                    return {"status": "pending", "code": 11217}
                data = payload.get("data") or {}
                if (
                    payload.get("code") != 0
                    or not isinstance(data, dict)
                    or not isinstance(data.get("accessToken"), str)
                    or not data.get("accessToken")
                ):
                    return {"status": "error"}
                token = {
                    "access_token": data["accessToken"],
                    "bearer_token": data["accessToken"],
                    "token_type": data.get("tokenType", "Bearer"),
                    "expires_in": data.get("expiresIn"),
                    "refresh_token": data.get("refreshToken"),
                    "session_state": data.get("sessionState"),
                    "scope": data.get("scope"),
                    "domain": data.get("domain"),
                }
                if self.account_lookup is not None:
                    token.update(
                        await self.account_lookup(
                            client,
                            base,
                            self.host(),
                            auth_state,
                            data["accessToken"],
                            self.product.user_agent,
                        )
                    )
                return {"status": "success", "token_data": token}
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning(
                "Device login poll failed (%s): %s",
                self.product.platform,
                type(exc).__name__,
            )
            return {"status": "error"}
