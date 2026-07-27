"""Tests for recursive secret redaction and logging protection."""

import logging

from pydantic import SecretStr

from app.config import Settings
from app.logging import configure_logging
from app.security.redaction import REDACTION_MASK, redact_sensitive_data


def test_recursive_redaction_preserves_source_and_safe_values() -> None:
    """Sensitive key variants are masked in copied nested containers."""
    source = {
        "ACCESS_TOKEN": "test-access-token-value",
        "accessToken": "test-camel-token-value",
        "auth[access_token]": "test-auth-token-value",
        "headers.Authorization": "Bearer test-header-token-value",
        "safe": "visible-value",
        "nested": {
            "password": "test-password-value",
            "items": [
                {"refresh-token": "test-refresh-token-value"},
                ("safe-tuple", {"Cookie": "test-cookie-value"}),
            ],
        },
    }

    redacted = redact_sensitive_data(source)

    assert source["ACCESS_TOKEN"] == "test-access-token-value"
    assert redacted["ACCESS_TOKEN"] == REDACTION_MASK
    assert redacted["accessToken"] == REDACTION_MASK
    assert redacted["auth[access_token]"] == REDACTION_MASK
    assert redacted["headers.Authorization"] == REDACTION_MASK
    assert redacted["safe"] == "visible-value"
    assert redacted["nested"]["password"] == REDACTION_MASK
    assert redacted["nested"]["items"][0]["refresh-token"] == REDACTION_MASK
    assert redacted["nested"]["items"][1][1]["Cookie"] == REDACTION_MASK


def test_string_redaction_covers_headers_forms_queries_and_dsn() -> None:
    """Common textual secret forms are sanitized while retaining useful context."""
    source = (
        "Authorization: Bearer test-bearer-value\n"
        "url=https://example.test/callback?access_token=test-query-token&safe=yes "
        "client_secret=test-client-secret password=test-form-password "
        "postgresql+asyncpg://test_user:test-database-password@db:5432/test_db"
    )
    redacted = str(redact_sensitive_data(source))

    for secret in (
        "test-bearer-value",
        "test-query-token",
        "test-client-secret",
        "test-form-password",
        "test-database-password",
    ):
        assert secret not in redacted
    assert REDACTION_MASK in redacted
    assert "safe=yes" in redacted
    assert "db:5432/test_db" in redacted


def test_secret_str_and_set_are_supported() -> None:
    """SecretStr is never unwrapped and sets are copied safely."""
    secret = SecretStr("test-secret-str-value")
    assert redact_sensitive_data(secret) == REDACTION_MASK
    assert redact_sensitive_data({secret, "safe"}) == {REDACTION_MASK, "safe"}


def test_logging_filter_redacts_message_args_and_exception(capsys) -> None:
    """Configured logging emits masks instead of message, argument, and exception secrets."""
    configure_logging(Settings())
    logger = logging.getLogger("tests.security")
    try:
        raise RuntimeError("password=test-exception-password")
    except RuntimeError:
        logger.exception(
            "Authorization: Bearer %s database=%s",
            "test-argument-token",
            "postgresql+asyncpg://user:test-log-password@db/test",
            extra={"access_token": "test-extra-token"},
        )
    logger.info("payload=%s", {"refresh_token": "test-structured-argument"})

    output = capsys.readouterr().err
    for secret in (
        "test-argument-token",
        "test-log-password",
        "test-extra-token",
        "test-exception-password",
        "test-structured-argument",
    ):
        assert secret not in output
    assert REDACTION_MASK in output
