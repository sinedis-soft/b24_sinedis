"""Stable public interface for Bitrix24 REST integration."""

from app.bitrix.client import BitrixClient, BitrixResponse
from app.bitrix.exceptions import (
    BitrixAPIError,
    BitrixAuthenticationError,
    BitrixConfigurationError,
    BitrixError,
    BitrixInvalidResponseError,
    BitrixOAuthConfigurationError,
    BitrixOAuthError,
    BitrixOAuthRefreshRejectedError,
    BitrixOAuthTemporaryError,
    BitrixPermanentError,
    BitrixPermissionError,
    BitrixRateLimitError,
    BitrixTemporaryError,
    BitrixTimeoutError,
    BitrixTransportError,
)
from app.bitrix.oauth import BitrixOAuthService, OAuthRefreshResult, PortalOAuthService
from app.bitrix.payload import BitrixAuthPayload, BitrixEventPayload, BitrixPayloadError

__all__ = [
    "BitrixAPIError",
    "BitrixAuthPayload",
    "BitrixAuthenticationError",
    "BitrixClient",
    "BitrixConfigurationError",
    "BitrixError",
    "BitrixEventPayload",
    "BitrixInvalidResponseError",
    "BitrixOAuthConfigurationError",
    "BitrixOAuthError",
    "BitrixOAuthRefreshRejectedError",
    "BitrixOAuthService",
    "BitrixOAuthTemporaryError",
    "BitrixPayloadError",
    "BitrixPermanentError",
    "BitrixPermissionError",
    "BitrixRateLimitError",
    "BitrixResponse",
    "BitrixTemporaryError",
    "BitrixTimeoutError",
    "BitrixTransportError",
    "OAuthRefreshResult",
    "PortalOAuthService",
]
