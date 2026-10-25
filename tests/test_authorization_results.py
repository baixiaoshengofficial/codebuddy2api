"""Both products expose saved outcomes without exposing provider credentials."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_auth_router, workbuddy_auth_router
from src.auth import authenticate
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.workbuddy_token_manager import WorkBuddyTokenManager


@pytest.fixture(params=["codebuddy", "workbuddy"])
def flow(request, tmp_path, monkeypatch):
    product = request.param
    module, manager_type = {
        "codebuddy": (codebuddy_auth_router, CodeBuddyTokenManager),
        "workbuddy": (workbuddy_auth_router, WorkBuddyTokenManager),
    }[product]
    manager = manager_type(str(tmp_path / product))
    monkeypatch.setattr(module, product + "_token_manager", manager)
    claims = base64.urlsafe_b64encode(b'{"sub":"test-user"}').decode().rstrip("=")
    token = "header." + claims + ".secret-access-token"
    payload = {
        "access_token": token,
        "refresh_token": "secret-refresh-token",
        "expires_in": 7200,
        "domain": "example.test",
        "full_response": {"private": "secret-upstream-payload"},
    }
    poll = AsyncMock(return_value={"status": "success", "token_data": payload})
    start = AsyncMock(
        return_value={
            "success": True,
            "auth_state": "state-test",
            "verification_uri_complete": "https://login.example.test/",
            "access_token": token,
            "details": payload,
        }
    )
    save = AsyncMock(wraps=getattr(module, "save_" + product + "_token"))
    monkeypatch.setattr(module, "poll_" + product + "_auth_status", poll)
    monkeypatch.setattr(module, "start_" + product + "_auth", start)
    monkeypatch.setattr(module, "save_" + product + "_token", save)
    app = FastAPI()
    app.include_router(module.router, prefix="/" + product)

    async def authorized():
        return "test"

    app.dependency_overrides[authenticate] = authorized
    return SimpleNamespace(
        product=product,
        manager=manager,
        start=start,
        poll=poll,
        save=save,
        payload=payload,
        app=app,
        directory=tmp_path,
    )


def exercise(flow, action):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=flow.app), base_url="http://test"
        ) as client:
            await action(client, "/" + flow.product + "/auth")

    asyncio.run(run())


def assert_no_secrets(value):
    serialized = json.dumps(value)
    for secret in (
        "secret-access-token",
        "secret-refresh-token",
        "secret-upstream-payload",
        "access_token",
        "refresh_token",
        "token_data",
        "full_response",
    ):
        assert secret not in serialized


def test_success_persists_real_store_but_only_returns_saved_outcome(flow):
    async def run(client, path):
        response = await client.post(path + "/poll", json={"auth_state": "state-test"})
        assert response.status_code == 200
        assert response.json() == {"saved": True, "message": "授权成功，凭证已保存"}
        assert_no_secrets(response.json())

    exercise(flow, run)
    assert flow.save.await_count == 1
    data = flow.manager.credentials[0]["data"]
    assert data["bearer_token"] == flow.payload["access_token"]
    assert data["refresh_token"] == flow.payload["refresh_token"]


@pytest.mark.parametrize(
    "state",
    [
        "pending",
        "upstream_error",
        "missing_token",
        "save_false",
        "save_exception",
        "missing_state",
    ],
)
def test_failure_results_are_consistent_and_never_contain_secrets(flow, state):
    expected = {
        "pending": (400, "authorization_pending"),
        "upstream_error": (400, "auth_error"),
        "missing_token": (502, "invalid_token_response"),
        "save_false": (500, "save_failed"),
        "save_exception": (500, "save_failed"),
        "missing_state": (400, "missing_parameters"),
    }[state]
    if state == "pending":
        flow.poll.return_value = {
            "status": "pending",
            "message": "secret-access-token",
            "token_data": flow.payload,
        }
    elif state == "upstream_error":
        flow.poll.return_value = {
            "status": "error",
            "message": "secret-refresh-token",
            "response": flow.payload,
        }
    elif state == "missing_token":
        flow.payload.pop("access_token")
    elif state == "save_false":
        flow.save.return_value = False
    elif state == "save_exception":
        flow.save.side_effect = OSError("save failed")

    async def run(client, path):
        response = await client.post(
            path + "/poll",
            json={} if state == "missing_state" else {"auth_state": "state-test"},
        )
        assert (response.status_code, response.json()["error"]) == expected
        assert_no_secrets(response.json())
        if state == "missing_state":
            assert not flow.poll.called

    exercise(flow, run)
    assert not flow.manager.credentials


def test_start_only_returns_public_fields(flow):
    async def run(client, path):
        response = await client.get(path + "/start")
        assert response.status_code == 200
        assert response.json()["auth_state"] == "state-test"
        assert_no_secrets(response.json())

    exercise(flow, run)


@pytest.mark.parametrize("success", [False, True])
def test_invalid_start_result_is_sanitized(flow, success):
    flow.start.return_value = {
        "success": success,
        "message": "secret-access-token",
        "data": flow.payload,
    }

    async def run(client, path):
        response = await client.get(path + "/start")
        assert response.status_code == 502
        assert_no_secrets(response.json())

    exercise(flow, run)


def test_authentication_is_required_without_calling_provider(flow):
    flow.app.dependency_overrides.clear()

    async def run(client, path):
        assert (await client.get(path + "/start")).status_code in (401, 403)
        assert (
            await client.post(path + "/poll", json={"auth_state": "state-test"})
        ).status_code in (401, 403)
        assert not flow.start.called and not flow.poll.called

    exercise(flow, run)


def test_adding_same_account_replaces_login_and_clears_old_session_fields(flow):
    flow.manager.add_credential_with_data(
        {
            "bearer_token": "old",
            "user_id": "test-user",
            "domain": "example.test",
            "created_at": 1,
            "expires_in": 1,
            "last_refresh_at": 1,
            "session_dead_at": 1,
            "session_dead_reason": "12153",
        },
        filename="existing.json",
    )

    async def run(client, path):
        response = await client.post(path + "/poll", json={"auth_state": "state-test"})
        assert response.status_code == 200 and response.json()["saved"]

    exercise(flow, run)
    assert len(flow.manager.credentials) == 1
    record = flow.manager.credentials[0]
    assert record["file_path"].endswith("/existing.json")
    assert record["data"]["bearer_token"] == flow.payload["access_token"]
    assert not any(
        key in record["data"]
        for key in ("session_dead_at", "session_dead_reason", "last_refresh_at")
    )
