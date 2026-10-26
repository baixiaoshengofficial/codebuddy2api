import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from src.checkin_manager import CodeBuddyCheckinManager
from src.workbuddy_auth_router import save_workbuddy_token
from src.oauth.client import WORKBUDDY, OAuthLoginClient
from src.workbuddy_token_manager import WorkBuddyTokenManager


class WorkBuddyAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_uses_workbuddy_platform(self):
        captured = []

        def handler(request):
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "state": "state-1",
                        "authUrl": "https://example.com/login",
                    },
                },
            )

        login = OAuthLoginClient(
            WORKBUDDY,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
        )
        result = await login.start()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["platform"], "workbuddy")
        self.assertEqual(captured[0].url.params["platform"], "workbuddy")
        self.assertEqual(captured[0].headers["X-Product"], "SaaS")
        self.assertEqual(result["auth_state"], "state-1")
        self.assertEqual(
            result["verification_uri_complete"], "https://example.com/login"
        )

    async def test_poll_pending_code(self):
        login = OAuthLoginClient(
            WORKBUDDY,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, json={"code": 11217, "msg": "login ing..."}
                    )
                )
            ),
        )
        result = await login.poll("state-1")
        self.assertEqual(result["status"], "pending")


class WorkBuddyTokenManagerTests(unittest.TestCase):
    def test_add_and_list_credentials(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manager = WorkBuddyTokenManager(creds_dir=temporary.name)
        ok = manager.add_credential_with_data(
            {
                "bearer_token": "token-1",
                "user_id": "uid-1",
                "domain": "copilot.tencent.com",
                "expires_in": 3600,
            }
        )
        self.assertTrue(ok)
        info = manager.get_credentials_info()
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["user_id"], "uid-1")
        self.assertEqual(info[0]["product"], "WorkBuddy")
        self.assertTrue(manager.delete_credential_by_index(0))
        self.assertEqual(manager.get_credentials_info(), [])

    def test_reauthorization_replaces_existing_account(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manager = WorkBuddyTokenManager(creds_dir=temporary.name)
        self.assertTrue(
            manager.add_credential_with_data(
                {
                    "bearer_token": "old-token",
                    "refresh_token": "old-refresh",
                    "user_id": "uid-1",
                    "domain": "www.codebuddy.cn",
                },
                filename="old-account.json",
            )
        )

        self.assertTrue(
            manager.add_credential_with_data(
                {
                    "bearer_token": "new-token",
                    "refresh_token": "new-refresh",
                    "user_id": "uid-1",
                    "domain": "www.codebuddy.cn",
                },
                filename="new-account.json",
            )
        )

        records = manager.get_credential_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["filename"], "old-account.json")
        self.assertEqual(records[0]["data"]["bearer_token"], "new-token")
        self.assertFalse(
            os.path.exists(os.path.join(temporary.name, "new-account.json"))
        )


class CheckinUsesWorkBuddyStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkin_reads_workbuddy_manager(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        creds_dir = os.path.join(temporary.name, "creds")
        os.makedirs(creds_dir)
        manager = WorkBuddyTokenManager(creds_dir=creds_dir)
        manager.add_credential_with_data(
            {
                "bearer_token": "secret",
                "user_id": "wb-user",
                "domain": "copilot.tencent.com",
            }
        )

        calls = []

        def handler(request: httpx.Request):
            calls.append(
                {
                    "path": request.url.path,
                    "headers": {k: v for k, v in request.headers.items()},
                }
            )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "active": True,
                        "today_checked_in": True,
                        "today_credit": 100,
                    },
                },
            )

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
        self.assertIsNone(calls[0]["headers"].get("x-product"))
        self.assertEqual(
            calls[0]["path"],
            "/v2/billing/meter/checkin-activity-status",
        )

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
            ok = await save_workbuddy_token(
                {
                    "access_token": "abc.def.ghi",
                    "refresh_token": "refresh",
                    "expires_in": 7200,
                    "domain": "copilot.tencent.com",
                    "account_uid": "12345",
                }
            )
        self.assertTrue(ok)
        records = manager.get_credential_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["data"]["product"], "WorkBuddy")
        self.assertEqual(records[0]["data"]["user_id"], "12345")
        self.assertTrue(records[0]["filename"].startswith("workbuddy_"))


if __name__ == "__main__":
    unittest.main()
