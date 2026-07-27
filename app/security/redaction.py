"""Best-effort redaction for logs and diagnostic data structures."""

import logging
import re
from collections.abc import Mapping

from pydantic import SecretStr

REDACTION_MASK = "***REDACTED***"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SENSITIVE_NAMES = {
    "access_token",
    "refresh_token",
    "event_token",
    "application_token",
    "client_secret",
    "authorization",
    "cookie",
    "set_cookie",
    "password",
    "passwd",
    "secret",
    "encryption_key",
    "admin_api_token",
    "database_url",
}
_DSN_PATTERN = re.compile(
    r"(?P<prefix>\b(?:postgres(?:ql)?(?:\+asyncpg)?|mysql(?:\+\w+)?|mariadb(?:\+\w+)?)"
    r"://[^\s:/@]+:)(?P<password>[^\s/@]+)(?P<suffix>@)",
    re.IGNORECASE,
)
_HEADER_PATTERN = re.compile(
    r"(?P<name>\b(?:authorization|cookie|set-cookie)\s*:\s*)(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?P<prefix>\bBearer\s+)(?P<token>[^\s,;]+)", re.IGNORECASE)
_KEY_VALUE_PATTERN = re.compile(
    r"(?P<prefix>(?<![a-z0-9_])[\"']?(?:access[_-]?token|refresh[_-]?token|event[_-]?token|"
    r"application[_-]?token|client[_-]?secret|password|passwd|encryption[_-]?key|"
    r"admin[_-]?api[_-]?token)[\"']?\s*[=:]\s*[\"']?)(?P<value>[^\s&#,;\"'}]+)",
    re.IGNORECASE,
)


def _normalized_key(key: object) -> str:
    text = _CAMEL_BOUNDARY.sub("_", str(key)).lower()
    return _NON_ALNUM.sub("_", text).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return any(normalized == name or normalized.endswith(f"_{name}") for name in _SENSITIVE_NAMES)


def _redact_string(value: str) -> str:
    redacted = _DSN_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTION_MASK}{match.group('suffix')}", value
    )
    redacted = _HEADER_PATTERN.sub(lambda match: f"{match.group('name')}{REDACTION_MASK}", redacted)
    redacted = _BEARER_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTION_MASK}", redacted
    )
    return _KEY_VALUE_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTION_MASK}", redacted
    )


def redact_sensitive_data(value: object) -> object:
    """Return a redacted copy of common containers and strings."""
    if isinstance(value, SecretStr):
        return REDACTION_MASK
    if isinstance(value, Mapping):
        return {
            key: REDACTION_MASK if _is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, set):
        return {redact_sensitive_data(item) for item in value}
    if isinstance(value, str):
        return _redact_string(value)
    return value


_STANDARD_LOG_RECORD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class SensitiveDataFilter(logging.Filter):
    """Sanitize standard messages, arguments, exceptions, and structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.args = redact_sensitive_data(record.args)
            rendered_message = record.getMessage()
            record.msg = redact_sensitive_data(rendered_message)
            record.args = ()
            for name, value in tuple(record.__dict__.items()):
                if name not in _STANDARD_LOG_RECORD_FIELDS:
                    record.__dict__[name] = redact_sensitive_data(value)
            if record.exc_info is not None:
                exception = record.exc_info[1]
                safe_exception = redact_sensitive_data(str(exception)) if exception else ""
                record.msg = f"{record.msg} | exception={safe_exception}"
                record.exc_info = None
                record.exc_text = None
            if record.stack_info:
                record.stack_info = str(redact_sensitive_data(record.stack_info))
        except Exception:
            record.msg = "Log message redacted after sanitization failure"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True
