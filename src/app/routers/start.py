# src/app/routers/start.py
from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from app.logging import logger

router = Router(name="start")

@router.message(CommandStart())
async def cmd_start(m: types.Message):
    logger.info(f"👤 Пользователь {m.from_user.id} (@{m.from_user.username}) запустил бота")
    
    # Убираем старую reply-клавиатуру
    remove_keyboard = types.ReplyKeyboardRemove()
    
    # Создаем inline-кнопки
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")],
        [types.InlineKeyboardButton(text="🧾 Мой тариф", callback_data="my_plan")],
        [types.InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    ])
    
    await m.answer("Привет! Это CRS VPN-бот. Выбирай опцию ниже:",
                   reply_markup=kb)
    
    # Отправляем второе сообщение с убиранием клавиатуры
    await m.answer("Используйте кнопки под сообщением ⬇️", 
                   reply_markup=remove_keyboard)
    
    logger.info("✅ Приветственное сообщение отправлено")

@router.callback_query(lambda c: c.data == "buy_subscription")
async def buy_subscription(callback: types.CallbackQuery):
    logger.info(f"🛒 Пользователь {callback.from_user.id} нажал 'Купить подписку'")
    await callback.answer()
    await callback.message.edit_text(
        "💳 <b>Покупка подписки</b>\n\n"
        "Доступные тарифы:\n"
        "• Базовый - 299₽/месяц\n"
        "• Премиум - 599₽/месяц\n"
        "• Про - 999₽/месяц\n\n"
        "Выберите тариф:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Базовый - 299₽", callback_data="plan_basic")],
            [types.InlineKeyboardButton(text="Премиум - 599₽", callback_data="plan_premium")],
            [types.InlineKeyboardButton(text="Про - 999₽", callback_data="plan_pro")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ])
    )

@router.callback_query(lambda c: c.data == "my_plan")
async def my_plan(callback: types.CallbackQuery):
    logger.info(f"📊 Пользователь {callback.from_user.id} нажал 'Мой тариф'")
    await callback.answer("Проверяю ваш тариф...")
    await callback.message.edit_text(
        "🚧 <b>Функция в разработке</b>\n\n"
        "Просмотр тарифа пока недоступен.\n"
        "Мы работаем над этим! Скоро все будет готово.\n\n"
        "Следите за обновлениями! 🔔",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ])
    )

@router.callback_query(lambda c: c.data == "help")
async def help_info(callback: types.CallbackQuery):
    logger.info(f"❓ Пользователь {callback.from_user.id} нажал 'Помощь'")
    await callback.answer("Показываю справку...")
    await callback.message.edit_text(
        "🚧 <b>Функция в разработке</b>\n\n"
        "Справка пока недоступна.\n"
        "Мы работаем над этим! Скоро все будет готово.\n\n"
        "Следите за обновлениями! 🔔",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ])
    )

@router.callback_query(lambda c: c.data == "plan_basic")
async def plan_basic(callback: types.CallbackQuery):
    logger.info(f"💳 Пользователь {callback.from_user.id} выбрал тариф 'Базовый'")
    await callback.answer("Функция в разработке!")
    await callback.message.edit_text(
        "🚧 <b>Функция в разработке</b>\n\n"
        "Покупка тарифа 'Базовый' пока недоступна.\n"
        "Мы работаем над этим! Скоро все будет готово.\n\n"
        "Следите за обновлениями! 🔔",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
        ])
    )

@router.callback_query(lambda c: c.data == "plan_premium")
async def plan_premium(callback: types.CallbackQuery):
    logger.info(f"💳 Пользователь {callback.from_user.id} выбрал тариф 'Премиум'")
    await callback.answer("Функция в разработке!")
    await callback.message.edit_text(
        "🚧 <b>Функция в разработке</b>\n\n"
        "Покупка тарифа 'Премиум' пока недоступна.\n"
        "Мы работаем над этим! Скоро все будет готово.\n\n"
        "Следите за обновлениями! 🔔",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
        ])
    )

@router.callback_query(lambda c: c.data == "plan_pro")
async def plan_pro(callback: types.CallbackQuery):
    logger.info(f"💳 Пользователь {callback.from_user.id} выбрал тариф 'Про'")
    await callback.answer("Функция в разработке!")
    await callback.message.edit_text(
        "🚧 <b>Функция в разработке</b>\n\n"
        "Покупка тарифа 'Про' пока недоступна.\n"
        "Мы работаем над этим! Скоро все будет готово.\n\n"
        "Следите за обновлениями! 🔔",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
        ])
    )

@router.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    logger.info(f"⬅️ Пользователь {callback.from_user.id} вернулся в главное меню")
    await callback.answer()
    await callback.message.edit_text(
        "Привет! Это CRS VPN-бот. Выбирай опцию ниже:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")],
            [types.InlineKeyboardButton(text="🧾 Мой тариф", callback_data="my_plan")],
            [types.InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
        ])
    )
