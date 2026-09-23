"""Texts: Main menu and status card texts.

Owner stream: D (User UI).
Plain module-level constants or small pure functions returning str.
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from typing import Optional

from app.domain.models import SubscriptionState
from app.domain.plans import PLAN_CATALOG
from app.domain.texts import days_ru, fmt_date_msk, fmt_gb, h


def plan_display(plan_code: Optional[str]) -> str:
    """Human plan title, or the raw code if it is not in the catalog."""
    if not plan_code:
        return "—"
    info = PLAN_CATALOG.get(plan_code)
    return info["display"] if info else plan_code


def profile_block(telegram_id: int, name: str) -> str:
    return (
        "👤 <b>Профиль</b>\n"
        "<blockquote>"
        f"ID: {h(telegram_id)}\n"
        f"Имя: {h(name)}"
        "</blockquote>"
    )


def _obhod_block(state: SubscriptionState) -> str:
    if (state.plan_code or "").lower() != "pro":
        return ""
    if not state.obhod_active:
        return "\n🛡 Обход: готовится, загляни чуть позже или нажми «Обновить»."
    used = fmt_gb(state.obhod_used_bytes)
    limit = fmt_gb(state.obhod_limit_bytes) if state.obhod_limit_bytes else "без лимита"
    return f"\n🛡 Обход: {h(used)} из {h(limit)}"


def subscription_block(state: SubscriptionState) -> str:
    """Status card: active / grace / expired / none, with plan, days left, devices, obhod."""
    if state.active:
        days = state.days_left()
        plan = plan_display(state.plan_code) + (" (пробный)" if state.is_trial else "")
        inner = [f"Тариф: {h(plan)}"]
        if state.is_lifetime:
            inner.append("Срок: бессрочно")
        else:
            inner.append(f"📅 До: {h(fmt_date_msk(state.expires_at))}")
            if days is not None:
                inner.append("⏳ Истекает сегодня" if days <= 0 else f"⏳ Осталось: {h(days_ru(days))}")
        if state.device_limit:
            if state.devices_used is None:
                inner.append(f"Устройства: до {h(state.device_limit)}")
            else:
                inner.append(f"Устройства: {h(state.devices_used)} из {h(state.device_limit)}")
        text = "<b>🟢 Подписка активна</b>\n<blockquote>" + "\n".join(inner) + "</blockquote>"
        return text + _obhod_block(state)

    if state.grace_until is not None:
        # Grace (review UX M5): the paid term ended, access is kept for a while.
        return (
            "<b>🟡 Льготный период</b>\n"
            "<blockquote>"
            f"Оплаченный срок закончился: {h(fmt_date_msk(state.expires_at))}\n"
            f"Доступ сохранен до {h(fmt_date_msk(state.grace_until, with_time=True))} (МСК): "
            "часть серверов и ограниченный трафик в сутки.\n"
            "Продли, чтобы не потерять доступ."
            "</blockquote>"
        )

    if state.expires_at is not None:
        return (
            "<b>🔴 Подписка истекла</b>\n"
            "<blockquote>"
            f"Истекла: {h(fmt_date_msk(state.expires_at))}\n"
            "Нажми «Подписка», чтобы продлить доступ."
            "</blockquote>"
        )

    return (
        "<b>💡 Подписка не оформлена</b>\n"
        "<blockquote>Нажми «Подписка» для активации VPN.</blockquote>"
    )


TRIAL_OFFER = "Есть бесплатный пробный период на 5 дней, без карты."

STALE_NOTE = "\n\n⚠️ Не удалось обновить данные с панели, показаны последние известные."


def main_menu_text(telegram_id: int, name: str, state: SubscriptionState, *, trial_available: bool = False) -> str:
    parts = [profile_block(telegram_id, name), "", subscription_block(state)]
    if state.stale:
        parts.extend(["", STALE_NOTE.strip()])
    if trial_available:
        parts.append("")
        parts.append(TRIAL_OFFER)
    return "\n".join(parts)
