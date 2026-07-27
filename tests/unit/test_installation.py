"""Unit tests for lifecycle endpoints and persistence orchestration."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from app.api.installation import (
    ApplicationLifecycleService,
    LifecycleCredentialError,
    LifecycleResponse,
    get_lifecycle_service,
)
from app.bitrix.exceptions import BitrixAuthenticationError, BitrixTransportError
from app.bitrix.payload import normalize_event_payload
from app.bitrix.registration import RegistrationResult
from app.main import app
from app.models.portal import PortalStatus


class FakeLifecycleService:
    def __init__(self) -> None:
        self.install = AsyncMock(
            return_value=LifecycleResponse(ok=True, member_id="test-member", status="active")
        )
        self.uninstall = AsyncMock(
            return_value=LifecycleResponse(ok=True, member_id="test-member", status="inactive")
        )


def override_with(value):
    def dependency():
        return value

    return dependency


def lifecycle_auth(*, include_tokens: bool = True) -> dict[str, object]:
    value: dict[str, object] = {
        "member_id": "test-member",
        "domain": "portal.test",
        "client_endpoint": "https://portal.test/rest/",
        "server_endpoint": "https://oauth.test/rest/",
        "application_token": "test-application-token",
    }
    if include_tokens:
        value.update(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
        )
    return value


async def test_install_endpoint_normalizes_json_and_returns_no_secrets() -> None:
    service = FakeLifecycleService()
    app.dependency_overrides[get_lifecycle_service] = override_with(service)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/bitrix/install",
                json={"event": "ONAPPINSTALL", "auth": lifecycle_auth()},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "member_id": "test-member",
        "status": "active",
        "robots": {},
        "events": {},
    }
    assert "test-access-token" not in response.text
    payload = service.install.await_args.args[0]
    assert payload.auth.access_token == "test-access-token"


async def test_uninstall_endpoint_allows_missing_oauth_tokens() -> None:
    service = FakeLifecycleService()
    app.dependency_overrides[get_lifecycle_service] = override_with(service)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/bitrix/uninstall",
                json={
                    "event": "ONAPPUNINSTALL",
                    "auth": lifecycle_auth(include_tokens=False),
                    "data": {"CLEAN": "1"},
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"
    service.uninstall.assert_awaited_once()


async def test_invalid_install_payload_returns_safe_400() -> None:
    service = FakeLifecycleService()
    app.dependency_overrides[get_lifecycle_service] = override_with(service)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/bitrix/install",
                json={"event": "ONAPPINSTALL", "auth": {"access_token": "test-secret"}},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400
    assert "test-secret" not in response.text
    service.install.assert_not_awaited()


async def test_install_credential_and_transport_failures_are_controlled() -> None:
    for error, expected_status in (
        (
            BitrixAuthenticationError(
                code="expired_token",
                http_status=401,
                method="app.info",
                retryable=False,
            ),
            401,
        ),
        (BitrixTransportError(method="app.info"), 503),
    ):
        service = FakeLifecycleService()
        service.install.side_effect = error
        app.dependency_overrides[get_lifecycle_service] = override_with(service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/bitrix/install",
                    json={"event": "ONAPPINSTALL", "auth": lifecycle_auth()},
                )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == expected_status
        assert "test-access-token" not in response.text


class FakeEncryption:
    def encrypt(self, value: str) -> str:
        return f"encrypted:{value}"


class FakeSession:
    def __init__(self, portal=None) -> None:
        self.portal = portal
        self.executed = []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement) -> None:
        self.executed.append(statement)

    async def scalar(self, statement):
        return self.portal


class FakeBitrixClient:
    calls: ClassVar[list[tuple[str, object]]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def call(self, method: str, params=None) -> None:
        self.calls.append((method, params))


class FakeRegistration:
    async def ensure_all(self, client) -> RegistrationResult:
        return RegistrationResult(
            robots={"sinedis.short_pause.v1": "added"},
            events={"ONAPPUNINSTALL": "bound"},
        )


async def test_install_verifies_then_encrypts_and_upserts(monkeypatch) -> None:
    session = FakeSession()
    monkeypatch.setattr("app.api.installation.BitrixClient", FakeBitrixClient)
    monkeypatch.setattr("app.api.installation.hash_application_token", lambda token: "hashed-token")
    service = ApplicationLifecycleService(
        session=session,
        encryption=FakeEncryption(),
        registration=FakeRegistration(),
    )
    auth = lifecycle_auth()
    auth.pop("expires_in")
    auth["expires"] = 2_000_000_000
    payload = normalize_event_payload(
        {"event": "ONAPPINSTALL", "auth": auth},
        expected_event="ONAPPINSTALL",
        require_oauth_tokens=True,
    )

    result = await service.install(payload)

    assert result.status == "active"
    assert FakeBitrixClient.calls[-1] == ("app.info", None)
    params = session.executed[0].compile().params
    assert "encrypted:test-access-token" in params.values()
    assert "encrypted:test-refresh-token" in params.values()
    assert "hashed-token" in params.values()


async def test_uninstall_preserves_tokens_and_jobs(monkeypatch) -> None:
    portal = SimpleNamespace(
        application_token_hash="stored-hash",
        access_token_encrypted="encrypted-access",
        refresh_token_encrypted="encrypted-refresh",
        jobs=["historical-job"],
        status=PortalStatus.ACTIVE.value,
    )
    session = FakeSession(portal)
    monkeypatch.setattr("app.api.installation.verify_application_token", lambda token, digest: True)
    service = ApplicationLifecycleService(session=session, encryption=FakeEncryption())
    payload = normalize_event_payload(
        {
            "event": "ONAPPUNINSTALL",
            "auth": lifecycle_auth(include_tokens=False),
            "data": {"CLEAN": "1"},
        },
        expected_event="ONAPPUNINSTALL",
        require_oauth_tokens=False,
    )
    result = await service.uninstall(payload)
    assert result.status == "inactive"
    assert portal.status == PortalStatus.INACTIVE.value
    assert portal.access_token_encrypted == "encrypted-access"
    assert portal.refresh_token_encrypted == "encrypted-refresh"
    assert portal.jobs == ["historical-job"]


async def test_uninstall_unknown_and_wrong_token_use_same_error(monkeypatch) -> None:
    payload = normalize_event_payload(
        {"event": "ONAPPUNINSTALL", "auth": lifecycle_auth(include_tokens=False)},
        expected_event="ONAPPUNINSTALL",
        require_oauth_tokens=False,
    )
    for portal in (None, SimpleNamespace(application_token_hash="stored")):
        monkeypatch.setattr("app.api.installation.verify_application_token", lambda *args: False)
        service = ApplicationLifecycleService(
            session=FakeSession(portal), encryption=FakeEncryption()
        )
        try:
            await service.uninstall(payload)
        except LifecycleCredentialError as exc:
            assert str(exc) == "Lifecycle credential is invalid"
        else:
            raise AssertionError("Uninstall credential should be rejected")
