"""Stale lists cannot redirect deletion to another WorkBuddy account."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src import workbuddy_auth_router
from src.auth import authenticate
from src.workbuddy_token_manager import WorkBuddyTokenManager


@pytest.fixture
def store(tmp_path, monkeypatch):
    manager = WorkBuddyTokenManager(str(tmp_path / "creds"))
    for account in ("a", "b", "c"):
        manager.add_credential_with_data(
            {"bearer_token": "token-" + account, "user_id": account},
            filename=account + ".json",
        )
    monkeypatch.setattr(workbuddy_auth_router, "workbuddy_token_manager", manager)
    app = FastAPI()
    app.include_router(workbuddy_auth_router.router, prefix="/workbuddy")

    async def authorized():
        return "test"

    app.dependency_overrides[authenticate] = authorized
    return SimpleNamespace(manager=manager, app=app, path=tmp_path / "creds")


def exercise(store, action):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=store.app), base_url="http://test"
        ) as client:
            await action(client)

    asyncio.run(run())


def test_reordered_list_deletes_exact_original_filename(store):
    async def run(client):
        snapshot = (await client.get("/workbuddy/credentials")).json()["credentials"]
        target = snapshot[1]
        survivor = (store.path / "c.json").read_bytes()
        assert (await client.delete("/workbuddy/credentials/a.json")).status_code == 200
        store.manager.add_credential_with_data(
            {"bearer_token": "other", "user_id": "other"}, filename="0.json"
        )
        assert (
            await client.delete("/workbuddy/credentials/" + target["filename"])
        ).status_code == 200
        assert {row["filename"] for row in store.manager.get_credentials_info()} == {
            "0.json",
            "c.json",
        }
        assert (store.path / "c.json").read_bytes() == survivor

    exercise(store, run)


def test_stale_request_for_removed_target_does_not_delete_next_account(store):
    async def run(client):
        assert (await client.delete("/workbuddy/credentials/a.json")).status_code == 200
        before = {p.name: p.read_bytes() for p in store.path.glob("*.json")}
        assert (await client.delete("/workbuddy/credentials/a.json")).status_code == 404
        assert {p.name: p.read_bytes() for p in store.path.glob("*.json")} == before

    exercise(store, run)


def test_legacy_index_request_is_rejected_without_deleting_files(store):
    async def run(client):
        before = {p.name: p.read_bytes() for p in store.path.glob("*.json")}
        response = await client.post("/workbuddy/credentials/delete", json={"index": 0})
        assert response.status_code == 409
        assert {p.name: p.read_bytes() for p in store.path.glob("*.json")} == before
        assert (
            await client.post(
                "/workbuddy/credentials/delete", json={"filename": "b.json"}
            )
        ).status_code == 200
        assert {row["filename"] for row in store.manager.get_credentials_info()} == {
            "a.json",
            "c.json",
        }

    exercise(store, run)


def test_deletion_requires_authentication_and_reports_storage_failure(
    store, monkeypatch
):
    async def run(client):
        def fail(filename):
            raise PermissionError("read only")

        monkeypatch.setattr(store.manager, "delete_credential", fail)
        assert (await client.delete("/workbuddy/credentials/a.json")).status_code == 500
        store.app.dependency_overrides.clear()
        assert (await client.delete("/workbuddy/credentials/a.json")).status_code in (
            401,
            403,
        )
        assert (
            await client.post(
                "/workbuddy/credentials/delete", json={"filename": "a.json"}
            )
        ).status_code in (401, 403)
        assert len(store.manager.credentials) == 3

    exercise(store, run)


@pytest.mark.parametrize(
    "filename", ["../a.json", "/a.json", "manager_state.json", "missing.json", ""]
)
def test_storage_rejects_unknown_files_and_paths(store, filename):
    before = {p.name: p.read_bytes() for p in store.path.glob("*.json")}
    with pytest.raises(FileNotFoundError):
        store.manager.delete_credential(filename)
    assert {p.name: p.read_bytes() for p in store.path.glob("*.json")} == before
