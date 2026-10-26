# Repository Guidelines

## Structure

The backend is Go. `cmd/codebuddy2api` is the executable entry point and `internal/app` is the composition root. Domain packages own chat, credentials, OAuth, check-in, configuration, model discovery and the SQLite ledger. `internal/httpapi` owns transport validation and presentation. The Vue admin console lives in `frontend/`.

Keep packages focused. Define small interfaces where they are consumed and inject dependencies through constructors. Domain code must not import HTTP handlers or the composition root. Do not add global mutable service singletons or provider-specific branches to unrelated domains.

## Development

Use Go 1.23+ with CGO and a C compiler for SQLite. The Docker build pins its toolchain. Frontend builds need Node.js 22.8+.

- `go run ./cmd/codebuddy2api`: run from the project root after configuring `.env` and building the frontend.
- `make build`: compile `bin/codebuddy2api`.
- `go test -race -timeout 120s ./...`: run isolated backend tests.
- `go vet ./...`: static checks.
- `cd frontend && npm test && npm run build`: frontend verification.
- `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait`: source deployment.

Run `gofmt` on Go changes. Use context cancellation for network operations and background jobs. Keep account JSON, routing state, configuration and SQLite schema compatible unless a documented migration is included. Model POSTs must never be retried after uncertain transport failures or partial output. Failover is limited to explicit credit exhaustion before output.

## Tests

Place Go tests beside their packages. Use temporary directories, injected clocks and mocked HTTP transports. Cover public behavior, concurrency, streaming, renewal, failover and persistence failures. Do not use real credentials or model calls in automated tests.

## Commits and Security

Use short Chinese commit summaries. Maintain a single `main` branch and a single published Docker tag, `latest`. Never commit `.env`, runtime configuration, account credentials or databases. Credential and authorization endpoints must not return tokens; previews and errors must redact secrets. Document deployment changes in `docs/architecture.md` and keep the README concise.
