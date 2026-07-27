"""Cryptographic fingerprints and verification for Bitrix24 callback tokens."""

import base64
import binascii
import hashlib
import hmac
import re

from app.config import get_settings
from app.security.exceptions import SecurityConfigurationError

APPLICATION_TOKEN_HASH_PREFIX = "hmac-sha256:v1:"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_HMAC_CONTEXT = b"sinedis-bitrix24-application-token-v1"


def event_token_fingerprint(event_token: str) -> str:
    """Return a deterministic full SHA-256 fingerprint for idempotency."""
    return hashlib.sha256(event_token.encode("utf-8")).hexdigest()


def _configured_hmac_keys() -> tuple[bytes, ...]:
    settings = get_settings()
    configured = [settings.encryption_key, settings.encryption_key_previous]
    keys: list[bytes] = []
    for secret in configured:
        if secret is None:
            continue
        try:
            raw_key = base64.b64decode(
                secret.get_secret_value().encode("ascii"), altchars=b"-_", validate=True
            )
        except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
            raise SecurityConfigurationError("Token verification key is invalid") from exc
        if len(raw_key) != 32:
            raise SecurityConfigurationError("Token verification key is invalid")
        keys.append(hmac.new(raw_key, _HMAC_CONTEXT, hashlib.sha256).digest())
    if not keys:
        raise SecurityConfigurationError("Token verification key is not configured")
    return tuple(keys)


def hash_application_token(token: str) -> str:
    """Create a versioned keyed hash without storing the source token."""
    if not token:
        raise ValueError("Application token must not be empty")
    digest = hmac.new(_configured_hmac_keys()[0], token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{APPLICATION_TOKEN_HASH_PREFIX}{digest}"


def verify_application_token(token: str, stored_hash: str) -> bool:
    """Verify a token in constant time against current and previous derived keys."""
    if not token or not stored_hash.startswith(APPLICATION_TOKEN_HASH_PREFIX):
        return False
    digest = stored_hash.removeprefix(APPLICATION_TOKEN_HASH_PREFIX)
    if _HEX_DIGEST.fullmatch(digest) is None:
        return False
    token_bytes = token.encode("utf-8")
    return any(
        hmac.compare_digest(hmac.new(key, token_bytes, hashlib.sha256).hexdigest(), digest)
        for key in _configured_hmac_keys()
    )
