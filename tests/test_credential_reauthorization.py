"""Credential-specific authorization uses isolated stores and simulated login."""

import asyncio
import base64
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_auth_router, workbuddy_auth_router
from src.auth import authenticate
from src.checkin_manager import CodeBuddyCheckinManager
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.workbuddy_token_manager import WorkBuddyTokenManager
from src.credential_reauthorization import (
    CredentialReauthorization,
    ReauthorizationError,
)


def jwt(subject, serial):
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": subject, "email": subject + "@example.test"}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return "header." + payload + "." + serial


@pytest.fixture(params=["codebuddy", "workbuddy"])
def scenario(request, tmp_path, monkeypatch):
    product = request.param
    module, manager_class = {
        "codebuddy": (codebuddy_auth_router, CodeBuddyTokenManager),
        "workbuddy": (workbuddy_auth_router, WorkBuddyTokenManager),
    }[product]
    manager = manager_class(str(tmp_path / product))
    for subject in ("a", "b"):
        manager.add_credential_with_data(
            {
                "bearer_token": jwt(subject, "old"),
                "user_id": subject + "@example.test",
                "created_at": 1,
                "expires_in": 3600,
                "refresh_token": "old-refresh",
                "domain": "example.test",
                "session_dead_at": 2,
                "session_dead_reason": "12153",
                "last_refresh_at": 1,
            },
            filename=subject + ".json",
        )
    if product == "codebuddy":
        manager.set_mode("manual", 0)
        manager.cooldowns[0] = 9999999999
    start = AsyncMock(
        side_effect=lambda: {
            "success": True,
            "auth_state": uuid.uuid4().hex,
            "verification_uri_complete": "https://login.example.test/authorize",
        }
    )
    poll = AsyncMock(
        return_value={
            "status": "success",
            "token_data": {
                "access_token": jwt("a", "new"),
                "expires_in": 7200,
                "domain": "example.test",
            },
        }
    )
    monkeypatch.setattr(module, product + "_token_manager", manager)
    monkeypatch.setattr(module, "start_" + product + "_auth", start)
    monkeypatch.setattr(module, "poll_" + product + "_auth_status", poll)
    app = FastAPI()
    app.include_router(module.router, prefix="/" + product)
    app.dependency_overrides[authenticate] = lambda: "test"
    service = CredentialReauthorization(
        lambda: manager,
        start,
        poll,
        getattr(module, "build_" + product + "_credential"),
    )
    return SimpleNamespace(
        product=product,
        module=module,
        manager=manager,
        start=start,
        poll=poll,
        app=app,
        service=service,
        directory=tmp_path,
    )


def exercise(scenario, action):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=scenario.app), base_url="http://test"
        ) as client:
            await action(client, "/" + scenario.product + "/auth/reauthorize")

    asyncio.run(run())


def test_targeted_login_updates_original_file_and_is_idempotent(scenario):
    s = scenario
    other_before = (s.directory / s.product / "b.json").read_bytes()

    async def delayed_login(state):
        await asyncio.sleep(0.01)
        return s.poll.return_value

    s.poll.side_effect = delayed_login

    async def run(client, path):
        started = await client.post(path, json={"filename": "a.json"})
        assert started.status_code == 200
        state = started.json()["auth_state"]
        values = await asyncio.gather(
            *[client.post(path + "/poll", json={"auth_state": state}) for _ in range(3)]
        )
        assert all(
            value.status_code == 200 and value.json()["saved"] for value in values
        )
        assert all("access_token" not in value.json() for value in values)
        assert values[0].json()["filename"] == "a.json"
        assert s.poll.await_count == 1

    exercise(s, run)
    assert len(s.manager.credentials) == 2
    data = s.manager.credentials[0]["data"]
    assert data["bearer_token"] == jwt("a", "new")
    assert not any(
        key in data
        for key in (
            "session_dead_at",
            "session_dead_reason",
            "last_refresh_at",
            "refresh_token",
        )
    )
    assert not s.manager.is_token_expired(data)
    assert (s.directory / s.product / "b.json").read_bytes() == other_before
    if s.product == "codebuddy":
        assert s.manager.mode == "manual"
        assert s.manager.manual_selected_index == 0
        assert not s.manager.cooldowns
    reloaded = type(s.manager)(s.manager.creds_dir)
    assert reloaded.credentials[0]["data"]["bearer_token"] == jwt("a", "new")


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_account",
        "wrong_domain",
        "unknown_identity",
        "missing_token",
        "deleted",
        "changed",
        "save_failed",
    ],
)
def test_failures_preserve_all_other_credentials(scenario, failure, monkeypatch):
    s = scenario
    before = {
        p.name: p.read_bytes() for p in (s.directory / s.product).glob("[ab].json")
    }

    async def run(client, path):
        response = await client.post(path, json={"filename": "a.json"})
        state = response.json()["auth_state"]
        token = s.poll.return_value["token_data"]
        expected = 409
        if failure == "wrong_account":
            token["access_token"] = jwt("b", "new")
        elif failure == "wrong_domain":
            token["domain"] = "another.test"
        elif failure == "unknown_identity":
            token["access_token"] = "opaque-token"
        elif failure == "missing_token":
            token.pop("access_token")
            expected = 502
        elif failure == "deleted":
            s.manager.delete_credential_by_index(0)
            expected = 404
        elif failure == "changed":
            s.manager.update_credential(
                "a.json", {"bearer_token": jwt("a", "concurrent")}
            )
        elif failure == "save_failed":

            def fail(*args):
                raise OSError("disk unavailable")

            monkeypatch.setattr(s.manager, "replace_credential", fail)
            expected = 500
        response = await client.post(path + "/poll", json={"auth_state": state})
        assert response.status_code == expected, response.text
        assert (s.directory / s.product / "b.json").read_bytes() == before["b.json"]
        if failure not in ("deleted", "changed"):
            assert (s.directory / s.product / "a.json").read_bytes() == before["a.json"]

    exercise(s, run)


def test_pending_missing_target_and_authentication(scenario):
    s = scenario

    async def run(client, path):
        for name in ("missing.json", "../a.json"):
            assert (await client.post(path, json={"filename": name})).status_code == 404
        assert not s.start.called
        response = await client.post(path, json={"filename": "a.json"})
        state = response.json()["auth_state"]
        s.poll.return_value = {"status": "pending", "message": "等待登录"}
        pending = await client.post(path + "/poll", json={"auth_state": state})
        assert pending.status_code == 400
        assert pending.json()["error"] == "authorization_pending"
        assert (
            await client.post(path + "/poll", json={"auth_state": "unknown"})
        ).status_code == 410
        s.app.dependency_overrides.clear()
        assert (await client.post(path, json={"filename": "a.json"})).status_code in (
            401,
            403,
        )
        assert (
            await client.post(path + "/poll", json={"auth_state": state})
        ).status_code in (401, 403)

    exercise(s, run)


def test_session_expiry_never_creates_or_updates_an_account(scenario):
    s = scenario
    clock = [0]
    s.service.clock = lambda: clock[0]

    async def run():
        response = await s.service.start("a.json")
        clock[0] = 901
        with pytest.raises(ReauthorizationError) as caught:
            await s.service.poll(response["auth_state"])
        assert caught.value.status == 410
        assert not s.poll.called

    asyncio.run(run())


def test_reordering_during_login_still_updates_target_filename(scenario):
    s = scenario

    async def run():
        response = await s.service.start("a.json")
        s.manager.delete_credential_by_index(1)
        s.manager.add_credential_with_data(
            {"bearer_token": jwt("c", "old"), "user_id": "c@example.test"},
            filename="0.json",
        )
        result = await s.service.poll(response["auth_state"])
        assert result["filename"] == "a.json"
        target = next(
            item
            for item in s.manager.credentials
            if item["file_path"].endswith("/a.json")
        )
        assert target["data"]["bearer_token"] == jwt("a", "new")

    asyncio.run(run())


def test_workbuddy_old_checkin_failure_clears_after_new_authorization(tmp_path):
    manager = WorkBuddyTokenManager(str(tmp_path / "creds"))
    manager.add_credential_with_data(
        {"bearer_token": "token", "user_id": "a", "created_at": 1, "expires_in": 1},
        filename="a.json",
    )
    checkin = CodeBuddyCheckinManager(
        token_manager=manager, state_file=str(tmp_path / "checkin.json")
    )
    checkin.state["accounts"]["a.json"] = {
        "filename": "a.json",
        "status": "expired",
        "checked_at": 2,
    }
    assert checkin.get_status()["accounts"][0]["status"] == "expired"
    manager.replace_credential(
        "a.json",
        {
            "bearer_token": "new",
            "user_id": "a",
            "created_at": 9999999999,
            "expires_in": 3600,
        },
    )
    assert checkin.get_status()["accounts"][0]["status"] == "pending"
