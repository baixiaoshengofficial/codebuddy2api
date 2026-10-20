import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from src.codebuddy_router import CodeBuddyStreamService, is_insufficient_credit_error
from src.codebuddy_token_manager import CodeBuddyTokenManager


class CreditResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        for index in range(2):
            path = os.path.join(self.directory.name, f"credential_{index}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"bearer_token": f"token-{index}", "user_id": f"user-{index}"}, handle)
        self.manager = CodeBuddyTokenManager(self.directory.name)
        self.manager_patch = patch("src.codebuddy_router.codebuddy_token_manager", self.manager)
        self.refresh_patch = patch(
            "src.codebuddy_router.codebuddy_credential_refresher.ensure_fresh",
            new=AsyncMock(side_effect=lambda credential: credential),
        )
        self.manager_patch.start()
        self.refresh_patch.start()

    def tearDown(self):
        self.refresh_patch.stop()
        self.manager_patch.stop()
        self.directory.cleanup()

    @staticmethod
    def _success():
        event = {"id": "chat-1", "model": "test", "choices": [{
            "delta": {"content": "ok"}, "finish_reason": "stop",
        }]}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    async def _request(self, responses, stream=False, retry=True):
        seen = []

        def handler(request):
            seen.append(request.headers["Authorization"])
            return responses[len(seen) - 1]

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.codebuddy_router.get_http_client", new=AsyncMock(return_value=client)):
                service = CodeBuddyStreamService()
                method = service.handle_stream_response if stream else service.handle_non_stream_response
                result = await method(
                    {"model": "test", "messages": []},
                    {"Authorization": "Bearer token-0", "X-User-Id": "user-0"},
                    0, retry_on_exhaustion=retry,
                )
                if stream:
                    result = "".join([chunk async for chunk in result.body_iterator])
        return result, seen

    async def test_non_stream_retries_credit_error_on_next_credential(self):
        result, seen = await self._request([
            httpx.Response(402, json={"error": {"message": "积分不足"}}), self._success(),
        ])
        self.assertEqual(seen, ["Bearer token-0", "Bearer token-1"])
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(self.manager.current_index, 1)

    async def test_non_stream_retries_codebuddy_nested_quota_error(self):
        result, seen = await self._request([
            httpx.Response(429, json={"error": {"data": {
                "code": 14018, "msg": "额度已用尽，请购买加量包",
            }}}),
            self._success(),
        ])
        self.assertEqual(seen, ["Bearer token-0", "Bearer token-1"])
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(self.manager.current_index, 1)

    async def test_stream_retries_sse_credit_error_before_output(self):
        result, seen = await self._request([
            httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"error":{"message":"积分不足"}}\n\n'
            )),
            self._success(),
        ], stream=True)
        self.assertEqual(seen, ["Bearer token-0", "Bearer token-1"])
        self.assertIn('"content": "ok"', result)
        self.assertNotIn('"error"', result)

    async def test_stream_retries_codebuddy_nested_quota_error(self):
        result, seen = await self._request([
            httpx.Response(429, json={"error": {"data": {
                "code": 14018, "msg": "额度已用尽，请购买加量包",
            }}}),
            self._success(),
        ], stream=True)
        self.assertEqual(seen, ["Bearer token-0", "Bearer token-1"])
        self.assertIn('"content": "ok"', result)
        self.assertNotIn('"error"', result)

    async def test_manual_request_does_not_switch(self):
        with self.assertRaises(HTTPException) as caught:
            await self._request([
                httpx.Response(402, json={"error": {"message": "积分不足"}}),
            ], retry=False)
        self.assertEqual(caught.exception.status_code, 402)
        self.assertEqual(self.manager.current_index, -1)

    async def test_rate_limit_does_not_switch(self):
        with self.assertRaises(HTTPException) as caught:
            await self._request([httpx.Response(429, text="rate limit exceeded")])
        self.assertEqual(caught.exception.status_code, 429)
        self.assertFalse(self.manager.cooldowns)

    async def test_all_credentials_exhausted_returns_error(self):
        with self.assertRaises(HTTPException) as caught:
            await self._request([
                httpx.Response(402, json={"error": {"message": "积分不足"}}),
                httpx.Response(402, json={"error": {"message": "积分不足"}}),
            ])
        self.assertEqual(caught.exception.status_code, 402)
        self.assertEqual(len(self.manager.cooldowns), 2)

    async def test_stream_disconnect_does_not_repeat_billable_request(self):
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                raise httpx.ReadError("connection lost")

            async def aclose(self):
                pass

        result, seen = await self._request([
            httpx.Response(200, stream=BrokenStream()),
        ], stream=True)
        self.assertEqual(seen, ["Bearer token-0"])
        self.assertIn("partial", result)
        self.assertIn("connection_failed", result)

    async def test_connect_error_does_not_repeat_billable_request(self):
        seen = []

        def handler(request):
            seen.append(request)
            raise httpx.ConnectError("connection failed", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.codebuddy_router.get_http_client", new=AsyncMock(return_value=client)):
                service = CodeBuddyStreamService()
                with self.assertRaises(HTTPException) as caught:
                    await service.handle_non_stream_response(
                        {"model": "test", "messages": []},
                        {"Authorization": "Bearer token-0"}, 0,
                    )
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(len(seen), 1)
        self.assertEqual(service.attempt_count, 1)

    async def test_non_stream_credit_error_after_output_does_not_repeat_request(self):
        with self.assertRaises(HTTPException) as caught:
            await self._request([
                httpx.Response(200, text=(
                    'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                    'data: {"error":{"message":"积分不足"}}\n\n'
                )),
            ])
        self.assertEqual(caught.exception.status_code, 402)
        self.assertIn(0, self.manager.cooldowns)
        self.assertNotIn(1, self.manager.cooldowns)

    async def test_stream_credit_error_after_output_does_not_repeat_request(self):
        result, seen = await self._request([
            httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                'data: {"error":{"message":"积分不足"}}\n\n'
            )),
        ], stream=True)
        self.assertEqual(seen, ["Bearer token-0"])
        self.assertIn("partial", result)
        self.assertIn("insufficient_credit", result)

    def test_credit_detection_is_specific(self):
        self.assertTrue(is_insufficient_credit_error({"error": {"code": "insufficient_quota"}}))
        self.assertTrue(is_insufficient_credit_error(json.dumps({"error": {"message": "积分不足"}})))
        self.assertTrue(is_insufficient_credit_error({"error": {"data": {
            "code": 14018, "msg": "额度已用尽",
        }}}))
        self.assertFalse(is_insufficient_credit_error({"error": {"message": "rate limit exceeded"}}))
        self.assertFalse(is_insufficient_credit_error({"choices": [{"delta": {"content": "积分不足"}}]}))


if __name__ == "__main__":
    unittest.main()
