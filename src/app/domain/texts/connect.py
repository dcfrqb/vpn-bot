"""Texts: Connect screen, apps, article link texts.

Owner stream: D (User UI).
Plain module-level constants or small pure functions returning str.
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from typing import Optional

from app.domain.texts import fmt_gb, h

LOADING = "⏳ <b>Получаем ссылку подключения</b>\n\nОдну секунду..."

NO_SUBSCRIPTION = (
    "🔒 <b>Подписка не активна</b>\n\n"
    "Для подключения к VPN нужна активная подписка.\n\n"
    "Попробуй бесплатно или выбери тариф ниже."
)

NO_SUBSCRIPTION_NO_TRIAL = (
    "🔒 <b>Подписка не активна</b>\n\n"
    "Для подключения к VPN нужна активная подписка.\n\n"
    "Выбери тариф ниже, чтобы оформить доступ."
)

ERROR = (
    "❌ <b>Не удалось получить ссылку подключения</b>\n\n"
    "Сервис временно недоступен. Попробуй нажать «Обновить» или зайди позже.\n\n"
    "Если проблема не исчезает: напиши в поддержку."
)

TRIAL_STARTED = "Пробный период включен на 5 дней. Открываю ссылку подключения."
TRIAL_ALREADY_USED = "Пробный период уже был использован на этом аккаунте."
TRIAL_NOT_ELIGIBLE = "Пробный период сейчас недоступен для этого аккаунта."
TRIAL_UNAVAILABLE = "Пробный период временно недоступен, попробуй чуть позже."
TRIAL_BUSY = "Уже включаем, секунду."

SUCCESS_HOWTO = (
    "\n\n💡 <b>Как подключить:</b>\n"
    "<blockquote>\n"
    "1. Открой ссылку\n"
    "2. Скачай подходящий VPN клиент\n"
    "3. Импортируй ссылку подписки в клиент\n"
    "</blockquote>"
)

# One description of the obhod link everywhere (review UX M8, ТЕКСТЫ_3.0 §1).
OBHOD_ABOUT = (
    "Отдельная ссылка для мобильного интернета, когда оператор пускает только "
    "в белый список сайтов. 100 ГБ в месяц."
)

OBHOD_PRO_ONLY = (
    "\n\n———\n"
    "🛡 <b>Обход блокировок</b>\n"
    f"Есть в тарифе Pro. {OBHOD_ABOUT}"
)

OBHOD_PREPARING = (
    "\n\n———\n"
    "🛡 <b>Обход блокировок</b>\n"
    "Готовим твою ссылку обхода. Загляни чуть позже или нажми «Обновить»."
)


def obhod_ready(url: str, used_bytes: Optional[int], limit_bytes: Optional[int]) -> str:
    used = fmt_gb(used_bytes)
    quota = f" ({h(used)} из {h(fmt_gb(limit_bytes))})" if limit_bytes else f" (использовано {h(used)})"
    return (
        "\n\n———\n"
        "🛡 <b>Обход блокировок</b>"
        f"{quota}\n"
        "<blockquote>"
        f"{OBHOD_ABOUT} Добавляется так же, как основная. "
        "Включай обход, когда сайт заблокирован по мобильному интернету, "
        "и выключай, когда все работает штатно."
        "</blockquote>\n\n"
        f"<code>{h(url)}</code>"
    )


def grace_note(grace_until) -> str:
    from app.domain.texts import fmt_date_msk

    return (
        "\n\n🟡 <b>Льготный период</b>\n"
        f"Оплаченный срок закончился, ссылка работает до {h(fmt_date_msk(grace_until, with_time=True))} "
        "(МСК) на части серверов. Продли, чтобы не потерять доступ."
    )


def success(subscription_url: str) -> str:
    return (
        "🚀 <b>Ссылка для подключения VPN</b>\n\n"
        f"<code>{h(subscription_url)}</code>"
        f"{SUCCESS_HOWTO}"
    )
