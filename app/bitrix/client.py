"""Universal asynchronous Bitrix24 REST client without persistence or OAuth refresh."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.bitrix.exceptions import (
    BitrixAPIError,
    BitrixAuthenticationError,
    BitrixConfigurationError,
    BitrixInvalidResponseError,
    BitrixPermanentError,
    BitrixPermissionError,
    BitrixRateLimitError,
    BitrixTemporaryError,
    BitrixTimeoutError,
    BitrixTransportError,
)
from app.config import Settings, get_settings
from app.security.redaction import redact_sensitive_data

logger = logging.getLogger(__name__)
_METHOD_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")
_AUTHENTICATION_CODES = {
    "EXPIRED_TOKEN",
    "NO_AUTH_FOUND",
    "INVALID_TOKEN",
    "WRONG_AUTH_TYPE",
}
_PERMISSION_CODES = {
    "ACCESS_DENIED",
    "INVALID_CREDENTIALS",
    "USER_ACCESS_ERROR",
    "INSUFFICIENT_SCOPE",
}
_RATE_LIMIT_CODES = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"}
_TEMPORARY_CODES = {"INTERNAL_SERVER_ERROR", "ERROR_UNEXPECTED_ANSWER"}
_PERMANENT_CODES = {"OVERLOAD_LIMIT"}


@dataclass(frozen=True, slots=True)
class BitrixResponse[T]:
    """Typed successful result together with Bitrix24 pagination and timing metadata."""

    result: T
    next: int | None = None
    total: int | None = None
    time: Mapping[str, Any] | None = None


def normalize_client_endpoint(endpoint: str) -> str:
    """Validate and normalize a trusted HTTPS Bitrix24 client endpoint."""
    if not isinstance(endpoint, str) or any(character.isspace() for character in endpoint):
        raise BitrixConfigurationError("Bitrix24 client endpoint is invalid")
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise BitrixConfigurationError("Bitrix24 client endpoint is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BitrixConfigurationError("Bitrix24 client endpoint is invalid")
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def validate_rest_method(method: str) -> str:
    """Accept only dot-separated REST identifiers, never URLs or path segments."""
    if not isinstance(method, str) or _METHOD_PATTERN.fullmatch(method) is None:
        raise BitrixConfigurationError("Bitrix24 REST method is invalid")
    return method


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse Retry-After seconds or HTTP-date without raising on malformed input."""
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(int(stripped))
    try:
        retry_at = parsedate_to_datetime(stripped)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        reference = now or datetime.now(UTC)
        return float(max(0, math.ceil((retry_at - reference).total_seconds())))
    except (TypeError, ValueError, OverflowError):
        return None


def _http_timeout(settings: Settings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.bitrix_http_connect_timeout_seconds,
        read=settings.bitrix_http_read_timeout_seconds,
        write=settings.bitrix_http_write_timeout_seconds,
        pool=settings.bitrix_http_pool_timeout_seconds,
    )


def _http_limits(settings: Settings) -> httpx.Limits:
    return httpx.Limits(
        max_connections=settings.bitrix_http_max_connections,
        max_keepalive_connections=settings.bitrix_http_max_connections,
    )


class BitrixClient:
    """Call Bitrix24 REST with a caller-supplied endpoint and ready access token."""

    def __init__(
        self,
        *,
        client_endpoint: str,
        access_token: str,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._endpoint = normalize_client_endpoint(client_endpoint)
        if not access_token:
            raise BitrixConfigurationError("Bitrix24 access token is not configured")
        self._access_token = access_token
        self._owns_http_client = http_client is None
        resolved_settings = settings or get_settings()
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"sinedis-bitrix24-automation/{resolved_settings.app_version}",
        }
        self._http_client = http_client or httpx.AsyncClient(
            timeout=_http_timeout(resolved_settings),
            limits=_http_limits(resolved_settings),
            follow_redirects=False,
        )

    async def __aenter__(self) -> BitrixClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only an HTTP client owned by this BitrixClient instance."""
        if self._owns_http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def call(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> BitrixResponse[Any]:
        """Execute exactly one POST request and classify its response."""
        safe_method = validate_rest_method(method)
        request_params = dict(params or {})
        if "auth" in request_params:
            raise BitrixConfigurationError("Bitrix24 REST params must not contain auth")
        body = {**request_params, "auth": self._access_token}
        url = f"{self._endpoint}{safe_method}"
        started_at = perf_counter()
        try:
            response = await self._http_client.post(
                url, json=body, headers=self._headers, follow_redirects=False
            )
        except httpx.TimeoutException as exc:
            self._log_transport_error(safe_method, started_at, "timeout")
            raise BitrixTimeoutError(method=safe_method) from exc
        except httpx.RequestError as exc:
            self._log_transport_error(safe_method, started_at, "transport")
            raise BitrixTransportError(method=safe_method) from exc

        duration = perf_counter() - started_at
        try:
            parsed = self._parse_response(response, safe_method)
        except BitrixAPIError as exc:
            logger.warning(
                "Bitrix24 REST error method=%s status=%s code=%s category=%s duration=%.6f",
                safe_method,
                response.status_code,
                exc.code,
                type(exc).__name__,
                duration,
            )
            raise
        except BitrixInvalidResponseError:
            logger.warning(
                "Bitrix24 invalid response method=%s status=%s duration=%.6f",
                safe_method,
                response.status_code,
                duration,
            )
            raise
        logger.debug(
            "Bitrix24 REST success method=%s status=%s duration=%.6f",
            safe_method,
            response.status_code,
            duration,
        )
        return parsed

    @staticmethod
    def _log_transport_error(method: str, started_at: float, category: str) -> None:
        logger.warning(
            "Bitrix24 REST transport error method=%s category=%s duration=%.6f",
            method,
            category,
            perf_counter() - started_at,
        )

    @staticmethod
    def _parse_response(response: httpx.Response, method: str) -> BitrixResponse[Any]:
        payload: Any = None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, Mapping) and "error" in payload:
            raise _classify_api_error(response=response, payload=payload, method=method)
        if not response.is_success:
            raise _classify_http_error(response=response, method=method)

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise BitrixInvalidResponseError(method=method, http_status=response.status_code)
        if not isinstance(payload, Mapping) or "result" not in payload:
            raise BitrixInvalidResponseError(method=method, http_status=response.status_code)
        next_value = _optional_int(payload.get("next"), response, method)
        total_value = _optional_int(payload.get("total"), response, method)
        time_value = payload.get("time")
        if time_value is not None and not isinstance(time_value, Mapping):
            raise BitrixInvalidResponseError(method=method, http_status=response.status_code)
        return BitrixResponse(
            result=payload["result"],
            next=next_value,
            total=total_value,
            time=dict(time_value) if time_value is not None else None,
        )


def _optional_int(value: Any, response: httpx.Response, method: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BitrixInvalidResponseError(method=method, http_status=response.status_code)
    return value


def _error_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = payload.get("time")
    return dict(value) if isinstance(value, Mapping) else None


def _classify_api_error(
    *, response: httpx.Response, payload: Mapping[str, Any], method: str
) -> BitrixAPIError:
    raw_code = payload.get("error")
    code = raw_code if isinstance(raw_code, str) and raw_code else "UNKNOWN_BITRIX_ERROR"
    normalized_code = code.upper()
    raw_description = payload.get("error_description")
    description = (
        str(redact_sensitive_data(raw_description)) if isinstance(raw_description, str) else None
    )
    retry_after = parse_retry_after(response.headers.get("Retry-After"))
    common = {
        "code": code,
        "http_status": response.status_code,
        "method": method,
        "error_description": description,
        "time": _error_metadata(payload),
    }
    if normalized_code in _AUTHENTICATION_CODES:
        return BitrixAuthenticationError(**common, retryable=False)
    if normalized_code in _PERMISSION_CODES:
        return BitrixPermissionError(**common, retryable=False)
    if normalized_code in _RATE_LIMIT_CODES:
        return BitrixRateLimitError(**common, retryable=True, retry_after_seconds=retry_after)
    if normalized_code in _TEMPORARY_CODES:
        return BitrixTemporaryError(**common, retryable=True, retry_after_seconds=retry_after)
    if normalized_code in _PERMANENT_CODES:
        return BitrixPermanentError(**common, retryable=False)
    return _unknown_error(
        response=response,
        method=method,
        code=code,
        description=description,
        time=_error_metadata(payload),
        retry_after=retry_after,
    )


def _classify_http_error(*, response: httpx.Response, method: str) -> BitrixAPIError:
    return _unknown_error(
        response=response,
        method=method,
        code=f"HTTP_{response.status_code}",
        description=None,
        time=None,
        retry_after=parse_retry_after(response.headers.get("Retry-After")),
    )


def _unknown_error(
    *,
    response: httpx.Response,
    method: str,
    code: str,
    description: str | None,
    time: Mapping[str, Any] | None,
    retry_after: float | None,
) -> BitrixAPIError:
    common = {
        "code": code,
        "http_status": response.status_code,
        "method": method,
        "error_description": description,
        "time": time,
    }
    if response.status_code == 429:
        return BitrixRateLimitError(**common, retryable=True, retry_after_seconds=retry_after)
    if response.status_code in {500, 502, 503, 504}:
        return BitrixTemporaryError(**common, retryable=True, retry_after_seconds=retry_after)
    return BitrixPermanentError(**common, retryable=False)
