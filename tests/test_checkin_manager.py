import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx

from src.checkin_manager import CHINA_TIMEZONE, CodeBuddyCheckinManager


class FakeTokenManager:
    def __init__(self, credentials):
        self.credentials = credentials

    def get_credential_records(self):
        return self.credentials

    def is_token_expired(self, _credential):
        return False


class CheckinManagerTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, responses):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        calls = []

        def handler(request):
            calls.append({
                "path": request.url.path,
                "headers": {k: v for k, v in request.headers.items()},
            })
            body = responses.pop(0)
            if isinstance(body, tuple):
                status_code, payload = body
            else:
                status_code, payload = 200, body
            return httpx.Response(status_code, json=payload)

        transport = httpx.MockTransport(handler)
        records = [{
            "filename": "account.json",
            "data": {
                "bearer_token": "secret",
                "user_id": "user-1",
                "domain": "copilot.tencent.com",
            },
        }]
        manager = CodeBuddyCheckinManager(
            token_manager=FakeTokenManager(records),
            state_file=os.path.join(temporary.name, "checkin.json"),
            client_factory=lambda: httpx.AsyncClient(
                base_url="https://copilot.tencent.com",
                transport=transport,
            ),
        )
        return manager, calls

    def _paths(self, calls):
        return [item["path"] for item in calls]

    async def test_claims_active_unchecked_account(self):
        manager, calls = self.make_manager([
            {"code": 0, "data": {"active": True, "today_checked_in": False}},
            {"code": 0, "data": {"credit": 100, "streak_days": 2}},
            {"code": 0, "data": {
                "active": True,
                "today_checked_in": True,
                "today_credit": 100,
                "streak_days": 2,
            }},
        ])

        status = await manager.run_all(force=True)

        account = status["accounts"][0]
        self.assertEqual(account["status"], "claimed")
        self.assertTrue(account["today_checked_in"])
        self.assertEqual(account["today_credit"], 100)
        self.assertEqual(account["streak_days"], 2)
        self.assertEqual(self._paths(calls), [
            "/v2/billing/meter/checkin-activity-status",
            "/v2/billing/meter/daily-checkin",
            "/v2/billing/meter/checkin-activity-status",
        ])
        headers = calls[0]["headers"]
        self.assertIsNone(headers.get("x-product"))
        self.assertEqual(headers.get("x-user-id"), "user-1")
        self.assertEqual(headers.get("authorization"), "Bearer secret")
        self.assertEqual(headers.get("x-domain"), "copilot.tencent.com")
        self.assertFalse(status["running"])

    async def test_does_not_claim_when_already_checked_in(self):
        manager, calls = self.make_manager([
            {"code": 0, "data": {
                "active": True,
                "today_checked_in": True,
                "today_credit": 100,
            }},
        ])

        status = await manager.run_all(force=True)

        self.assertEqual(status["accounts"][0]["status"], "already_checked_in")
        self.assertEqual(self._paths(calls), ["/v2/billing/meter/checkin-activity-status"])

    async def test_inactive_campaign_is_recorded_without_claim(self):
        manager, calls = self.make_manager([
            {"code": 0, "data": {"active": False, "today_checked_in": False}},
        ])

        status = await manager.run_all(force=True)

        self.assertEqual(status["accounts"][0]["status"], "inactive")
        self.assertEqual(status["accounts"][0]["message"], "当前账号未开放签到活动")
        self.assertEqual(
            self._paths(calls),
            ["/v2/billing/meter/checkin-activity-status"],
        )

    async def test_claim_without_reward_data_is_not_reported_as_success(self):
        manager, calls = self.make_manager([
            {"code": 0, "data": {"active": True, "today_checked_in": False}},
            {"code": 0, "msg": "OK", "data": None},
        ])

        status = await manager.run_all(force=True)

        account = status["accounts"][0]
        self.assertEqual(account["status"], "error")
        self.assertFalse(account["today_checked_in"])
        self.assertEqual(account["message"], "签到接口未返回奖励数据")
        self.assertEqual(self._paths(calls), [
            "/v2/billing/meter/checkin-activity-status",
            "/v2/billing/meter/daily-checkin",
        ])

    async def test_successful_result_is_not_repeated_same_day(self):
        manager, calls = self.make_manager([
            {"code": 0, "data": {"active": True, "today_checked_in": True}},
        ])

        await manager.run_all(force=True)
        await manager.run_all(force=False)

        self.assertEqual(self._paths(calls), ["/v2/billing/meter/checkin-activity-status"])
        with open(manager.state_file, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["accounts"]["account.json"]["status"], "already_checked_in")

    def test_next_run_waits_until_scheduled_time(self):
        manager, _ = self.make_manager([])
        now = datetime(2026, 7, 14, 9, 30, tzinfo=CHINA_TIMEZONE)
        with patch("src.checkin_manager.get_checkin_hour_minute", return_value=(11, 0)):
            next_run = manager._next_run_datetime(now)
        self.assertEqual(next_run, datetime(2026, 7, 14, 11, 0, tzinfo=CHINA_TIMEZONE))

    def test_next_run_is_tomorrow_after_scheduled_completion(self):
        manager, _ = self.make_manager([])
        manager.state["last_scheduled_date"] = "2026-07-14"
        now = datetime(2026, 7, 14, 11, 5, tzinfo=CHINA_TIMEZONE)
        with patch("src.checkin_manager.get_checkin_hour_minute", return_value=(11, 0)):
            next_run = manager._next_run_datetime(now)
        self.assertEqual(next_run, datetime(2026, 7, 15, 11, 0, tzinfo=CHINA_TIMEZONE))

    def test_next_run_is_immediate_when_past_schedule_and_not_done(self):
        manager, _ = self.make_manager([])
        now = datetime(2026, 7, 14, 12, 0, tzinfo=CHINA_TIMEZONE)
        with patch("src.checkin_manager.get_checkin_hour_minute", return_value=(11, 0)):
            next_run = manager._next_run_datetime(now)
        self.assertEqual(next_run, now)

    def test_format_bark_body_summarizes_accounts(self):
        body = CodeBuddyCheckinManager._format_bark_body({
            "accounts": [
                {
                    "user_id": "alice",
                    "status": "claimed",
                    "message": "签到成功，奖励已领取",
                    "today_credit": 100,
                },
                {
                    "user_id": "bob",
                    "status": "already_checked_in",
                    "message": "今日奖励已领取",
                    "today_credit": 100,
                },
            ],
        })
        self.assertIn("完成 2/2，新领取 1", body)
        self.assertIn("alice:", body)
        self.assertIn("bob:", body)

    async def test_scheduled_run_marks_date_and_notifies(self):
        manager, _ = self.make_manager([
            {"code": 0, "data": {
                "active": True,
                "today_checked_in": True,
                "today_credit": 100,
                "user_id": "user-1",
            }},
        ])
        with patch.object(manager, "_push_bark", new_callable=AsyncMock) as push:
            await manager.run_all(force=True, scheduled=True)
        push.assert_awaited_once()
        self.assertEqual(manager.state["last_scheduled_date"], manager._today())


if __name__ == "__main__":
    unittest.main()
