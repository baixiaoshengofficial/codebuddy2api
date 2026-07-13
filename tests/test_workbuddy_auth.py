import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.checkin_manager import CodeBuddyCheckinManager
from src.workbuddy_auth_router import (
    WORKBUDDY_PLATFORM,
    poll_workbuddy_auth_status,
    save_workbuddy_token,
    start_workbuddy_auth,
)
from src.workbuddy_token_manager import WorkBuddyTokenManager


class WorkBuddyAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_uses_workbuddy_platform(self):
        captured = {}

        async def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = str(url)
            captured["headers"] = headers or {}
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "code": 0,
                "data": {"state": "state-1", "authUrl": "https://example.com/login"},
            }
            return response

        client = AsyncMock()
        client.post = fake_post
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=client)
        client_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.workbuddy_auth_router.httpx.AsyncClient", return_value=client_cm):
            result = await start_workbuddy_auth()

        self.assertTrue(result["success"], result)
        self.assertEqual(result["platform"], WORKBUDDY_PLATFORM)
        self.assertIn(f"platform={WORKBUDDY_PLATFORM}", captured["url"])
        self.assertEqual(result["auth_state"], "state-1")
        self.assertEqual(result["verification_uri_complete"], "https://example.com/login")

    async def test_poll_pending_code(self):
        async def fake_get(url, headers=None, timeout=None):
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {"code": 11217, "msg": "login ing..."}
            return response

        client = AsyncMock()
        client.get = fake_get
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=client)
        client_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("src.workbuddy_auth_router.httpx.AsyncClient", return_value=client_cm):
            result = await poll_workbuddy_auth_status("state-1")
        self.assertEqual(result["status"], "pending")


class WorkBuddyTokenManagerTests(unittest.TestCase):
    def test_add_and_list_credentials(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manager = WorkBuddyTokenManager(creds_dir=temporary.name)
        ok = manager.add_credential_with_data({
            "bearer_token": "token-1",
            "user_id": "uid-1",
            "domain": "copilot.tencent.com",
            "expires_in": 3600,
        })
        self.assertTrue(ok)
        info = manager.get_credentials_info()
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["user_id"], "uid-1")
        self.assertEqual(info[0]["product"], "WorkBuddy")
        self.assertTrue(manager.delete_credential_by_index(0))
        self.assertEqual(manager.get_credentials_info(), [])


class CheckinUsesWorkBuddyStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkin_reads_workbuddy_manager(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        creds_dir = os.path.join(temporary.name, "creds")
        os.makedirs(creds_dir)
        manager = WorkBuddyTokenManager(creds_dir=creds_dir)
        manager.add_credential_with_data({
            "bearer_token": "secret",
            "user_id": "wb-user",
            "domain": "copilot.tencent.com",
        })

        calls = []

        def handler(request: httpx.Request):
            calls.append({
                "path": request.url.path,
                "headers": {k: v for k, v in request.headers.items()},
            })
            return httpx.Response(200, json={
                "code": 0,
                "data": {"active": True, "today_checked_in": True, "today_credit": 100},
            })

        transport = httpx.MockTransport(handler)
        checkin = CodeBuddyCheckinManager(
            token_manager=manager,
            state_file=os.path.join(temporary.name, "checkin.json"),
            client_factory=lambda: httpx.AsyncClient(
                base_url="https://copilot.tencent.com",
                transport=transport,
            ),
        )
        status = await checkin.run_all(force=True)
        self.assertTrue(status["authorized"])
        self.assertEqual(status["accounts"][0]["status"], "already_checked_in")
        self.assertEqual(calls[0]["headers"].get("x-product"), "SaaS")
        self.assertEqual(calls[0]["path"], "/billing/meter/checkin-status")

    async def test_unauthorized_message_without_workbuddy_creds(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manager = WorkBuddyTokenManager(creds_dir=os.path.join(temporary.name, "creds"))
        checkin = CodeBuddyCheckinManager(
            token_manager=manager,
            state_file=os.path.join(temporary.name, "checkin.json"),
        )
        status = checkin.get_status()
        self.assertFalse(status["authorized"])
        self.assertEqual(status["message"], "请先授权 WorkBuddy")
        self.assertEqual(status["accounts"], [])


class SaveWorkbuddyTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_workbuddy_token_writes_product(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manager = WorkBuddyTokenManager(creds_dir=temporary.name)
        with patch("src.workbuddy_auth_router.workbuddy_token_manager", manager):
            ok = await save_workbuddy_token({
                "access_token": "abc.def.ghi",
                "refresh_token": "refresh",
                "expires_in": 7200,
                "domain": "copilot.tencent.com",
                "account_uid": "12345",
            })
        self.assertTrue(ok)
        records = manager.get_credential_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["data"]["product"], "WorkBuddy")
        self.assertEqual(records[0]["data"]["user_id"], "12345")
        self.assertTrue(records[0]["filename"].startswith("workbuddy_"))


if __name__ == "__main__":
    unittest.main()
