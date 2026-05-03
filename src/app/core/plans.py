"""
Единый справочник тарифов.

Источник правды по squad_name (для Remnawave), device_limit, ценам и фичам.
Любой код, которому нужно "по plan_code узнать squad/limit/цену" — ходит сюда.

UI-меню тарифов (`MENU_PLAN_CODES`) одинаковое для всех юзеров — это новые
тарифы lite/standard/pro. Legacy basic/premium остаются в каталоге для:
- продления существующих подписок через кнопку "🔄 Продлить" (см. services.users.get_user_last_plan);
- админ-выдачи и аналитики;
- корректного рендера "Мой тариф" у юзеров со старыми подписками.

Cohort:
- LEGACY_PLAN_CODES = ("basic", "premium") — для аналитики/админки.
- NEW_PLAN_CODES = ("lite", "standard", "pro") — для UI-меню.
- "trial" — служебный, всегда provisions через standard squad
  (см. services/remna_service.py TARIFF_TO_DAYS::trial_standard_10d).
"""
from datetime import datetime, timezone
from typing import Optional

from app.logger import logger


# =============================================================================
# Cohort cutoff
# =============================================================================
# Юзер считается legacy если у него есть Payment(status='succeeded', provider != 'promo')
# с paid_at/created_at < LEGACY_CUTOFF. См. services/users.py::is_legacy_user.
LEGACY_CUTOFF = datetime(2026, 5, 4, 0, 0, 0, tzinfo=timezone.utc)


# =============================================================================
# Plan catalog — единственный источник правды
# =============================================================================
# Поля:
#   squad         — имя squad'а в Remnawave (см. get_squad_by_name)
#   device_limit  — hwidDeviceLimit для Remna user.update
#   display       — что показывать юзеру (заголовок тарифа)
#   prices        — RUB по периодам {months: amount}
#   features      — список строк для рендера экрана выбора/деталей
PLAN_CATALOG: dict[str, dict] = {
    # --- LEGACY (только для cohort=legacy, не показываем новым) ---
    "basic": {
        "squad": "basic",
        "device_limit": 5,
        "display": "Базовый тариф",
        "prices": {1: 99, 3: 249, 6: 499, 12: 899},
        "features": [
            "Неограниченный трафик и скорость",
            "Поддержка разных устройств",
            "YouTube без рекламы",
            "Сервер NL",
            "Подключение до 5 устройств",
        ],
    },
    "premium": {
        "squad": "premium",
        "device_limit": 15,
        "display": "Премиум тариф",
        "prices": {1: 199, 3: 549, 6: 999, 12: 1799},
        "features": [
            "Неограниченный трафик и скорость",
            "Поддержка разных устройств",
            "YouTube без рекламы",
            "Серверы NL, USA, FR",
            "Подключение до 15 устройств",
        ],
    },

    # --- NEW (только для cohort=new) ---
    "lite": {
        "squad": "lite",
        "device_limit": 2,
        "display": "Lite",
        "prices": {1: 129, 3: 329, 6: 599, 12: 1099},
        "features": [
            "Неограниченный трафик и скорость",
            "YouTube без рекламы",
            "Серверы: NL",
            "Подключение до 2 устройств",
        ],
    },
    "standard": {
        "squad": "standard",
        "device_limit": 5,
        "display": "Standard",
        "prices": {1: 249, 3: 649, 6: 1199, 12: 2199},
        "features": [
            "Неограниченный трафик и скорость",
            "YouTube без рекламы",
            "Серверы: NL + FR",
            "Подключение до 5 устройств",
        ],
    },
    "pro": {
        "squad": "pro",
        "device_limit": 10,
        "display": "Pro",
        "prices": {1: 449, 3: 1199, 6: 2199, 12: 3999},
        "features": [
            "Неограниченный трафик и скорость",
            "YouTube без рекламы",
            "Все серверы: NL, FR, USA, ESP",
            "Обход блокировок",
            "Подключение до 10 устройств",
        ],
    },

    # --- TRIAL (служебный — squad/limit берутся через TARIFF_TO_DAYS) ---
    "trial": {
        "squad": "standard",  # новые триалы → standard squad
        "device_limit": 5,
        "display": "Пробный период",
        "prices": {},  # триал не покупается
        "features": [],
    },
}

LEGACY_PLAN_CODES: tuple[str, ...] = ("basic", "premium")
NEW_PLAN_CODES: tuple[str, ...] = ("lite", "standard", "pro")
# Алиас — то, что показываем в UI-меню всем юзерам.
MENU_PLAN_CODES: tuple[str, ...] = NEW_PLAN_CODES
VALID_PLAN_CODES: frozenset[str] = frozenset(PLAN_CATALOG.keys())

# Для обратной совместимости — старый PLAN_NAMES dict (используется helpers/get_plan_name).
PLAN_NAMES: dict[str, str] = {code: meta["display"] for code, meta in PLAN_CATALOG.items()}
PLAN_NAME_FALLBACK = "Тариф (обновите меню)"


# =============================================================================
# Lookup helpers
# =============================================================================


def _lookup(plan_code: Optional[str]) -> Optional[dict]:
    if not plan_code:
        return None
    return PLAN_CATALOG.get(str(plan_code).lower().strip())


def get_plan_name(plan_code: Optional[str]) -> str:
    """Человекочитаемое имя по plan_code, с fallback'ом."""
    meta = _lookup(plan_code)
    if meta:
        return meta["display"]
    if plan_code:
        logger.warning(
            f"Неизвестный plan_code: {plan_code!r}, "
            f"используем fallback. Добавьте в PLAN_CATALOG при необходимости."
        )
    return PLAN_NAME_FALLBACK


def is_valid_plan_code(plan_code: Optional[str]) -> bool:
    return _lookup(plan_code) is not None


def get_plan_squad(plan_code: Optional[str]) -> Optional[str]:
    """Имя squad'а в Remnawave для plan_code, или None."""
    meta = _lookup(plan_code)
    return meta["squad"] if meta else None


def get_plan_device_limit(plan_code: Optional[str]) -> int:
    """hwidDeviceLimit для plan_code. Дефолт = 5 (как старый basic)."""
    meta = _lookup(plan_code)
    return int(meta["device_limit"]) if meta else 5


def get_plan_price(plan_code: Optional[str], months: int) -> int:
    """Цена в RUB. 0 если plan_code или период невалидны."""
    meta = _lookup(plan_code)
    if not meta:
        return 0
    return int(meta["prices"].get(int(months), 0))


def get_plan_features(plan_code: Optional[str]) -> list[str]:
    """Копия списка фич для plan_code, [] для неизвестных."""
    meta = _lookup(plan_code)
    return list(meta["features"]) if meta else []


