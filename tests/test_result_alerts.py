"""Result presentation never repeats an operation or discards a long explanation."""
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery

from app.bot.views import result_text, show_result
from tests.fakes.bot import callback_update, make_bot, message_update, user


def test_result_plain_text_preserves_lines_and_escaped_user_input():
    assert result_text('<b>Готово</b>\n<blockquote>Код: &lt;promo&gt; &amp; бонус</blockquote>') == 'Готово\nКод: <promo> & бонус'


async def test_short_result_is_alert_without_chat_message():
    bot, session = make_bot()
    callback = callback_update(bot, user(), 'test').callback_query.as_(bot)
    assert await show_result(callback, '<b>Готово</b>\nНастройка сохранена.')
    assert [c.method for c in session.calls] == ['AnswerCallbackQuery']
    assert session.calls[0].params['show_alert'] is True
    assert session.calls[0].text == 'Готово\nНастройка сохранена.'


async def test_long_result_edits_screen_without_truncation():
    bot, session = make_bot()
    callback = callback_update(bot, user(), 'test').callback_query.as_(bot)
    text = 'Важные условия. ' * 25
    assert not await show_result(callback, text)
    assert session.calls_of('EditMessageText')[0].text == text
    assert not session.calls_of('SendMessage')


async def test_expired_alert_edits_existing_message_without_new_message():
    bot, session = make_bot()
    session.fail['AnswerCallbackQuery'] = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id='x'), message='query is too old')
    callback = callback_update(bot, user(), 'test').callback_query.as_(bot)
    assert not await show_result(callback, '<b>Готово</b>')
    assert session.calls_of('EditMessageText')[0].text == '<b>Готово</b>'
    assert not session.calls_of('SendMessage')


async def test_message_command_has_normal_reply_without_callback():
    bot, session = make_bot()
    message = message_update(user(), '/command').message.as_(bot)
    assert not await show_result(message, 'Готово')
    assert session.calls_of('SendMessage')[0].text == 'Готово'
    assert not session.calls_of('AnswerCallbackQuery')
