"""Вход на сайт через бота (tg-login relay).

Пользователь жмет "войти через telegram" на сайте, попадает в бота по
диплинку /start login_<payload>. Бот пересылает это сайту и показывает
текст и кнопки, которые вернул сайт. Нажатие кнопки тоже пересылается
сайту. Бот ничего не решает сам — все тексты, кнопки и проверки на
стороне сайта (см. docs/САЙТ_2026-09-23/bot-site-login-spec.md).

Регистрируется ДО start-роутера и до catch-all колбэк-роутеров, чтобы
перехватывать свои два случая раньше и не создавать строку в
telegram_users и не сбрасывать навигатор — это не обычный /start.
"""
import re

import aiohttp
from aiogram import Router, types
from aiogram.filters import CommandObject, CommandStart

from app.config import settings
from app.logger import logger

router = Router(name="site_login")

DEEP_LINK_RE = re.compile(r"^login_[A-Za-z0-9_-]{16,58}$")

CLAIM_PATH = "/api/internal/tg-login/claim"
ANSWER_PATH = "/api/internal/tg-login/answer"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=5)

FEATURE_OFF_TEXT = "вход на сайт временно недоступен."
SITE_UNAVAILABLE_TEXT = "сайт сейчас недоступен, попробуй войти чуть позже."
SITE_UNAVAILABLE_CALLBACK_TEXT = "сайт недоступен, попробуй позже"

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """Один общий session на все запросы к сайту, создается лениво."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
    return _session


async def close_session() -> None:
    """Закрыть общий session при graceful shutdown бота."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _build_keyboard(buttons: list[dict]) -> types.InlineKeyboardMarkup:
    rows = [
        [types.InlineKeyboardButton(text=b["text"], callback_data=b["data"])]
        for b in buttons
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _post(path: str, json_body: dict) -> dict | None:
    """POST на внутренний API сайта. None при любой ошибке (сеть, таймаут,
    не-200, битый JSON). Токен и payload в лог не пишем."""
    url = f"{settings.SITE_INTERNAL_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Token": settings.SITE_INTERNAL_TOKEN}
    session = _get_session()
    try:
        async with session.post(url, json=json_body, headers=headers) as resp:
            if resp.status != 200:
                logger.warning(f"site_login: сайт ответил статусом {resp.status}")
                return None
            try:
                return await resp.json(content_type=None)
            except ValueError:
                logger.warning("site_login: сайт вернул невалидный JSON")
                return None
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning(f"site_login: ошибка запроса к сайту ({type(e).__name__})")
        return None


def _is_login_deep_link(message: types.Message, command: CommandObject) -> bool:
    """Фильтр аргумента /start: ровно login_<16-58 base64url-символов>.

    Все остальные /start (обычный, /sun718, слишком короткий/кривой
    login_*) сюда не попадают — этот роутер их не видит, и они уходят
    дальше, к обычному cmd_start в start-роутере.
    """
    return bool(command.args and DEEP_LINK_RE.match(command.args))


@router.message(CommandStart(deep_link=True), _is_login_deep_link)
async def cmd_site_login(message: types.Message, command: CommandObject) -> None:
    """Handler 1: /start login_<payload>."""
    if not settings.SITE_INTERNAL_TOKEN:
        await message.answer(FEATURE_OFF_TEXT)
        return

    user = message.from_user
    body = {
        "payload": command.args,
        "user": {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
        },
    }
    data = await _post(CLAIM_PATH, body)
    if data is None:
        await message.answer(SITE_UNAVAILABLE_TEXT)
        return

    text = data.get("text", "")
    buttons = data.get("buttons")
    if buttons:
        await message.answer(text, reply_markup=_build_keyboard(buttons))
    else:
        await message.answer(text)


@router.callback_query(lambda c: c.data is not None and c.data.startswith("sitelogin:"))
async def cb_site_login(callback: types.CallbackQuery) -> None:
    """Handler 2: нажатие кнопки подтверждения/отмены."""
    if not settings.SITE_INTERNAL_TOKEN:
        await callback.answer(FEATURE_OFF_TEXT, show_alert=False)
        return

    body = {"data": callback.data, "user_id": callback.from_user.id}
    data = await _post(ANSWER_PATH, body)
    if data is None:
        await callback.answer(SITE_UNAVAILABLE_CALLBACK_TEXT, show_alert=False)
        return

    text = data.get("text", "")
    await callback.answer()
    await callback.message.edit_text(text)
