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
    # 3.0 (все по умолчанию выключены, кроме DEVICE_CLEANUP_DRY_RUN)
    "AUTOPAY_ENABLED",
    "STARS_ENABLED",
    "GIFTS_ENABLED",
    "REFUND_24H_ENABLED",
    "PROMO_CODES_ENABLED",
    "DEVICES_UNLINK_ENABLED",
    "MAINTENANCE_AUTO_ENABLED",
    "GRACE_ENABLED",
    "DEVICE_CLEANUP_DRY_RUN",
    "TASK_DEVICE_CLEANUP_ENABLED",
    "TASK_OBHOD_LIFECYCLE_ENABLED",
    "TASK_AUTOPAY_ENABLED",
    "TASK_GRACE_ENABLED",
    "TASK_PANEL_HEALTH_ENABLED",
    "TASK_REMINDERS_ENABLED",
    "TASK_PANEL_SYNC_ENABLED",
    "OBHOD_ORPHAN_DEACTIVATE_ENABLED",
)

# Выключатели фоновых задач (ревью N5): непонятное значение = False (fail safe).
# На отладочном боте, который ходит в прод-панель, опечатка вроде «fasle» не
# должна запускать recovery, реконсилер и рассылки по проду.
_KILL_SWITCH_FIELDS = frozenset({
    "BACKGROUND_TASKS_ENABLED",
    "TASK_RECOVERY_ENABLED",
    "TASK_EXPIRY_NOTIFIER_ENABLED",
    "TASK_RECONCILER_ENABLED",
    "TASK_SUN718_REVERT_ENABLED",
    "TASK_BROADCAST_RESUME_ENABLED",
    "TASK_DEVICE_CLEANUP_ENABLED",
    "TASK_OBHOD_LIFECYCLE_ENABLED",
    "TASK_AUTOPAY_ENABLED",
    "TASK_GRACE_ENABLED",
    "TASK_PANEL_HEALTH_ENABLED",
    "TASK_REMINDERS_ENABLED",
    "TASK_PANEL_SYNC_ENABLED",
    "OBHOD_ORPHAN_DEACTIVATE_ENABLED",
})

# 3.0: числовые и опциональные поля, которые в .env.example записаны пустыми.
_EMPTY_IS_UNSET_FIELDS = (
    "ADMIN_CHAT_ID",
    "ADMIN_TOPIC_PAYMENTS",
    "ADMIN_TOPIC_REFUNDS",
    "ADMIN_TOPIC_PANEL",
    "ADMIN_TOPIC_ERRORS",
    "ADMIN_TOPIC_PROMO",
    "ADMIN_TOPIC_BROADCAST",
    "STARS_RATE",
    "RECONCILER_INTERVAL_S",
    "DEVICE_CLEANUP_DAYS",
    "GRACE_DAYS",
    "GRACE_DAILY_GB",
    "PANEL_WEBHOOK_SECRET",
    "GRACE_SQUAD",
    "CONNECT_ARTICLE_URL",
    "PRIVACY_URL",
    "SUPPORT_HANDLE",
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
    # Откуда принимать X-Real-IP. Nginx на хосте ходит на 127.0.0.1:8001, и в
    # контейнер соединение приходит с адреса шлюза docker-сети compose.
    # docker-gateway = этот адрес, определяется при старте из /proc/net/route.
    # Остальным адресам и заголовкам IP не верим.
    WEBHOOK_TRUSTED_PROXIES: str = "127.0.0.1/32,::1/128,docker-gateway"

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

    # Вход на сайт через бота (tg-login relay). SITE_INTERNAL_TOKEN пустой —
    # фича выключена, site_login отвечает заглушкой и на сайт не ходит.
    SITE_INTERNAL_URL: str = "http://vpn-site-api:8000"
    SITE_INTERNAL_TOKEN: Union[str, None] = None
    # Обратное направление: сайт -> бот, /internal/site/* в webhook-api.
    # Отдельный секрет (у сайта это BOT_API_TOKEN). Пустой = маршруты отвечают 503.
    BOT_INTERNAL_TOKEN: Union[str, None] = None

    # =====================================================================
    # Release 3.0. Все новое поведение за флагами, по умолчанию ВЫКЛЮЧЕНО.
    # Заголовки секций заморожены (Foundation); поле добавляет поток-владелец
    # в свою секцию через коммит оркестратора.
    # =====================================================================

    # --- 3.0 Foundation: админ-чат с темами (Notifier) ---
    # ADMIN_CHAT_ID пустой -> уведомления в личку каждому из ADMINS (как в 2.x).
    ADMIN_CHAT_ID: Union[int, None] = None
    ADMIN_TOPIC_PAYMENTS: Union[int, None] = None   # message_thread_id темы
    ADMIN_TOPIC_REFUNDS: Union[int, None] = None
    ADMIN_TOPIC_PANEL: Union[int, None] = None
    ADMIN_TOPIC_ERRORS: Union[int, None] = None
    ADMIN_TOPIC_PROMO: Union[int, None] = None
    ADMIN_TOPIC_BROADCAST: Union[int, None] = None

    # --- 3.0 Stream A: Money (оплата, автоплатеж, Stars, возвраты, подарки) ---
    AUTOPAY_ENABLED: bool = False
    STARS_ENABLED: bool = False
    STARS_RATE: float = 0.0          # рублей за 1 звезду (XTR); 0 = не настроено
    GIFTS_ENABLED: bool = False
    REFUND_24H_ENABLED: bool = False
    TASK_AUTOPAY_ENABLED: bool = False

    # --- 3.0 Stream B: Panel core (статус, устройства, реконсилер, обход) ---
    RECONCILER_INTERVAL_S: int = 600
    DEVICES_UNLINK_ENABLED: bool = False
    DEVICE_CLEANUP_DAYS: int = 30
    DEVICE_CLEANUP_DRY_RUN: bool = True
    TASK_DEVICE_CLEANUP_ENABLED: bool = False
    TASK_OBHOD_LIFECYCLE_ENABLED: bool = False
    # Обход без основного Pro («сироты»): решение владельца 23.09.2026 - не трогать.
    # false = джоба только считает и шлет сводку; true = выключает такие обходы.
    OBHOD_ORPHAN_DEACTIVATE_ENABLED: bool = False
    TASK_PANEL_SYNC_ENABLED: bool = False   # новый реконсилер по всем юзерам (pull-forward)

    # --- 3.0 Stream C: Panel events (вебхуки панели, напоминания, техработы, льготный период) ---
    PANEL_WEBHOOK_SECRET: Union[str, None] = None  # пустой = POST /webhook/remnawave отвечает 503
    MAINTENANCE_AUTO_ENABLED: bool = False
    GRACE_ENABLED: bool = False
    GRACE_SQUAD: Union[str, None] = None
    GRACE_DAYS: int = 3
    GRACE_DAILY_GB: int = 5
    TASK_GRACE_ENABLED: bool = False
    TASK_PANEL_HEALTH_ENABLED: bool = False
    TASK_REMINDERS_ENABLED: bool = False

    # --- 3.0 Stream D: User UI (меню, подключение, помощь) ---
    CONNECT_ARTICLE_URL: Union[str, None] = None
    PRIVACY_URL: Union[str, None] = None
    SUPPORT_HANDLE: Union[str, None] = None  # @username поддержки; пусто = ADMIN_SUPPORT_USERNAME

    # --- 3.0 Stream E: Growth & admin (промокоды, подарки, рассылки) ---
    PROMO_CODES_ENABLED: bool = False

    # --- 3.0 Stream F: Data & quality ---
    # (пока без полей)

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
        логируется и заменяется: для PROMO_* дефолтом поля, для выключателей
        фоновых задач (BACKGROUND_TASKS_ENABLED, TASK_*) значением False."""
        parsed = parse_bool(v)
        if parsed is None:
            from app.logger import logger
            if info.field_name in _KILL_SWITCH_FIELDS:
                logger.warning(
                    f"{info.field_name}={v!r}: не булево значение, задача ВЫКЛЮЧЕНА "
                    f"(fail safe). Исправьте .env: true или false"
                )
                return False
            default = cls.model_fields[info.field_name].default
            logger.warning(f"{info.field_name}={v!r}: не булево значение, используем дефолт {default}")
            return default
        return parsed

    @field_validator(*_EMPTY_IS_UNSET_FIELDS, mode="before")
    @classmethod
    def _empty_is_unset(cls, v, info):
        """Пустое значение в .env (ADMIN_CHAT_ID=) = поле не задано: None или
        дефолт поля, а не ValidationError на старте."""
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

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

    name: RECOVERY, EXPIRY_NOTIFIER, RECONCILER, SUN718_REVERT, BROADCAST_RESUME
    (2.x, по умолчанию включены) и 3.0: DEVICE_CLEANUP, OBHOD_LIFECYCLE, AUTOPAY,
    GRACE, PANEL_HEALTH, REMINDERS, PANEL_SYNC (по умолчанию выключены).
    Неизвестное имя = False (раньше AttributeError).
    """
    if not settings.BACKGROUND_TASKS_ENABLED:
        return False
    return bool(getattr(settings, f"TASK_{name.upper()}_ENABLED", False))
