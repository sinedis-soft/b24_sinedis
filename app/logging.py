"""Standard-library logging configuration with centralized secret redaction."""

import logging

from app.config import Settings
from app.security.redaction import SensitiveDataFilter


class _SafeStreamHandler(logging.StreamHandler):
    """Stream handler preconfigured with the application redaction filter."""

    def __init__(self) -> None:
        super().__init__()
        self.addFilter(SensitiveDataFilter())


def configure_logging(settings: Settings) -> None:
    """Configure root logging without emitting configuration values or secrets."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = _SafeStreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
