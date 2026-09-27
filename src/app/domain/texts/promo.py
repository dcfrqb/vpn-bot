"""Texts: Promo codes, trial, gifts, deep-link texts.

Owner stream: E (Growth & admin).
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from typing import Optional

from app.domain.models import PromoOutcome, PromoReward
from app.domain.texts import days_ru, fmt_date_msk, h, months_ru, plural_ru, ui
from app.domain.texts.ui import B, E

# ----------------------------------------------------------------- buttons

BTN_CONNECT = B.CONNECT  # shared vocabulary (docs/SCREENS.md)
BTN_MENU = B.MENU
BTN_ENTER_CODE = "🎟 Ввести промокод"
BTN_TRIAL = "🎁 Попробовать 5 дней"
BTN_WRITE_USER = "📩 Написать пользователю"
BTN_WRITE_ADMIN = B.WRITE_ADMIN

# ----------------------------------------------------------------- prompts (prompt / result / toast)

ENTER_CODE_SCREEN = ui.prompt("Пришли промокод", "Одним сообщением, как он написан.", hint="Отмена: /cancel")
ENTER_CODE = ENTER_CODE_SCREEN.html()
ENTER_CANCELLED_SCREEN = ui.result("info", "Ввод промокода отменен")
ENTER_CANCELLED = ENTER_CANCELLED_SCREEN.html()
FRIEND_REQUEST_CANCELLED_SCREEN = ui.result("info", "Запрос отменен")
FRIEND_REQUEST_CANCELLED = FRIEND_REQUEST_CANCELLED_SCREEN.html()
FRIEND_USE_COMMAND = ui.toast("Используй команду /friend")
CODES_DISABLED = ui.toast("Промокоды сейчас не принимаются.")
CODES_DISABLED_SCREEN = ui.result("info", "Промокоды сейчас не принимаются")
GIFTS_DISABLED = "Подарки пока не активируются. Попробуй позже."

# /friend and /admin (non-admin) access requests
REQUEST_SENT_SCREEN = ui.result("wait", "Запрос отправлен администратору", hint="Ответ придет сюда.")
REQUEST_ALREADY_ACTIVE_SCREEN = ui.result("info", "У тебя уже есть активная подписка")
REQUEST_CHECK_FAILED_SCREEN = ui.result("error", "Не получилось проверить подписку", hint="Попробуй чуть позже.")
REQUEST_DUPLICATE_SCREEN = ui.result("info", "Запрос уже отправлен", hint="Администратор скоро ответит.")
NO_ADMIN_RIGHTS_SCREEN = ui.result("error", "У тебя нет прав администратора")
REQUEST_SENT = REQUEST_SENT_SCREEN.html()
REQUEST_ALREADY_ACTIVE = REQUEST_ALREADY_ACTIVE_SCREEN.html()
REQUEST_CHECK_FAILED = REQUEST_CHECK_FAILED_SCREEN.html()
REQUEST_DUPLICATE = REQUEST_DUPLICATE_SCREEN.html()


def access_granted_screen(what_html: str) -> ui.Screen:
    """``what_html``: an HTML-safe phrase («Pro на 1 месяц», «Подписка продлена на 7 дней»)."""
    return ui.push("ok", "Тебе выдан доступ", what_html, hint="Нажми «Подключиться», чтобы настроить приложение.")


def access_granted(what_html: str) -> str:
    return access_granted_screen(what_html).html()


ACCESS_REJECTED_SCREEN = ui.push("error", "Запрос на доступ отклонен", hint="Если есть вопросы, напиши администратору.")
ACCESS_REJECTED = ACCESS_REJECTED_SCREEN.html()


def _support_line(support: Optional[str]) -> str:
    who = f"@{h(support.lstrip('@'))}" if support else "администратору"
    return f"Если это ошибка, напиши {who}."


def _until(reward: PromoReward) -> Optional[str]:
    if reward.expires_at is None:
        return None
    return f"{E.DATE} " + ui.field("Действует до", fmt_date_msk(reward.expires_at))


_CONNECT_HINT = "Нажми «Подключиться», чтобы настроить приложение."


def applied_screen(code: str, reward: PromoReward, *, plan_title: str, support: Optional[str] = None) -> ui.Screen:
    """Success. ``plan_title`` comes from domain.plans (display name)."""
    code_l = (code or "").lower()
    days = reward.days or 0
    hint = _CONNECT_HINT
    if code_l == "trial":
        title = "Пробный период включен"
        body = f"{h(plan_title)} на {days_ru(days)}, чтобы спокойно попробовать."
    elif code_l.startswith("g_"):
        title = "Подарок активирован"
        # gifts grant paid calendar months (A-5); show the same unit the buyer saw
        duration = months_ru(reward.months) if reward.months else days_ru(days)
        body = f"Подписка {h(plan_title)} на {duration} уже подключена."
    elif code_l == "sun718":
        title = "Промокод активирован"
        body = (f"Тебе {days_ru(days)} тарифа <b>Pro</b>. Если у тебя был другой тариф, "
                "через эти дни он вернется, а общий срок подписки станет длиннее.")
        hint = f"{_CONNECT_HINT}\n{_support_line(support)}"
    else:
        title = "Промокод активирован"
        body = f"{h(plan_title)}: +{days_ru(days)}." if days else "Бонус начислен."
    return ui.result("ok", title, body, _until(reward), hint=hint)


def applied_text(code: str, reward: PromoReward, *, plan_title: str, support: Optional[str] = None) -> str:
    return applied_screen(code, reward, plan_title=plan_title, support=support).html()


def outcome_screen(code: str, reward: PromoReward, *, support: Optional[str] = None) -> ui.Screen:
    """Every non-applied outcome."""
    code_l = (code or "").lower()
    o = reward.outcome
    shown = f"<b>{h(code)}</b>"
    sun = _support_line(support) if code_l == "sun718" else ""
    if o is PromoOutcome.RATE_LIMITED:
        return ui.result("warn", "Слишком много попыток", "Слишком много неверных промокодов подряд.",
                         hint="Попробуй снова через час.")
    if o is PromoOutcome.BUSY:
        return ui.result("wait", "Уже обрабатываем промокод", hint="Подожди пару секунд.")
    if o is PromoOutcome.ALREADY_USED:
        if code_l == "trial":
            return ui.result("info", "Пробный период уже был использован",
                             hint="Оформить подписку можно в меню.")
        if code_l.startswith("g_"):
            return ui.result("info", "Этот подарок уже активирован")
        return ui.result("info", "Промокод уже использован", f"Промокод {shown} ты уже использовал(а) раньше.",
                         hint=sun)
    if o is PromoOutcome.NOT_ELIGIBLE:
        if code_l == "sun718" and reward.plan_code == "lifetime":
            return ui.result("info", "Промокод не нужен",
                             "У тебя бессрочная подписка. Спасибо, что ты с нами!", hint=_support_line(support))
        if code_l.startswith("g_"):
            return ui.result("info", "Подарок тебе не нужен", "У тебя бессрочная подписка.",
                             hint="Передай ссылку другу, она еще действует.")
        if code_l in ("trial", "solokhin"):
            return ui.result("info", "У тебя уже есть активная подписка",
                             "Этот промокод только для тех, у кого подписки сейчас нет.")
        return ui.result("warn", "Промокод не подходит", f"Промокод {shown} тебе не подходит.")
    if o is PromoOutcome.NOT_FOUND:
        return ui.result("warn", "Промокод не найден", f"Промокод {shown} не найден.",
                         hint="Проверь, нет ли опечатки.")
    if o is PromoOutcome.EXPIRED:
        if code_l.startswith("g_"):
            return ui.result("warn", "Подарок больше не действует", "Покупку отменили.")
        return ui.result("warn", "Срок промокода закончился", f"Срок действия промокода {shown} закончился.")
    if o is PromoOutcome.EXHAUSTED:
        return ui.result("warn", "Промокод закончился", f"Промокод {shown} уже закончился.")
    if o is PromoOutcome.DISABLED:
        if code_l.startswith("g_"):
            return ui.result("info", "Подарки пока не активируются", hint="Попробуй позже.")
        return ui.result("info", "Этот промокод сейчас не работает")
    return ui.result("error", "Не получилось активировать промокод",
                     hint=sun or "Попробуй позже или напиши в поддержку.")


def outcome_text(code: str, reward: PromoReward, *, support: Optional[str] = None) -> str:
    return outcome_screen(code, reward, support=support).html()


# ----------------------------------------------------------------- gifts and referral (type: push)

GIFT_USED_BUYER_SCREEN = ui.push(E.GIFT, "Твой подарок активирован", "Друг уже пользуется подпиской. Спасибо!")
GIFT_USED_BUYER = GIFT_USED_BUYER_SCREEN.html()


def referral_payout_screen(months: int, note: Optional[str], available: int) -> ui.Screen:
    """SUN718 owner: the admin recorded a payout."""
    return ui.push(E.GIFT, f"Тебе выдано бонусных месяцев: {int(months)}",
                   ui.field("Комментарий", note) if note else None,
                   ui.field("Осталось доступно", f"{int(available)} мес."),
                   hint="Спасибо за приглашенных!")


def referral_new_payment_screen(months: int, earned: int, bonus: float, available: int) -> ui.Screen:
    """SUN718 owner: an invited user paid for Pro."""
    return ui.push(E.MONEY, "Новая оплата приглашенного",
                   f"Один из приглашенных тобой пользователей оплатил Pro на <b>{months_ru(int(months or 0))}</b>.",
                   extra=[ui.block(ui.field("Заработано Pro-месяцев", earned),
                                   ui.field("Бонусных месяцев", f"{bonus:.2f}"),
                                   f"<b>Доступно к выдаче: {int(available)} мес.</b>",
                                   title="Твой прогресс", emoji="📊")])


def referral_bonus_screen(delta: int, full: int, available: int) -> ui.Screen:
    """SUN718 owner: whole bonus months earned."""
    word = plural_ru(delta, "бонусный месяц", "бонусных месяца", "бонусных месяцев")
    return ui.push(E.GIFT, "Поздравляем!", f"Ты заработал еще {int(delta)} {word} подписки.",
                   ui.field("Всего заработано бонусов", f"{int(full)} мес."),
                   ui.field("Доступно к выдаче", f"{int(available)} мес."),
                   hint="Напиши админу, чтобы получить.")


__all__ = [
    "BTN_CONNECT", "BTN_MENU", "BTN_ENTER_CODE", "BTN_TRIAL", "BTN_WRITE_USER", "BTN_WRITE_ADMIN",
    "ENTER_CODE", "ENTER_CANCELLED", "CODES_DISABLED", "FRIEND_REQUEST_CANCELLED", "FRIEND_USE_COMMAND", "GIFTS_DISABLED",
    "REQUEST_SENT", "REQUEST_ALREADY_ACTIVE", "REQUEST_CHECK_FAILED", "REQUEST_DUPLICATE",
    "ACCESS_REJECTED", "access_granted", "applied_text", "outcome_text",
    "applied_screen", "outcome_screen", "access_granted_screen",
]
