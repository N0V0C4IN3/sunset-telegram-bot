from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., min_length=1)
    database_url: str = Field(..., min_length=1)
    location_encryption_key: str = ""
    sunsethue_api_key: str = ""
    sunsethue_fallback_api_key: str = ""
    notification_scan_interval_minutes: int = Field(30, ge=1, le=240)
    default_notification_threshold: int = Field(70, ge=0, le=100)
    default_notification_lead_time_minutes: int = Field(90, ge=15, le=180)
    forecast_cache_ttl_minutes: int = Field(300, ge=1, le=1440)
    forecast_cache_retention_days: int = Field(7, ge=1, le=90)
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
