import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from src.codebuddy_router import router


class EmbeddingsEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router, prefix="/codebuddy")
        self.transport = httpx.ASGITransport(app=app)

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    async def test_models_only_advertise_discovered_codebuddy_models(self):
        with patch(
            "src.codebuddy_router.codebuddy_model_manager.get_models",
            new=AsyncMock(return_value=["glm-5.2", "glm-5.2"]),
        ):
            response = await self.request("GET", "/codebuddy/v1/models")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [{
            "id": "glm-5.2",
            "object": "model",
            "created": response.json()["data"][0]["created"],
            "capabilities": ["chat.completions", "responses"],
            "owned_by": "codebuddy",
        }])

    async def test_embeddings_are_forwarded_to_codebuddy(self):
        payload = {
            "model": "upstream-embedding-model",
            "input": ["第一段", "第二段"],
            "dimensions": 1024,
        }
        upstream_body = {
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, -0.2]}],
            "model": "upstream-embedding-model",
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        }
        upstream = httpx.Response(200, json=upstream_body)
        client = AsyncMock()
        client.post.return_value = upstream
        credential = {
            "bearer_token": "codebuddy-token",
            "user_id": "codebuddy-user",
            "_credential_filename": "credential.json",
        }
        generated_headers = {"Authorization": "Bearer codebuddy-token"}

        with (
            patch("src.auth.get_server_password", return_value="test-password"),
            patch("src.codebuddy_router.CredentialManager.get_valid_credential", return_value=credential),
            patch("src.codebuddy_router.get_http_client", new=AsyncMock(return_value=client)),
            patch("src.codebuddy_router.get_codebuddy_embeddings_url", return_value="https://copilot.tencent.com/v2/embeddings"),
            patch("src.codebuddy_router.codebuddy_api_client.generate_codebuddy_headers", return_value=generated_headers) as generate_headers,
            patch("src.codebuddy_router.start_request_audit", return_value={"request_id": "audit-request"}),
            patch("src.codebuddy_router.finish_request_audit") as finish_audit,
            patch("src.codebuddy_router.usage_stats_manager.record_model_usage") as record_usage,
        ):
            response = await self.request(
                "POST",
                "/codebuddy/v1/embeddings",
                headers={"Authorization": "Bearer test-password"},
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), upstream_body)
        client.post.assert_awaited_once_with(
            "https://copilot.tencent.com/v2/embeddings",
            json=payload,
            headers=generated_headers,
        )
        generate_headers.assert_called_once_with(
            bearer_token="codebuddy-token",
            user_id="codebuddy-user",
            conversation_id=None,
            conversation_request_id=None,
            conversation_message_id=None,
            request_id="audit-request",
        )
        record_usage.assert_called_once_with("upstream-embedding-model")
        self.assertEqual(finish_audit.call_args.kwargs["status_code"], 200)
        self.assertEqual(finish_audit.call_args.kwargs["usage"], upstream_body["usage"])

    async def test_upstream_embedding_error_is_returned_unchanged(self):
        error_body = {
            "code": 11351,
            "msg": "embedding model [missing] not found",
            "requestId": "upstream-request",
        }
        client = AsyncMock()
        client.post.return_value = httpx.Response(500, json=error_body)

        with (
            patch("src.auth.get_server_password", return_value="test-password"),
            patch(
                "src.codebuddy_router.CredentialManager.get_valid_credential",
                return_value={"bearer_token": "token", "user_id": "user"},
            ),
            patch("src.codebuddy_router.get_http_client", new=AsyncMock(return_value=client)),
            patch("src.codebuddy_router.codebuddy_api_client.generate_codebuddy_headers", return_value={}),
            patch("src.codebuddy_router.start_request_audit", return_value={"request_id": "request"}),
            patch("src.codebuddy_router.finish_request_audit") as finish_audit,
            patch("src.codebuddy_router.usage_stats_manager.record_model_usage"),
        ):
            response = await self.request(
                "POST",
                "/codebuddy/v1/embeddings",
                headers={"Authorization": "Bearer test-password"},
                json={"model": "missing", "input": "test"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), error_body)
        self.assertEqual(finish_audit.call_args.kwargs["status_code"], 500)
        self.assertIn("embedding model", finish_audit.call_args.kwargs["error"])

    async def test_embeddings_require_authentication(self):
        response = await self.request(
            "POST",
            "/codebuddy/v1/embeddings",
            json={"model": "test", "input": "test"},
        )

        self.assertEqual(response.status_code, 403)

    async def test_embeddings_require_model(self):
        with patch("src.auth.get_server_password", return_value="test-password"):
            response = await self.request(
                "POST",
                "/codebuddy/v1/embeddings",
                headers={"Authorization": "Bearer test-password"},
                json={"input": "test"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("model", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
