"""Tests for validated settings and protected secret representations."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_secret_settings_are_not_exposed_by_repr() -> None:
    """Pydantic representations mask configured secrets and the database URL."""
    settings = Settings(
        database_url="postgresql+asyncpg://user:test-password@db/test",
        bitrix_client_secret="test-client-secret",
        encryption_key="test-key-for-repr-only",
        admin_api_token="test-admin-token",
    )
    rendered = repr(settings)
    assert "test-password" not in rendered
    assert "test-client-secret" not in rendered
    assert "test-key-for-repr-only" not in rendered
    assert "test-admin-token" not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"short_pause_min_seconds": 0},
        {"short_pause_min_seconds": 10, "short_pause_max_seconds": 9},
        {"worker_batch_size": 0},
        {"worker_poll_interval_seconds": 0},
        {"worker_lock_timeout_seconds": 0},
        {"bitrix_http_max_connections": 0},
        {"bitrix_oauth_expiry_skew_seconds": -1},
        {"bitrix_oauth_connect_timeout_seconds": 0},
        {"bitrix_oauth_read_timeout_seconds": 0},
    ],
)
def test_invalid_operational_limits_are_rejected(overrides: dict[str, int]) -> None:
    """Unsafe worker, delay, and HTTP limits fail configuration validation."""
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_worker_retry_range_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(WORKER_RETRY_BASE_SECONDS=10, WORKER_RETRY_MAX_SECONDS=5)
