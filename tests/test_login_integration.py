"""Exercise HTTP authorization through real provider and JSON store adapters."""

import asyncio
import base64
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_auth_router, workbuddy_auth_router
from src.auth import authenticate
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.workbuddy_token_manager import WorkBuddyTokenManager
from src.oauth.client import CODEBUDDY, WORKBUDDY, OAuthLoginClient
from src.oauth.account import lookup_workbuddy_account


@pytest.mark.parametrize("product", ["codebuddy", "workbuddy"])
def test_add_then_reauthorize_through_http_keeps_the_original_account(
    tmp_path, monkeypatch, product
):
    module, manager_type, profile = {
        "codebuddy": (codebuddy_auth_router, CodeBuddyTokenManager, CODEBUDDY),
        "workbuddy": (workbuddy_auth_router, WorkBuddyTokenManager, WORKBUDDY),
    }[product]
    manager = manager_type(str(tmp_path / product))
    requests, attempts, states = [], {}, []
    claims = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": "user-a", "email": "a@example.test"}).encode()
        )
        .decode()
        .rstrip("=")
    )

    def bearer(state):
        return "header." + claims + "." + state

    def upstream(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/auth/state"):
            assert request.url.params["platform"] == profile.platform
            state = uuid.uuid4().hex
            states.append(state)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"state": state, "authUrl": "https://login.example.test/"},
                },
            )
        if request.url.path.endswith("/auth/token"):
            state = request.url.params["state"]
            attempts[state] = attempts.get(state, 0) + 1
            if attempts[state] == 1:
                return httpx.Response(200, json={"code": 11217})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "accessToken": bearer(state),
                        "refreshToken": "refresh-" + state,
                        "expiresIn": 7200,
                        "domain": "example.test",
                    },
                },
            )
        assert product == "workbuddy" and request.url.path.endswith("/login/account")
        return httpx.Response(
            200, json={"data": {"uid": "user-a", "email": "a@example.test"}}
        )

    login = OAuthLoginClient(
        profile,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        ),
        account_lookup=lookup_workbuddy_account if product == "workbuddy" else None,
        endpoint=lambda: "https://auth.example.test",
        host=lambda: "auth.example.test",
    )
    monkeypatch.setattr(module, product + "_token_manager", manager)
    monkeypatch.setattr(module, "start_" + product + "_auth", login.start)
    monkeypatch.setattr(module, "poll_" + product + "_auth_status", login.poll)
    app = FastAPI()
    app.include_router(module.router, prefix="/" + product)

    async def authorized():
        return "test"

    app.dependency_overrides[authenticate] = authorized

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = "/" + product + "/auth"
            result = await client.get(path + "/start")
            state = result.json()["auth_state"]
            assert (
                await client.post(path + "/poll", json={"auth_state": state})
            ).json()["error"] == "authorization_pending"
            result = await client.post(path + "/poll", json={"auth_state": state})
            assert result.status_code == 200 and result.json()["saved"]
            record = manager.get_credential_records()[0]
            name = record["filename"]
            assert record["data"]["bearer_token"] == bearer(state)
            if product == "codebuddy":
                manager.set_mode("manual", 0)
                manager.mark_session_dead(name)
            else:
                manager.update_credential(name, {"created_at": 1, "expires_in": 1})
            result = await client.post(path + "/reauthorize", json={"filename": name})
            state = result.json()["auth_state"]
            assert (
                await client.post(
                    path + "/reauthorize/poll", json={"auth_state": state}
                )
            ).status_code == 400
            result = await client.post(
                path + "/reauthorize/poll", json={"auth_state": state}
            )
            assert result.status_code == 200 and result.json()["saved"]
            assert result.json()["filename"] == name
            assert (
                "access_token" not in result.json()
                and "refresh_token" not in result.json()
            )
            records = manager.get_credential_records()
            assert len(records) == 1 and records[0]["filename"] == name
            data = records[0]["data"]
            assert (
                data["bearer_token"] == bearer(state)
                and data["refresh_token"] == "refresh-" + state
            )
            assert "full_response" not in data and "session_dead_at" not in data
            if product == "codebuddy":
                assert manager.mode == "manual" and manager.manual_selected_index == 0
            assert type(manager)(manager.creds_dir).get_credential(name)[
                "bearer_token"
            ] == bearer(state)

    asyncio.run(run())
    assert len(states) == 2
    assert all(
        path
        in [
            "/v2/plugin/auth/state",
            "/v2/plugin/auth/token",
            "/v2/plugin/login/account",
        ]
        for path in requests
    )
