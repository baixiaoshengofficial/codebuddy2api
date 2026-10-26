"""Application composition does not duplicate routes or initialize runtime data."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.workbuddy_token_manager import WorkBuddyTokenManager


@pytest.mark.parametrize("manager_type", [CodeBuddyTokenManager, WorkBuddyTokenManager])
def test_empty_manager_reads_without_creating_a_credential_directory(
    tmp_path, manager_type
):
    directory = tmp_path / "credentials"
    manager = manager_type(str(directory))
    assert manager.get_credential_records() == []
    assert not directory.exists()
    assert manager.add_credential_with_data(
        {"bearer_token": "fake", "user_id": "a"}, filename="a.json"
    )
    assert (directory / "a.json").is_file()


def test_importing_the_app_does_not_create_runtime_directories(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = dict(
        os.environ,
        PYTHONPATH=str(repo),
        PYTHONDONTWRITEBYTECODE="1",
        CODEBUDDY_CREDS_DIR=str(tmp_path / "codebuddy"),
        WORKBUDDY_CREDS_DIR=str(tmp_path / "workbuddy"),
    )
    result = subprocess.run(
        [sys.executable, "-c", "from web import app; print(app.title)"],
        cwd=str(tmp_path),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "CodeBuddy2API" in result.stdout
    assert not list(tmp_path.iterdir())


def test_root_and_admin_have_one_frontend_route_and_health_stays_public(
    tmp_path, monkeypatch
):
    from web import app
    from src import frontend_router

    html = tmp_path / "index.html"
    html.write_text("<html>isolated frontend</html>")
    monkeypatch.setattr(frontend_router, "HTML_FILE_PATH", str(html))
    roots = [
        route
        for route in app.routes
        if route.path == "/" and "GET" in getattr(route, "methods", [])
    ]
    assert len(roots) == 1

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ["/", "/admin"]:
                result = await client.get(path)
                assert (
                    result.status_code == 200
                    and result.text == "<html>isolated frontend</html>"
                )
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/codebuddy/auth/start")).status_code in (401, 403)

    asyncio.run(run())
