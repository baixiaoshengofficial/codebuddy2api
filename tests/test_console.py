import asyncio
import csv
import io
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from src.auth import authenticate
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.console.diagnostics import DiagnosticsRepository
from src.chat.observation import AttemptObserver
from src.request_query import RequestFilter, time_range
from src.console.router import router, get_logs
from src.console.settings import validate_settings
from src.request_log_manager import RequestLogManager


def make_logs(tmp_path):
    logs = RequestLogManager(str(tmp_path / "requests.db"))
    for number in range(3):
        logs.start_request(
            str(number),
            None,
            "/chat",
            "test",
            "pytest",
            None,
            None,
            "account.json",
            None,
            "User: hello",
            "hash",
            False,
        )
    with logs.connect() as db:
        db.execute("UPDATE request_logs SET created_at=id * 1000")
    return logs


def test_list_and_export_share_exact_boundaries_and_literal_search(tmp_path):
    logs = make_logs(tmp_path)
    query = RequestFilter(start=2000, end=3000)
    assert [row["id"] for row in logs.list_requests(query=query)["items"]] == [2]
    _, content = logs.export_csv(query=query)
    assert [
        row["request_id"]
        for row in csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    ] == ["1"]
    assert logs.list_requests(query=RequestFilter(search="%"))["total"] == 0
    assert logs.list_requests(query=RequestFilter(search="' OR 1=1 --"))["total"] == 0


def test_today_uses_midnight_in_selected_timezone():
    now = datetime(2026, 10, 23, 2, 30, tzinfo=timezone.utc).timestamp()
    start, end = time_range("today", offset=480, now=now)
    assert (
        datetime.fromtimestamp(start / 1000, timezone.utc).isoformat()
        == "2026-10-22T16:00:00+00:00"
    )
    assert end == int(now * 1000) + 1
    assert time_range("24h", now=now)[0] == int((now - 86400) * 1000)
    with pytest.raises(ValueError):
        time_range("custom", 3000, 1000)


def test_observer_finishes_cancelled_and_failed_attempts_without_replay(tmp_path):
    logs = make_logs(tmp_path)
    repository = DiagnosticsRepository(logs.connect)
    observer = AttemptObserver(repository, 1)
    with pytest.raises(asyncio.CancelledError):
        with observer.attempt(1, "account.json"):
            raise asyncio.CancelledError()
    attempt = repository.attempts(1)[0]
    assert attempt["status_code"] == 499
    assert attempt["completed_at"] is not None
    with logs.connect() as db:
        db.execute("DELETE FROM request_logs WHERE id=1")
    repository.prune()
    assert repository.attempts(1) == []


def test_observer_storage_failure_cannot_change_model_result():
    class BrokenRepository:
        def start(self, *args):
            raise OSError("disk unavailable")

    with AttemptObserver(BrokenRepository(), 1).attempt(1, "file.json"):
        pass


def test_snapshot_is_read_only_and_manual_filename_survives_deletion(tmp_path):
    for index in range(3):
        (tmp_path / f"account_{index}.json").write_text(
            json.dumps({"bearer_token": "secret", "user_id": str(index)})
        )
    manager = CodeBuddyTokenManager(str(tmp_path))
    manager.set_mode("manual", 2)
    assert manager.delete_credential_by_index(0)
    snapshot = manager.get_routing_snapshot()
    assert snapshot["mode"] == "manual"
    assert (
        snapshot["credentials"][snapshot["current_index"]]["filename"]
        == "account_2.json"
    )
    before = manager.usage_count
    assert "secret" not in json.dumps(snapshot)
    assert "file_path" not in json.dumps(snapshot)
    assert manager.usage_count == before
    manager.set_mode("auto")
    manager.mark_credential_exhausted(1)
    assert manager.get_routing_snapshot()["credentials"][1]["state"] == "cooldown"


@pytest.mark.parametrize(
    "settings",
    [
        {"CODEBUDDY_SITE": "invalid"},
        {"CODEBUDDY_CHECKIN_TIME": "25:10"},
        {"CODEBUDDY_PASSWORD": ""},
        {"CODEBUDDY_BARK_URL": "javascript:alert(1)"},
        {"CODEBUDDY_AUTO_CHECKIN": "perhaps"},
        {"unknown": "x"},
    ],
)
def test_settings_reject_invalid_changes(settings):
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_console_routes_auth_detail_and_invalid_time_range(tmp_path):
    logs = make_logs(tmp_path)
    app = FastAPI()
    app.include_router(router)

    # Async overrides avoid dispatch to a worker thread in ASGI-only tests.
    async def log_store():
        return logs

    app.dependency_overrides[get_logs] = log_store

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/console/status")).status_code in (401, 403)

            async def authorized():
                return "test"

            app.dependency_overrides[authenticate] = authorized
            response = await client.get("/api/console/requests/2")
            assert response.status_code == 200
            assert response.json()["attempts"] == []
            assert (await client.get("/api/console/requests/999")).status_code == 404
            assert (
                await client.get("/api/console/requests?period=custom&start=3&end=1")
            ).status_code == 422
            overview = (
                await client.get(
                    "/api/console/overview?period=custom&start=1000&end=3000"
                )
            ).json()
            assert overview["requests"] == 2
            assert overview["success_rate"] is None

    asyncio.run(exercise())


def test_completed_upstream_attempt_is_not_marked_cancelled_after_done(tmp_path):
    logs = make_logs(tmp_path)
    repository = DiagnosticsRepository(logs.connect)
    observer = AttemptObserver(repository, 1)
    with pytest.raises(asyncio.CancelledError):
        with observer.attempt(1, 'account.json') as result:
            result['complete'] = True
            raise asyncio.CancelledError()
    assert repository.attempts(1)[0]['status_code'] == 200


def test_attempt_errors_redact_quoted_upstream_secrets(tmp_path):
    logs = make_logs(tmp_path)
    repository = DiagnosticsRepository(logs.connect)
    attempt_id = repository.start(1, 1, 'account.json')
    repository.finish(attempt_id, 502, {'access_token': 'secret-value', 'password': 'secret-password'})
    error = repository.attempts(1)[0]['error']
    assert 'secret-value' not in error
    assert 'secret-password' not in error
    assert '[REDACTED]' in error
