from pathlib import Path
from typing import Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator


_LENIENT_BOOL_FIELDS = (
    "PROMO_SUN718_ENABLED",
    "PROMO_SOLOKHIN_ENABLED",
    "PROMO_TRIAL_ENABLED",
    "PROMO_ADMIN_ENABLED",
    "BACKGROUND_TASKS_ENABLED",
    "TASK_RECOVERY_ENABLED",
    "TASK_EXPIRY_NOTIFIER_ENABLED",
    "TASK_RECONCILER_ENABLED",
    "TASK_SUN718_REVERT_ENABLED",
    "TASK_BROADCAST_RESUME_ENABLED",
)

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


def parse_bool(v):
    """bool из env-значения или None, если значение непонятное."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().strip('"').strip("'").lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


class Settings(BaseSettings):
    BOT_TOKEN: Union[str, None] = None
    ADMINS: Union[str, list[int], int, None] = None
    admin_ids: Union[str, None] = None
    ADMIN_SUPPORT_USERNAME: Union[str, None] = None  # Username админа для кнопки «Написать»
    BLOCKED_TELEGRAM_IDS: Union[str, list[int], int, None] = None  # Заблокированные пользователи

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
    BOT_SECRET_TOKEN: Union[str, None] = None  # X-Telegram-Bot-Api-Secret-Token для webhook
    YOOKASSA_WEBHOOK_URL: Union[str, None] = None  # URL для YooKassa webhook
    WEBHOOK_API_PORT: Union[int, None] = 8001  # Порт для FastAPI webhook сервера
    # Откуда принимать X-Real-IP (nginx на хосте приходит в контейнер с адреса
    # docker-шлюза из 172.16.0.0/12). Остальным заголовкам IP не верим.
    WEBHOOK_TRUSTED_PROXIES: str = "127.0.0.1/32,::1/128,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16"

    # Базовый URL subscription-сервера (например https://sub.example.com).
    # Если задан — используется для domain override в subscription URL из API
    # и для построения URL из subscription token.
    SUBSCRIPTION_BASE_URL: Union[str, None] = None

    # HMAC секрет для подписи платежных запросов
    PAYREQ_HMAC_SECRET: Union[str, None] = None
    payreq_hmac_secret: Union[str, None] = None
    LOG_DIR: str = "./logs"

    # Промокод /sun718 (реферальный, Pro 5 дней).
    # OWNER_TG_ID — владелец рефералки, его собственные оплаты не считаются в /referral_stats.
    PROMO_SUN718_ENABLED: bool = True
    PROMO_SUN718_OWNER_TG_ID: Union[int, None] = None

    # Выключатели промо (хотфикс 2.1). Раньше читались через getattr(..., True),
    # но не были полями Settings, поэтому значение из .env игнорировалось.
    PROMO_SOLOKHIN_ENABLED: bool = True   # /solokhin — Premium 15 дней
    PROMO_TRIAL_ENABLED: bool = True      # /trial — Standard 5 дней
    PROMO_ADMIN_ENABLED: bool = True      # /admin от не-админа — запрос доступа

    # Фоновые задачи бота (фикс-раунд 1). Отладочный бот ходит в боевую панель
    # Remnawave, поэтому там все выключается одним BACKGROUND_TASKS_ENABLED=false.
    # Главный выключатель гасит все; TASK_* выключают по одной.
    BACKGROUND_TASKS_ENABLED: bool = True
    TASK_RECOVERY_ENABLED: bool = True          # recovery платежей (SubscriptionChecker)
    TASK_EXPIRY_NOTIFIER_ENABLED: bool = True   # «подписка истекает» юзерам
    TASK_RECONCILER_ENABLED: bool = True        # сверка подписок с Remnawave
    TASK_SUN718_REVERT_ENABLED: bool = True     # откат /sun718 по истечении
    TASK_BROADCAST_RESUME_ENABLED: bool = True  # дослать рассылки после рестарта

    # Не используются кодом, но есть в прод .env: объявлены, чтобы локальный
    # запуск с этим .env не падал (см. model_config extra).
    CRYPTO_USDT_TRC20_ADDRESS: Union[str, None] = None
    CRYPTO_NETWORK: Union[str, None] = None

    # Путь к .env файлу
    _base_path = Path("/opt/crs-vpn-bot/.env")
    _local_path = Path(__file__).resolve().parents[2] / ".env"
    _env_path = str(_base_path if _base_path.exists() else _local_path)
    # extra="ignore": лишние ключи в .env (POSTGRES_* для compose и т.п.) не
    # роняют старт. Из окружения контейнера неизвестные переменные и так
    # игнорировались; forbid ломал только локальный запуск с реальным .env.
    model_config = SettingsConfigDict(env_file=_env_path, env_file_encoding="utf-8", extra="ignore")

    @field_validator(*_LENIENT_BOOL_FIELDS, mode="before")
    @classmethod
    def _lenient_bool(cls, v, info):
        """true/false/1/0/yes/no/on/off без учета регистра. Непонятное значение
        не роняет старт (раньше ValidationError клал оба контейнера), а
        логируется и заменяется дефолтом поля."""
        default = cls.model_fields[info.field_name].default
        parsed = parse_bool(v)
        if parsed is None:
            from app.logger import logger
            logger.warning(f"{info.field_name}={v!r}: не булево значение, используем дефолт {default}")
            return default
        return parsed

    @field_validator("ADMINS", "BLOCKED_TELEGRAM_IDS", mode="after")
    @classmethod
    def _parse_id_list(cls, v):
        return parse_id_list(v)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.ADMINS and self.admin_ids:
            # Раньше тут был вызов несуществующего self._parse_admins (AttributeError).
            self.ADMINS = parse_id_list(self.admin_ids)
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


def parse_id_list(v):
    """ADMINS / BLOCKED_TELEGRAM_IDS / admin_ids: "1,2" | "1 2" | 1 | [1,2] -> [int]."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, int):
        return [v]
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


settings = Settings()

def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMINS


def task_enabled(name: str) -> bool:
    """Включена ли фоновая задача: BACKGROUND_TASKS_ENABLED и TASK_<NAME>_ENABLED.

    name: RECOVERY, EXPIRY_NOTIFIER, RECONCILER, SUN718_REVERT, BROADCAST_RESUME.
    """
    if not settings.BACKGROUND_TASKS_ENABLED:
        return False
    return bool(getattr(settings, f"TASK_{name.upper()}_ENABLED"))
