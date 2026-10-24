import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_router
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.console.diagnostics import DiagnosticsRepository
from src.request_log_manager import RequestLogManager


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_credit_failover_updates_headers_next_request_and_audit(
    tmp_path,
    monkeypatch,
    endpoint,
    stream,
):
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    for index in range(2):
        (credential_dir / f"account_{index}.json").write_text(
            json.dumps(
                {
                    "bearer_token": f"fake-token-{index}",
                    "user_id": f"fake-user-{index}",
                }
            ),
            encoding="utf-8",
        )
    manager = CodeBuddyTokenManager(str(credential_dir))
    logs = RequestLogManager(str(tmp_path / "requests.db"))
    monkeypatch.setattr(codebuddy_router, "codebuddy_token_manager", manager)
    monkeypatch.setattr(codebuddy_router, "request_log_manager", logs)
    monkeypatch.setattr(
        codebuddy_router.codebuddy_credential_refresher,
        "ensure_fresh",
        AsyncMock(side_effect=lambda credential: credential),
    )

    app = FastAPI()
    app.include_router(codebuddy_router.router, prefix="/codebuddy")

    async def authenticated():
        return "test"

    app.dependency_overrides[codebuddy_router.authenticate] = authenticated
    sent_credentials = []

    def upstream(request):
        sent_credentials.append(
            (
                request.headers["Authorization"],
                request.headers["X-User-Id"],
            )
        )
        if request.headers["Authorization"] == "Bearer fake-token-0":
            return httpx.Response(402, json={"error": {"message": "积分不足"}})
        event = {
            "model": "test",
            "choices": [
                {
                    "delta": {"content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }
        return httpx.Response(
            200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        ) as upstream_client:
            monkeypatch.setattr(
                codebuddy_router,
                "get_http_client",
                AsyncMock(return_value=upstream_client),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                body = {"model": "test", "stream": stream}
                if endpoint == "responses":
                    body["input"] = "hello"
                else:
                    body["messages"] = [{"role": "user", "content": "hello"}]
                for _ in range(2):
                    response = await client.post(f"/codebuddy/v1/{endpoint}", json=body)
                    assert response.status_code == 200
                    assert "ok" in response.text
                    assert '"error":' not in response.text
                current = await client.get("/codebuddy/v1/credentials/current")
                assert current.json()["index"] == 1

    asyncio.run(exercise())
    assert sent_credentials == [
        ("Bearer fake-token-0", "fake-user-0"),
        ("Bearer fake-token-1", "fake-user-1"),
        ("Bearer fake-token-1", "fake-user-1"),
    ]
    first, second = sorted(logs.list_requests()["items"], key=lambda item: item["id"])
    assert first["upstream_attempts"] == 2
    assert second["upstream_attempts"] == 1
    assert first["credential"] == second["credential"] == "account_1.json"
    assert first["credential_user_id"] == second["credential_user_id"] == "fake-user-1"

    diagnostics = DiagnosticsRepository(logs.connect)
    attempts = diagnostics.attempts(first["id"])
    assert [item["credential"] for item in attempts] == [
        "account_0.json",
        "account_1.json",
    ]
    assert [item["status_code"] for item in attempts] == [402, 200]
    assert len(diagnostics.attempts(second["id"])) == 1
    assert diagnostics.events()[0]["reason"] == "insufficient_credit"


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_explicit_filename_test_never_switches_or_forwards_selector(
    tmp_path, monkeypatch, endpoint, stream
):
    directory = tmp_path / "credentials"
    directory.mkdir()
    for index in range(2):
        (directory / f"account_{index}.json").write_text(
            json.dumps(
                {
                    "bearer_token": f"test-{index}",
                    "user_id": str(index),
                }
            )
        )
    manager = CodeBuddyTokenManager(str(directory))
    manager.set_mode("manual", 1)
    logs = RequestLogManager(str(tmp_path / "audit.db"))
    monkeypatch.setattr(codebuddy_router, "codebuddy_token_manager", manager)
    monkeypatch.setattr(codebuddy_router, "request_log_manager", logs)
    monkeypatch.setattr(
        codebuddy_router.codebuddy_credential_refresher,
        "ensure_fresh",
        AsyncMock(side_effect=lambda credential: credential),
    )
    sent = []

    def upstream(request):
        sent.append(request)
        assert request.headers["Authorization"] == "Bearer test-0"
        assert "_credential_filename" not in json.loads(request.content)
        return httpx.Response(402, json={"error": {"message": "积分不足"}})

    app = FastAPI()
    app.include_router(codebuddy_router.router, prefix="/codebuddy")

    async def authorized():
        return "test"

    app.dependency_overrides[codebuddy_router.authenticate] = authorized

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        ) as upstream_client:
            monkeypatch.setattr(
                codebuddy_router,
                "get_http_client",
                AsyncMock(return_value=upstream_client),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                body = {
                    "model": "test",
                    "stream": stream,
                    "_credential_filename": "account_0.json",
                }
                body.update(
                    {"input": "hello"}
                    if endpoint == "responses"
                    else {"messages": [{"role": "user", "content": "hello"}]}
                )
                response = await client.post("/codebuddy/v1/" + endpoint, json=body)
                assert response.status_code == (
                    200 if stream and endpoint == "chat/completions" else 402
                )

    asyncio.run(exercise())
    assert len(sent) == 1
    assert manager.current_index == manager.manual_selected_index == 1
    assert manager.cooldowns == {}
    assert logs.list_requests()["items"][0]["upstream_attempts"] == 1
