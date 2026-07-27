"""Mock-only tests for OAuth refresh transport and expiry handling."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.bitrix.exceptions import (
    BitrixOAuthConfigurationError,
    BitrixOAuthRefreshRejectedError,
    BitrixOAuthTemporaryError,
)
from app.bitrix.oauth import BitrixOAuthService, build_oauth_refresh_url, token_expiry
from app.config import Settings

REFRESH_URL = "https://oauth.test/oauth/token/"


def settings() -> Settings:
    return Settings(
        bitrix_client_id="test-client-id", bitrix_client_secret=SecretStr("test-secret")
    )


def success_payload() -> dict[str, object]:
    return {
        "access_token": "test-new-access-token",
        "refresh_token": "test-new-refresh-token",
        "member_id": "test-member",
        "client_endpoint": "https://portal.test/rest/",
        "server_endpoint": "https://oauth.test/rest/",
        "domain": "portal.test",
        "expires_in": 3600,
        "scope": "bizproc",
        "status": "L",
        "user_id": 1,
    }


def test_oauth_url_uses_only_validated_origin() -> None:
    assert build_oauth_refresh_url("https://oauth.test/rest/") == REFRESH_URL
    assert (
        build_oauth_refresh_url("https://oauth.test:8443/custom/path")
        == "https://oauth.test:8443/oauth/token/"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://oauth.test/rest/",
        "https://user:test-password@oauth.test/rest/",
        "https://oauth.test/rest/?secret=value",
        "https://oauth.test/rest/#fragment",
    ],
)
def test_invalid_oauth_endpoint_is_rejected_safely(endpoint: str) -> None:
    with pytest.raises(BitrixOAuthConfigurationError) as captured:
        build_oauth_refresh_url(endpoint)
    assert "test-password" not in str(captured.value)


@respx.mock
async def test_refresh_sends_form_and_returns_new_pair() -> None:
    route = respx.post(REFRESH_URL).mock(return_value=httpx.Response(200, json=success_payload()))
    async with BitrixOAuthService(settings=settings()) as service:
        result = await service.refresh(
            server_endpoint="https://oauth.test/rest/",
            refresh_token="test-old-refresh-token",
            expected_member_id="test-member",
        )
    form = route.calls.last.request.content.decode()
    assert "grant_type=refresh_token" in form
    assert "client_id=test-client-id" in form
    assert "client_secret=test-secret" in form
    assert "refresh_token=test-old-refresh-token" in form
    assert result.access_token == "test-new-access-token"
    assert result.refresh_token == "test-new-refresh-token"
    assert "test-new-access-token" not in repr(result)


@pytest.mark.parametrize("code", ["invalid_client", "invalid_grant", "PAYMENT_REQUIRED"])
@respx.mock
async def test_permanent_refresh_rejection(code: str) -> None:
    respx.post(REFRESH_URL).mock(
        return_value=httpx.Response(400, json={"error": code, "error_description": "private"})
    )
    async with BitrixOAuthService(settings=settings()) as service:
        with pytest.raises(BitrixOAuthRefreshRejectedError) as captured:
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-old-refresh-token",
                expected_member_id="test-member",
            )
    assert "test-old-refresh-token" not in str(captured.value)


@pytest.mark.parametrize("status", [500, 502, 503])
@respx.mock
async def test_temporary_http_refresh_failure(status: int) -> None:
    respx.post(REFRESH_URL).mock(return_value=httpx.Response(status, text="private body"))
    async with BitrixOAuthService(settings=settings()) as service:
        with pytest.raises(BitrixOAuthTemporaryError):
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-old-refresh-token",
                expected_member_id="test-member",
            )


@respx.mock
async def test_timeout_and_invalid_json_are_temporary() -> None:
    route = respx.post(REFRESH_URL)
    route.side_effect = [httpx.ReadTimeout("private"), httpx.Response(200, text="not-json")]
    async with BitrixOAuthService(settings=settings()) as service:
        with pytest.raises(BitrixOAuthTemporaryError):
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-refresh-token",
                expected_member_id="test-member",
            )
        with pytest.raises(BitrixOAuthTemporaryError):
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-refresh-token",
                expected_member_id="test-member",
            )


@pytest.mark.parametrize("missing", ["access_token", "refresh_token"])
@respx.mock
async def test_missing_token_in_success_is_invalid(missing: str) -> None:
    payload = success_payload()
    payload.pop(missing)
    respx.post(REFRESH_URL).mock(return_value=httpx.Response(200, json=payload))
    async with BitrixOAuthService(settings=settings()) as service:
        with pytest.raises(BitrixOAuthTemporaryError):
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-refresh-token",
                expected_member_id="test-member",
            )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(client_endpoint="http://portal.test/rest/"),
        lambda payload: payload.pop("expires_in"),
    ],
)
@respx.mock
async def test_invalid_refresh_metadata_is_temporary(mutation) -> None:
    payload = success_payload()
    mutation(payload)
    respx.post(REFRESH_URL).mock(return_value=httpx.Response(200, json=payload))
    async with BitrixOAuthService(settings=settings()) as service:
        with pytest.raises(BitrixOAuthTemporaryError):
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-refresh-token",
                expected_member_id="test-member",
            )


@respx.mock
async def test_member_mismatch_is_permanent() -> None:
    respx.post(REFRESH_URL).mock(return_value=httpx.Response(200, json=success_payload()))
    async with BitrixOAuthService(settings=settings()) as service:
        with pytest.raises(BitrixOAuthRefreshRejectedError):
            await service.refresh(
                server_endpoint="https://oauth.test/rest/",
                refresh_token="test-refresh-token",
                expected_member_id="different-member",
            )


def test_token_expiry_prefers_absolute_timestamp() -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    absolute = int((now + timedelta(hours=2)).timestamp())
    assert token_expiry(expires=absolute, expires_in=10, now=now) == datetime.fromtimestamp(
        absolute, tz=UTC
    )
    assert token_expiry(expires=None, expires_in=10, now=now) == now + timedelta(seconds=10)
