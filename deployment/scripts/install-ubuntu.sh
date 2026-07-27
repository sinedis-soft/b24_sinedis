#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || { echo "Ubuntu is required" >&2; exit 1; }
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv postgresql-client nginx curl ca-certificates
id sinedis-bitrix >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/bitrix24-automation --shell /usr/sbin/nologin sinedis-bitrix
install -d -o sinedis-bitrix -g sinedis-bitrix /opt/bitrix24-automation
if [[ ! -e /opt/bitrix24-automation/.env ]]; then
  echo "Create /opt/bitrix24-automation/.env manually; no secrets were generated."
fi
