# src/app/routers/start.py
from aiogram import Router, types
from aiogram.filters import CommandStart

router = Router(name="start")

@router.message(CommandStart())
async def cmd_start(m: types.Message):
    kb = [[types.KeyboardButton(text="💳 Купить подписку")],
          [types.KeyboardButton(text="🧾 Мой тариф")]]
    await m.answer("Привет! Это CRS VPN-бот. Выбирай опцию ниже:",
                   reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))