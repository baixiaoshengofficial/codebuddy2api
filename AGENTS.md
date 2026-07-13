# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python service that wraps CodeBuddy with an OpenAI-compatible API. The main application entrypoint is `web.py`, which creates the FastAPI app and mounts routers. Core modules live in `src/`: API routing in `codebuddy_router.py`, authentication in `auth.py` and `codebuddy_auth_router.py`, credential rotation in `codebuddy_token_manager.py`, settings in `settings_router.py`, and shared schemas in `models.py`. The browser admin UI is a single static file at `frontend/admin.html`. Runtime configuration is loaded from `config.py` and environment variables, with examples in `.env.example`. Docker packaging is defined by `Dockerfile`, `docker-compose.yml`, and `entrypoint.sh`.

## Build, Test, and Development Commands

- `python3 -m venv venv && source venv/bin/activate`: create and activate a local environment.
- `pip install -r requirements.txt`: install FastAPI, Hypercorn, HTTP clients, and configuration dependencies.
- `cp .env.example .env`: create local configuration; set `CODEBUDDY_PASSWORD` before running.
- `python web.py`: run the service locally, defaulting to `http://127.0.0.1:8001`.
- `docker compose up --build`: build and run the containerized service with mounted config and credential directories.

## Coding Style & Naming Conventions

Use Python 3.8+ syntax and keep code compatible with the pinned dependencies in `requirements.txt`. Follow PEP 8 conventions: 4-space indentation, `snake_case` for functions and variables, `PascalCase` for Pydantic models, and descriptive module names. Keep FastAPI routers focused by domain, and place request/response schemas in `src/models.py` when shared. Prefer async I/O for network paths, matching the existing `httpx` and FastAPI style.

## Testing Guidelines

No test suite is currently committed. When adding tests, use `pytest` and place them under `tests/`, mirroring module names such as `tests/test_codebuddy_router.py`. Cover authentication behavior, credential rotation, streaming/non-streaming chat responses, and settings persistence. Until automated tests exist, verify changes by running `python web.py`, checking `GET /health`, and exercising `POST /codebuddy/v1/chat/completions` with a valid bearer token.

## Commit & Pull Request Guidelines

Recent commits use short Chinese summaries focused on the change, such as `优化性能` or `修复凭证轮换功能`. Keep commits concise, present-tense, and scoped to one logical change. Pull requests should include a brief description, configuration changes, manual test steps, and screenshots when `frontend/admin.html` changes. Never include `.env`, credential files, or local runtime data in commits.

## Security & Configuration Tips

Treat `CODEBUDDY_PASSWORD` and files in `.codebuddy_creds/` as secrets. Keep local overrides in `.env`, not source files. When changing defaults in `.env.example` or `config.py`, document operational impact in the PR.
