"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings with safe local-development defaults."""

    app_env: str = "development"
    app_name: str = "SINEDIS Bitrix24 Automation"
    app_version: str = "0.1.0"
    app_host: str = "0.0.0.0"
    app_port: int = 8010
    app_base_url: str = "http://localhost:8010"
    log_level: str = "INFO"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://bitrix_app:change_me@localhost:5432/bitrix_automation"
    )

    bitrix_client_id: str | None = None
    bitrix_client_secret: SecretStr | None = None
    encryption_key: SecretStr | None = None
    encryption_key_previous: SecretStr | None = None
    admin_api_token: SecretStr | None = None

    worker_poll_interval_seconds: float = Field(default=1, gt=0)
    worker_batch_size: int = Field(default=50, ge=1)
    worker_lock_timeout_seconds: int = Field(default=120, ge=1)
    worker_recovery_interval_seconds: float = Field(default=60, gt=0)
    worker_retry_base_seconds: float = Field(default=5, gt=0)
    worker_retry_max_seconds: float = 300
    worker_retry_jitter_seconds: float = Field(default=2, ge=0)
    short_pause_min_seconds: int = Field(default=1, ge=1)
    short_pause_max_seconds: int = 3600
    bitrix_http_connect_timeout_seconds: float = Field(default=5, gt=0)
    bitrix_http_read_timeout_seconds: float = Field(default=20, gt=0)
    bitrix_http_write_timeout_seconds: float = Field(default=20, gt=0)
    bitrix_http_pool_timeout_seconds: float = Field(default=5, gt=0)
    bitrix_http_max_connections: int = Field(default=20, ge=1)
    bitrix_oauth_expiry_skew_seconds: int = Field(default=60, ge=0)
    bitrix_oauth_connect_timeout_seconds: float = Field(default=5, gt=0)
    bitrix_oauth_read_timeout_seconds: float = Field(default=20, gt=0)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator(
        "bitrix_client_secret",
        "encryption_key",
        "encryption_key_previous",
        "admin_api_token",
        mode="before",
    )
    @classmethod
    def empty_secret_is_unconfigured(cls, value: object) -> object:
        """Treat empty environment entries as absent optional secrets."""
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_short_pause_range(self) -> "Settings":
        """Ensure the configured upper delay bound is not below the lower bound."""
        if self.short_pause_max_seconds < self.short_pause_min_seconds:
            raise ValueError("SHORT_PAUSE_MAX_SECONDS must not be below the minimum")
        if self.worker_retry_max_seconds < self.worker_retry_base_seconds:
            raise ValueError("WORKER_RETRY_MAX_SECONDS must not be below the base")
        return self

    @property
    def service_name(self) -> str:
        """Return the stable machine-readable service identifier."""
        return "sinedis-bitrix24-automation"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings; clear the cache when overriding environment in tests."""
    return Settings()
