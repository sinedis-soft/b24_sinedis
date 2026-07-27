"""Safe exception hierarchy for Bitrix24 REST calls."""

from collections.abc import Mapping
from typing import Any


class BitrixError(Exception):
    """Base exception for Bitrix integration errors."""


class BitrixConfigurationError(BitrixError):
    """Bitrix24 client configuration is invalid."""


class BitrixTransportError(BitrixError):
    """A Bitrix24 request failed before a response was received."""

    def __init__(self, *, method: str) -> None:
        self.method = method
        super().__init__("Bitrix24 REST transport failed")


class BitrixTimeoutError(BitrixTransportError):
    """A Bitrix24 request exceeded a configured timeout."""


class BitrixInvalidResponseError(BitrixError):
    """Bitrix24 returned a response outside the documented contract."""

    def __init__(self, *, method: str, http_status: int) -> None:
        self.method = method
        self.http_status = http_status
        super().__init__("Bitrix24 REST returned an invalid response")


class BitrixAPIError(BitrixError):
    """Bitrix24 returned a structured or HTTP API error."""

    def __init__(
        self,
        *,
        code: str,
        http_status: int,
        method: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
        error_description: str | None = None,
        time: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.http_status = http_status
        self.method = method
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.error_description = error_description
        self.time = time
        super().__init__(f"Bitrix24 REST call failed: {code}")


class BitrixAuthenticationError(BitrixAPIError):
    """The supplied Bitrix24 access token is missing, invalid, or expired."""


class BitrixPermissionError(BitrixAPIError):
    """The authenticated principal cannot perform the REST operation."""


class BitrixRateLimitError(BitrixAPIError):
    """Bitrix24 throttled the REST operation."""


class BitrixTemporaryError(BitrixAPIError):
    """The REST failure may be retried by a policy-aware caller."""


class BitrixPermanentError(BitrixAPIError):
    """The REST failure should not be retried without changing the request or configuration."""


class BitrixOAuthError(BitrixError):
    """Base safe error for the OAuth token lifecycle."""


class BitrixOAuthConfigurationError(BitrixOAuthError):
    """OAuth settings or portal authentication state are invalid."""


class BitrixOAuthTemporaryError(BitrixOAuthError):
    """The authorization service failed temporarily."""


class BitrixOAuthRefreshRejectedError(BitrixOAuthError):
    """The authorization service permanently rejected refresh credentials."""
