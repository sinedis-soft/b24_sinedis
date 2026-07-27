#!/usr/bin/env bash
set -Eeuo pipefail
BASE_URL=${BASE_URL:-http://127.0.0.1:8010}
curl --fail --silent --show-error "$BASE_URL/health" >/dev/null
curl --fail --silent --show-error "$BASE_URL/ready" >/dev/null
systemctl is-active --quiet sinedis-bitrix-api
systemctl is-active --quiet sinedis-bitrix-worker
echo "API, readiness, and services are healthy"
