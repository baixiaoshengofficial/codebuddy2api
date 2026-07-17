import math
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from src.codebuddy_router import LOCAL_EMBEDDING_MODEL_ID, router


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

    async def test_models_advertise_local_embedding_model_once(self):
        upstream = ["L1", LOCAL_EMBEDDING_MODEL_ID]
        with patch(
            "src.codebuddy_router.codebuddy_model_manager.get_models",
            new=AsyncMock(return_value=upstream),
        ):
            response = await self.request("GET", "/codebuddy/v1/models")

        self.assertEqual(response.status_code, 200)
        models = response.json()["data"]
        matching = [item for item in models if item["id"] == LOCAL_EMBEDDING_MODEL_ID]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["owned_by"], "codebuddy2api")

    async def test_embeddings_return_openai_compatible_batch(self):
        payload = {
            "model": LOCAL_EMBEDDING_MODEL_ID,
            "input": ["相同文本", "相同文本"],
            "dimensions": 8,
        }
        with (
            patch("src.auth.get_server_password", return_value="test-password"),
            patch("src.codebuddy_router.start_request_audit", return_value={}),
            patch("src.codebuddy_router.finish_request_audit"),
            patch("src.codebuddy_router.usage_stats_manager.record_model_usage"),
        ):
            response = await self.request(
                "POST",
                "/codebuddy/v1/embeddings",
                headers={"Authorization": "Bearer test-password"},
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "list")
        self.assertEqual(body["model"], LOCAL_EMBEDDING_MODEL_ID)
        self.assertEqual(len(body["data"]), 2)
        self.assertEqual(body["data"][0]["embedding"], body["data"][1]["embedding"])
        self.assertEqual(len(body["data"][0]["embedding"]), 8)
        norm = math.sqrt(sum(value * value for value in body["data"][0]["embedding"]))
        self.assertAlmostEqual(norm, 1.0, places=5)
        self.assertGreater(body["usage"]["prompt_tokens"], 0)

    async def test_embeddings_require_authentication(self):
        response = await self.request(
            "POST",
            "/codebuddy/v1/embeddings",
            json={"input": "test"},
        )

        self.assertEqual(response.status_code, 403)

    async def test_embeddings_reject_invalid_dimensions(self):
        with patch("src.auth.get_server_password", return_value="test-password"):
            response = await self.request(
                "POST",
                "/codebuddy/v1/embeddings",
                headers={"Authorization": "Bearer test-password"},
                json={"input": "test", "dimensions": 0},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("dimensions", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
