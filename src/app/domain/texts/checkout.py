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

from app.domain.texts import fmt_date_msk, fmt_rub, h, months_ru, ui
from app.domain.texts import common as _c
from app.domain.texts.ui import B, E

Money = Union[int, float, Decimal]

# --- buttons -------------------------------------------------------------------------------

# Shared vocabulary: app.domain.texts.ui.B (docs/SCREENS.md, «Словарь»).
BTN_BACK = B.BACK
BTN_BACK_MAIN = B.MENU
BTN_CHECK = B.CHECK_PAYMENT
BTN_CONNECT = B.CONNECT
BTN_PLANS = B.SUBSCRIPTION
BTN_SUPPORT = B.SUPPORT
BTN_AUTOPAY_ON = B.AUTOPAY_ON
BTN_AUTOPAY_OFF = B.AUTOPAY_OFF
BTN_AUTOPAY_STOP = B.AUTOPAY_STOP
BTN_RENEW = B.RENEW
BTN_REFUND = B.REFUND
BTN_REFUND_OK = "Вернуть"
BTN_REFUND_NO = "Отклонить"
BTN_REVIEW_OK = "Одобрить и выдать"
BTN_REVIEW_NO = "Отклонить"
BTN_GIFT = B.GIFT


def btn_pay(amount: Money) -> str:
    return f"{B.PAY_PREFIX} {fmt_rub(amount)}"


def btn_pay_stars(stars: int) -> str:
    return B.PAY_STARS.format(stars=int(stars))


def btn_period(months: int, amount: Money, saving_percent: int = 0) -> str:
    tail = f" (выгода {saving_percent}%)" if saving_percent > 0 else ""
    return f"{months_ru(months)} · {fmt_rub(amount)}{tail}"


def btn_plan(name: str, from_amount: Optional[Money]) -> str:
    return f"{name} · от {fmt_rub(from_amount)}/мес" if from_amount else name


def btn_obhod_package(name: str, price: Money) -> str:
    return f"{name} · {fmt_rub(price)}"


# --- plans and periods (type: choice) --------------------------------------------------------

PLAN_EMOJI = {"lite": "🟢", "standard": "🔵", "pro": "💎", "premium": "👑"}
DEFAULT_PLAN_EMOJI = "🔹"


def plan_emoji(code: Optional[str]) -> str:
    return PLAN_EMOJI.get((code or "").lower(), DEFAULT_PLAN_EMOJI)


def _features(features: Iterable[str]) -> list[str]:
    return [f"· {h(f)}" for f in features]


def plans_screen_of(plans: Sequence[tuple], *, gift: bool = False) -> ui.Screen:
    """plans: [(display name, features)] or [(display name, features, code)] in menu order."""
    options = [ui.block(*_features(p[1]), title=h(p[0]), emoji=plan_emoji(p[2] if len(p) > 2 else None))
               for p in plans]
    if gift:
        return ui.choice("Подарок другу", emoji=E.GIFT, options=options,
                         intro=["Выбери тариф, который подаришь. После оплаты пришлем ссылку, "
                                "ее нужно отправить другу."],
                         hint="Выбери тариф кнопкой ниже.")
    return ui.choice("Тарифы CRS VPN", emoji=E.SUBSCRIPTION, options=options, hint="Выбери тариф кнопкой ниже.")


def plans_screen(plans: Sequence[tuple], *, gift: bool = False) -> str:
    return plans_screen_of(plans, gift=gift).html()


def periods_screen_of(name: str, features: Iterable[str], *, gift: bool = False,
                      code: Optional[str] = None) -> ui.Screen:
    title = f"Подарок: {h(name)}" if gift else h(name)
    feats = _features(features)
    return ui.choice(title, emoji=E.GIFT if gift else plan_emoji(code),
                     options=[ui.block(*feats, title="Что входит", emoji="📋") if feats else None],
                     hint="Выбери срок кнопкой ниже.")


def periods_screen(name: str, features: Iterable[str], *, gift: bool = False, code: Optional[str] = None) -> str:
    return periods_screen_of(name, features, gift=gift, code=code).html()


def obhod_packages_screen_of(base_gb: int, packages: Sequence[tuple[str, Money]]) -> ui.Screen:
    """packages: [(display, price)] on sale."""
    intro = [f"В тарифе Pro обход включен с лимитом {int(base_gb)} ГБ в месяц. Если нужно больше, "
             "возьми пакет: месячный лимит обхода поднимется на твоей ссылке обхода."]
    if not packages:
        return ui.choice("Больше трафика обхода", emoji=E.OBHOD, intro=intro, hint="Пакеты скоро появятся.")
    lines = [f"· <b>{h(name)}</b>: {fmt_rub(price)}" for name, price in packages]
    return ui.choice("Больше трафика обхода", emoji=E.OBHOD, intro=intro,
                     options=[ui.block(*lines, title="Пакеты", emoji="📦")],
                     hint="Выбери пакет кнопкой ниже.")


def obhod_packages_screen(base_gb: int, packages: Sequence[tuple[str, Money]]) -> str:
    return obhod_packages_screen_of(base_gb, packages).html()


# --- checkout (type: checkout) ----------------------------------------------------------------


def checkout_screen_of(name: str, months: int, amount: Money, *, autorenew: Optional[bool],
                       gift: bool = False, stars: Optional[int] = None,
                       offer_url: str = _c.OFFER_URL, privacy_url: str = _c.PRIVACY_URL) -> ui.Screen:
    """autorenew=None: the autorenew line is hidden (AUTOPAY_ENABLED off, gifts)."""
    price = f"<b>{fmt_rub(amount)}</b>" + (f" или {int(stars)} ⭐" if stars else "")
    fields = [
        ui.field("Подарок" if gift else "Тариф", name),
        ui.field("Срок", months_ru(months)),
        f"К оплате: {price}",
    ]
    if autorenew is True:
        fields.append("Автопродление: включено")
        fields.append(f"Карта сохранится, за день до конца срока спишем {fmt_rub(amount)}. "
                      "Отключить можно в любой момент.")
    elif autorenew is False:
        fields.append("Автопродление: выключено")
    after = ("ссылку для друга пришлем в этот чат." if gift
             else "доступ включится сам, обычно за минуту.")
    return ui.checkout(
        "Оплата подарка" if gift else "Оплата", fields,
        hint=f"Нажми «Оплатить», откроется страница ЮKassa. После оплаты вернись сюда: {after}",
        legal=_c.legal_line(offer_url, privacy_url),
    )


def checkout_screen(name: str, months: int, amount: Money, *, autorenew: Optional[bool],
                    gift: bool = False, stars: Optional[int] = None) -> str:
    return checkout_screen_of(name, months, amount, autorenew=autorenew, gift=gift, stars=stars).html()


# --- errors on the way to a payment -----------------------------------------------------------
# Plain strings are toasts and Stars pre-checkout errors; *_SCREEN are result screens.

OBHOD_NEEDS_PRO = ("Пакеты обхода доступны только при активном тарифе Pro. Оформи или продли Pro, "
                   "потом возьми пакет.")
PLAN_UNAVAILABLE = "Этот тариф сейчас недоступен для покупки. Выбери тариф из списка."
PAYMENT_BLOCKED = "Оплата для этого аккаунта недоступна. Если это ошибка, напиши в поддержку."
PAYMENT_CREATE_FAILED = "Не получилось создать платеж. Попробуй еще раз через минуту."
PAYMENT_BUSY = ui.toast("⏳ Платеж уже создается, подожди пару секунд.")
STARS_UNAVAILABLE = ui.toast("Оплата звездами сейчас недоступна.")
GIFTS_UNAVAILABLE = ui.toast("Подарки сейчас недоступны.")

OBHOD_NEEDS_PRO_SCREEN = ui.result("warn", "Нужен тариф Pro",
                                   "Пакеты обхода доступны только при активном тарифе Pro.",
                                   hint="Оформи или продли Pro, потом возьми пакет.")
PLAN_UNAVAILABLE_SCREEN = ui.result("warn", "Тариф недоступен", "Этот тариф сейчас нельзя купить.",
                                    hint="Выбери тариф из списка.")
PAYMENT_BLOCKED_SCREEN = ui.result("error", "Оплата недоступна", "Оплата для этого аккаунта недоступна.",
                                   hint="Если это ошибка, напиши в поддержку.")
PAYMENT_CREATE_FAILED_SCREEN = ui.result("error", "Не получилось создать платеж",
                                         hint="Попробуй еще раз через минуту.")
STARS_UNAVAILABLE_SCREEN = ui.result("warn", "Оплата звездами недоступна", hint="Оплати картой или попробуй позже.")


# --- payment check (type: result) ------------------------------------------------------------

CHECK_PENDING_SCREEN = ui.result("wait", "Оплата пока не пришла", "Если ты уже оплатил, подожди минуту.",
                                 hint="Потом нажми «Проверить оплату» еще раз.")
CHECK_PAID_SCREEN = ui.result("ok", "Оплата прошла, подписка активна",
                              hint="Жми «Подключиться», если еще не настроил VPN.")
CHECK_GIFT_PAID_SCREEN = ui.result("ok", "Подарок оплачен", "Ссылку для друга мы прислали отдельным сообщением.")
CHECK_PROVISIONING_SCREEN = ui.result("wait", "Оплата получена, включаем доступ",
                                      "Это может занять несколько минут.",
                                      hint="Пришлем сообщение, когда все будет готово.")
CHECK_HELD_SCREEN = ui.result("wait", "Оплата на проверке", "Платеж на ручной проверке у администратора.",
                              hint="Он скоро разберется и напишет тебе.")
CHECK_REJECTED_SCREEN = ui.result("error", "Платеж не подтвержден", "Администратор не подтвердил этот платеж.",
                                  hint="Напиши в поддержку, разберемся с возвратом.")
CHECK_CANCELED_SCREEN = ui.result("info", "Платеж отменен", "Деньги не списаны.", hint="Можно создать новый.")
CHECK_REFUNDED_SCREEN = ui.result("info", "Деньги вернули", "Доступ по этому платежу не действует.")
CHECK_NOT_FOUND_SCREEN = ui.result("warn", "Платеж не найден", hint="Создай новый в разделе «Подписка».")
CHECK_ERROR_SCREEN = ui.result("error", "Не удалось проверить оплату", hint="Попробуй через минуту.")

CHECK_PENDING = CHECK_PENDING_SCREEN.html()
CHECK_PAID = CHECK_PAID_SCREEN.html()
CHECK_GIFT_PAID = CHECK_GIFT_PAID_SCREEN.html()
CHECK_PROVISIONING = CHECK_PROVISIONING_SCREEN.html()
CHECK_HELD = CHECK_HELD_SCREEN.html()
CHECK_REJECTED = CHECK_REJECTED_SCREEN.html()
CHECK_CANCELED = CHECK_CANCELED_SCREEN.html()
CHECK_REFUNDED = CHECK_REFUNDED_SCREEN.html()
CHECK_NOT_FOUND = CHECK_NOT_FOUND_SCREEN.html()
CHECK_ERROR = CHECK_ERROR_SCREEN.html()


def check_rate_limited(seconds: int) -> str:
    return ui.toast(f"Подожди {int(seconds)} сек. перед следующей проверкой.")


# --- after payment (type: push) ---------------------------------------------------------------


def paid_user_screen(name: str, months: int, expires_at: Optional[datetime]) -> ui.Screen:
    return ui.push("ok", "Оплата прошла, спасибо!",
                   ui.field("Тариф", f"{name}, {months_ru(months)}"),
                   ui.field("Доступ до", fmt_date_msk(expires_at)) if expires_at else None,
                   hint="Если еще не подключался, жми «Подключиться».")


def paid_user(name: str, months: int, expires_at: Optional[datetime]) -> str:
    return paid_user_screen(name, months, expires_at).html()


def autorenew_paid_screen(name: str, amount: Money, expires_at: Optional[datetime]) -> ui.Screen:
    return ui.push(E.AUTOPAY, "Подписка продлена",
                   f"Автопродление: списали {fmt_rub(amount)}.",
                   ui.field("Тариф", name),
                   ui.field("Доступ до", fmt_date_msk(expires_at)) if expires_at else None)


def autorenew_paid_user(name: str, amount: Money, expires_at: Optional[datetime]) -> str:
    return autorenew_paid_screen(name, amount, expires_at).html()


OBHOD_PACKAGE_PAID_SCREEN = ui.push("ok", "Пакет обхода подключен", "Лимит обхода поднят.",
                                    hint="Ссылка обхода на экране «Подключиться».")


def obhod_package_paid() -> str:
    return OBHOD_PACKAGE_PAID_SCREEN.html()


OBHOD_PACKAGE_MANUAL_SCREEN = ui.push("wait", "Оплата пакета обхода получена",
                                      "Автоматически применить пакет не получилось.",
                                      hint="Администратор применит его вручную и напишет тебе.")
OBHOD_PACKAGE_MANUAL = OBHOD_PACKAGE_MANUAL_SCREEN.html()

HELD_USER_SCREEN = ui.push("wait", "Оплата получена", "Платеж передан на ручную проверку, администратор скоро "
                           "разберется.", hint="Если есть вопросы, напиши в поддержку.")
HELD_USER = HELD_USER_SCREEN.html()


def gift_paid_buyer_screen(name: str, months: int, link: str) -> ui.Screen:
    return ui.push(E.GIFT, "Подарок оплачен!",
                   f"Отправь другу ссылку ниже, по ней он активирует {h(name)} на {months_ru(months)}.",
                   extra=[ui.plain(h(link))], hint="Ссылка сработает один раз.")


def gift_paid_buyer(name: str, months: int, link: str) -> str:
    return gift_paid_buyer_screen(name, months, link).html()


GIFT_PENDING_SCREEN = ui.push("wait", "Подарок оплачен", "Ссылку для друга готовим, пришлем ее сюда.",
                              hint="Если долго нет, напиши в поддержку.")
GIFT_PENDING = GIFT_PENDING_SCREEN.html()


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

REFUND_REQUESTED_SCREEN = ui.result("ok", "Запрос на возврат принят", "Обычно рассматриваем в течение суток.",
                                    hint="Напишем сюда о решении.")
REFUND_ALREADY_SCREEN = ui.result("info", "Запрос уже есть", "Запрос по этому платежу уже отправлен.",
                                  hint="Ответим сюда.")
REFUND_NOT_ELIGIBLE_SCREEN = ui.result("warn", "Вернуть через бота нельзя",
                                       "Вернуть деньги через бота можно в течение 24 часов после оплаты.",
                                       hint="Напиши в поддержку, разберемся.")
REFUND_REQUESTED = REFUND_REQUESTED_SCREEN.html()
REFUND_ALREADY = REFUND_ALREADY_SCREEN.html()
REFUND_NOT_ELIGIBLE = REFUND_NOT_ELIGIBLE_SCREEN.html()

REFUND_APPROVED_CARD_SCREEN = ui.push(E.REFUND, "Возврат одобрен",
                                      "Деньги вернутся на карту в течение нескольких дней, сроки зависят от банка.",
                                      "Доступ к VPN по этой оплате отключен.")
REFUND_APPROVED_STARS_SCREEN = ui.push(E.REFUND, "Возврат одобрен",
                                       "Звезды вернулись на твой баланс в Telegram.",
                                       "Доступ к VPN по этой оплате отключен.")
REFUND_APPROVED_CARD = REFUND_APPROVED_CARD_SCREEN.html()
REFUND_APPROVED_STARS = REFUND_APPROVED_STARS_SCREEN.html()


def refund_rejected_screen(support: str) -> ui.Screen:
    return ui.push("error", "Возврат не одобрен", "По этому платежу возврат не одобрен.",
                   hint=f"Если не согласен или есть вопросы, напиши {h(support)}, разберем отдельно.")


def refund_rejected(support: str) -> str:
    return refund_rejected_screen(support).html()


def refund_done_screen(until: Optional[datetime] = None, *, expired: bool) -> ui.Screen:
    """Refund made in the YooKassa dashboard (refund webhook)."""
    if expired:
        return ui.push(E.REFUND, "Возврат оформлен",
                       "Деньги по платежу возвращены, доступ по этой оплате закончился.",
                       hint="Если захочешь вернуться, оформи подписку в меню.")
    return ui.push(E.REFUND, "Возврат оформлен", "Деньги по платежу возвращены, оплаченный период снят.",
                   ui.field("Подписка действует до", fmt_date_msk(until)) if until else None)


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

AUTOPAY_INFO_SCREEN = ui.result(
    "info", "Автопродление",
    "Карта сохраняется в ЮKassa при оплате. За день до конца срока спишем цену того же тарифа и срока.",
    "За 3 дня пришлем напоминание с кнопкой отключения.",
    hint="Если списание не пройдет два раза, автопродление выключится само.",
)
AUTOPAY_INFO = AUTOPAY_INFO_SCREEN.html()
AUTOPAY_UNAVAILABLE = ui.toast("Автопродление сейчас недоступно.")


def autopay_notice_screen(name: str, months: int, amount: Money, charge_on: Optional[datetime] = None) -> ui.Screen:
    """``charge_on``: the day of the charge (a day before the end), review UX M6."""
    return ui.push(E.AUTOPAY, "Скоро автопродление",
                   ui.field("Дата списания", fmt_date_msk(charge_on) if charge_on is not None
                            else "за день до конца срока"),
                   ui.field("Сумма", fmt_rub(amount)),
                   ui.field("Тариф", f"{name}, {months_ru(months)}"),
                   "Карта уже сохранена.",
                   hint="Если продление не нужно, отключи автопродление.")


def autopay_notice(name: str, months: int, amount: Money, charge_on: Optional[datetime] = None) -> str:
    return autopay_notice_screen(name, months, amount, charge_on).html()


AUTOPAY_FAILED_SCREEN = ui.push("warn", "Не получилось списать оплату",
                                "Карта могла не пройти платеж. Подписка пока активна, но скоро закончится.",
                                hint="Продли вручную или попробуем еще раз завтра.")
AUTOPAY_TURNED_OFF_FAILS_SCREEN = ui.push("warn", "Автопродление выключено",
                                          "Два раза не получилось списать оплату.",
                                          hint="Продли подписку вручную, это займет минуту.")
AUTOPAY_FAILED = AUTOPAY_FAILED_SCREEN.html()
AUTOPAY_TURNED_OFF_FAILS = AUTOPAY_TURNED_OFF_FAILS_SCREEN.html()


def autopay_stopped_screen(expires_at: Optional[datetime]) -> ui.Screen:
    return ui.result("ok", "Автопродление выключено",
                     ui.field("Подписка действует до", fmt_date_msk(expires_at)) if expires_at else None)


def autopay_stopped(expires_at: Optional[datetime]) -> str:
    return autopay_stopped_screen(expires_at).html()


AUTOPAY_NOTHING_TO_STOP_SCREEN = ui.result("info", "Автопродление и так выключено")
AUTOPAY_NOTHING_TO_STOP = AUTOPAY_NOTHING_TO_STOP_SCREEN.html()


# --- Stars ---------------------------------------------------------------------------------


def stars_invoice_title(name: str) -> str:
    return f"CRS VPN {name}"[:32]


def stars_invoice_description(name: str, months: int, gift: bool = False) -> str:
    lead = "Подарок: " if gift else ""
    return f"{lead}{name}, {months_ru(months)}. Доступ включится сразу после оплаты."


STARS_INVOICE_SENT = ui.toast("Счет в звездах отправлен ниже.")
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
