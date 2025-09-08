# src/app/routers/payments.py
from aiogram import Router, types, F
from app.services.payments.yookassa import create_payment

router = Router(name="payments")

@router.message(F.text == "💳 Купить подписку")
async def buy(m: types.Message):
    url = await create_payment(299, "CRS VPN — 30 дней", m.from_user.id)
    await m.answer(f"Оплатить подписку: {url}")