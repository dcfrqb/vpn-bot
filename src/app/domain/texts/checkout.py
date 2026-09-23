"""Texts: plans, periods, checkout, payment check, refunds, autopay, Stars, gifts.

Owner stream: A (Money). Pure functions of already computed values: prices
arrive as arguments (from app.domain.plans via CheckoutService), never as
literals here. Russian without the letter U+0451, no em dashes, "ты".
Values that go into HTML are escaped with ``h``.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional, Sequence, Union

from app.domain.texts import fmt_date_msk, fmt_rub, h, months_ru
from app.domain.texts import common as _c

Money = Union[int, float, Decimal]

# --- buttons -------------------------------------------------------------------------------

# Shared vocabulary (review UX M4): BTN_BACK goes to the parent screen,
# BTN_BACK_MAIN to the main menu.
BTN_BACK = _c.BTN_BACK
BTN_BACK_MAIN = _c.BTN_BACK_MAIN
BTN_CHECK = _c.BTN_CHECK_PAYMENT
BTN_CONNECT = _c.BTN_CONNECT
BTN_PLANS = _c.BTN_SUBSCRIPTION
BTN_SUPPORT = _c.BTN_SUPPORT
BTN_AUTOPAY_ON = "Включить автопродление"
BTN_AUTOPAY_OFF = "Без автопродления"
BTN_AUTOPAY_STOP = "Отключить автопродление"
BTN_RENEW = "💳 Продлить подписку"
BTN_REFUND = "Не смог подключиться"
BTN_REFUND_OK = "Вернуть"
BTN_REFUND_NO = "Отклонить"
BTN_REVIEW_OK = "Одобрить и выдать"
BTN_REVIEW_NO = "Отклонить"
BTN_GIFT = "Подарить подписку"


def btn_pay(amount: Money) -> str:
    return f"{_c.BTN_PAY_PREFIX} {fmt_rub(amount)}"


def btn_pay_stars(stars: int) -> str:
    return f"Оплатить звездами ({int(stars)} ⭐)"


def btn_period(months: int, amount: Money, saving_percent: int = 0) -> str:
    tail = f" (выгода {saving_percent}%)" if saving_percent > 0 else ""
    return f"{months_ru(months)} · {fmt_rub(amount)}{tail}"


def btn_plan(name: str, from_amount: Optional[Money]) -> str:
    return f"{name} · от {fmt_rub(from_amount)}/мес" if from_amount else name


# --- plans and periods ----------------------------------------------------------------------


def plans_screen(plans: Sequence[tuple[str, Sequence[str]]], *, gift: bool = False) -> str:
    """plans: [(display name, features)] in menu order."""
    head = ("<b>Подарок другу</b>\n\nВыбери тариф, который подаришь. После оплаты пришлем ссылку, "
            "ее нужно отправить другу.") if gift else "<b>Тарифы CRS VPN</b>\n\nВыбери тариф:"
    blocks = []
    for name, features in plans:
        lines = "\n".join(f"· {h(f)}" for f in features)
        blocks.append(f"<b>{h(name)}</b>\n{lines}" if lines else f"<b>{h(name)}</b>")
    return head + ("\n\n" + "\n\n".join(blocks) if blocks else "")


def periods_screen(name: str, features: Iterable[str], *, gift: bool = False) -> str:
    lines = "\n".join(f"· {h(f)}" for f in features)
    lead = "Подарок: " if gift else ""
    body = f"<b>{lead}{h(name)}</b>"
    if lines:
        body += f"\n\n{lines}"
    return body + "\n\nВыбери срок:"


def obhod_packages_screen(base_gb: int, packages: Sequence[tuple[str, Money]]) -> str:
    """packages: [(display, price)] on sale."""
    head = ("<b>Обход блокировок: больше трафика</b>\n\n"
            f"В тарифе Pro обход включен с лимитом {int(base_gb)} ГБ в месяц. Если нужно больше, "
            "возьми пакет: месячный лимит обхода поднимется на твоей ссылке обхода.")
    if not packages:
        return head + "\n\nПакеты скоро появятся."
    return head + "\n\n" + "\n".join(f"· <b>{h(name)}</b>: {fmt_rub(price)}" for name, price in packages)


def btn_obhod_package(name: str, price: Money) -> str:
    return f"{name}: {fmt_rub(price)}"


OBHOD_NEEDS_PRO = ("Пакеты обхода доступны только при активном тарифе Pro. Оформи или продли Pro, "
                   "потом возьми пакет.")
PLAN_UNAVAILABLE = "Этот тариф сейчас недоступен для покупки. Выбери тариф из списка."
PAYMENT_BLOCKED = "Оплата для этого аккаунта недоступна. Если это ошибка, напиши в поддержку."
PAYMENT_CREATE_FAILED = "Не получилось создать платеж. Попробуй еще раз через минуту."
PAYMENT_BUSY = "Платеж уже создается, подожди пару секунд."
STARS_UNAVAILABLE = "Оплата звездами сейчас недоступна."
GIFTS_UNAVAILABLE = "Подарки сейчас недоступны."


def checkout_screen(name: str, months: int, amount: Money, *, autorenew: Optional[bool],
                    gift: bool = False, stars: Optional[int] = None) -> str:
    """autorenew=None: the autorenew line is hidden (AUTOPAY_ENABLED off, gifts)."""
    title = f"Подарок: {h(name)}" if gift else h(name)
    text = f"<b>{title}, {months_ru(months)}</b>\nК оплате: <b>{fmt_rub(amount)}</b>"
    if stars:
        text += f" или {int(stars)} ⭐"
    if autorenew is True:
        text += (f"\n\nАвтопродление: включено. Карта сохранится, за день до конца срока "
                 f"спишем {fmt_rub(amount)}. Отключить можно в любой момент.")
    elif autorenew is False:
        text += "\n\nАвтопродление: выключено."
    if gift:
        text += ("\n\nНажми «Оплатить», откроется страница ЮKassa. После оплаты вернись сюда: "
                 "ссылку для друга пришлем в этот чат.")
    else:
        text += ("\n\nНажми «Оплатить», откроется страница ЮKassa. После оплаты вернись сюда: "
                 "доступ включится сам, обычно за минуту.")
    return text


# --- payment check ---------------------------------------------------------------------------

CHECK_PENDING = ("Оплата пока не пришла. Если ты уже оплатил, подожди минуту и нажми "
                 "«Проверить оплату» еще раз.")
CHECK_PAID = "Оплата прошла, подписка активна. Жми «Подключиться», если еще не настроил VPN."
CHECK_GIFT_PAID = "Оплата прошла, ссылку для друга мы прислали отдельным сообщением."
CHECK_PROVISIONING = ("Оплата получена, включаем доступ. Это может занять несколько минут, "
                      "пришлем сообщение, когда все будет готово.")
CHECK_HELD = ("Оплата получена, платеж на ручной проверке у администратора. Он скоро разберется "
              "и напишет тебе.")
CHECK_REJECTED = "Администратор не подтвердил этот платеж. Напиши в поддержку, разберемся с возвратом."
CHECK_CANCELED = "Платеж отменен, деньги не списаны. Можно создать новый."
CHECK_REFUNDED = "Деньги по этому платежу вернули, доступ по нему не действует."
CHECK_NOT_FOUND = "Платеж не найден. Создай новый в разделе «Тарифы»."
CHECK_ERROR = "Не удалось проверить оплату. Попробуй через минуту."


def check_rate_limited(seconds: int) -> str:
    return f"Подожди {int(seconds)} сек. перед следующей проверкой."


# --- after payment ----------------------------------------------------------------------------


def paid_user(name: str, months: int, expires_at: Optional[datetime]) -> str:
    until = f"\nДоступ до: {fmt_date_msk(expires_at)}" if expires_at else ""
    return (f"<b>Оплата прошла, спасибо!</b>\n\nТариф: {h(name)}, {months_ru(months)}{until}\n\n"
            "Если еще не подключался, жми «Подключиться».")


def autorenew_paid_user(name: str, amount: Money, expires_at: Optional[datetime]) -> str:
    until = f" до {fmt_date_msk(expires_at)}" if expires_at else ""
    return f"Автопродление: списали {fmt_rub(amount)}, подписка {h(name)} продлена{until}."


def obhod_package_paid() -> str:
    return "<b>Пакет обхода подключен</b>\n\nЛимит обхода поднят. Ссылка обхода на экране «Подключиться»."


OBHOD_PACKAGE_MANUAL = ("<b>Оплата пакета обхода получена</b>\n\nАвтоматически применить пакет не получилось. "
                        "Администратор применит его вручную и напишет тебе.")

HELD_USER = ("<b>Оплата получена</b>\n\nПлатеж передан на ручную проверку, администратор скоро разберется. "
             "Если есть вопросы, напиши в поддержку.")


def gift_paid_buyer(name: str, months: int, link: str) -> str:
    return (f"<b>Подарок оплачен!</b>\n\nОтправь другу эту ссылку, по ней он активирует "
            f"{h(name)} на {months_ru(months)}:\n{h(link)}\n\nСсылка сработает один раз.")


GIFT_PENDING = ("<b>Подарок оплачен</b>\n\nСсылку для друга готовим, пришлем ее сюда. Если долго нет, "
                "напиши в поддержку.")


# --- admin ---------------------------------------------------------------------------------


def admin_paid(*, full_name: str, username: str, telegram_id: int, plan_label: str, amount: Money,
               currency: str, payment_number: int, total_rub: Money, expires_at: Optional[datetime],
               external_id: str, method: str) -> str:
    """HTML, built from h()-escaped values (Notifier html=True)."""
    who = f"👤 <b>{h(full_name or 'Без имени')}</b>\n"
    if username:
        who += f"🔗 @{h(username)}\n"
    count = ("🟢 Новый клиент · 1-я оплата" if payment_number <= 1
             else f"🔁 Постоянный клиент · {int(payment_number)}-я оплата")
    paid = f"{int(amount)} ⭐" if currency == "XTR" else fmt_rub(amount)
    until = f"📅 Действует до: {fmt_date_msk(expires_at)}\n\n" if expires_at else ""
    return (
        f"💰 <b>Новая оплата VPN</b>\n\n{who}🆔 ID: <code>{int(telegram_id)}</code>\n\n"
        f"<blockquote>Тариф: {h(plan_label)}\nСумма: {paid}\nСпособ: {h(method)}</blockquote>\n\n"
        f"<blockquote>{count}\n📈 Всего с клиента: {fmt_rub(total_rub)}</blockquote>\n\n"
        f"{until}Payment ID: <code>{h(external_id)}</code>"
    )


def admin_held(*, payment_id: int, external_id: str, telegram_id: int, amount: Money, currency: str,
               reason: str) -> str:
    return (
        "🚨 <b>Платеж на ручной проверке</b>\n\n"
        f"Payment: #{int(payment_id)} <code>{h(external_id)}</code>\n"
        f"Telegram ID: <code>{int(telegram_id)}</code>\n"
        f"Сумма: {h(amount)} {h(currency)}\n"
        f"Причина: {h(reason)}\n\n"
        "Доступ НЕ выдан. «Одобрить и выдать» проведет обычную выдачу, «Отклонить» оставит без доступа "
        "(деньги возвращаются в кабинете ЮKassa)."
    )


def admin_not_provisioned(*, payment_id: int, telegram_id: int, error: str) -> str:
    return (
        "⚠️ <b>Оплата есть, доступ не выдан</b>\n\n"
        f"Telegram ID: <code>{int(telegram_id)}</code>\n"
        f"Payment row id: <code>{int(payment_id)}</code>\n"
        f"Ошибка: <code>{h(error[:300])}</code>\n\n"
        "Бот повторит выдачу сам (повтор вебхука, recovery, реконсилер). Если не пройдет, проверь "
        "сквады и юзера в Remnawave."
    )


def admin_obhod_manual(*, telegram_id: int, package: str, external_id: str) -> str:
    return (
        "⚠️ <b>Пакет обхода оплачен, но НЕ применен</b>\n\n"
        f"Telegram ID: <code>{int(telegram_id)}</code>\nПакет: {h(package)}\n"
        f"Payment: <code>{h(external_id)}</code>\n\n"
        "Скорее всего нет активного обхода (Pro истек). Примени кап вручную или оформи возврат."
    )


def admin_gift_pending(*, telegram_id: int, payment_id: int) -> str:
    return (
        "⚠️ <b>Подарок оплачен, код не создан</b>\n\n"
        f"Покупатель: <code>{int(telegram_id)}</code>\nPayment row id: <code>{int(payment_id)}</code>\n\n"
        "Бот повторит создание кода сам. Если не выйдет, выдай код вручную."
    )


def admin_blocked_card(*, external_id: str, fingerprint: str, reason: str) -> str:
    return (
        "⚠️ <b>Оплата с карты из стоп-листа</b>\n"
        f"Платеж: <code>{h(external_id)}</code>\nКарта: <code>{h(fingerprint)}</code>\n"
        f"Причина: {h(reason or '-')}\n\nПодписка выдается штатно. Реши по возврату вручную."
    )


# --- 24h refund ------------------------------------------------------------------------------

REFUND_REQUESTED = ("Запрос на возврат принят. Обычно рассматриваем в течение суток, напишем сюда "
                    "о решении.")
REFUND_ALREADY = "Запрос по этому платежу уже есть, ответим сюда."
REFUND_NOT_ELIGIBLE = ("Вернуть деньги через бота можно в течение 24 часов после оплаты. "
                       "Напиши в поддержку, разберемся.")
REFUND_APPROVED_CARD = ("Возврат одобрен, деньги вернутся на карту в течение нескольких дней (сроки "
                        "зависят от банка). Доступ к VPN по этой оплате отключен.")
REFUND_APPROVED_STARS = "Возврат одобрен, звезды вернулись на твой баланс в Telegram. Доступ к VPN по этой оплате отключен."


def refund_rejected(support: str) -> str:
    return (f"По этому платежу возврат не одобрен. Если не согласен или есть вопросы, напиши {h(support)}, "
            "разберем отдельно.")


def admin_refund_request(*, request_id: int, full_name: str, username: str, telegram_id: int, payment_id: int,
                         external_id: str, plan_label: str, amount: Money, currency: str,
                         paid_at: Optional[datetime]) -> str:
    who = h(full_name or "Без имени") + (f" @{h(username)}" if username else "")
    paid = f"{int(amount)} ⭐" if currency == "XTR" else fmt_rub(amount)
    return (
        f"↩️ <b>Запрос на возврат #{int(request_id)}</b> (24 часа)\n\n"
        f"Клиент: {who} (<code>{int(telegram_id)}</code>)\n"
        f"Платеж: #{int(payment_id)} <code>{h(external_id)}</code>\n"
        f"Тариф: {h(plan_label)}\nСумма: {paid}\n"
        f"Оплачен: {fmt_date_msk(paid_at, with_time=True)}\n"
        "Причина: не смог подключиться\n\n"
        "«Вернуть» вернет деньги и отключит доступ по этой оплате."
    )


ADMIN_REFUND_DONE = "✅ Возврат оформлен, доступ по оплате отключен."
ADMIN_REFUND_DONE_NO_REVOKE = "⚠️ Деньги вернули, но доступ отключить не удалось. Проверь юзера в панели вручную."
ADMIN_REFUND_REJECTED = "❌ Отклонено, клиенту отправлен ответ."
ADMIN_REFUND_BUSY = "⏳ Этот запрос уже обрабатывается."
ADMIN_REFUND_NOT_FOUND = "⚠️ Запрос не найден."


def admin_refund_failed(detail: str) -> str:
    return f"⚠️ Вернуть деньги не получилось ({h(detail)}). Запрос остался открытым, можно нажать «Вернуть» еще раз."


def admin_refund_already(status: str) -> str:
    names = {"approved": "в работе", "rejected": "отклонен", "refunded": "деньги возвращены", "failed": "ошибка возврата"}
    return f"ℹ️ Запрос уже решен: {names.get(status, status)}."


# --- autopay -------------------------------------------------------------------------------

AUTOPAY_INFO = ("<b>Автопродление</b>\n\nКарта сохраняется в ЮKassa при оплате. За день до конца срока "
                "спишем цену того же тарифа и срока. За 3 дня пришлем напоминание с кнопкой отключения. "
                "Если списание не пройдет два раза, автопродление выключится само.")
AUTOPAY_UNAVAILABLE = "Автопродление сейчас недоступно."


def autopay_notice(name: str, months: int, amount: Money, charge_on: Optional[datetime] = None) -> str:
    """``charge_on``: the day of the charge (a day before the end), review UX M6."""
    when = fmt_date_msk(charge_on) if charge_on is not None else "За день до конца срока"
    return (f"{when} спишем {fmt_rub(amount)} за продление подписки {h(name)} на {months_ru(months)}, "
            "карта уже сохранена. Если не нужно, отключи автопродление.")


AUTOPAY_FAILED = ("Не получилось списать оплату за автопродление, карта могла не пройти платеж. Подписка "
                  "пока активна, но скоро закончится. Продли вручную или попробуем еще раз завтра.")
AUTOPAY_TURNED_OFF_FAILS = ("Автопродление выключено: два раза не получилось списать оплату. Продли "
                            "подписку вручную, это займет минуту.")


def autopay_stopped(expires_at: Optional[datetime]) -> str:
    until = f" Подписка действует до {fmt_date_msk(expires_at)}." if expires_at else ""
    return f"Автопродление выключено.{until}"


AUTOPAY_NOTHING_TO_STOP = "Автопродление и так выключено."


# --- Stars ---------------------------------------------------------------------------------


def stars_invoice_title(name: str) -> str:
    return f"CRS VPN {name}"[:32]


def stars_invoice_description(name: str, months: int, gift: bool = False) -> str:
    lead = "Подарок: " if gift else ""
    return f"{lead}{name}, {months_ru(months)}. Доступ включится сразу после оплаты."


STARS_INVOICE_SENT = "Счет в звездах отправлен ниже."
PRECHECK_PRICE_CHANGED = "Цена изменилась. Открой оплату заново."
PRECHECK_STALE = "Этот счет уже не действует. Открой оплату заново."


# --- review (admin) -----------------------------------------------------------------------

REVIEW_TEXT = {
    "approved": "✅ Одобрено, доступ выдан.",
    "pending": "⏳ Одобрено, но выдача не прошла (панель). Бот повторит сам.",
    "rejected": "❌ Отклонено. Доступ не выдан, оформи возврат в кабинете ЮKassa.",
    "already_done": "ℹ️ Уже одобрено и выдано.",
    "already_rejected": "ℹ️ Платеж уже отклонен.",
    "already_approved": "ℹ️ Платеж уже одобрен, отклонить нельзя.",
    "not_held": "ℹ️ Платеж не на ручной проверке.",
    "not_paid": "⚠️ Платеж не в статусе succeeded, выдавать нечего.",
    "not_found": "⚠️ Платеж не найден.",
    "busy": "⏳ Решение по этому платежу уже обрабатывается.",
}

NOT_ADMIN = "Недоступно."
