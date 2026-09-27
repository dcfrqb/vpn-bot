"""Texts: Main menu and status card texts.

Owner stream: D (User UI).
Plain module-level constants or small pure functions returning str.
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.domain.models import SubscriptionState
from app.domain.plans import PLAN_CATALOG
from app.domain.texts import days_ru, fmt_date_msk, fmt_gb, h, to_msk, ui
from app.domain.texts.ui import E


def plan_display(plan_code: Optional[str]) -> str:
    """Human plan title, or the raw code if it is not in the catalog."""
    if not plan_code:
        return "—"
    info = PLAN_CATALOG.get(plan_code)
    return info["display"] if info else plan_code


def _expires_today(state: SubscriptionState, now: Optional[datetime] = None) -> bool:
    """The last day by the Moscow calendar (days_left() rounds up, so it never says 0)."""
    if state.expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return to_msk(state.expires_at).date() == to_msk(now).date()


def profile_section(telegram_id: int, name: str) -> ui.Block:
    return ui.block(ui.field("ID", telegram_id), ui.field("Имя", name), title="Профиль", emoji=E.PROFILE)


def subscription_section(state: SubscriptionState) -> ui.Block:
    """Status: active / grace / expired / none, with plan, days left, devices."""
    if state.active:
        plan = plan_display(state.plan_code) + (" (пробный)" if state.is_trial else "")
        lines = [ui.field("Тариф", plan)]
        if state.is_lifetime:
            lines.append("Срок: бессрочно")
        else:
            lines.append(f"{E.DATE} " + ui.field("До", fmt_date_msk(state.expires_at)))
            days = state.days_left()
            if days is not None:
                lines.append(f"{E.LEFT} Истекает сегодня" if days <= 0 or _expires_today(state)
                             else f"{E.LEFT} " + ui.field("Осталось", days_ru(days)))
        if state.device_limit:
            if state.devices_used is None:
                lines.append(f"Устройства: до {h(state.device_limit)}")
            else:
                lines.append(f"Устройства: {h(state.devices_used)} из {h(state.device_limit)}")
        return ui.block(*lines, title="Подписка активна", emoji=E.ACTIVE)

    if state.grace_until is not None:
        # Grace (review UX M5): the paid term ended, access is kept for a while.
        return ui.block(
            ui.field("Оплаченный срок закончился", fmt_date_msk(state.expires_at)),
            ui.field("Доступ сохранен до", fmt_date_msk(state.grace_until, with_time=True)) + " (МСК)",
            "Работает часть серверов, трафик в сутки ограничен.",
            "Продли, чтобы не потерять доступ.",
            title="Льготный период", emoji=E.GRACE,
        )

    if state.expires_at is not None:
        return ui.block(
            f"{E.DATE} " + ui.field("Истекла", fmt_date_msk(state.expires_at)),
            "Нажми «Подписка», чтобы продлить доступ.",
            title="Подписка истекла", emoji=E.EXPIRED,
        )

    return ui.block("Нажми «Подписка», чтобы подключить VPN.", title="Подписка не оформлена", emoji=E.NONE)


def obhod_section(state: SubscriptionState) -> Optional[ui.Block]:
    """Pro only, while the subscription is active."""
    if not state.active or (state.plan_code or "").lower() != "pro":
        return None
    if not state.obhod_active:
        return ui.block("Ссылка готовится, загляни чуть позже или нажми «Обновить».",
                        title="Обход блокировок", emoji=E.OBHOD)
    used = fmt_gb(state.obhod_used_bytes)
    limit = fmt_gb(state.obhod_limit_bytes) if state.obhod_limit_bytes else "без лимита"
    return ui.block(f"Использовано: {h(used)} из {h(limit)}", title="Обход блокировок", emoji=E.OBHOD)


TRIAL_OFFER = "Есть бесплатный пробный период на 5 дней, без карты."

STALE_NOTE = f"{E.WARN} Не удалось обновить данные с панели, показаны последние известные."


def main_menu_screen(telegram_id: int, name: str, state: SubscriptionState, *,
                     trial_available: bool = False) -> ui.Screen:
    hints = [STALE_NOTE if state.stale else "", TRIAL_OFFER if trial_available else ""]
    return ui.status(
        [profile_section(telegram_id, name), subscription_section(state), obhod_section(state)],
        hint="\n".join(x for x in hints if x),
    )


def main_menu_text(telegram_id: int, name: str, state: SubscriptionState, *, trial_available: bool = False) -> str:
    return main_menu_screen(telegram_id, name, state, trial_available=trial_available).html()
