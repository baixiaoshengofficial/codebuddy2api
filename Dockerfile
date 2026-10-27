FROM --platform=$BUILDPLATFORM hub.baixiaosheng.de/node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM --platform=$BUILDPLATFORM hub.baixiaosheng.de/debian:bookworm-slim AS backend-builder
ARG BUILDARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$BUILDARCH" in \
      amd64) go_sha256=65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40 ;; \
      arm64) go_sha256=a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49 ;; \
      *) echo "Unsupported build architecture" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 --connect-timeout 10 --max-time 180 \
      "https://go.dev/dl/go1.27.3.linux-${BUILDARCH}.tar.gz" -o /tmp/go.tar.gz; \
    printf '%s  /tmp/go.tar.gz\n' "$go_sha256" | sha256sum -c -; \
    tar -C /usr/local -xzf /tmp/go.tar.gz; \
    rm /tmp/go.tar.gz
ENV PATH=/usr/local/go/bin:/go/bin:$PATH GOPATH=/go GOTOOLCHAIN=local
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
ARG TARGETOS
ARG TARGETARCH
RUN set -eu; \
    target_cc=gcc; \
    case "${TARGETARCH}/$(dpkg --print-architecture)" in \
      arm64/amd64) target_cc=aarch64-linux-gnu-gcc; set -- gcc-aarch64-linux-gnu libc6-dev-arm64-cross ;; \
      amd64/arm64) target_cc=x86_64-linux-gnu-gcc; set -- gcc-x86-64-linux-gnu libc6-dev-amd64-cross ;; \
      amd64/amd64|arm64/arm64) set -- ;; \
      *) echo "Unsupported build/target architecture" >&2; exit 1 ;; \
    esac; \
    if [ "$#" -gt 0 ]; then \
      apt-get update && apt-get install -y --no-install-recommends "$@"; \
      rm -rf /var/lib/apt/lists/*; \
    fi; \
    printf '#!/bin/sh\nexec %s "$@"\n' "$target_cc" > /usr/local/bin/target-cc; \
    chmod +x /usr/local/bin/target-cc
ENV CGO_ENABLED=1 GOOS=${TARGETOS} GOARCH=${TARGETARCH} CC=/usr/local/bin/target-cc
COPY cmd/ ./cmd/
COPY internal/ ./internal/
RUN --mount=type=cache,target=/root/.cache/go-build \
    go build -trimpath -ldflags="-s -w" -o /out/codebuddy2api ./cmd/codebuddy2api

FROM hub.baixiaosheng.de/debian:bookworm-slim AS runtime
ARG CODEBUDDY_VERSION=1.0.0
ARG CODEBUDDY_REVISION=""
ENV CODEBUDDY_VERSION=${CODEBUDDY_VERSION} \
    CODEBUDDY_REVISION=${CODEBUDDY_REVISION} \
    CODEBUDDY_HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=backend-builder /out/codebuddy2api /usr/local/bin/codebuddy2api
COPY --from=frontend-builder /frontend/dist ./frontend/dist
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && chmod -R a+rX /app/frontend/dist
ENTRYPOINT ["entrypoint.sh"]
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 CMD ["codebuddy2api", "healthcheck"]
CMD ["codebuddy2api"]

FROM backend-builder AS test-builder
RUN --mount=type=cache,target=/root/.cache/go-build \
    mkdir -p /out/tests && go test -c -o /out/tests/ ./internal/...

# Execute target binaries on their own runtime, including the SQLite CGO tests.
FROM runtime AS test
COPY --from=test-builder /out/tests/ /tmp/tests/
RUN --network=none set -eu; for test_binary in /tmp/tests/*.test; do "$test_binary" -test.v -test.timeout=120s; done
RUN --network=none set -eu; \
    export CODEBUDDY_PASSWORD=container-build-test CODEBUDDY_AUTO_CHECKIN=false; \
    /usr/local/bin/entrypoint.sh codebuddy2api > /tmp/startup.log 2>&1 & service_pid=$!; \
    trap 'kill "$service_pid" 2>/dev/null || true; wait "$service_pid" 2>/dev/null || true' EXIT; \
    attempts=0; \
    until codebuddy2api healthcheck; do \
      attempts=$((attempts + 1)); \
      if [ "$attempts" -ge 15 ]; then cat /tmp/startup.log; exit 1; fi; \
      sleep 1; \
    done; \
    kill -0 "$service_pid"

# Keep test binaries, fixture data and test settings out of published images.
FROM runtime AS final
