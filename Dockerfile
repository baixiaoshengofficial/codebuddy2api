FROM hub.baixiaosheng.de/node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM hub.baixiaosheng.de/golang:1.27.3-bookworm AS backend-builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/ ./cmd/
COPY internal/ ./internal/
ARG CODEBUDDY_REVISION=""
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w -X main.revision=${CODEBUDDY_REVISION}" -o /out/codebuddy2api ./cmd/codebuddy2api

FROM hub.baixiaosheng.de/debian:bookworm-slim
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
