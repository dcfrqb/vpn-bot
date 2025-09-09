from pathlib import Path
from typing import Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator


class Settings(BaseSettings):
    # Required minimal
    BOT_TOKEN: str
    ADMINS: list[int] = []

    # Optional for minimal startup
    DATABASE_URL: Union[str, None] = None
    REDIS_URL: Union[str, None] = None

    YOOKASSA_SHOP_ID: Union[str, None] = None
    YOOKASSA_API_KEY: Union[str, None] = None
    YOOKASSA_RETURN_URL: Union[AnyHttpUrl, None] = None
    YOOKASSA_WEBHOOK_SECRET: Union[str, None] = None

    REMNA_API_BASE: Union[AnyHttpUrl, None] = None
    REMNA_API_KEY: Union[str, None] = None

    WEBHOOK_URL: Union[str, None] = None

    # Resolve .env relative to repo root to avoid CWD issues
    _env_path = str(Path(__file__).resolve().parents[2] / ".env")
    model_config = SettingsConfigDict(env_file=_env_path, env_file_encoding="utf-8")

    @field_validator("ADMINS", mode="before")
    @classmethod
    def _parse_admins(cls, v):
        if v is None or isinstance(v, list):
            return v or []
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            parts = [p.strip() for p in s.replace(";", ",").split(",")]
            return [int(p) for p in parts if p]
        return v


settings = Settings()