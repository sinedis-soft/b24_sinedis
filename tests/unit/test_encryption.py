"""Tests for versioned Fernet encryption and key rotation."""

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.security.encryption import (
    ENCRYPTION_PREFIX,
    DecryptionError,
    EncryptionService,
    InvalidEncryptionKeyError,
    get_encryption_service,
)
from app.security.exceptions import SecurityConfigurationError


def key() -> str:
    """Create an isolated test-only Fernet key."""
    return Fernet.generate_key().decode("ascii")


def test_encrypt_decrypt_and_randomized_ciphertext() -> None:
    """Encryption round-trips without exposing plaintext or becoming deterministic."""
    service = EncryptionService(key())
    plaintext = "test-access-token-value"
    first = service.encrypt(plaintext)
    second = service.encrypt(plaintext)

    assert first.startswith(ENCRYPTION_PREFIX)
    assert plaintext not in first
    assert first != second
    assert service.decrypt(first) == plaintext
    assert service.needs_rotation(first) is False


def test_empty_string_round_trip() -> None:
    """An empty string has an explicit encrypted representation and round-trips."""
    service = EncryptionService(key())
    assert service.decrypt(service.encrypt("")) == ""


def test_invalid_key_is_rejected_without_key_in_message() -> None:
    """A password-like value is not accepted as a Fernet key."""
    invalid_key = "test-invalid-encryption-key"
    with pytest.raises(InvalidEncryptionKeyError) as captured:
        EncryptionService(invalid_key)
    assert invalid_key not in str(captured.value)


@pytest.mark.parametrize(
    "ciphertext",
    ["fernet:v1:damaged-test-value", "unknown:v1:test-value", "fernet:v2:test-value"],
)
def test_invalid_ciphertext_is_rejected_safely(ciphertext: str) -> None:
    """Damaged tokens and unknown envelopes produce only safe application errors."""
    service = EncryptionService(key())
    with pytest.raises(DecryptionError) as captured:
        service.decrypt(ciphertext)
    assert ciphertext not in str(captured.value)


def test_previous_key_decryption_and_rotation() -> None:
    """A previous key remains readable and can be rotated to the current key."""
    current_key = key()
    previous_key = key()
    old_ciphertext = EncryptionService(previous_key).encrypt("test-refresh-token-value")
    service = EncryptionService(current_key, previous_key)

    assert service.decrypt(old_ciphertext) == "test-refresh-token-value"
    assert service.needs_rotation(old_ciphertext) is True

    rotated = service.rotate(old_ciphertext)
    assert rotated.startswith(ENCRYPTION_PREFIX)
    assert rotated != old_ciphertext
    assert service.decrypt(rotated) == "test-refresh-token-value"
    assert service.needs_rotation(rotated) is False
    assert EncryptionService(current_key).decrypt(rotated) == "test-refresh-token-value"


def test_missing_configured_key_fails_only_when_factory_is_used(monkeypatch) -> None:
    """Importing is harmless, while requesting an unconfigured service fails safely."""

    class SettingsWithoutKey:
        encryption_key = None
        encryption_key_previous = SecretStr(key())

    monkeypatch.setattr("app.security.encryption.get_settings", lambda: SettingsWithoutKey())
    get_encryption_service.cache_clear()
    try:
        with pytest.raises(SecurityConfigurationError) as captured:
            get_encryption_service()
        assert "test" not in str(captured.value).lower()
    finally:
        get_encryption_service.cache_clear()
