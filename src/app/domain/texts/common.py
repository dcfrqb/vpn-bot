"""Texts: Shared words: buttons Back/Close/Refresh, generic error text, support line.

Owner stream: D (User UI). Created empty by Foundation.
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose.
"""

from app.domain.texts import ui
from app.domain.texts.ui import B, E

# Used by app.bot.middlewares.errors: never show exception text to users.
GENERIC_ERROR_SCREEN = ui.result("error", "Что-то пошло не так", hint="Попробуйте еще раз чуть позже.")
GENERIC_ERROR = GENERIC_ERROR_SCREEN.html()
GENERIC_ERROR_ALERT = ui.toast("Ошибка, попробуйте еще раз")

# --- Buttons: aliases of the dictionary (app.domain.texts.ui.B) ---
BTN_CONNECT = B.CONNECT
BTN_SUBSCRIPTION = B.SUBSCRIPTION
BTN_REFRESH = B.REFRESH
BTN_HELP = B.HELP
BTN_DEVICES = B.DEVICES
BTN_ADMIN_PANEL = B.ADMIN_PANEL
BTN_BACK_MAIN = B.MENU
BTN_BACK = B.BACK
BTN_SUPPORT = B.SUPPORT
BTN_OFFER = B.OFFER
BTN_PRIVACY = B.PRIVACY
BTN_ARTICLE = B.ARTICLE  # the article URL; "🚀 Подключиться" opens the connect screen
BTN_TRIAL = B.TRIAL
BTN_PAY_PREFIX = B.PAY_PREFIX
BTN_CHECK_PAYMENT = B.CHECK_PAYMENT

REFRESHED = ui.toast("Обновлено")

# Legal documents (2.1.1 links). Settings OFFER_URL / PRIVACY_URL override them.
OFFER_URL = "https://telegra.ph/Publichnaya-oferta--CRS-VPN-04-08"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti--CRS-VPN-04-08"


def legal_urls(settings=None) -> tuple[str, str]:
    """(offer, privacy) links: settings first, the 2.1.1 documents otherwise."""
    offer = getattr(settings, "OFFER_URL", None) if settings is not None else None
    privacy = getattr(settings, "PRIVACY_URL", None) if settings is not None else None
    return (offer or OFFER_URL), (privacy or PRIVACY_URL)


def legal_line(offer_url: str = OFFER_URL, privacy_url: str = PRIVACY_URL) -> str:
    """The offer acceptance line under every bill (as in 2.1.1)."""
    return (f"Нажимая «Оплатить», вы принимаете условия {ui.link('оферты', offer_url)} "
            f"и {ui.link('политики конфиденциальности', privacy_url)}.")


DEFAULT_SUPPORT_HANDLE = "dcfrq"


def support_handle(settings) -> "str | None":
    """Support contact: SUPPORT_HANDLE, else ADMIN_SUPPORT_USERNAME (review UX m14)."""
    handle = getattr(settings, "SUPPORT_HANDLE", None) or getattr(settings, "ADMIN_SUPPORT_USERNAME", None)
    return str(handle).strip() if handle else None


def support_url(handle: "str | None" = None) -> str:
    """t.me link to the support contact; falls back to the default handle."""
    h = (handle or DEFAULT_SUPPORT_HANDLE).lstrip("@")
    return f"https://t.me/{h}"


# --- Help screen (article) ---
def help_screen(unlink_enabled: bool = False) -> ui.Screen:
    """FAQ. Unlinking is promised only when DEVICES_UNLINK_ENABLED is on
    (review UX M3); the refresh tip points at the VPN app, not the bot."""
    change_device = (
        "<b>Как сменить устройство:</b> отвяжите старое в «Мои устройства» и "
        "подключите новое той же ссылкой."
        if unlink_enabled else
        "<b>Как сменить устройство:</b> напишите в поддержку. Мы освободим место под новое устройство. Затем "
        "подключите его той же ссылкой."
    )
    return ui.article("Справка по CRS VPN", emoji=E.HELP, sections=[
        ui.block("VPN создает защищенное соединение между вашим устройством и интернетом.",
                 title="Что такое VPN", emoji=E.VPN),
        ui.block(
            "\n\n".join((
                "<b>VPN не подключается:</b> обновите подписку в самом приложении (кнопка обновления "
                "или свайп вниз по списку серверов) и проверьте, что добавлена ссылка из «Подключиться».",
                "<b>Сколько устройств можно подключить:</b> смотрите в разделе «Мои устройства», "
                "лимит зависит от тарифа.",
                change_device,
            )),
            title="Частые вопросы", emoji=E.FAQ,
        ),
    ], hint="Не нашли ответ? Напишите в поддержку.")


def help_text(unlink_enabled: bool = False) -> str:
    return help_screen(unlink_enabled).html()


HELP_TEXT = help_text(False)


# --- fallback router (unknown or retired buttons) and small commands ---
STALE_BUTTON = ui.toast("Эта кнопка устарела, открыл главное меню")
UNKNOWN_MESSAGE_SCREEN = ui.result(
    "info", "Не понял сообщение", "Выберите действие кнопкой ниже или откройте помощь.",
)


def myid_screen(user_id: int, is_admin: bool) -> ui.Screen:
    return ui.result("info", "Ваш Telegram ID", f"<code>{int(user_id)}</code>",
                     "Статус: администратор" if is_admin else None)


def myid_text(user_id: int, is_admin: bool) -> str:
    return myid_screen(user_id, is_admin).html()
