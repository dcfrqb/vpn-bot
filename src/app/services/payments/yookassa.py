# src/app/services/payments/yookassa.py
from yookassa import Configuration, Payment
from app.config import settings

Configuration.account_id = settings.YOOKASSA_SHOP_ID
Configuration.secret_key = settings.YOOKASSA_API_KEY

async def create_payment(amount_rub: int, description: str, user_id: int) -> str:
    p = Payment.create({
        "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": settings.YOOKASSA_RETURN_URL},
        "description": description,
        "metadata": {"tg_user_id": user_id}
    })
    return p.confirmation.confirmation_url