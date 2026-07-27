"""Safe exceptions for security configuration failures."""


class SecurityConfigurationError(Exception):
    """Security configuration is missing or invalid."""
