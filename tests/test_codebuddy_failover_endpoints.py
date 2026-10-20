import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_router
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.request_log_manager import RequestLogManager


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_credit_failover_updates_headers_next_request_and_audit(
    tmp_path, monkeypatch, endpoint, stream,
):
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    for index in range(2):
        (credential_dir / f"account_{index}.json").write_text(json.dumps({
            "bearer_token": f"fake-token-{index}", "user_id": f"fake-user-{index}",
        }), encoding="utf-8")
    manager = CodeBuddyTokenManager(str(credential_dir))
    logs = RequestLogManager(str(tmp_path / "requests.db"))
    monkeypatch.setattr(codebuddy_router, "codebuddy_token_manager", manager)
    monkeypatch.setattr(codebuddy_router, "request_log_manager", logs)
    monkeypatch.setattr(
        codebuddy_router.codebuddy_credential_refresher, "ensure_fresh",
        AsyncMock(side_effect=lambda credential: credential),
    )

    app = FastAPI()
    app.include_router(codebuddy_router.router, prefix="/codebuddy")

    async def authenticated():
        return "test"

    app.dependency_overrides[codebuddy_router.authenticate] = authenticated
    sent_credentials = []

    def upstream(request):
        sent_credentials.append((
            request.headers["Authorization"], request.headers["X-User-Id"],
        ))
        if request.headers["Authorization"] == "Bearer fake-token-0":
            return httpx.Response(402, json={"error": {"message": "积分不足"}})
        event = {"model": "test", "choices": [{
            "delta": {"content": "ok"}, "finish_reason": "stop",
        }]}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
            monkeypatch.setattr(
                codebuddy_router, "get_http_client", AsyncMock(return_value=upstream_client),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
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
