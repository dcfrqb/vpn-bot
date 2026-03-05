"""
Таблица тарифов (из audit).
basic: 1/3/6/12 → 99/249/499/899
premium: 1/3/6/12 → 199/549/999/1799
"""
from typing import List, Tuple

TARIFFS: List[Tuple[str, int, int]] = [
    ("basic", 1, 99),
    ("basic", 3, 249),
    ("basic", 6, 499),
    ("basic", 12, 899),
    ("premium", 1, 199),
    ("premium", 3, 549),
    ("premium", 6, 999),
    ("premium", 12, 1799),
]


def list_tariffs() -> List[Tuple[str, int, int]]:
    """Возвращает список (plan, months, amount)."""
    return list(TARIFFS)


def get_amount(plan: str, months: int) -> int:
    """Сумма в рублях для плана и периода."""
    for p, m, a in TARIFFS:
        if p == plan and m == months:
            return a
    return 0


def to_tariff_code(plan: str, months: int) -> str:
    """Код тарифа для Remnawave: BASIC_3M, PREMIUM_12M."""
    return f"{plan.upper()}_{months}M"
