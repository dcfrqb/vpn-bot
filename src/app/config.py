from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMINS: list[int] = []

    DATABASE_URL: str
    REDIS_URL: str

    YOOKASSA_SHOP_ID: str
    YOOKASSA_API_KEY: str
    YOOKASSA_RETURN_URL: AnyHttpUrl
    YOOKASSA_WEBHOOK_SECRET: str

    REMNA_API_BASE: AnyHttpUrl
    REMNA_API_KEY: str

    WEBHOOK_URL: str | None = None
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()