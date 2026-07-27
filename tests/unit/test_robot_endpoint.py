"""HTTP contract tests for short-pause job intake."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.api.robots import get_short_pause_service
from app.config import Settings, get_settings
from app.jobs.service import RobotCredentialError
from app.main import app


class FakeService:
    def __init__(self):
        job = SimpleNamespace(id=uuid4(), status="pending", run_at=datetime.now(UTC))
        self.create = AsyncMock(return_value=SimpleNamespace(job=job, existing=False))


def override(value):
    return lambda: value


def callback():
    return {
        "auth": {"member_id": "member", "application_token": "test-application-token"},
        "EVENT_TOKEN": "test-event-token",
        "properties": {"delay_seconds": 10},
    }


async def test_callback_returns_safe_job_response() -> None:
    service = FakeService()
    app.dependency_overrides[get_short_pause_service] = override(service)
    app.dependency_overrides[get_settings] = override(Settings())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/bitrix/robots/short-pause", json=callback())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200 and response.json()["status"] == "pending"
    assert "test-event-token" not in response.text and "test-application-token" not in response.text


async def test_callback_uses_same_forbidden_response_for_credential_failure() -> None:
    service = FakeService()
    service.create.side_effect = RobotCredentialError()
    app.dependency_overrides[get_short_pause_service] = override(service)
    app.dependency_overrides[get_settings] = override(Settings())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/bitrix/robots/short-pause", json=callback())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
