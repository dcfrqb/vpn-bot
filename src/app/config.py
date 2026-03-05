from pathlib import Path
from typing import Literal, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator


class Settings(BaseSettings):
    # legacy = с БД, YooKassa webhook; no_db = без БД, ручная модерация
    BOT_MODE: Literal["legacy", "no_db"] = "legacy"
    BOT_TOKEN: Union[str, None] = None
    ADMINS: Union[str, list[int], None] = None
    admin_ids: Union[str, None] = None
    ADMIN_SUPPORT_USERNAME: Union[str, None] = None  # Username админа для кнопки «Написать»

    DATABASE_URL: Union[str, None] = None
    REDIS_URL: Union[str, None] = None

    YOOKASSA_SHOP_ID: Union[str, None] = None
    YOOKASSA_API_KEY: Union[str, None] = None
    YOOKASSA_RETURN_URL: Union[AnyHttpUrl, None] = None
    YOOKASSA_WEBHOOK_SECRET: Union[str, None] = None

    REMNA_API_BASE: Union[AnyHttpUrl, None] = None
    REMNA_API_KEY: Union[str, None] = None
    remna_base_url: Union[AnyHttpUrl, None] = None
    remna_api_token: Union[str, None] = None
    REMNAWAVE_API_URL: Union[str, None] = None  # Алиас REMNA_API_BASE
    REMNAWAVE_API_TOKEN: Union[str, None] = None  # Алиас REMNA_API_KEY

    REMNA_USERNAME: Union[str, None] = None
    REMNA_PASSWORD: Union[str, None] = None

    TELEGRAM_WEBHOOK_URL: Union[str, None] = None  # URL для Telegram webhook
    YOOKASSA_WEBHOOK_URL: Union[str, None] = None  # URL для YooKassa webhook
    WEBHOOK_API_PORT: Union[int, None] = 8001  # Порт для FastAPI webhook сервера

    # Crypto Payment Configuration
    CRYPTO_USDT_TRC20_ADDRESS: Union[str, None] = None
    CRYPTO_NETWORK: Union[str, None] = "TRC20"  # TRC20, ERC20, etc.

    # Промокоды (slash-команды, NoDB)
    PROMO_SOLOKHIN_ENABLED: bool = True
    PROMO_ADMIN_ENABLED: bool = True

    # Ручная модерация платежей (обязателен только в no_db)
    PAYREQ_HMAC_SECRET: Union[str, None] = None
    payreq_hmac_secret: Union[str, None] = None
    LOG_DIR: str = "./logs"

    # Путь к .env файлу
    _base_path = Path("/opt/crs-vpn-bot/.env")
    _local_path = Path(__file__).resolve().parents[2] / ".env"
    _env_path = str(_base_path if _base_path.exists() else _local_path)
    model_config = SettingsConfigDict(env_file=_env_path, env_file_encoding="utf-8", extra="allow")

    @field_validator("ADMINS", mode="after")
    @classmethod
    def _parse_admins(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            parts = []
            for separator in [",", ";", " "]:
                if separator in s:
                    parts = [p.strip() for p in s.split(separator)]
                    break
            if not parts:
                parts = [s]
            return [int(p) for p in parts if p and p.isdigit()]
        return []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.ADMINS and self.admin_ids:
            self.ADMINS = self._parse_admins(self.admin_ids)
        if self.PAYREQ_HMAC_SECRET is None and getattr(self, "payreq_hmac_secret", None):
            self.PAYREQ_HMAC_SECRET = self.payreq_hmac_secret
        # Алиасы Remnawave: REMNAWAVE_* → REMNA_* (оба формата env работают)
        if self.REMNA_API_BASE is None and self.REMNAWAVE_API_URL:
            self.REMNA_API_BASE = self.REMNAWAVE_API_URL
        if self.REMNA_API_KEY is None and self.REMNAWAVE_API_TOKEN:
            self.REMNA_API_KEY = self.REMNAWAVE_API_TOKEN
        # Обратный маппинг: REMNA_* → REMNAWAVE_* для совместимости
        if self.REMNAWAVE_API_URL is None and self.REMNA_API_BASE:
            self.REMNAWAVE_API_URL = self.REMNA_API_BASE
        if self.REMNAWAVE_API_TOKEN is None and self.REMNA_API_KEY:
            self.REMNAWAVE_API_TOKEN = self.REMNA_API_KEY


settings = Settings()

def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMINS