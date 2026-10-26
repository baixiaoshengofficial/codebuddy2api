"""Persistence failure, data isolation and replaceable storage contracts."""

import asyncio
import copy
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.credential_store import JsonCredentialStore
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.workbuddy_token_manager import WorkBuddyTokenManager
from src.credential_reauthorization import CredentialReauthorization


def test_loading_skips_state_invalid_and_incomplete_files_without_rewriting(tmp_path):
    store = JsonCredentialStore(str(tmp_path))
    store.write("account.json", {"bearer_token": "old", "user_id": "a"})
    store.write("manager_state.json", {"bearer_token": "not-an-account"})
    store.write("incomplete.json", {"user_id": "missing-token"})
    (tmp_path / "broken.json").write_text("not-json")
    (tmp_path / "array.json").write_text("[]")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    records = store.records()
    assert [Path(row["file_path"]).name for row in records] == ["account.json"]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize(
    "name", ["../outside.json", "/outside.json", "folder/account.json", "file.txt", ""]
)
def test_store_rejects_paths_before_file_operations(tmp_path, name):
    store = JsonCredentialStore(str(tmp_path))
    for operation in [
        lambda: store.read(name),
        lambda: store.write(name, {}),
        lambda: store.delete(name),
    ]:
        with pytest.raises(ValueError):
            operation()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_uses_private_permissions_and_updates_requested_fields(tmp_path):
    store = JsonCredentialStore(str(tmp_path))
    store.write(
        "account.json",
        {"bearer_token": "old", "refresh_token": "old-refresh", "user_id": "a"},
    )
    updated = store.update(
        "account.json", {"bearer_token": "new"}, remove_keys=["refresh_token"]
    )
    assert updated == {"bearer_token": "new", "user_id": "a"}
    assert store.read("account.json") == updated
    assert (os.stat(tmp_path / "account.json").st_mode & 0o777) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["account.json"]


@pytest.mark.parametrize("manager_type", [CodeBuddyTokenManager, WorkBuddyTokenManager])
def test_failed_replace_keeps_file_and_cached_credential_consistent(
    tmp_path, monkeypatch, manager_type
):
    manager = manager_type(str(tmp_path))
    manager.add_credential_with_data(
        {"bearer_token": "old", "user_id": "a"}, filename="account.json"
    )
    before = (tmp_path / "account.json").read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("src.credential_store.os.replace", fail)
    with pytest.raises(OSError):
        manager.update_credential("account.json", {"bearer_token": "new"})
    assert (tmp_path / "account.json").read_bytes() == before
    assert manager.get_credential("account.json")["bearer_token"] == "old"
    assert not list(tmp_path.glob(".credential-*"))


def test_serialization_failure_does_not_truncate_an_existing_account(tmp_path):
    store = JsonCredentialStore(str(tmp_path))
    store.write("account.json", {"bearer_token": "old"})
    before = (tmp_path / "account.json").read_bytes()
    with pytest.raises(TypeError):
        store.write("account.json", {"bearer_token": object()})
    assert (tmp_path / "account.json").read_bytes() == before
    assert not list(tmp_path.glob(".credential-*"))


class MemoryStore:
    def __init__(self, directory):
        self.directory = str(directory)
        self.data = {}

    def records(self):
        return [
            {"file_path": str(Path(self.directory) / name), "data": copy.deepcopy(data)}
            for name, data in sorted(self.data.items())
            if name != "manager_state.json" and "bearer_token" in data
        ]

    def read(self, filename):
        if filename not in self.data:
            raise FileNotFoundError(filename)
        return copy.deepcopy(self.data[filename])

    def write(self, filename, data):
        self.data[filename] = copy.deepcopy(data)

    def update(self, filename, updates, remove_keys=()):
        data = self.read(filename)
        data.update(updates)
        for key in remove_keys:
            data.pop(key, None)
        self.write(filename, data)
        return data

    def delete(self, filename):
        if filename not in self.data:
            raise FileNotFoundError(filename)
        del self.data[filename]


@pytest.mark.parametrize("manager_type", [CodeBuddyTokenManager, WorkBuddyTokenManager])
def test_managers_work_with_a_store_that_has_no_filesystem(tmp_path, manager_type):
    store = MemoryStore(tmp_path / "not-created")
    manager = manager_type(store=store)
    assert manager.add_credential_with_data(
        {"bearer_token": "old", "user_id": "a"}, filename="a.json"
    )
    manager.update_credential("a.json", {"bearer_token": "new"})
    assert manager.get_credential("a.json")["bearer_token"] == "new"
    if isinstance(manager, CodeBuddyTokenManager):
        manager.set_mode("manual", 0)
        restored = manager_type(store=store)
        assert restored.mode == "manual" and restored.manual_selected_index == 0
    assert manager.delete_credential_by_index(0)
    assert manager.get_credential_records() == []
    assert not Path(store.directory).exists()


def test_reauthorization_needs_only_public_read_and_replace_operations():
    class CredentialPort:
        def __init__(self):
            self.data = {
                "bearer_token": "old",
                "user_id": "a",
                "domain": "example.test",
            }

        def get_credential(self, filename):
            assert filename == "a.json"
            return dict(self.data)

        def replace_credential(self, filename, data):
            assert filename == "a.json"
            self.data = dict(data)

    port = CredentialPort()
    flow = CredentialReauthorization(
        lambda: port,
        AsyncMock(
            return_value={
                "success": True,
                "auth_state": "s1",
                "verification_uri_complete": "https://login.example.test/",
            }
        ),
        AsyncMock(
            return_value={"status": "success", "token_data": {"access_token": "new"}}
        ),
        lambda token: {
            "bearer_token": token["access_token"],
            "user_id": "a",
            "domain": "example.test",
        },
    )

    async def run():
        await flow.start("a.json")
        result = await flow.poll("s1")
        assert result["saved"] and port.data["bearer_token"] == "new"

    asyncio.run(run())
