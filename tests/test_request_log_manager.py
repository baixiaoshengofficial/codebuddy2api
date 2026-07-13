import os
import tempfile
import unittest

from src.request_log_manager import (
    RequestLogManager,
    build_request_preview,
    build_response_preview,
)


class RequestLogManagerTests(unittest.TestCase):
    def test_request_preview_keeps_only_latest_user_turn(self):
        preview = build_request_preview({
            "messages": [
                {"role": "user", "content": "现在呢"},
                {"role": "assistant", "content": "还是 GLM-5.2"},
                {"role": "user", "content": "123"},
            ]
        })

        self.assertEqual(preview, "User: 123")

    def test_completed_request_stores_response_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = RequestLogManager(os.path.join(temporary, "requests.db"))
            record_id = manager.start_request(
                request_id="request-1",
                conversation_id=None,
                endpoint="/codebuddy/v1/chat/completions",
                model="glm-5.2",
                client="Hermes",
                client_detail="OpenAI/Python",
                client_host="127.0.0.1",
                credential="account.json",
                credential_user_id="user-1",
                request_preview="User: 123",
                request_hash="hash",
                is_streaming=True,
            )

            manager.finish_request(
                record_id=record_id,
                status_code=200,
                latency_ms=120,
                estimated_input_tokens=1,
                estimated_output_tokens=4,
                response_preview=build_response_preview("在的，你说。"),
            )

            item = manager.list_requests()["items"][0]
            self.assertEqual(item["request_preview"], "User: 123")
            self.assertEqual(item["response_preview"], "在的，你说。")
            self.assertEqual(item["status"], "success")


if __name__ == "__main__":
    unittest.main()
