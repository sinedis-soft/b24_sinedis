#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR=${APP_DIR:-/opt/bitrix24-automation}
[[ -f "$APP_DIR/.env" ]] || { echo "Missing $APP_DIR/.env" >&2; exit 1; }
cd "$APP_DIR"
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' || { echo "Python 3.12+ required" >&2; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install .
APP_DIR="$APP_DIR" deployment/scripts/migrate.sh
sudo install -m 0644 deployment/systemd/sinedis-bitrix-api.service /etc/systemd/system/
sudo install -m 0644 deployment/systemd/sinedis-bitrix-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sinedis-bitrix-api sinedis-bitrix-worker
sudo systemctl restart sinedis-bitrix-api sinedis-bitrix-worker
sudo systemctl --no-pager --full status sinedis-bitrix-api sinedis-bitrix-worker
