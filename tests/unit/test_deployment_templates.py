"""Static safety checks for production deployment templates."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_systemd_templates_use_dedicated_user_and_commands():
    api = (ROOT / "deployment/systemd/sinedis-bitrix-api.service").read_text()
    worker = (ROOT / "deployment/systemd/sinedis-bitrix-worker.service").read_text()
    assert "User=sinedis-bitrix" in api and "User=sinedis-bitrix" in worker
    assert ".venv/bin/uvicorn app.main:app" in api
    assert ".venv/bin/python -m app.jobs.worker" in worker


def test_nginx_template_has_public_name_and_loopback_proxy():
    value = (ROOT / "deployment/nginx/bitrix24.sinedis.pl.conf").read_text()
    assert "server_name bitrix24.sinedis.pl" in value
    assert "proxy_pass http://127.0.0.1:8010" in value


def test_scripts_are_strict_and_dockerfile_does_not_copy_env():
    for path in (ROOT / "deployment/scripts").glob("*.sh"):
        assert "set -Eeuo pipefail" in path.read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY .env" not in dockerfile
