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
  (см. services/remna_service.py TARIFF_TO_DAYS::trial_standard_5d, 5 дней).
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
            "Неограниченный трафик и скорость (не считая обход)",
            "YouTube без рекламы",
            "Все серверы: NL, FR, USA, ESP",
            "Обход блокировок (100 ГБ/мес)",
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


# =============================================================================
# Обход блокировок (вторая подписка с лимитом трафика)
# =============================================================================
# Архитектура «две подписки»: при оплате тарифа Pro клиенту выдается ДВА
# Remnawave-юзера одной оплатой:
#   main  — зарубежный выход, тарифный сквад (как сейчас), без лимита трафика;
#   obhod — отдельный сквад OBHOD_SQUAD_NAME, базовый кап OBHOD_BASE_LIMIT_GB
#           в месяц (trafficLimitStrategy=MONTH), срок = срок Pro.
#
# Обход доступен ТОЛЬКО в тарифе Pro. lite/standard/basic/premium его не получают.
# Пакеты «Обход +трафик» поднимают месячный кап на ТОМ ЖЕ obhod-юзере
# (не создают третью сущность / новую ссылку).

# Тарифы, которым полагается обход. Сейчас только Pro.
OBHOD_ELIGIBLE_PLAN_CODES: frozenset[str] = frozenset({"pro"})

# Имя сквада обхода в Remnawave (см. get_squad_by_name).
OBHOD_SQUAD_NAME: str = "obhod"

# Базовый месячный кап трафика обхода для Pro, в гигабайтах.
# TODO(заказчик): подтвердить итоговый размер базового капа (предв. 100 ГБ).
OBHOD_BASE_LIMIT_GB: int = 100

# Стратегия лимита трафика в Remnawave: помесячный сброс.
OBHOD_TRAFFIC_LIMIT_STRATEGY: str = "MONTH"

# 1 ГБ в байтах (Remnawave принимает trafficLimitBytes в байтах).
GIB_IN_BYTES: int = 1024 * 1024 * 1024


def obhod_base_limit_bytes() -> int:
    """Базовый месячный кап обхода в байтах."""
    return OBHOD_BASE_LIMIT_GB * GIB_IN_BYTES


def is_obhod_eligible_plan(plan_code: Optional[str]) -> bool:
    """True если тариф дает обход (сейчас только Pro)."""
    if not plan_code:
        return False
    return str(plan_code).lower().strip() in OBHOD_ELIGIBLE_PLAN_CODES


# Каталог платных пакетов «Обход +трафик».
# Покупаются только при активном Pro. Поднимают месячный кап obhod-юзера на
# оплаченный период; по истечении пакета кап откатывается к базовым 100 ГБ.
#
# Поля пакета:
#   limit_gb       — итоговый месячный кап (НЕ добавка к базовому, а целевой кап)
#                    пока пакет активен;
#   period_months  — на сколько месяцев продается пакет;
#   price          — цена в RUB. TODO(заказчик): проставить финальные цены.
#   display        — заголовок для UI.
#
# ВНИМАНИЕ: цены — плейсхолдеры (0). Продукт НЕ должен продаваться, пока
# заказчик не проставит реальные цены (см. is_obhod_package_purchasable).
OBHOD_PACKAGE_CATALOG: dict[str, dict] = {
    "obhod_250": {
        "limit_gb": 250,
        "period_months": 1,
        "price": 599,  # решено заказчиком 2026-07-01 (2.40₽/ГБ, себест. 1.36); цена плавающая
        "display": "Обход 250 ГБ / мес",
    },
    "obhod_500": {
        "limit_gb": 500,
        "period_months": 1,
        "price": 1199,  # решено заказчиком 2026-07-01 (2.40₽/ГБ, себест. 1.36); цена плавающая
        "display": "Обход 500 ГБ / мес",
    },
}

OBHOD_PACKAGE_CODES: tuple[str, ...] = tuple(OBHOD_PACKAGE_CATALOG.keys())


def get_obhod_package(package_code: Optional[str]) -> Optional[dict]:
    """Метаданные пакета обхода по коду, или None."""
    if not package_code:
        return None
    return OBHOD_PACKAGE_CATALOG.get(str(package_code).lower().strip())


def is_obhod_package_code(code: Optional[str]) -> bool:
    return get_obhod_package(code) is not None


def is_obhod_package_purchasable(package_code: Optional[str]) -> bool:
    """True если пакет существует И у него проставлена реальная (ненулевая) цена.

    Защита от продажи пакета с плейсхолдер-ценой 0 — пока заказчик не заполнит
    OBHOD_PACKAGE_CATALOG, кнопки покупки не показываются.
    """
    meta = get_obhod_package(package_code)
    return bool(meta) and int(meta.get("price", 0)) > 0


def get_obhod_package_limit_bytes(package_code: Optional[str]) -> Optional[int]:
    """Целевой месячный кап пакета в байтах, или None для неизвестного пакета."""
    meta = get_obhod_package(package_code)
    if not meta:
        return None
    return int(meta["limit_gb"]) * GIB_IN_BYTES

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




# =============================================================================
# Серверная цена покупки (хотфикс 2.1: цена никогда не берется из callback_data)
# =============================================================================


def get_expected_amount(plan_code: Optional[str], period_months: Optional[int]) -> int:
    """Цена в RUB, которую сервер ждет за покупку plan_code/period_months.

    - Тариф из PLAN_CATALOG: цена периода (0 если период не продается, например trial).
    - Пакет обхода: цена пакета (период пакета фиксирован каталогом).
    - Иначе 0 (неизвестная покупка, продавать нельзя).
    """
    package = get_obhod_package(plan_code)
    if package is not None:
        return int(package.get("price") or 0)
    try:
        months = int(period_months) if period_months is not None else 0
    except (TypeError, ValueError):
        return 0
    if months <= 0:
        return 0
    return get_plan_price(plan_code, months)


def quote_purchase(
    plan_code: Optional[str],
    period_months: Optional[int],
    *,
    last_plan: Optional[str] = None,
    allow_obhod_package: bool = False,
) -> int:
    """Цена покупки в RUB или 0, если такую покупку продавать нельзя.

    Единственное правило «что можно купить» (ревью A-M1 / F1):
      - тарифы из меню (MENU_PLAN_CODES) по цене каталога;
      - legacy-тариф (basic/premium) только его владельцу: last_plan юзера
        совпадает с plan_code (кнопка «Продлить» ведет туда же);
      - пакет обхода, если он продается (is_obhod_package_purchasable) и
        вызывающий явно разрешил пакеты (allow_obhod_package: только экран
        покупки пакета, который сам проверяет активный Pro; кнопка тарифа
        pay_yookassa_* пакет не продает);
      - все остальное (trial, неизвестные коды, непродаваемые периоды) -> 0.
    Проверка «есть активный Pro» для пакета обхода живет у вызывающего (UI),
    здесь только цена и каталог.
    """
    code = (plan_code or "").lower().strip()
    if is_obhod_package_code(code):
        if allow_obhod_package and is_obhod_package_purchasable(code):
            return get_expected_amount(code, period_months)
        return 0
    if code in MENU_PLAN_CODES or (code in LEGACY_PLAN_CODES and last_plan == code):
        return get_expected_amount(code, period_months)
    return 0


def amounts_match(paid: float, expected: float) -> bool:
    """Сравнение сумм в рублях с допуском на копейки float."""
    try:
        return abs(float(paid) - float(expected)) < 0.01
    except (TypeError, ValueError):
        return False
