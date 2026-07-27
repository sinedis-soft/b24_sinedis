"""Safe normalization of Bitrix24 robot callback payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


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
