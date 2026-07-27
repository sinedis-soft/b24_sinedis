"""Unit tests for portal-scoped OAuth decisions and one authentication retry."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.bitrix.exceptions import (
    BitrixAuthenticationError,
    BitrixOAuthConfigurationError,
    BitrixOAuthRefreshRejectedError,
    BitrixOAuthTemporaryError,
    BitrixPermissionError,
    BitrixRateLimitError,
    BitrixTransportError,
)
from app.bitrix.oauth import OAuthRefreshResult, PortalOAuthService
from app.config import Settings
from app.models.portal import PortalStatus


class Encryption:
    def encrypt(self, value: str) -> str:
        return f"enc:{value}"

    def decrypt(self, value: str) -> str:
        return value.removeprefix("enc:")


class OAuth:
    def __init__(self) -> None:
        self.refresh = AsyncMock(
            return_value=OAuthRefreshResult(
                member_id="member",
                access_token="new-access",
                refresh_token="new-refresh",
                client_endpoint="https://portal.test/rest/",
                server_endpoint="https://oauth.test/rest/",
                expires_in=3600,
                domain="new.portal.test",
            )
        )


class Session:
    def __init__(self, portal) -> None:
        self.portal = portal

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    @asynccontextmanager
    async def begin(self):
        yield

    async def get(self, model, portal_id, **kwargs):
        return self.portal

    async def scalar(self, statement):
        return self.portal


class Factory:
    def __init__(self, portal) -> None:
        self.portal = portal

    def __call__(self):
        return Session(self.portal)


def portal(**overrides):
    values = {
        "id": uuid4(),
        "member_id": "member",
        "status": PortalStatus.ACTIVE.value,
        "access_token_encrypted": "enc:current-access",
        "refresh_token_encrypted": "enc:current-refresh",
        "token_expires_at": datetime.now(UTC) + timedelta(hours=1),
        "client_endpoint": "https://portal.test/rest/",
        "server_endpoint": "https://oauth.test/rest/",
        "domain": "portal.test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def service(portal_value) -> tuple[PortalOAuthService, OAuth]:
    oauth = OAuth()
    result = PortalOAuthService(
        session_factory=Factory(portal_value),
        encryption=Encryption(),
        oauth=oauth,
        settings=Settings(),
    )
    return result, oauth


async def test_fresh_access_token_does_not_refresh() -> None:
    value = portal()
    oauth_service, transport = service(value)
    assert await oauth_service.get_access_token(value.id) == "current-access"
    transport.refresh.assert_not_awaited()


@pytest.mark.parametrize("offset", [30, -30])
async def test_near_expiry_and_expired_tokens_refresh(offset: int) -> None:
    value = portal(token_expires_at=datetime.now(UTC) + timedelta(seconds=offset))
    oauth_service, transport = service(value)
    assert await oauth_service.get_access_token(value.id) == "new-access"
    transport.refresh.assert_awaited_once()
    assert value.access_token_encrypted == "enc:new-access"
    assert value.refresh_token_encrypted == "enc:new-refresh"
    assert value.status == PortalStatus.ACTIVE.value


@pytest.mark.parametrize(
    "portal_status", [PortalStatus.INACTIVE.value, PortalStatus.AUTH_ERROR.value]
)
async def test_non_active_portal_is_rejected(portal_status: str) -> None:
    value = portal(status=portal_status)
    oauth_service, _ = service(value)
    with pytest.raises(BitrixOAuthConfigurationError):
        await oauth_service.get_access_token(value.id)


async def test_missing_refresh_token_is_rejected() -> None:
    value = portal(refresh_token_encrypted=None, token_expires_at=datetime.now(UTC))
    oauth_service, _ = service(value)
    with pytest.raises(BitrixOAuthRefreshRejectedError):
        await oauth_service.get_access_token(value.id)
    assert value.status == PortalStatus.AUTH_ERROR.value


async def test_permanent_refresh_rejection_marks_auth_error() -> None:
    value = portal(token_expires_at=datetime.now(UTC))
    oauth_service, transport = service(value)
    transport.refresh.side_effect = BitrixOAuthRefreshRejectedError("rejected")
    with pytest.raises(BitrixOAuthRefreshRejectedError):
        await oauth_service.refresh_access_token(value.id)
    assert value.status == PortalStatus.AUTH_ERROR.value


async def test_temporary_refresh_failure_keeps_portal_active() -> None:
    value = portal(token_expires_at=datetime.now(UTC))
    oauth_service, transport = service(value)
    transport.refresh.side_effect = BitrixOAuthTemporaryError("temporary")
    with pytest.raises(BitrixOAuthTemporaryError):
        await oauth_service.refresh_access_token(value.id)
    assert value.status == PortalStatus.ACTIVE.value


def api_error(error_type):
    return error_type(
        code="TEST",
        http_status=401,
        method="app.info",
        retryable=False,
    )


async def test_one_authentication_error_forces_one_refresh_and_retry() -> None:
    value = portal()
    oauth_service, _ = service(value)
    oauth_service.get_access_token = AsyncMock(return_value="old")
    oauth_service.refresh_access_token = AsyncMock(return_value="new")
    oauth_service._call_once = AsyncMock(
        side_effect=[api_error(BitrixAuthenticationError), "successful-result"]
    )
    assert await oauth_service.call_portal(value.id, "app.info") == "successful-result"
    oauth_service.refresh_access_token.assert_awaited_once_with(value.id, force=True)
    assert oauth_service._call_once.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        api_error(BitrixPermissionError),
        BitrixTransportError(method="app.info"),
        BitrixRateLimitError(
            code="QUERY_LIMIT_EXCEEDED",
            http_status=429,
            method="app.info",
            retryable=True,
        ),
    ],
)
async def test_non_authentication_errors_are_not_retried(error) -> None:
    value = portal()
    oauth_service, _ = service(value)
    oauth_service.get_access_token = AsyncMock(return_value="old")
    oauth_service.refresh_access_token = AsyncMock()
    oauth_service._call_once = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await oauth_service.call_portal(value.id, "app.info")
    oauth_service.refresh_access_token.assert_not_awaited()
    assert oauth_service._call_once.await_count == 1


async def test_second_authentication_error_marks_auth_error() -> None:
    value = portal()
    oauth_service, _ = service(value)
    oauth_service.get_access_token = AsyncMock(return_value="old")
    oauth_service.refresh_access_token = AsyncMock(return_value="new")
    oauth_service._call_once = AsyncMock(side_effect=api_error(BitrixAuthenticationError))
    with pytest.raises(BitrixAuthenticationError):
        await oauth_service.call_portal(value.id, "app.info")
    assert value.status == PortalStatus.AUTH_ERROR.value
