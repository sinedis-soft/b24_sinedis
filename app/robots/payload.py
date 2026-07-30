"""Safe normalization of Bitrix24 robot callback payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jsonpath_ng import parse as parse_jsonpath
from jsonpath_ng.exceptions import JSONPathError

from app.bitrix.client import validate_rest_method
from app.bitrix.exceptions import BitrixConfigurationError


class RobotPayloadError(ValueError):
    """Robot callback data does not satisfy the public contract."""


@dataclass(frozen=True, slots=True)
class RobotExecutionPayload:
    member_id: str
    application_token: str = field(repr=False)
    event_token: str = field(repr=False)
    delay_seconds: int
    comment: str | None
    document_id: Any | None = None
    document_type: Any | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionPayload:
    member_id: str
    application_token: str = field(repr=False)
    event_token: str = field(repr=False)
    properties: dict[str, Any]
    error_recipients: tuple[int, ...]
    document_id: Any | None = None
    document_type: Any | None = None


def normalize_rest_request_payload(source: Mapping[str, Any]) -> SubscriptionPayload:
    common, properties = _subscription(source)
    method = _required(properties.get("rest_method"), "rest_method")
    try:
        validate_rest_method(method)
    except BitrixConfigurationError as exc:
        raise RobotPayloadError("rest_method is invalid") from exc
    raw_json = properties.get("request_params_json", "{}")
    if not isinstance(raw_json, str) or len(raw_json) > 100_000:
        raise RobotPayloadError("request_params_json is invalid or too large")
    import json

    try:
        params = json.loads(raw_json)
    except (TypeError, ValueError) as exc:
        raise RobotPayloadError("request_params_json must contain valid JSON") from exc
    if not isinstance(params, dict):
        raise RobotPayloadError("request_params_json must contain a JSON object")
    if "auth" in params:
        raise RobotPayloadError("auth must not be supplied in request_params_json")
    expression = _required(properties.get("jsonpath", "$"), "jsonpath")
    try:
        parse_jsonpath(expression)
    except JSONPathError as exc:
        raise RobotPayloadError("jsonpath is invalid") from exc
    return _with_properties(
        common,
        {"rest_method": method, "request_params": params, "jsonpath": expression},
        properties,
    )


def normalize_wait_field_payload(source: Mapping[str, Any]) -> SubscriptionPayload:
    common, properties = _subscription(source)
    entity_type_id = _positive_integer(properties.get("entity_type_id"), "entity_type_id")
    entity_id = _positive_integer(properties.get("entity_id"), "entity_id")
    field_name = _required(properties.get("field_name"), "field_name")
    if len(field_name) > 128 or not all(c.isalnum() or c == "_" for c in field_name):
        raise RobotPayloadError("field_name is invalid")
    poll = _positive_integer(properties.get("poll_interval_seconds", 30), "poll_interval_seconds")
    timeout = _positive_integer(properties.get("timeout_seconds", 86400), "timeout_seconds")
    if poll > 86400 or timeout > 31_536_000:
        raise RobotPayloadError("wait interval is outside the supported range")
    return _with_properties(
        common,
        {
            "entity_type_id": entity_type_id,
            "entity_id": entity_id,
            "field_name": field_name,
            "poll_interval_seconds": poll,
            "timeout_seconds": timeout,
        },
        properties,
    )


def _subscription(source: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    data = _nested_copy(source)
    auth = data.get("auth") if isinstance(data.get("auth"), Mapping) else {}
    raw = data.get("properties", data.get("PROPERTIES"))
    properties = raw if isinstance(raw, Mapping) else {}
    common = {
        "member_id": _required(auth.get("member_id"), "member_id"),
        "application_token": _required(auth.get("application_token"), "application_token"),
        "event_token": _required(data.get("event_token", data.get("EVENT_TOKEN")), "event_token"),
        "document_id": data.get("document_id", data.get("DOCUMENT_ID")),
        "document_type": data.get("document_type", data.get("DOCUMENT_TYPE")),
    }
    return common, properties


def _with_properties(
    common: dict[str, Any], normalized: dict[str, Any], raw: Mapping[str, Any]
) -> SubscriptionPayload:
    recipients = _recipients(raw.get("error_recipients"))
    return SubscriptionPayload(**common, properties=normalized, error_recipients=recipients)


def _recipients(value: Any) -> tuple[int, ...]:
    values = value if isinstance(value, (list, tuple)) else ([] if value in (None, "") else [value])
    result: list[int] = []
    for item in values:
        text = str(item).strip()
        if text.startswith("user_"):
            text = text[5:]
        try:
            user_id = int(text)
        except ValueError as exc:
            raise RobotPayloadError("error_recipients contains an invalid user") from exc
        if user_id <= 0:
            raise RobotPayloadError("error_recipients contains an invalid user")
        if user_id not in result:
            result.append(user_id)
    return tuple(result)


def _positive_integer(value: Any, name: str) -> int:
    result = _integer(value, name)
    if result <= 0:
        raise RobotPayloadError(f"{name} must be positive")
    return result


def normalize_robot_payload(
    source: Mapping[str, Any], *, minimum_delay: int, maximum_delay: int
) -> RobotExecutionPayload:
    """Copy and validate nested JSON or bracket-notation callback data."""
    data = _nested_copy(source)
    auth = data.get("auth") if isinstance(data.get("auth"), Mapping) else {}
    raw_properties = data.get("properties", data.get("PROPERTIES"))
    properties = raw_properties if isinstance(raw_properties, Mapping) else {}
    member_id = _required(auth.get("member_id"), "member_id")
    if len(member_id) > 64:
        raise RobotPayloadError("member_id is too long")
    application_token = _required(auth.get("application_token"), "application_token")
    event_token = _required(data.get("event_token", data.get("EVENT_TOKEN")), "event_token")
    delay = _integer(properties.get("delay_seconds"), "delay_seconds")
    if not minimum_delay <= delay <= maximum_delay:
        raise RobotPayloadError("delay_seconds is outside the configured range")
    raw_comment = properties.get("comment")
    comment = None if raw_comment is None else str(raw_comment).strip() or None
    if comment is not None and len(comment) > 1000:
        raise RobotPayloadError("comment is too long")
    return RobotExecutionPayload(
        member_id,
        application_token,
        event_token,
        delay,
        comment,
        data.get("document_id", data.get("DOCUMENT_ID")),
        data.get("document_type", data.get("DOCUMENT_TYPE")),
    )


def _nested_copy(source: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in source.items():
        if isinstance(value, Mapping):
            copied[str(key)] = _nested_copy(value)
        else:
            text = str(key)
            if "[" in text and text.endswith("]"):
                group, child = text[:-1].split("[", 1)
                if group in {"auth", "properties", "PROPERTIES"} and child:
                    target = copied.setdefault(group, {})
                    if isinstance(target, dict):
                        target[child] = value
                    continue
            copied[text] = value
    return copied


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RobotPayloadError(f"{name} is required")
    return value.strip()


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise RobotPayloadError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip() and value.strip().lstrip("-").isdigit():
        return int(value)
    raise RobotPayloadError(f"{name} must be an integer")
