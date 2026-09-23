"""Тесты для роутера site_login (вход на сайт через бота).

Покрывает:
- /start login_<valid> с кнопками и без кнопок;
- обычные /start, /sun718, слишком короткий/кривой login_* не перехватываются
  этим роутером и уходят к обычному cmd_start (проверяется через реальный
  Dispatcher.feed_update и порядок роутеров);
- нажатие кнопки sitelogin:*;
- сайт недоступен/таймаут — фолбэк-тексты, исключение наружу не выходит;
- пустой токен — фича выключена, сайт не вызывается.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher, Router, types
from aiogram.fsm.storage.memory import MemoryStorage

from app.routers import site_login


def _user(user_id=555, username="test_user", first_name="Test"):
    return types.User(id=user_id, is_bot=False, first_name=first_name, username=username)


def _message(text, user=None):
    user = user or _user()
    message = MagicMock(spec=types.Message)
    message.message_id = 1
    message.date = None
    message.chat = MagicMock()
    message.chat.id = user.id
    message.chat.type = "private"
    message.from_user = user
    message.text = text
    message.answer = AsyncMock()
    message.bot = AsyncMock()
    return message


def _callback(data, user=None):
    user = user or _user()
    callback = MagicMock(spec=types.CallbackQuery)
    callback.id = "1"
    callback.from_user = user
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.chat = MagicMock()
    callback.message.chat.id = user.id
    return callback


@pytest.fixture(autouse=True)
def _reset_session():
    """Общий session модуля — сбрасываем между тестами, чтобы моки не текли."""
    yield
    site_login._session = None


class _FakeResponse:
    def __init__(self, status=200, json_body=None, json_exc=None):
        self.status = status
        self._json_body = json_body
        self._json_exc = json_exc

    async def json(self, content_type=None):
        if self._json_exc:
            raise self._json_exc
        return self._json_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _mock_session(response=None, side_effect=None):
    session = MagicMock()
    session.closed = False
    if side_effect is not None:
        session.post = MagicMock(side_effect=side_effect)
    else:
        session.post = MagicMock(return_value=response)
    return session


# --- Handler 1: deep link -------------------------------------------------


@pytest.mark.asyncio
async def test_deep_link_with_buttons_sent_as_is():
    message = _message("/start login_abcdefghijklmnop")
    resp_body = {
        "text": "войти на vpn.crs-projects.com?",
        "buttons": [
            {"text": "подтвердить", "data": "sitelogin:ok:11111111-1111-1111-1111-111111111111"},
            {"text": "отмена", "data": "sitelogin:no:11111111-1111-1111-1111-111111111111"},
        ],
    }
    session = _mock_session(_FakeResponse(200, resp_body))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once()
    args, kwargs = message.answer.call_args
    assert args[0] == resp_body["text"]
    keyboard = kwargs["reply_markup"]
    assert [btn.callback_data for row in keyboard.inline_keyboard for btn in row] == [
        "sitelogin:ok:11111111-1111-1111-1111-111111111111",
        "sitelogin:no:11111111-1111-1111-1111-111111111111",
    ]
    assert [btn.text for row in keyboard.inline_keyboard for btn in row] == ["подтвердить", "отмена"]

    # payload и user ушли в теле запроса как есть
    call = session.post.call_args
    assert call.kwargs["json"]["payload"] == "login_abcdefghijklmnop"
    assert call.kwargs["json"]["user"]["id"] == message.from_user.id
    assert call.kwargs["headers"]["X-Internal-Token"] == "shared-secret"


@pytest.mark.asyncio
async def test_deep_link_without_buttons_sends_text_only():
    message = _message("/start login_abcdefghijklmnop")
    resp_body = {"text": "ссылка для входа устарела или уже использована."}
    session = _mock_session(_FakeResponse(200, resp_body))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once_with(resp_body["text"], parse_mode=None)


@pytest.mark.asyncio
async def test_site_unreachable_fallback_text_no_exception():
    message = _message("/start login_abcdefghijklmnop")
    session = _mock_session(side_effect=site_login.aiohttp.ClientConnectionError("boom"))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once_with(site_login.SITE_UNAVAILABLE_TEXT, parse_mode=None)


@pytest.mark.asyncio
async def test_site_timeout_fallback_text_no_exception():
    message = _message("/start login_abcdefghijklmnop")
    session = _mock_session(side_effect=asyncio.TimeoutError())

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once_with(site_login.SITE_UNAVAILABLE_TEXT, parse_mode=None)


@pytest.mark.asyncio
async def test_non_200_status_fallback_text():
    message = _message("/start login_abcdefghijklmnop")
    session = _mock_session(_FakeResponse(500))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once_with(site_login.SITE_UNAVAILABLE_TEXT, parse_mode=None)


@pytest.mark.asyncio
async def test_bad_json_fallback_text():
    message = _message("/start login_abcdefghijklmnop")
    session = _mock_session(_FakeResponse(200, json_exc=ValueError("bad json")))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once_with(site_login.SITE_UNAVAILABLE_TEXT, parse_mode=None)


@pytest.mark.asyncio
async def test_empty_token_feature_off_site_never_called():
    message = _message("/start login_abcdefghijklmnop")
    session = _mock_session()

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", None), \
         patch.object(site_login, "_get_session", return_value=session):
        from aiogram.filters import CommandObject
        command = CommandObject(prefix="/", command="start", args="login_abcdefghijklmnop")
        await site_login.cmd_site_login(message, command)

    message.answer.assert_awaited_once_with(site_login.FEATURE_OFF_TEXT, parse_mode=None)
    session.post.assert_not_called()


# --- Handler 2: callback ---------------------------------------------------


@pytest.mark.asyncio
async def test_callback_posts_data_and_user_id_then_edits_message():
    callback = _callback("sitelogin:ok:11111111-1111-1111-1111-111111111111")
    resp_body = {"text": "готово. вернись в браузер, вход уже выполнен."}
    session = _mock_session(_FakeResponse(200, resp_body))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        await site_login.cb_site_login(callback)

    call = session.post.call_args
    assert call.kwargs["json"] == {
        "data": "sitelogin:ok:11111111-1111-1111-1111-111111111111",
        "user_id": callback.from_user.id,
    }
    callback.answer.assert_awaited_once_with()
    callback.message.edit_text.assert_awaited_once_with(resp_body["text"], parse_mode=None)


@pytest.mark.asyncio
async def test_callback_site_unreachable_fallback_alert_message_unchanged():
    callback = _callback("sitelogin:no:11111111-1111-1111-1111-111111111111")
    session = _mock_session(side_effect=site_login.aiohttp.ClientConnectionError("boom"))

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session):
        await site_login.cb_site_login(callback)

    callback.answer.assert_awaited_once_with(site_login.SITE_UNAVAILABLE_CALLBACK_TEXT, show_alert=False)
    callback.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_empty_token_feature_off_site_never_called():
    callback = _callback("sitelogin:ok:11111111-1111-1111-1111-111111111111")
    session = _mock_session()

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", None), \
         patch.object(site_login, "_get_session", return_value=session):
        await site_login.cb_site_login(callback)

    callback.answer.assert_awaited_once_with(site_login.FEATURE_OFF_TEXT, show_alert=False)
    session.post.assert_not_called()
    callback.message.edit_text.assert_not_awaited()


# --- Routing: only the right /start payloads reach this router ------------


def _fresh_site_login_router() -> Router:
    """Роутер site_login можно include_router только один раз за время его
    жизни (aiogram запрещает переприкреплять роутер к другому диспетчеру),
    а тест на порядок роутеров гоняется параметризованно — поэтому здесь
    заново регистрируем те же хендлеры с теми же фильтрами на свежем Router."""
    from aiogram.filters import CommandStart

    r = Router(name="site_login_fresh")
    r.message.register(site_login.cmd_site_login, CommandStart(deep_link=True), site_login._is_login_deep_link)
    r.callback_query.register(
        site_login.cb_site_login, lambda c: c.data is not None and c.data.startswith("sitelogin:")
    )
    return r


def _build_dispatcher(spy_start):
    """Диспетчер с site_login-роутером и фейковым start-роутером за ним,
    как в app.main.setup_dispatcher (site_login регистрируется первым)."""
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(_fresh_site_login_router())

    fallback_router = Router(name="fallback_start")

    @fallback_router.message()
    async def _fallback_start(message: types.Message):
        await spy_start(message.text)

    dp.include_router(fallback_router)
    return dp


def _real_update(text, user_id=555):
    """Настоящий (не MagicMock) types.Update — feed_update ревалидирует
    update через pydantic (model_dump/model_validate) для привязки к bot,
    так что моки-заглушки Message здесь не проходят."""
    from datetime import datetime

    chat = types.Chat(id=user_id, type="private")
    user = types.User(id=user_id, is_bot=False, first_name="Test", username="test_user")
    message = types.Message(
        message_id=1,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text=text,
    )
    return types.Update(update_id=1, message=message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "/start sun718",
        "/start login_x",  # слишком короткий
        "/start login_bad!chars",  # запрещенный символ
    ],
)
async def test_non_matching_start_payloads_fall_through_to_old_handler(text):
    spy = AsyncMock()
    dp = _build_dispatcher(spy)
    bot = Bot(token="123456:FAKE-TOKEN-FOR-TESTS-0000000000000")

    update = _real_update(text)
    fake_call = AsyncMock()

    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch("aiogram.client.bot.Bot.__call__", fake_call):
        await dp.feed_update(bot, update)

    spy.assert_awaited_once_with(text)
    fake_call.assert_not_awaited()

    await bot.session.close()


@pytest.mark.asyncio
async def test_valid_deep_link_does_not_reach_old_start_handler():
    spy = AsyncMock()
    dp = _build_dispatcher(spy)
    bot = Bot(token="123456:FAKE-TOKEN-FOR-TESTS-0000000000000")

    update = _real_update("/start login_abcdefghijklmnop")
    fake_call = AsyncMock()

    session = _mock_session(_FakeResponse(200, {"text": "ok"}))
    with patch.object(site_login.settings, "SITE_INTERNAL_TOKEN", "shared-secret"), \
         patch.object(site_login, "_get_session", return_value=session), \
         patch("aiogram.client.bot.Bot.__call__", fake_call):
        await dp.feed_update(bot, update)

    spy.assert_not_awaited()
    fake_call.assert_awaited_once()
    sent_method = fake_call.call_args.args[0]
    assert sent_method.text == "ok"

    await bot.session.close()
