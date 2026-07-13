#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-192.168.100.3}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/codebuddy2api}"
DEPLOY_PORT="${DEPLOY_PORT:-8001}"
CODEBUDDY_SITE="${CODEBUDDY_SITE:-china}"
CODEBUDDY_PASSWORD="${CODEBUDDY_PASSWORD:-}"
INSTALL_DOCKER="${INSTALL_DOCKER:-0}"
OVERWRITE_ENV="${OVERWRITE_ENV:-0}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="$(mktemp -t codebuddy2api.XXXXXX.tar.gz)"
ENV_FILE="$(mktemp -t codebuddy2api.env.XXXXXX)"
REMOTE_ARCHIVE="/tmp/codebuddy2api-deploy.tar.gz"

cleanup() {
  rm -f "$ARCHIVE"
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

if [[ -z "$CODEBUDDY_PASSWORD" && -f "$ROOT_DIR/.env" ]]; then
  CODEBUDDY_PASSWORD="$(
    python3 - "$ROOT_DIR/.env" <<'PY'
import pathlib
import sys

for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "CODEBUDDY_PASSWORD":
        print(value.strip().strip('"').strip("'"))
        break
PY
  )"
fi

if [[ -z "$CODEBUDDY_PASSWORD" ]]; then
  echo "CODEBUDDY_PASSWORD is required. Set it in .env or pass CODEBUDDY_PASSWORD=... ." >&2
  exit 2
fi

run_ssh() {
  local command="$1"
  if [[ -n "${DEPLOY_PASSWORD:-}" ]]; then
    expect <<EOF
set timeout -1
spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$DEPLOY_USER@$DEPLOY_HOST" "$command"
expect {
  -re "(?i)password:" { send "$DEPLOY_PASSWORD\r"; exp_continue }
  eof
}
catch wait result
exit [lindex \$result 3]
EOF
  else
    ssh -o StrictHostKeyChecking=accept-new "$DEPLOY_USER@$DEPLOY_HOST" "$command"
  fi
}

copy_file() {
  local source="$1"
  local target="$2"
  if [[ -n "${DEPLOY_PASSWORD:-}" ]]; then
    expect <<EOF
set timeout -1
spawn scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$source" "$DEPLOY_USER@$DEPLOY_HOST:$target"
expect {
  -re "(?i)password:" { send "$DEPLOY_PASSWORD\r"; exp_continue }
  eof
}
catch wait result
exit [lindex \$result 3]
EOF
  else
    scp -o StrictHostKeyChecking=accept-new "$source" "$DEPLOY_USER@$DEPLOY_HOST:$target"
  fi
}

echo "==> Packaging repository"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$ARCHIVE" -C "$ROOT_DIR" \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='frontend/node_modules' \
  --exclude='frontend/dist' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='.env' \
  --exclude='config/config.json' \
  --exclude='config/*.db' \
  --exclude='config/*.db-*' \
  --exclude='.codebuddy_creds' \
  .

cat > "$ENV_FILE" <<EOF
CODEBUDDY_PASSWORD=$CODEBUDDY_PASSWORD
CODEBUDDY_HOST=0.0.0.0
CODEBUDDY_PORT=8001
CODEBUDDY_SITE=$CODEBUDDY_SITE
CODEBUDDY_CREDS_DIR=.codebuddy_creds
CODEBUDDY_LOG_LEVEL=INFO
CODEBUDDY_ROTATION_COUNT=1
CODEBUDDY_AUTO_CHECKIN=true
CODEBUDDY_CHECKIN_TIME=11:00
CODEBUDDY_BARK_URL=https://bark.chenqinfeng.cn/a4K9KCJ56wmgoxyTjPsh3N/
EOF

echo "==> Preparing remote directory: $DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_DIR"
run_ssh "mkdir -p '$DEPLOY_DIR' '$DEPLOY_DIR/config' '$DEPLOY_DIR/.codebuddy_creds'"

echo "==> Uploading archive"
copy_file "$ARCHIVE" "$REMOTE_ARCHIVE"

if [[ "$OVERWRITE_ENV" == "1" ]]; then
  echo "==> Uploading .env (OVERWRITE_ENV=1)"
  copy_file "$ENV_FILE" "$DEPLOY_DIR/.env"
else
  echo "==> Preserving remote .env if it already exists"
  copy_file "$ENV_FILE" "$DEPLOY_DIR/.env.new"
  run_ssh "if [[ ! -f '$DEPLOY_DIR/.env' ]]; then mv '$DEPLOY_DIR/.env.new' '$DEPLOY_DIR/.env'; else rm -f '$DEPLOY_DIR/.env.new'; fi"
fi

echo "==> Deploying container"
run_ssh "set -Eeuo pipefail
if ! command -v docker >/dev/null 2>&1; then
  if [[ '$INSTALL_DOCKER' == '1' ]]; then
    curl -fsSL https://get.docker.com | sh
  else
    echo 'Docker is not installed. Re-run with INSTALL_DOCKER=1 to install it automatically.' >&2
    exit 2
  fi
fi
if docker compose version >/dev/null 2>&1; then
  COMPOSE='docker compose'
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE='docker-compose'
else
  echo 'Docker Compose is not installed.' >&2
  exit 2
fi
find '$DEPLOY_DIR' -mindepth 1 -maxdepth 1 ! -name config ! -name .codebuddy_creds ! -name .env -exec rm -rf {} +
tar -xzf '$REMOTE_ARCHIVE' -C '$DEPLOY_DIR'
chmod 600 '$DEPLOY_DIR/.env'
cd '$DEPLOY_DIR'
\$COMPOSE up -d --build
\$COMPOSE ps
rm -f '$REMOTE_ARCHIVE'"

echo "==> Done"
echo "URL: http://$DEPLOY_HOST:$DEPLOY_PORT"
echo "CODEBUDDY_SITE: $CODEBUDDY_SITE"
