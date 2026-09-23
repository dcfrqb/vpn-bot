"""Серверная цена покупки (фикс-раунд 1, ревью A-M1 / F1).

Правило «что этому юзеру можно купить и почем» — core/plans.quote_purchase.
Здесь только асинхронная обвязка: подтянуть last_plan юзера для продления
legacy-тарифа. Роутеры и create_payment зовут resolve_purchase_amount и сами
цену не считают.
"""
from typing import Optional

from app.core.plans import LEGACY_PLAN_CODES, quote_purchase
from app.logger import logger


async def resolve_purchase_amount(
    plan_code: Optional[str],
    period_months: Optional[int],
    user_id: int,
    *,
    allow_obhod_package: bool = False,
) -> int:
    """Цена покупки для юзера или 0 (продавать нельзя). См. core/plans.quote_purchase."""
    code = (plan_code or "").lower().strip()
    last_plan = None
    if code in LEGACY_PLAN_CODES:
        try:
            from app.services.users import get_user_last_plan
            last_plan = await get_user_last_plan(int(user_id))
        except Exception as e:
            # fail-closed: без last_plan legacy-тариф не продаем
            logger.warning(f"resolve_purchase_amount: get_user_last_plan failed user={user_id} err={e}")
    return quote_purchase(
        code, period_months, last_plan=last_plan, allow_obhod_package=allow_obhod_package
    )
