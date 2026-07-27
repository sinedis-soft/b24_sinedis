"""Tests for liveness and database readiness endpoints."""

from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.database import get_db_session
from app.main import app


class SuccessfulSession:
    """Session double whose lightweight query succeeds."""

    async def execute(self, statement: Any) -> None:
        """Accept a SQL statement without contacting a database."""


class FailingSession:
    """Session double whose lightweight query fails safely."""

    async def execute(self, statement: Any) -> None:
        """Simulate a database driver failure."""
        raise RuntimeError("private database failure detail")


async def request_with_session(path: str, session: object):
    """Issue a request with an isolated database dependency override."""

    async def override_session() -> AsyncIterator[object]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path)
    finally:
        app.dependency_overrides.clear()


async def test_health() -> None:
    """The liveness endpoint exposes configured service metadata."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    payload = response.json()
    settings = get_settings()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["service"] == settings.service_name
    assert payload["version"] == settings.app_version


async def test_ready_when_database_is_available() -> None:
    """A successful SELECT 1 produces a positive readiness result."""
    response = await request_with_session("/ready", SuccessfulSession())

    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": "available"}


async def test_ready_when_database_is_unavailable() -> None:
    """A driver failure produces a sanitized negative readiness result."""
    response = await request_with_session("/ready", FailingSession())

    assert response.status_code == 503
    assert response.json() == {"ok": False, "database": "unavailable"}
    assert "private database failure detail" not in response.text
