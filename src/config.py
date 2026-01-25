from typing import Any, Tuple, Type

from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import EnvSettingsSource


class CustomEnvSettingsSource(EnvSettingsSource):
    """Custom environment settings source that handles comma-separated lists."""

    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        # Handle comma-separated list for telegram_allowed_users
        if field_name == "telegram_allowed_users" and isinstance(value, str):
            if not value.strip():
                raise ValueError("TELEGRAM_ALLOWED_USERS cannot be empty")
            try:
                return [int(x.strip()) for x in value.split(",") if x.strip()]
            except ValueError as e:
                raise ValueError(f"TELEGRAM_ALLOWED_USERS must be comma-separated integers, got: {value}") from e
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    telegram_bot_token: str
    telegram_allowed_users: list[int]
    anthropic_api_key: str | None = None
    config_path: str = "config/config.yaml"
    log_level: str = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            CustomEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )
