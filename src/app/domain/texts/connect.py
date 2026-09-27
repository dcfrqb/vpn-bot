"""Texts: Connect screen, apps, article link texts.

Owner stream: D (User UI).
Plain module-level constants or small pure functions returning str.
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from typing import Optional

from app.domain.models import SubscriptionState
from app.domain.texts import fmt_date_msk, fmt_gb, h, ui
from app.domain.texts.ui import E

LOADING = "⏳ <b>Получаем ссылку подключения</b>\n\nОдну секунду..."

NO_SUBSCRIPTION_SCREEN = ui.result(
    "info", "Подписка не активна", "Для подключения к VPN нужна активная подписка.",
    hint="Попробуй бесплатно или выбери тариф ниже.",
)
NO_SUBSCRIPTION_NO_TRIAL_SCREEN = ui.result(
    "info", "Подписка не активна", "Для подключения к VPN нужна активная подписка.",
    hint="Выбери тариф ниже, чтобы оформить доступ.",
)
ERROR_SCREEN = ui.result(
    "error", "Не удалось получить ссылку подключения",
    "Сервис временно недоступен.",
    hint="Нажми «Обновить» или загляни позже. Если не пройдет, напиши в поддержку.",
)
NO_SUBSCRIPTION = NO_SUBSCRIPTION_SCREEN.html()
NO_SUBSCRIPTION_NO_TRIAL = NO_SUBSCRIPTION_NO_TRIAL_SCREEN.html()
ERROR = ERROR_SCREEN.html()

# Toasts (callback alerts).
TRIAL_STARTED = ui.toast("Пробный период включен на 5 дней. Открываю ссылку подключения.")
TRIAL_ALREADY_USED = ui.toast("Пробный период уже был использован на этом аккаунте.")
TRIAL_NOT_ELIGIBLE = ui.toast("Пробный период сейчас недоступен для этого аккаунта.")
TRIAL_UNAVAILABLE = ui.toast("Пробный период временно недоступен, попробуй чуть позже.")
TRIAL_BUSY = ui.toast("⏳ Уже включаем, секунду.")
TRIAL_UNAVAILABLE_SCREEN = ui.result("info", "Пробный период временно недоступен", hint="Попробуй чуть позже.")

HOWTO = ("1. Открой ссылку", "2. Скачай подходящий VPN клиент", "3. Импортируй ссылку подписки в клиент")

# One description of the obhod link everywhere (review UX M8, ТЕКСТЫ_3.0 §1).
OBHOD_ABOUT = (
    "Отдельная ссылка для мобильного интернета, когда оператор пускает только "
    "в белый список сайтов. 100 ГБ в месяц."
)
OBHOD_HOWTO = ("Добавляется так же, как основная. Включай обход, когда сайт заблокирован "
               "по мобильному интернету, и выключай, когда все работает штатно.")
OBHOD_PRO_ONLY = f"Есть в тарифе Pro. {OBHOD_ABOUT}"
OBHOD_PREPARING = "Готовим твою ссылку обхода. Загляни чуть позже или нажми «Обновить»."


def _obhod_title() -> dict:
    return {"title": "Обход блокировок", "emoji": E.OBHOD}


def obhod_sections(state: SubscriptionState) -> list[Optional[ui.Block]]:
    """Pro and ready: about + usage + the link; Pro, not ready: preparing; others: the Pro teaser.
    Never an empty «использовано 0 ГБ» block with an empty link."""
    if (state.plan_code or "").lower() != "pro":
        return [ui.block(OBHOD_PRO_ONLY, **_obhod_title())]
    if not state.obhod_active or not state.obhod_subscription_url:
        return [ui.block(OBHOD_PREPARING, **_obhod_title())]
    used = fmt_gb(state.obhod_used_bytes)
    usage = (f"Использовано: {h(used)} из {h(fmt_gb(state.obhod_limit_bytes))}" if state.obhod_limit_bytes
             else f"Использовано: {h(used)}")
    return [ui.block(OBHOD_ABOUT, OBHOD_HOWTO, usage, **_obhod_title()),
            ui.plain(ui.code(state.obhod_subscription_url))]


def grace_section(grace_until) -> ui.Block:
    return ui.block(
        f"Оплаченный срок закончился, ссылка работает до {h(fmt_date_msk(grace_until, with_time=True))} "
        "(МСК) на части серверов.",
        "Продли, чтобы не потерять доступ.",
        title="Льготный период", emoji=E.GRACE,
    )


def success_screen(state: SubscriptionState) -> ui.Screen:
    in_grace = state.grace_until is not None and not state.active
    return ui.article("Подключение VPN", emoji=E.CONNECT, sections=[
        ui.block(ui.code(state.subscription_url or ""), title="Твоя ссылка", emoji=E.LINK, quote=False),
        ui.block(*HOWTO, title="Как подключить", emoji=E.HOWTO),
        *([grace_section(state.grace_until)] if in_grace else obhod_sections(state)),
    ])

