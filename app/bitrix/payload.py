"""Typed normalization and validation of Bitrix24 callback payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.bitrix.client import normalize_client_endpoint
from app.bitrix.exceptions import BitrixConfigurationError

MAX_MEMBER_ID_LENGTH = 64


class BitrixPayloadError(ValueError):
    """A callback payload does not satisfy the expected safe contract."""


@dataclass(frozen=True, slots=True)
class BitrixAuthPayload:
    """Normalized callback authentication data with secrets excluded from repr."""

    member_id: str
    domain: str
    client_endpoint: str
    server_endpoint: str
    application_token: str | None = field(default=None, repr=False)
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_in: int | None = None
    expires: int | None = None
    scope: str | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class BitrixEventPayload:
    """Normalized event data without retaining the original request body."""

    event: str
    timestamp: int | None
    data: Mapping[str, Any]
    auth: BitrixAuthPayload


def _nested_copy(source: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in source.items():
        key = str(raw_key)
        if "[" in key and key.endswith("]"):
            parent, child = key.split("[", 1)
            child = child[:-1]
            if parent and child:
                nested = result.setdefault(parent, {})
                if isinstance(nested, dict):
                    nested[child] = value
                continue
        result[key] = dict(value) if isinstance(value, Mapping) else value
    return result


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise BitrixPayloadError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BitrixPayloadError(f"{field_name} must be a positive integer") from exc
    if parsed <= 0 or str(value).strip() not in {str(parsed), f"+{parsed}"}:
        raise BitrixPayloadError(f"{field_name} must be a positive integer")
    return parsed


def _optional_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise BitrixPayloadError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BitrixPayloadError(f"{field_name} must be an integer") from exc


def _required_text(value: Any, field_name: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BitrixPayloadError(f"{field_name} is missing or invalid")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _validate_domain(value: Any) -> str:
    domain = _required_text(value, "domain", maximum=255).lower()
    if any(marker in domain for marker in ("://", "/", "?", "#", "@")) or any(
        character.isspace() for character in domain
    ):
        raise BitrixPayloadError("domain is invalid")
    return domain


def normalize_event_payload(
    source: Mapping[str, Any], *, expected_event: str, require_oauth_tokens: bool
) -> BitrixEventPayload:
    """Normalize nested or bracket notation and validate one lifecycle event."""
    copied = _nested_copy(source)
    event = _required_text(copied.get("event"), "event", maximum=64).upper()
    if event != expected_event:
        raise BitrixPayloadError("event does not match callback endpoint")
    auth_value = copied.get("auth")
    if not isinstance(auth_value, Mapping):
        raise BitrixPayloadError("auth is missing or invalid")
    auth = dict(auth_value)
    member_id = _required_text(auth.get("member_id"), "member_id", maximum=MAX_MEMBER_ID_LENGTH)
    domain = _validate_domain(auth.get("domain"))
    try:
        client_endpoint = normalize_client_endpoint(
            _required_text(auth.get("client_endpoint"), "client_endpoint")
        )
        server_endpoint = normalize_client_endpoint(
            _required_text(auth.get("server_endpoint"), "server_endpoint")
        )
    except BitrixConfigurationError as exc:
        raise BitrixPayloadError("OAuth endpoint is invalid") from exc
    access_token = _optional_text(auth.get("access_token"))
    refresh_token = _optional_text(auth.get("refresh_token"))
    application_token = _optional_text(auth.get("application_token"))
    if application_token is None:
        raise BitrixPayloadError("application_token is missing or invalid")
    if require_oauth_tokens and (access_token is None or refresh_token is None):
        raise BitrixPayloadError("OAuth token pair is missing")
    expires = _optional_positive_int(auth.get("expires"), "expires")
    expires_in = _optional_positive_int(auth.get("expires_in"), "expires_in")
    if require_oauth_tokens and expires is None and expires_in is None:
        raise BitrixPayloadError("OAuth expiry is missing")
    data_value = copied.get("data", {})
    data = dict(data_value) if isinstance(data_value, Mapping) else {}
    return BitrixEventPayload(
        event=event,
        timestamp=_optional_int(copied.get("ts"), "ts"),
        data=data,
        auth=BitrixAuthPayload(
            member_id=member_id,
            domain=domain,
            client_endpoint=client_endpoint,
            server_endpoint=server_endpoint,
            application_token=application_token,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            expires=expires,
            scope=_optional_text(auth.get("scope")),
            status=_optional_text(auth.get("status")),
        ),
    )
