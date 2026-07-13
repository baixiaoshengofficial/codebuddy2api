"""
Runtime model discovery for CodeBuddy sites.
"""
import io
import json
import logging
import tarfile
import time
import asyncio
from typing import Any, Dict, List, Optional

import httpx

from config import get_codebuddy_site

logger = logging.getLogger(__name__)

NPM_PACKAGE_METADATA_URL = "https://registry.npmjs.org/@tencent-ai%2Fcodebuddy-code/latest"
MODEL_CACHE_TTL_SECONDS = 1800
FALLBACK_MODELS = {
    "international": [
        "default-model",
        "gemini-3.1-pro",
        "gemini-3.0-flash",
        "gemini-3.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.3-codex",
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
        "deepseek-v3-2-volc",
        "glm-5.0",
        "kimi-k2.5",
    ],
    "china": [
        "glm-5.2",
        "glm-5.1",
        "glm-5v-turbo",
        "minimax-m3",
        "minimax-m2.7",
        "kimi-k2.7",
        "kimi-k2.6",
        "hy3-preview",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-v3-2-volc",
    ],
}


class CodeBuddyModelManager:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_models(self, force_refresh: bool = False) -> List[str]:
        site = get_codebuddy_site()
        cached = self._cache.get(site)
        if (
            cached
            and not force_refresh
            and time.time() - cached["fetched_at"] < MODEL_CACHE_TTL_SECONDS
        ):
            return cached["models"]

        async with self._lock:
            cached = self._cache.get(site)
            if (
                cached
                and not force_refresh
                and time.time() - cached["fetched_at"] < MODEL_CACHE_TTL_SECONDS
            ):
                return cached["models"]

            try:
                models = await self._fetch_models_for_site(site)
            except Exception:
                if cached and cached.get("models"):
                    logger.exception("Failed to refresh CodeBuddy models, using stale cache")
                    return cached["models"]
                logger.exception("Failed to refresh CodeBuddy models, using fallback models")
                models = FALLBACK_MODELS.get(site) or FALLBACK_MODELS["international"]

            self._cache[site] = {
                "models": models,
                "fetched_at": time.time(),
            }
            return models

    async def _fetch_models_for_site(self, site: str) -> List[str]:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            metadata_response = await client.get(NPM_PACKAGE_METADATA_URL)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()

            tarball_url = metadata.get("dist", {}).get("tarball")
            if not tarball_url:
                raise RuntimeError("CodeBuddy package metadata does not include a tarball URL")

            package_response = await client.get(tarball_url)
            package_response.raise_for_status()

        product_file = "package/product.internal.json" if site == "china" else "package/product.json"
        product_config = self._read_product_config(package_response.content, product_file)
        models = self._extract_models(product_config)

        if not models:
            raise RuntimeError(f"No models found in {product_file}")

        logger.info("Loaded %s CodeBuddy models from %s", len(models), product_file)
        return models

    @staticmethod
    def _read_product_config(tarball_content: bytes, product_file: str) -> Dict[str, Any]:
        with tarfile.open(fileobj=io.BytesIO(tarball_content), mode="r:gz") as archive:
            member = archive.getmember(product_file)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"{product_file} not found in CodeBuddy package")
            return json.loads(extracted.read().decode("utf-8"))

    @staticmethod
    def _extract_models(product_config: Dict[str, Any]) -> List[str]:
        agents = product_config.get("agents") or []
        primary_agent: Optional[Dict[str, Any]] = None

        for agent in agents:
            tags = set(agent.get("tags") or [])
            if agent.get("name") == "cli" or {"default", "model:craft"}.issubset(tags):
                primary_agent = agent
                break

        if primary_agent is None and agents:
            primary_agent = agents[0]

        seen = set()
        models = []
        for model in (primary_agent or {}).get("models") or []:
            if isinstance(model, str) and model and model not in seen:
                seen.add(model)
                models.append(model)
        return models


codebuddy_model_manager = CodeBuddyModelManager()
