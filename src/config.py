from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str
    telegram_allowed_users: list[int] | str  # Accept string, convert to list
    anthropic_api_key: str | None = None
    config_path: str = "config/config.yaml"
    log_level: str = "INFO"

    @field_validator("telegram_allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, v: Any) -> list[int]:
        """Parse comma-separated string of user IDs into list of integers."""
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("TELEGRAM_ALLOWED_USERS cannot be empty")
            try:
                return [int(x.strip()) for x in v.split(",") if x.strip()]
            except ValueError:
                raise ValueError(
                    f"TELEGRAM_ALLOWED_USERS must be comma-separated integers, got: {v}"
                )
        raise ValueError(f"TELEGRAM_ALLOWED_USERS must be a string or list, got: {type(v)}")
