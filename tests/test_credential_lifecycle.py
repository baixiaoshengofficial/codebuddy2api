"""Exercise real routing, refresh and persistence against an isolated upstream."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_router, codebuddy_refresh
from src.codebuddy_refresh import CodeBuddyCredentialRefresher
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.console import router as console_router
from src.console.diagnostics import DiagnosticsRepository
from src.request_log_manager import RequestLogManager


class Clock:
    def __init__(self):
        self.now = 1800000000

    def advance(self, seconds):
        self.now += seconds


class LifecycleSimulation:
    def __init__(self, directory, monkeypatch):
        self.clock = Clock()
        monkeypatch.setattr(codebuddy_refresh.time, "time", lambda: self.clock.now)
        self.directory = directory / "credentials"
        self.directory.mkdir()
        for index in range(3):
            self.write(index, created_at=self.clock.now - 600, expires_in=86400)
        self.manager = CodeBuddyTokenManager(str(self.directory))
        self.logs = RequestLogManager(str(directory / "audit.db"))
        self.diagnostics = DiagnosticsRepository(self.logs.connect)
        self.events = []
        self.quota = set()
        self.sse_quota = False
        self.partial_quota = False
        self.refresh_status = 200
        self.refresh_code = 0
        self.refresh_network_down = False
        self.refresh_delay = 0
        self.refresher = CodeBuddyCredentialRefresher(
            self.manager,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(self.refresh_upstream)
            ),
        )
        monkeypatch.setattr(codebuddy_router, "codebuddy_token_manager", self.manager)
        monkeypatch.setattr(
            codebuddy_router, "codebuddy_credential_refresher", self.refresher
        )
        monkeypatch.setattr(codebuddy_router, "request_log_manager", self.logs)
        monkeypatch.setattr(
            codebuddy_router,
            "get_codebuddy_api_url",
            lambda: "https://chat.example.test/v2/chat/completions",
        )
        self.chat_client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.chat_upstream)
        )

        async def client():
            return self.chat_client

        monkeypatch.setattr(codebuddy_router, "get_http_client", client)
        self.app = FastAPI()
        self.app.include_router(codebuddy_router.router, prefix="/codebuddy")
        self.app.include_router(console_router.router)

        async def authenticate():
            return "simulation"

        async def tokens():
            return self.manager

        async def logs():
            return self.logs

        self.app.dependency_overrides[codebuddy_router.authenticate] = authenticate
        self.app.dependency_overrides[console_router.get_tokens] = tokens
        self.app.dependency_overrides[console_router.get_logs] = logs

    def write(self, index, **updates):
        data = {
            "bearer_token": f"token-{index}",
            "refresh_token": f"refresh-{index}",
            "user_id": f"user-{index}",
            "domain": "www.codebuddy.cn",
            **updates,
        }
        (self.directory / f"account_{index}.json").write_text(json.dumps(data))

    def expire(self, index):
        self.manager.update_credential(
            f"account_{index}.json",
            {
                "created_at": self.clock.now - 7200,
                "expires_in": 3600,
            },
        )

    @staticmethod
    def success():
        event = {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}
        return httpx.Response(
            200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
        )

    def chat_upstream(self, request):
        authorization = request.headers["Authorization"]
        index = int(authorization.rsplit("-", 1)[-1])
        self.events.append(
            {
                "kind": "chat",
                "index": index,
                "authorization": authorization,
                "user_id": request.headers["X-User-Id"],
            }
        )
        if index in self.quota:
            if self.sse_quota or self.partial_quota:
                content = (
                    {"content": "partial"}
                    if self.partial_quota
                    else {"role": "assistant"}
                )
                event = {"id": "abandoned-account", "choices": [{"delta": content}]}
                error = {"error": {"data": {"code": 14018, "msg": "额度已用尽"}}}
                return httpx.Response(
                    200,
                    text="data: "
                    + json.dumps(event)
                    + "\n\ndata: "
                    + json.dumps(error)
                    + "\n\n",
                )
            return httpx.Response(
                429, json={"error": {"data": {"code": 14018, "msg": "额度已用尽"}}}
            )
        return self.success()

    async def refresh_upstream(self, request):
        refresh_token = request.headers["X-Refresh-Token"]
        index = int(refresh_token.rsplit("-", 1)[-1])
        self.events.append(
            {
                "kind": "refresh",
                "index": index,
                "url": str(request.url),
                "source": request.headers["X-Auth-Refresh-Source"],
            }
        )
        if self.refresh_delay:
            await asyncio.sleep(self.refresh_delay)
        if self.refresh_network_down:
            raise httpx.ConnectError(
                "simulated refresh connection failure", request=request
            )
        if self.refresh_status != 200:
            return httpx.Response(
                self.refresh_status, json={"error": "refresh unavailable"}
            )
        if self.refresh_code:
            return httpx.Response(
                200,
                json={"code": self.refresh_code, "msg": "offline session not found"},
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "accessToken": f"renewed-{index}",
                    "refreshToken": f"rotated-{index}",
                    "expiresIn": 86400,
                },
            },
        )

    def run(self, scenario):
        async def exercise():
            async with self.chat_client, httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://simulation",
            ) as client:
                self.client = client
                await scenario()

        asyncio.run(exercise())

    async def chat(self, endpoint="chat/completions", stream=False):
        body = {"model": "simulation", "stream": stream}
        body.update(
            {"input": "hello"}
            if endpoint == "responses"
            else {"messages": [{"role": "user", "content": "hello"}]}
        )
        return await self.client.post("/codebuddy/v1/" + endpoint, json=body)

    async def pool(self):
        response = await self.client.get("/api/console/credentials")
        assert response.status_code == 200
        return response.json()

    def model_events(self):
        return [event for event in self.events if event["kind"] == "chat"]

    def refresh_events(self):
        return [event for event in self.events if event["kind"] == "refresh"]


@pytest.fixture
def simulation(tmp_path, monkeypatch):
    return LifecycleSimulation(tmp_path, monkeypatch)


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("sse_quota", [False, True], ids=["http-quota", "sse-quota"])
def test_multiple_exhaustion_switches_in_order_and_stays_on_success(
    simulation, endpoint, stream, sse_quota
):
    sim = simulation
    sim.quota = {0, 1}
    sim.sse_quota = sse_quota

    async def scenario():
        first = await sim.chat(endpoint, stream)
        assert first.status_code == 200 and "OK" in first.text
        assert "abandoned-account" not in first.text
        second = await sim.chat(endpoint, stream)
        assert second.status_code == 200 and "OK" in second.text
        pool = await sim.pool()
        assert pool["current_index"] == 2
        assert [row["state"] for row in pool["credentials"]] == [
            "cooldown",
            "cooldown",
            "available",
        ]
        # Passing the cooldown deadline does not rotate away from a working account.
        sim.clock.advance(sim.manager.cooldown_seconds + 1)
        third = await sim.chat(endpoint, stream)
        assert third.status_code == 200
        assert (await sim.pool())["current_index"] == 2

    sim.run(scenario)
    assert [event["index"] for event in sim.model_events()] == [0, 1, 2, 2, 2]
    assert [event["user_id"] for event in sim.model_events()] == [
        "user-0",
        "user-1",
        "user-2",
        "user-2",
        "user-2",
    ]
    rows = sorted(sim.logs.list_requests()["items"], key=lambda item: item["id"])
    assert [row["upstream_attempts"] for row in rows] == [3, 1, 1]
    assert all(row["credential"] == "account_2.json" for row in rows)
    assert [
        item["status_code"] for item in sim.diagnostics.attempts(rows[0]["id"])
    ] == [402, 402, 200]
    # The observer classifies quota exceptions as 402 regardless of upstream 429.
    assert len(sim.diagnostics.events()) == 2
    restarted = CodeBuddyTokenManager(str(sim.directory))
    assert restarted.get_next_credential()["_credential_filename"] == "account_2.json"


def test_all_exhausted_stops_without_replay_and_recovers_after_cooldown(simulation):
    sim = simulation
    sim.quota = {0, 1, 2}

    async def scenario():
        assert (await sim.chat()).status_code == 402
        assert (await sim.chat()).status_code == 402
        assert len(sim.model_events()) == 3
        assert (await sim.pool())["current_index"] is None
        restarted = CodeBuddyTokenManager(str(sim.directory))
        assert restarted.get_next_credential() is None
        sim.clock.advance(sim.manager.cooldown_seconds + 1)
        sim.quota.clear()
        assert (await sim.chat()).status_code == 200
        assert (await sim.pool())["available"] == 3

    sim.run(scenario)
    assert len(sim.model_events()) == 4


def test_manual_mode_does_not_fail_over_on_exhaustion(simulation):
    sim = simulation
    sim.manager.set_mode("manual", 0)
    sim.quota = {0}

    async def scenario():
        assert (await sim.chat()).status_code == 402
        assert (await sim.pool())["mode"] == "manual"

    sim.run(scenario)
    assert [event["index"] for event in sim.model_events()] == [0]
    assert sim.manager.current_index == 0


@pytest.mark.parametrize("stream", [False, True])
def test_quota_after_output_only_switches_future_requests(simulation, stream):
    sim = simulation
    sim.quota = {0}
    sim.partial_quota = True

    async def scenario():
        response = await sim.chat(stream=stream)
        assert response.status_code == (200 if stream else 402)
        if stream:
            assert "partial" in response.text and "insufficient_credit" in response.text
        assert len(sim.model_events()) == 1
        assert (await sim.pool())["current_index"] == 1
        assert (await sim.chat(stream=stream)).status_code == 200

    sim.run(scenario)
    assert [event["index"] for event in sim.model_events()] == [0, 1]
    rows = sim.logs.list_requests()["items"]
    assert len(rows) == 2 and all(row["upstream_attempts"] == 1 for row in rows)


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_expired_token_refreshes_before_chat_and_persists_rotation(
    simulation, endpoint, stream
):
    sim = simulation
    sim.expire(0)

    async def scenario():
        assert (await sim.pool())["credentials"][0]["state"] == "refreshable"
        assert (await sim.chat(endpoint, stream)).status_code == 200
        assert (await sim.chat(endpoint, stream)).status_code == 200
        assert (await sim.pool())["current_index"] == 0

    sim.run(scenario)
    assert [event["kind"] for event in sim.events] == ["refresh", "chat", "chat"]
    assert [event["authorization"] for event in sim.model_events()] == [
        "Bearer renewed-0"
    ] * 2
    assert (
        sim.refresh_events()[0]["url"]
        == "https://www.codebuddy.cn/v2/plugin/auth/token/refresh"
    )
    assert sim.refresh_events()[0]["source"] == "codebuddy"
    persisted = json.loads((sim.directory / "account_0.json").read_text())
    assert persisted["refresh_token"] == "rotated-0"
    restarted = CodeBuddyTokenManager(str(sim.directory))
    assert restarted.get_next_credential()["bearer_token"] == "renewed-0"
    assert not restarted.is_token_expired(persisted)
    assert all(
        row["upstream_attempts"] == 1 for row in sim.logs.list_requests()["items"]
    )


def test_proactive_keepalive_refreshes_still_valid_session(simulation):
    sim = simulation
    sim.manager.update_credential(
        "account_0.json",
        {
            "created_at": sim.clock.now - sim.refresher.REFRESH_INTERVAL - 1,
            "expires_in": 3 * 86400,
        },
    )

    async def scenario():
        assert (await sim.chat()).status_code == 200

    sim.run(scenario)
    assert len(sim.refresh_events()) == 1
    assert sim.model_events()[0]["authorization"] == "Bearer renewed-0"


@pytest.mark.parametrize("status,code", [(401, 0), (403, 0), (200, 12153)])
def test_revoked_session_is_marked_for_reauthorization_and_skipped(
    simulation, status, code
):
    sim = simulation
    sim.expire(0)
    sim.refresh_status, sim.refresh_code = status, code

    async def scenario():
        assert (await sim.chat()).status_code == 200
        pool = await sim.pool()
        assert pool["credentials"][0]["state"] == "reauthorize"
        assert pool["current_index"] == 1

    sim.run(scenario)
    assert [event["index"] for event in sim.model_events()] == [1]
    persisted = json.loads((sim.directory / "account_0.json").read_text())
    assert persisted.get("session_dead_at")


def test_concurrent_requests_refresh_once_and_all_use_new_token(simulation):
    sim = simulation
    sim.expire(0)
    sim.refresh_delay = 0.01

    async def scenario():
        responses = await asyncio.gather(*(sim.chat() for _ in range(8)))
        assert all(response.status_code == 200 for response in responses)

    sim.run(scenario)
    assert len(sim.refresh_events()) == 1
    assert len(sim.model_events()) == 8
    assert all(
        event["authorization"] == "Bearer renewed-0" for event in sim.model_events()
    )


@pytest.mark.parametrize(
    "network_down", [False, True], ids=["refresh-503", "refresh-connect-error"]
)
def test_expired_token_with_transient_refresh_failure_never_reaches_chat(
    simulation, network_down
):
    sim = simulation
    sim.expire(0)
    sim.refresh_status = 503
    sim.refresh_network_down = network_down

    async def scenario():
        response = await sim.chat()
        assert response.status_code == 503
        assert sim.model_events() == []
        assert (await sim.pool())["current_index"] == 0
        assert not sim.manager.credentials[0]["data"].get("session_dead_at")
        sim.refresh_network_down = False
        sim.refresh_status = 200
        assert (await sim.chat()).status_code == 200

    sim.run(scenario)
    assert sim.model_events()[0]["authorization"] == "Bearer renewed-0"


def test_transient_keepalive_failure_can_use_unexpired_token(simulation):
    sim = simulation
    sim.manager.update_credential(
        "account_0.json",
        {
            "created_at": sim.clock.now - sim.refresher.REFRESH_INTERVAL - 1,
            "expires_in": 3 * 86400,
        },
    )
    sim.refresh_status = 503

    async def scenario():
        assert (await sim.chat()).status_code == 200

    sim.run(scenario)
    assert len(sim.refresh_events()) == 1
    assert sim.model_events()[0]["authorization"] == "Bearer token-0"


def test_all_revoked_sessions_require_reauthorization_without_model_calls(simulation):
    sim = simulation
    for index in range(3):
        sim.expire(index)
    sim.refresh_code = 12153

    async def scenario():
        assert (await sim.chat()).status_code == 401
        pool = await sim.pool()
        assert pool["current_index"] is None
        assert [row["state"] for row in pool["credentials"]] == ["reauthorize"] * 3

    sim.run(scenario)
    assert [event["index"] for event in sim.refresh_events()] == [0, 1, 2]
    assert sim.model_events() == []


def test_expired_session_without_refresh_token_is_skipped_without_network(simulation):
    sim = simulation
    sim.expire(0)
    sim.manager.update_credential("account_0.json", {}, remove_keys=["refresh_token"])

    async def scenario():
        assert (await sim.chat()).status_code == 200
        pool = await sim.pool()
        assert pool["credentials"][0]["state"] == "expired"
        assert pool["current_index"] == 1

    sim.run(scenario)
    assert sim.refresh_events() == []
    assert [event["index"] for event in sim.model_events()] == [1]
