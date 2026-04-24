"""
Renderers для payment UI

Единый стиль сообщений о платежах.
Все тексты платежей должны формироваться здесь, не в handlers.
"""
from typing import Optional
from datetime import datetime
from app.utils.html import escape_html


def render_payment_loading(plan_name: str, amount: int) -> str:
    """Рендерит сообщение о создании платежа"""
    return (
        f"⏳ <b>Создание платежа</b>\n\n"
        f"Тариф: {escape_html(plan_name)}\n"
        f"Сумма: {escape_html(str(amount))} ₽\n\n"
        f"Обрабатываем запрос..."
    )


def render_payment_success(payment_id: str, payment_url: str, amount: int) -> str:
    """Рендерит сообщение об успешном создании платежа"""
    return (
        f"✅ <b>Платеж создан</b>\n\n"
        f"ID платежа: <code>{escape_html(payment_id)}</code>\n"
        f"Сумма: {escape_html(str(amount))} ₽\n\n"
        f"Нажмите кнопку ниже для оплаты."
    )


def render_payment_error(error_message: str) -> str:
    """Рендерит сообщение об ошибке платежа"""
    return (
        f"❌ <b>Ошибка создания платежа</b>\n\n"
        f"{escape_html(error_message)}\n\n"
        f"Попробуйте позже или обратитесь к администратору."
    )


def render_payment_notification(
    user_id: int,
    username: Optional[str],
    amount: int,
    payment_id: str,
    status: str
) -> str:
    """Рендерит уведомление администратору о платеже"""
    user_info = f"@{escape_html(username)}" if username else f"ID: {escape_html(str(user_id))}"
    
    status_emoji = {
        "succeeded": "✅",
        "pending": "⏳",
        "canceled": "❌",
        "failed": "❌"
    }.get(status, "❓")
    
    return (
        f"{status_emoji} <b>Уведомление о платеже</b>\n\n"
        f"Пользователь: {user_info}\n"
        f"ID платежа: <code>{escape_html(payment_id)}</code>\n"
        f"Сумма: {escape_html(str(amount))} ₽\n"
        f"Статус: {escape_html(status)}"
    )


