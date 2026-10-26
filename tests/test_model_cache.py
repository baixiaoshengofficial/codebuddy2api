"""Model discovery returns cached data while an isolated refresh is running."""

import asyncio

from src.codebuddy_model_manager import CodeBuddyModelManager, FALLBACK_MODELS


def test_initial_discovery_returns_fallback_then_uses_refreshed_cache(monkeypatch):
    monkeypatch.setattr(
        "src.codebuddy_model_manager.get_codebuddy_site", lambda: "china"
    )

    async def run():
        manager = CodeBuddyModelManager()
        calls = []

        async def fetch(site):
            calls.append(site)
            await asyncio.sleep(0.01)
            return ["discovered-model"]

        manager._fetch_models_for_site = fetch
        assert await manager.get_models() == FALLBACK_MODELS["china"]
        assert await manager.get_models() == FALLBACK_MODELS["china"]
        await manager._refresh_tasks["china"]
        assert await manager.get_models() == ["discovered-model"]
        assert calls == ["china"]

    asyncio.run(run())


def test_refresh_failure_preserves_previously_discovered_models(monkeypatch):
    monkeypatch.setattr(
        "src.codebuddy_model_manager.get_codebuddy_site", lambda: "china"
    )

    async def run():
        manager = CodeBuddyModelManager()
        manager._cache["china"] = {"models": ["known-model"], "fetched_at": 0}

        async def fail(site):
            raise RuntimeError("offline")

        manager._fetch_models_for_site = fail
        assert await manager.get_models(force_refresh=True) == ["known-model"]

    asyncio.run(run())
