#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR=${APP_DIR:-/opt/bitrix24-automation}
[[ -f "$APP_DIR/.env" ]] || { echo "Missing $APP_DIR/.env" >&2; exit 1; }
cd "$APP_DIR"
exec .venv/bin/alembic upgrade head
