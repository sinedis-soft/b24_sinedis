"""Low-level OAuth refresh transport and portal-scoped token lifecycle service."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bitrix.client import BitrixClient, BitrixResponse, normalize_client_endpoint
from app.bitrix.exceptions import (
    BitrixAuthenticationError,
    BitrixConfigurationError,
    BitrixOAuthConfigurationError,
    BitrixOAuthRefreshRejectedError,
    BitrixOAuthTemporaryError,
)
from app.config import Settings, get_settings
from app.models.portal import BitrixPortal, PortalStatus
from app.security.encryption import EncryptionService

_SAFE_OAUTH_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


@dataclass(frozen=True, slots=True)
class OAuthRefreshResult:
    """Validated replacement OAuth pair with secrets omitted from repr."""

    member_id: str
    client_endpoint: str
    server_endpoint: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires: int | None = None
    expires_in: int | None = None
    domain: str | None = None
    scope: str | None = None
    status: str | None = None
    user_id: int | None = None


def build_oauth_refresh_url(server_endpoint: str) -> str:
    """Build a fixed OAuth path from only the validated authorization origin."""
    try:
        parsed = urlsplit(server_endpoint)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise BitrixOAuthConfigurationError("OAuth server endpoint is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in server_endpoint)
    ):
        raise BitrixOAuthConfigurationError("OAuth server endpoint is invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit(("https", netloc, "/oauth/token/", "", ""))


def token_expiry(*, expires: int | None, expires_in: int | None, now: datetime) -> datetime:
    """Calculate an exact timezone-aware expiry, preferring the absolute timestamp."""
    if expires is not None and expires > 0:
        return datetime.fromtimestamp(expires, tz=UTC)
    if expires_in is not None and expires_in > 0:
        return now + timedelta(seconds=expires_in)
    raise BitrixOAuthConfigurationError("OAuth expiry is missing")


class BitrixOAuthService:
    """Perform exactly one safe refresh request against an authorization server."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._settings.bitrix_oauth_connect_timeout_seconds,
                read=self._settings.bitrix_oauth_read_timeout_seconds,
                write=self._settings.bitrix_http_write_timeout_seconds,
                pool=self._settings.bitrix_http_pool_timeout_seconds,
            ),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._owns_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def __aenter__(self) -> BitrixOAuthService:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def refresh(
        self, *, server_endpoint: str, refresh_token: str, expected_member_id: str
    ) -> OAuthRefreshResult:
        """Exchange one refresh token without retrying or exposing request credentials."""
        if not self._settings.bitrix_client_id or self._settings.bitrix_client_secret is None:
            raise BitrixOAuthConfigurationError("OAuth client credentials are not configured")
        if not refresh_token:
            raise BitrixOAuthConfigurationError("OAuth refresh token is not configured")
        url = build_oauth_refresh_url(server_endpoint)
        form = {
            "grant_type": "refresh_token",
            "client_id": self._settings.bitrix_client_id,
            "client_secret": self._settings.bitrix_client_secret.get_secret_value(),
            "refresh_token": refresh_token,
        }
        try:
            response = await self._http_client.post(url, data=form, follow_redirects=False)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise BitrixOAuthTemporaryError("OAuth refresh is temporarily unavailable") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise BitrixOAuthTemporaryError("OAuth server returned an invalid response") from exc
        if not isinstance(payload, Mapping):
            raise BitrixOAuthTemporaryError("OAuth server returned an invalid response")
        if "error" in payload or not response.is_success:
            code = payload.get("error")
            safe_code = (
                code
                if isinstance(code, str) and _SAFE_OAUTH_CODE.fullmatch(code)
                else f"HTTP_{response.status_code}"
            )
            if response.status_code in {500, 502, 503, 504}:
                raise BitrixOAuthTemporaryError(f"OAuth refresh failed: {safe_code}")
            raise BitrixOAuthRefreshRejectedError(f"OAuth refresh rejected: {safe_code}")
        return self._parse_success(payload, expected_member_id)

    @staticmethod
    def _parse_success(payload: Mapping[str, Any], expected_member_id: str) -> OAuthRefreshResult:
        required: dict[str, str] = {}
        for name in (
            "access_token",
            "refresh_token",
            "member_id",
            "client_endpoint",
            "server_endpoint",
        ):
            value = payload.get(name)
            if not isinstance(value, str) or not value:
                raise BitrixOAuthTemporaryError("OAuth server returned an invalid response")
            required[name] = value
        if required["member_id"] != expected_member_id:
            raise BitrixOAuthRefreshRejectedError("OAuth refresh portal identity mismatch")
        try:
            client_endpoint = normalize_client_endpoint(required["client_endpoint"])
            build_oauth_refresh_url(required["server_endpoint"])
            server_endpoint = normalize_client_endpoint(required["server_endpoint"])
            expires = _positive_optional_int(payload.get("expires"))
            expires_in = _positive_optional_int(payload.get("expires_in"))
            if expires is None and expires_in is None:
                raise ValueError
            user_id = _optional_integer(payload.get("user_id"))
        except (ValueError, BitrixConfigurationError, BitrixOAuthConfigurationError) as exc:
            raise BitrixOAuthTemporaryError("OAuth server returned an invalid response") from exc
        domain = payload.get("domain")
        if domain is not None and (
            not isinstance(domain, str)
            or not domain
            or len(domain) > 255
            or any(x in domain for x in ("://", "/", "?", "#", "@"))
            or any(character.isspace() for character in domain)
        ):
            raise BitrixOAuthTemporaryError("OAuth server returned an invalid response")
        return OAuthRefreshResult(
            member_id=required["member_id"],
            access_token=required["access_token"],
            refresh_token=required["refresh_token"],
            client_endpoint=client_endpoint,
            server_endpoint=server_endpoint,
            expires=expires,
            expires_in=expires_in,
            domain=domain.lower() if isinstance(domain, str) else None,
            scope=_optional_string(payload.get("scope")),
            status=_optional_string(payload.get("status")),
            user_id=user_id,
        )


def _positive_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError
    parsed = int(value)
    if parsed <= 0 or str(value).strip() not in {str(parsed), f"+{parsed}"}:
        raise ValueError
    return parsed


def _optional_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError
    parsed = int(value)
    if str(value).strip() not in {str(parsed), f"+{parsed}"}:
        raise ValueError
    return parsed


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class PortalOAuthService:
    """Coordinate encrypted portal tokens, row locks, refresh, and one auth retry."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        encryption: EncryptionService,
        oauth: BitrixOAuthService,
        settings: Settings | None = None,
        rest_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._encryption = encryption
        self._oauth = oauth
        self._settings = settings or get_settings()
        self._rest_http_client = rest_http_client

    async def get_access_token(self, portal_id: UUID) -> str:
        """Return a usable token, refreshing only near its actual expiry."""
        async with self._session_factory() as session:
            portal = await session.get(BitrixPortal, portal_id)
            self._require_active(portal)
            assert portal is not None
            if portal.access_token_encrypted is None:
                raise BitrixOAuthConfigurationError("Portal access token is missing")
            cutoff = datetime.now(UTC) + timedelta(
                seconds=self._settings.bitrix_oauth_expiry_skew_seconds
            )
            if portal.token_expires_at is not None and portal.token_expires_at > cutoff:
                return self._encryption.decrypt(portal.access_token_encrypted)
        return await self.refresh_access_token(portal_id)

    async def refresh_access_token(self, portal_id: UUID, *, force: bool = False) -> str:
        """Lock one portal and atomically replace its complete OAuth token pair."""
        rejected: BitrixOAuthRefreshRejectedError | None = None
        refreshed_token: str | None = None
        async with self._session_factory() as session, session.begin():
            portal = await session.scalar(
                select(BitrixPortal).where(BitrixPortal.id == portal_id).with_for_update()
            )
            self._require_active(portal)
            assert portal is not None
            cutoff = datetime.now(UTC) + timedelta(
                seconds=self._settings.bitrix_oauth_expiry_skew_seconds
            )
            if (
                not force
                and portal.token_expires_at is not None
                and portal.token_expires_at > cutoff
                and portal.access_token_encrypted is not None
            ):
                return self._encryption.decrypt(portal.access_token_encrypted)
            if portal.refresh_token_encrypted is None or portal.server_endpoint is None:
                portal.status = PortalStatus.AUTH_ERROR.value
                rejected = BitrixOAuthRefreshRejectedError("Portal refresh credentials are missing")
            else:
                refresh_token = self._encryption.decrypt(portal.refresh_token_encrypted)
                try:
                    result = await self._oauth.refresh(
                        server_endpoint=portal.server_endpoint,
                        refresh_token=refresh_token,
                        expected_member_id=portal.member_id,
                    )
                except BitrixOAuthRefreshRejectedError as exc:
                    portal.status = PortalStatus.AUTH_ERROR.value
                    rejected = exc
                else:
                    now = datetime.now(UTC)
                    portal.access_token_encrypted = self._encryption.encrypt(result.access_token)
                    portal.refresh_token_encrypted = self._encryption.encrypt(result.refresh_token)
                    portal.token_expires_at = token_expiry(
                        expires=result.expires, expires_in=result.expires_in, now=now
                    )
                    portal.client_endpoint = result.client_endpoint
                    portal.server_endpoint = result.server_endpoint
                    if result.domain:
                        portal.domain = result.domain
                    portal.status = PortalStatus.ACTIVE.value
                    refreshed_token = result.access_token
        if rejected is not None:
            raise rejected
        if refreshed_token is None:
            raise BitrixOAuthTemporaryError("OAuth refresh did not produce a token")
        return refreshed_token

    async def call_portal(
        self, portal_id: UUID, method: str, params: Mapping[str, Any] | None = None
    ) -> BitrixResponse[Any]:
        """Retry once only when Bitrix explicitly rejects the access token."""
        token = await self.get_access_token(portal_id)
        try:
            return await self._call_once(portal_id, token, method, params)
        except BitrixAuthenticationError:
            token = await self.refresh_access_token(portal_id, force=True)
        try:
            return await self._call_once(portal_id, token, method, params)
        except BitrixAuthenticationError:
            await self._mark_auth_error(portal_id)
            raise

    async def _call_once(
        self, portal_id: UUID, token: str, method: str, params: Mapping[str, Any] | None
    ) -> BitrixResponse[Any]:
        async with self._session_factory() as session:
            portal = await session.get(BitrixPortal, portal_id)
            self._require_active(portal)
            assert portal is not None
            endpoint = portal.client_endpoint
        async with BitrixClient(
            client_endpoint=endpoint,
            access_token=token,
            http_client=self._rest_http_client,
            settings=self._settings,
        ) as client:
            return await client.call(method, params)

    async def _mark_auth_error(self, portal_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            portal = await session.get(BitrixPortal, portal_id, with_for_update=True)
            if portal is not None:
                portal.status = PortalStatus.AUTH_ERROR.value

    @staticmethod
    def _require_active(portal: BitrixPortal | None) -> None:
        if portal is None:
            raise BitrixOAuthConfigurationError("Portal is unavailable")
        if portal.status != PortalStatus.ACTIVE.value:
            raise BitrixOAuthConfigurationError("Portal is not active")
