"""Tests for Bitrix24 token fingerprints and keyed hashes."""

import hmac
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.bitrix.security import (
    APPLICATION_TOKEN_HASH_PREFIX,
    event_token_fingerprint,
    hash_application_token,
    verify_application_token,
)


def configure_test_key(monkeypatch) -> None:
    """Provide a stable test-only key without changing process environment."""
    settings = SimpleNamespace(
        encryption_key=SecretStr(Fernet.generate_key().decode("ascii")),
        encryption_key_previous=None,
    )
    monkeypatch.setattr("app.bitrix.security.get_settings", lambda: settings)


def test_event_token_fingerprint_is_full_deterministic_sha256() -> None:
    """The fingerprint is stable lowercase hexadecimal and does not expose its source."""
    token = "test-event-token-value"
    fingerprint = event_token_fingerprint(token)

    assert fingerprint == event_token_fingerprint(token)
    assert fingerprint != event_token_fingerprint("different-test-event-token")
    assert len(fingerprint) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    assert token not in fingerprint


def test_application_token_hash_is_versioned_and_deterministic(monkeypatch) -> None:
    """The keyed application-token hash verifies only the original token."""
    configure_test_key(monkeypatch)
    token = "test-application-token-value"
    stored_hash = hash_application_token(token)

    assert stored_hash.startswith(APPLICATION_TOKEN_HASH_PREFIX)
    assert len(stored_hash) <= 128
    assert stored_hash == hash_application_token(token)
    assert verify_application_token(token, stored_hash) is True
    assert verify_application_token("wrong-test-token", stored_hash) is False


@pytest.mark.parametrize(
    ("token", "stored_hash"),
    [
        ("", f"{APPLICATION_TOKEN_HASH_PREFIX}{'a' * 64}"),
        ("test-token", "hmac-sha256:v2:invalid"),
        ("test-token", f"{APPLICATION_TOKEN_HASH_PREFIX}damaged"),
    ],
)
def test_application_token_verification_rejects_invalid_inputs(
    monkeypatch, token: str, stored_hash: str
) -> None:
    """Empty tokens, unknown versions, and damaged hashes are safely rejected."""
    configure_test_key(monkeypatch)
    assert verify_application_token(token, stored_hash) is False


def test_empty_application_token_cannot_be_hashed(monkeypatch) -> None:
    """An empty application token is never considered a valid credential."""
    configure_test_key(monkeypatch)
    with pytest.raises(ValueError):
        hash_application_token("")


def test_verification_uses_compare_digest(monkeypatch) -> None:
    """Verification delegates equality checking to constant-time compare_digest."""
    configure_test_key(monkeypatch)
    stored_hash = hash_application_token("test-token")
    original = hmac.compare_digest
    comparison = Mock(side_effect=original)
    monkeypatch.setattr("app.bitrix.security.hmac.compare_digest", comparison)

    assert verify_application_token("test-token", stored_hash) is True
    comparison.assert_called()


def test_previous_derived_key_verifies_hash_during_rotation(monkeypatch) -> None:
    """Hashes made before key rotation remain verifiable while the previous key is configured."""
    previous_key = SecretStr(Fernet.generate_key().decode("ascii"))
    old_settings = SimpleNamespace(encryption_key=previous_key, encryption_key_previous=None)
    monkeypatch.setattr("app.bitrix.security.get_settings", lambda: old_settings)
    stored_hash = hash_application_token("test-rotation-token")

    rotated_settings = SimpleNamespace(
        encryption_key=SecretStr(Fernet.generate_key().decode("ascii")),
        encryption_key_previous=previous_key,
    )
    monkeypatch.setattr("app.bitrix.security.get_settings", lambda: rotated_settings)
    assert verify_application_token("test-rotation-token", stored_hash) is True
