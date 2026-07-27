"""Versioned authenticated encryption for secrets stored by the application."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings
from app.security.exceptions import SecurityConfigurationError

ENCRYPTION_PREFIX = "fernet:v1:"


class EncryptionError(Exception):
    """Base encryption error."""


class InvalidEncryptionKeyError(EncryptionError):
    """Encryption key has an invalid format."""


class DecryptionError(EncryptionError):
    """Encrypted value cannot be decrypted."""


class EncryptionService:
    """Encrypt with the current Fernet key and decrypt with current or previous keys."""

    def __init__(self, current_key: str, previous_key: str | None = None) -> None:
        self._current = self._build_fernet(current_key)
        fernets = [self._current]
        if previous_key is not None:
            fernets.append(self._build_fernet(previous_key))
        self._keyring = MultiFernet(fernets)

    @staticmethod
    def _build_fernet(key: str) -> Fernet:
        try:
            return Fernet(key.encode("ascii"))
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise InvalidEncryptionKeyError("Encryption key has an invalid format") from exc

    @staticmethod
    def _token(ciphertext: str) -> bytes:
        if not isinstance(ciphertext, str) or not ciphertext.startswith(ENCRYPTION_PREFIX):
            raise DecryptionError("Encrypted value has an unsupported format")
        token = ciphertext.removeprefix(ENCRYPTION_PREFIX)
        if not token:
            raise DecryptionError("Encrypted value cannot be decrypted")
        try:
            return token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise DecryptionError("Encrypted value cannot be decrypted") from exc

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string using the current key and versioned envelope."""
        if not isinstance(plaintext, str):
            raise EncryptionError("Plaintext must be a string")
        token = self._current.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENCRYPTION_PREFIX}{token}"

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a supported envelope without leaking its contents in errors."""
        token = self._token(ciphertext)
        try:
            return self._keyring.decrypt(token).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise DecryptionError("Encrypted value cannot be decrypted") from exc

    def needs_rotation(self, ciphertext: str) -> bool:
        """Return whether only a non-current configured key can decrypt the value."""
        token = self._token(ciphertext)
        try:
            self._current.decrypt(token)
            return False
        except InvalidToken:
            try:
                self._keyring.decrypt(token)
            except InvalidToken as exc:
                raise DecryptionError("Encrypted value cannot be decrypted") from exc
            return True

    def rotate(self, ciphertext: str) -> str:
        """Re-encrypt an envelope with the current key while preserving its timestamp."""
        token = self._token(ciphertext)
        try:
            rotated = self._keyring.rotate(token).decode("ascii")
        except InvalidToken as exc:
            raise DecryptionError("Encrypted value cannot be decrypted") from exc
        return f"{ENCRYPTION_PREFIX}{rotated}"


@lru_cache
def get_encryption_service() -> EncryptionService:
    """Build the encryption service lazily from protected settings."""
    settings = get_settings()
    if settings.encryption_key is None:
        raise SecurityConfigurationError("Encryption key is not configured")
    current_key = settings.encryption_key.get_secret_value()
    previous_key = (
        settings.encryption_key_previous.get_secret_value()
        if settings.encryption_key_previous is not None
        else None
    )
    return EncryptionService(current_key=current_key, previous_key=previous_key)
