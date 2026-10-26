"""Device login contracts using an HTTP transport, without provider accounts."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src.oauth.client import CODEBUDDY, WORKBUDDY, OAuthLoginClient
from src.oauth.account import lookup_workbuddy_account
from src.chat.headers import chat_request_headers


@pytest.fixture(params=[CODEBUDDY, WORKBUDDY], ids=["codebuddy", "workbuddy"])
def product(request):
    return request.param


def client_for(product, handler, **options):
    return OAuthLoginClient(
        product,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
        endpoint=lambda: "https://auth.example.test",
        host=lambda: "auth.example.test",
        **options,
    )


def test_start_preserves_product_headers_platform_and_nonce(product):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST" and request.url.path == "/v2/plugin/auth/state"
        assert request.url.params["platform"] == product.platform
        assert json.loads(request.content)["nonce"] == request.url.params["nonce"]
        assert request.headers["User-Agent"] == product.user_agent
        assert request.headers["X-Product"] == "SaaS"
        assert (
            request.headers["Host"]
            == request.headers["X-Domain"]
            == "auth.example.test"
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"state": "s1", "authUrl": "https://login.example.test/"},
            },
        )

    result = asyncio.run(client_for(product, handler).start())
    assert result["success"] and result["auth_state"] == "s1"
    assert (
        result["interval"] == product.interval
        and result["expires_in"] == product.expires_in
    )
    assert len(requests) == 1


def test_poll_preserves_token_fields_and_encodes_state(product):
    def handler(request):
        assert request.method == "GET" and request.url.path == "/v2/plugin/auth/token"
        assert request.url.params["state"] == "s&other=not-a-query"
        assert "other" not in request.url.params
        assert ("Content-Type" in request.headers) == product.poll_content_type
        assert request.headers["X-B3-TraceId"] == request.headers["X-Request-ID"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "accessToken": "access",
                    "refreshToken": "refresh",
                    "expiresIn": 7200,
                    "domain": "example.test",
                    "sessionState": "session",
                },
            },
        )

    result = asyncio.run(client_for(product, handler).poll("s&other=not-a-query"))
    assert result["status"] == "success"
    assert result["token_data"]["bearer_token"] == "access"
    assert result["token_data"]["refresh_token"] == "refresh"
    assert result["token_data"]["session_state"] == "session"
    assert "full_response" not in result["token_data"]


@pytest.mark.parametrize(
    "failure",
    [
        "http",
        "json",
        "business",
        "missing_url",
        "invalid_data",
        "invalid_state",
        "invalid_url",
        "transport",
    ],
)
def test_start_failures_return_safe_result(product, failure):
    def handler(request):
        if failure == "transport":
            raise httpx.ConnectError("secret-provider-message", request=request)
        if failure == "http":
            return httpx.Response(503, text="secret-provider-message")
        if failure == "json":
            return httpx.Response(200, text="secret-provider-message")
        payload = {
            "business": {"code": 9, "msg": "secret-provider-message"},
            "missing_url": {"code": 0, "data": {"state": "s1"}},
            "invalid_data": {"code": 0, "data": ["secret-provider-message"]},
            "invalid_state": {
                "code": 0,
                "data": {"state": [], "authUrl": "https://login.example.test/"},
            },
            "invalid_url": {"code": 0, "data": {"state": "s1", "authUrl": 123}},
        }[failure]
        return httpx.Response(200, json=payload)

    result = asyncio.run(client_for(product, handler).start())
    assert not result["success"]
    assert "secret-provider-message" not in json.dumps(result)


@pytest.mark.parametrize(
    "status,payload",
    [
        (503, {}),
        (200, {"code": 8}),
        (200, {"code": 0, "data": {"refreshToken": "secret"}}),
        (200, {"code": 0, "data": []}),
        (200, {"code": 0, "data": {"accessToken": 123}}),
    ],
)
def test_invalid_poll_never_runs_account_lookup(product, status, payload):
    lookup = AsyncMock()
    client = client_for(
        product,
        lambda request: httpx.Response(status, json=payload),
        account_lookup=lookup,
    )
    result = asyncio.run(client.poll("s1"))
    assert result["status"] == "error" and not lookup.called
    assert "token_data" not in result


def test_pending_does_not_enrich_or_store_tokens(product):
    lookup = AsyncMock()
    client = client_for(
        product,
        lambda request: httpx.Response(200, json={"code": 11217}),
        account_lookup=lookup,
    )
    result = asyncio.run(client.poll("s1"))
    assert result == {"status": "pending", "code": 11217}
    assert not lookup.called


def test_duplicate_state_uses_new_nonce_and_fails_if_it_cannot_get_a_new_session(
    product,
):
    nonces = []
    states = iter(["first", "first", "second", "second", "second"])

    def handler(request):
        nonces.append(request.url.params["nonce"])
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "state": next(states),
                    "authUrl": "https://login.example.test/",
                },
            },
        )

    async def run():
        client = client_for(product, handler)
        assert (await client.start())["auth_state"] == "first"
        assert (await client.start())["auth_state"] == "second"
        assert not (await client.start())["success"]
        assert len(nonces) == len(set(nonces)) == 5

    asyncio.run(run())


def test_concurrent_starts_cannot_share_mutable_session_state(product):
    active, peak, count = 0, 0, 0

    async def handler(request):
        nonlocal active, peak, count
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        count += 1
        active -= 1
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "state": "s" + str(count),
                    "authUrl": "https://login.example.test/",
                },
            },
        )

    async def run():
        client = client_for(product, handler)
        results = await asyncio.gather(client.start(), client.start())
        assert {result["auth_state"] for result in results} == {"s1", "s2"}
        assert peak == 1

    asyncio.run(run())


def test_account_lookup_preserves_workbuddy_identity_and_header_differences():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/auth/token"):
            assert "Authorization" not in request.headers
            return httpx.Response(
                200, json={"code": 0, "data": {"accessToken": "access"}}
            )
        assert request.url.path == "/v2/plugin/login/account"
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["X-Product"] == "WorkBuddy"
        assert request.url.params["state"] == "s&1"
        return httpx.Response(
            200,
            json={
                "data": {
                    "uid": "outer-id",
                    "account": {
                        "uid": "inner-id",
                        "mobilePhone": "13800000000",
                        "nickname": "Test",
                        "email": "test@example.test",
                    },
                }
            },
        )

    result = asyncio.run(
        client_for(WORKBUDDY, handler, account_lookup=lookup_workbuddy_account).poll(
            "s&1"
        )
    )
    token = result["token_data"]
    assert token["account_uid"] == "outer-id" and token["phone"] == "13800000000"
    assert (
        token["account_name"] == "Test"
        and token["account_email"] == "test@example.test"
    )
    assert len(requests) == 2


@pytest.mark.parametrize(
    "account_result",
    [
        httpx.Response(503),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, text="not-json"),
    ],
)
def test_optional_account_failure_does_not_discard_successful_login(account_result):
    def handler(request):
        if request.url.path.endswith("/auth/token"):
            return httpx.Response(
                200, json={"code": 0, "data": {"accessToken": "access"}}
            )
        return account_result

    result = asyncio.run(
        client_for(WORKBUDDY, handler, account_lookup=lookup_workbuddy_account).poll(
            "s1"
        )
    )
    assert (
        result["status"] == "success"
        and result["token_data"]["access_token"] == "access"
    )


def test_endpoints_and_chat_headers_follow_current_site(monkeypatch):
    site = ["https://first.example.test"]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "state": request.url.host,
                    "authUrl": "https://login.example.test/",
                },
            },
        )

    async def run():
        client = OAuthLoginClient(
            CODEBUDDY,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
            endpoint=lambda: site[0],
            host=lambda: site[0].split("//")[1],
        )
        await client.start()
        site[0] = "https://second.example.test"
        await client.start()

    asyncio.run(run())
    assert [request.url.host for request in requests] == [
        "first.example.test",
        "second.example.test",
    ]
    assert all(request.headers["X-Domain"] == request.url.host for request in requests)
    monkeypatch.setattr("config.get_codebuddy_api_host", lambda: "current.example.test")
    headers = chat_request_headers(
        "fake-access",
        "user-id",
        "conversation",
        "conversation-request",
        "message",
        "request",
    )
    assert headers["X-Domain"] == headers["Host"] == "current.example.test"
    assert headers["Authorization"] == "Bearer fake-access"
    assert (
        headers["X-Conversation-ID"] == "conversation"
        and headers["X-Request-ID"] == "request"
    )
    assert headers["X-Conversation-Request-ID"] == "conversation-request"
    assert headers["X-Conversation-Message-ID"] == "message"
    assert headers["x-stainless-retry-count"] == "0"
