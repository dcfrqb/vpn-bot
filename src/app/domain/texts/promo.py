"""Texts: Promo codes, trial, gifts, deep-link texts.

Owner stream: E (Growth & admin).
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from typing import Optional

from app.domain.models import PromoOutcome, PromoReward
from app.domain.texts import days_ru, fmt_date_msk, h

# ----------------------------------------------------------------- buttons

BTN_CONNECT = "🔌 Подключить VPN"
BTN_MENU = "🏠 Главное меню"
BTN_ENTER_CODE = "🎟 Ввести промокод"
BTN_TRIAL = "🎁 Попробовать 5 дней"
BTN_WRITE_USER = "📩 Написать пользователю"

# ----------------------------------------------------------------- prompts

ENTER_CODE = "Пришли промокод одним сообщением. Отмена: /cancel"
ENTER_CANCELLED = "Ввод промокода отменен."
FRIEND_REQUEST_CANCELLED = "Запрос отменен"
FRIEND_USE_COMMAND = "Используй команду /friend"
CODES_DISABLED = "Промокоды сейчас не принимаются."
GIFTS_DISABLED = "Подарки пока не активируются. Попробуй позже."

# /friend and /admin (non-admin) access requests
REQUEST_SENT = "⏳ Запрос отправлен администратору. Ответ придет сюда."
REQUEST_ALREADY_ACTIVE = "У тебя уже есть активная подписка."
REQUEST_CHECK_FAILED = "Не получилось проверить подписку. Попробуй чуть позже."
REQUEST_DUPLICATE = "Запрос уже отправлен, администратор скоро ответит."
ACCESS_GRANTED = "✅ <b>Тебе выдан доступ</b>\n\n{what}. Нажми «Подключить VPN», чтобы настроить приложение."
ACCESS_REJECTED = "Запрос на доступ отклонен. Если есть вопросы, напиши администратору."


def _support_line(support: Optional[str]) -> str:
    if support:
        return f"\n\n💬 Если это ошибка, напиши @{h(support.lstrip('@'))}"
    return "\n\n💬 Если это ошибка, напиши администратору"


def _until(reward: PromoReward) -> str:
    if reward.expires_at is None:
        return ""
    return f"\n📅 Действует до: <b>{fmt_date_msk(reward.expires_at)}</b>"


def applied_text(code: str, reward: PromoReward, *, plan_title: str, support: Optional[str] = None) -> str:
    """Success text. ``plan_title`` comes from domain.plans (display name)."""
    code_l = (code or "").lower()
    days = reward.days or 0
    if code_l == "trial":
        head = "🎁 <b>Пробный период включен</b>"
        body = f"{h(plan_title)} на {days_ru(days)}, чтобы спокойно попробовать."
    elif code_l.startswith("g_"):
        head = "🎁 <b>Подарок активирован</b>"
        body = f"Подписка {h(plan_title)} на {days_ru(days)} уже подключена."
    elif code_l == "sun718":
        head = "🎉 <b>Промокод активирован</b>"
        body = (
            f"Тебе {days_ru(days)} тарифа <b>Pro</b>. Если у тебя был другой тариф, "
            "через эти дни он вернется, а общий срок подписки станет длиннее."
        )
    else:
        head = "🎉 <b>Промокод активирован</b>"
        body = f"{h(plan_title)}: +{days_ru(days)}." if days else "Бонус начислен."
    tail = "\n\nНажми «Подключить VPN», чтобы настроить приложение."
    extra = _support_line(support) if code_l == "sun718" else ""
    return f"{head}\n\n{body}{_until(reward)}{tail}{extra}"


def outcome_text(code: str, reward: PromoReward, *, support: Optional[str] = None) -> str:
    """Text for every non-applied outcome."""
    code_l = (code or "").lower()
    o = reward.outcome
    shown = h(code)
    if o is PromoOutcome.RATE_LIMITED:
        return "Слишком много неверных промокодов подряд. Попробуй снова через час."
    if o is PromoOutcome.BUSY:
        return "⏳ Уже обрабатываем твой промокод, подожди пару секунд."
    if o is PromoOutcome.ALREADY_USED:
        if code_l == "trial":
            return "Пробный период уже был использован. Оформить подписку можно в меню."
        if code_l.startswith("g_"):
            return "Этот подарок уже активирован."
        return f"Промокод <b>{shown}</b> ты уже использовал(а) раньше." + (
            _support_line(support) if code_l == "sun718" else "")
    if o is PromoOutcome.NOT_ELIGIBLE:
        if code_l == "sun718" and reward.plan_code == "lifetime":
            return "🌟 У тебя бессрочная подписка, промокод не нужен. Спасибо, что ты с нами!" + _support_line(support)
        if code_l.startswith("g_"):
            return "У тебя бессрочная подписка, подарок тебе не нужен. Передай ссылку другу, она еще действует."
        if code_l in ("trial", "solokhin"):
            return (
                "У тебя уже есть активная подписка. Этот промокод только для тех, "
                "у кого подписки сейчас нет."
            )
        return f"Промокод <b>{shown}</b> тебе не подходит."
    if o is PromoOutcome.NOT_FOUND:
        return f"Промокод <b>{shown}</b> не найден. Проверь, нет ли опечатки."
    if o is PromoOutcome.EXPIRED:
        if code_l.startswith("g_"):
            return "Этот подарок больше не действует: покупку отменили."
        return f"Срок действия промокода <b>{shown}</b> закончился."
    if o is PromoOutcome.EXHAUSTED:
        return f"Промокод <b>{shown}</b> уже закончился."
    if o is PromoOutcome.DISABLED:
        if code_l.startswith("g_"):
            return GIFTS_DISABLED
        return "Этот промокод сейчас не работает."
    return "Не получилось активировать промокод. Попробуй позже или напиши в поддержку." + (
        _support_line(support) if code_l == "sun718" else "")


# ----------------------------------------------------------------- gifts (stream A sends these)

def gift_link_text(period_title: str, link: str) -> str:
    return f"Тебе подарили подписку CRS VPN на {h(period_title)}! Открой ссылку в боте, чтобы активировать: {h(link)}"


__all__ = [
    "BTN_CONNECT", "BTN_MENU", "BTN_ENTER_CODE", "BTN_TRIAL", "BTN_WRITE_USER",
    "ENTER_CODE", "ENTER_CANCELLED", "CODES_DISABLED", "FRIEND_REQUEST_CANCELLED", "FRIEND_USE_COMMAND", "GIFTS_DISABLED",
    "REQUEST_SENT", "REQUEST_ALREADY_ACTIVE", "REQUEST_CHECK_FAILED", "REQUEST_DUPLICATE",
    "ACCESS_GRANTED", "ACCESS_REJECTED", "applied_text", "outcome_text", "gift_link_text",
]
