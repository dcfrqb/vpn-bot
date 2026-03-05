"""
Роутер платежей — ручная модерация через админа.
"""
from datetime import datetime, timezone

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings, is_admin
from app.logger import logger
from app.keyboards import (
    get_subscription_info_keyboard,
    get_main_menu_keyboard,
    get_back_to_plans_keyboard,
)
from app.services.payment_request import (
    generate_req_id,
    build_payreq_block,
    parse_payreq_block,
    verify_payreq,
)
from app.services.remna_service import provision_tariff
from app.services.jsonl_logger import (
    log_payment_event,
    EVENT_PAYMENT_REQUEST_CREATED,
    EVENT_ADMIN_NOTIFIED,
    EVENT_PAYMENT_APPROVED,
    EVENT_PAYMENT_REJECTED,
    EVENT_USER_NOTIFIED,
    EVENT_TELEGRAM_ERROR,
)


router = Router(name="payments")

# Маппинг plan_period -> tariff для provision
def _plan_period_to_tariff(plan_code: str, period_months: int) -> str:
    return f"{plan_code}_{period_months}"


def _parse_pay_callback_data(data: str, prefix: str) -> tuple:
    """Парсит callback_data для pay_yookassa_ или pay_crypto_."""
    parts = data.replace(prefix, "").split("_")
    if len(parts) == 3:
        plan_code, period_months, amount_rub = parts
        return plan_code, int(period_months), int(amount_rub)
    if len(parts) == 1:
        plan_code = parts[0]
        if plan_code == "basic":
            return "basic", 1, 99
        if plan_code == "premium":
            return "premium", 1, 199
    return None


@router.callback_query(F.data.startswith("pay_yookassa_"))
@router.callback_query(F.data.startswith("pay_crypto_"))
async def handle_payment_request(callback: types.CallbackQuery):
    """Обработчик 'Оплатить' (карта или крипто) — создаёт заявку и отправляет админу."""
    await callback.answer("⏳ Создаю заявку...")

    prefix = "pay_yookassa_" if callback.data.startswith("pay_yookassa_") else "pay_crypto_"
    parsed = _parse_pay_callback_data(callback.data, prefix)
    if not parsed:
        await callback.message.edit_text(
            "❌ Неверный формат данных",
            reply_markup=get_back_to_plans_keyboard()
        )
        return

    try:
        plan_code, period_months, amount_rub = parsed

        plan_name = "Базовый тариф" if plan_code == "basic" else "Премиум тариф"
        period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
        tariff = _plan_period_to_tariff(plan_code, period_months)

        req_id = generate_req_id()
        username = f"@{callback.from_user.username}" if callback.from_user.username else f"ID:{callback.from_user.id}"
        name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip() or username

        payreq_block = build_payreq_block(
            req_id=req_id,
            tg_id=callback.from_user.id,
            username=username,
            name=name,
            tariff=tariff,
            amount=amount_rub,
            currency="RUB",
        )

        log_payment_event(
            EVENT_PAYMENT_REQUEST_CREATED,
            req_id=req_id,
            tg_id=callback.from_user.id,
            payload={
                "plan_code": plan_code,
                "period_months": period_months,
                "amount": amount_rub,
                "username": username,
            },
        )

        # Сообщение пользователю
        user_text = (
            "✅ <b>Ваша заявка на оплату отправлена администратору.</b>\n\n"
            "После подтверждения доступ будет выдан автоматически.\n\n"
            "Если оплата не прошла — напишите администратору."
        )
        admin_username = getattr(settings, "ADMIN_SUPPORT_USERNAME", None) or "dcfrq"
        user_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Написать администратору", url=f"https://t.me/{admin_username.replace('@', '')}")],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data="pay_i_paid")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_subscription")],
        ])
        await callback.message.edit_text(user_text, reply_markup=user_keyboard)

        # Сообщение админам
        admin_ids = settings.ADMINS if isinstance(settings.ADMINS, list) else []
        created_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        admin_text = (
            "📋 <b>НОВАЯ ЗАЯВКА НА ОПЛАТУ</b>\n\n"
            f"👤 <b>Пользователь:</b> {username}\n"
            f"🆔 <b>Telegram ID:</b> {callback.from_user.id}\n"
            f"📝 <b>Имя:</b> {name}\n\n"
            f"📦 <b>Тариф:</b> {tariff}\n"
            f"💰 <b>Сумма:</b> {amount_rub} RUB\n\n"
            f"🕐 <b>Время:</b> {created_str}\n\n"
            f"<pre>{payreq_block}</pre>"
        )
        admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Платёж прошёл", callback_data="pay_approve")],
            [InlineKeyboardButton(text="❌ Платёж не прошёл", callback_data="pay_reject")],
            [InlineKeyboardButton(text="📩 Написать пользователю", url=f"tg://user?id={callback.from_user.id}")],
        ])

        for admin_id in admin_ids:
            try:
                await callback.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=admin_keyboard,
                    parse_mode="HTML"
                )
                log_payment_event(EVENT_ADMIN_NOTIFIED, req_id=req_id, tg_id=callback.from_user.id, payload={"admin_id": admin_id})
            except Exception as e:
                logger.error(f"Ошибка отправки заявки админу {admin_id}: {e}")
                log_payment_event(EVENT_TELEGRAM_ERROR, req_id=req_id, payload={"error": str(e), "admin_id": admin_id})

    except ValueError as e:
        logger.error(f"Ошибка создания заявки: {e}")
        await callback.message.edit_text(
            "❌ Ошибка создания заявки. Попробуйте позже.",
            reply_markup=get_back_to_plans_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")
        await callback.message.edit_text(
            "❌ Ошибка создания заявки. Попробуйте позже или обратитесь в поддержку.",
            reply_markup=get_back_to_plans_keyboard()
        )


@router.callback_query(F.data == "pay_i_paid")
async def handle_i_paid(callback: types.CallbackQuery):
    """Пользователь нажал 'Я оплатил' — напоминание подождать."""
    await callback.answer("⏳ Ожидайте подтверждения администратора. Обычно это занимает несколько минут.")


@router.callback_query(F.data == "pay_approve")
async def handle_pay_approve(callback: types.CallbackQuery):
    """Админ подтверждает платёж."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    text = callback.message.text or ""
    pr = parse_payreq_block(text)
    if not pr:
        await callback.answer("❌ Заявка не найдена в сообщении", show_alert=True)
        return

    if pr.status != "NEW":
        await callback.answer("✅ Заявка уже обработана", show_alert=True)
        return

    if not verify_payreq(pr):
        await callback.answer("❌ Ошибка проверки подписи", show_alert=True)
        return

    await callback.answer("⏳ Обрабатываю...")

    success = await provision_tariff(pr.tg_id, pr.tariff, req_id=pr.req_id)
    if not success:
        await callback.answer("❌ Ошибка выдачи доступа. Проверьте логи.", show_alert=True)
        return

    log_payment_event(EVENT_PAYMENT_APPROVED, req_id=pr.req_id, tg_id=pr.tg_id, payload={"admin_id": callback.from_user.id})

    # Уведомление пользователю
    try:
        from app.keyboards import get_subscription_link_keyboard
        await callback.bot.send_message(
            chat_id=pr.tg_id,
            text=(
                "✅ <b>Оплата подтверждена!</b>\n\n"
                "Подписка активирована. Нажмите «Получить ссылку» для настройки VPN."
            ),
            reply_markup=get_subscription_link_keyboard(),
            parse_mode="HTML"
        )
        log_payment_event(EVENT_USER_NOTIFIED, req_id=pr.req_id, tg_id=pr.tg_id, payload={"type": "approved"})
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {pr.tg_id}: {e}")
        log_payment_event(EVENT_TELEGRAM_ERROR, req_id=pr.req_id, payload={"error": str(e)})

    # Редактируем сообщение админа — убираем кнопки, обновляем статус
    resolved_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_block = text.replace("status=NEW", f"status=APPROVED\nadmin_id={callback.from_user.id}\nresolved={resolved_ts}")
    await callback.message.edit_text(new_block, reply_markup=None)
    await callback.answer("✅ Доступ выдан")


@router.callback_query(F.data == "pay_reject")
async def handle_pay_reject(callback: types.CallbackQuery):
    """Админ отклоняет платёж."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    text = callback.message.text or ""
    pr = parse_payreq_block(text)
    if not pr:
        await callback.answer("❌ Заявка не найдена в сообщении", show_alert=True)
        return

    if pr.status != "NEW":
        await callback.answer("✅ Заявка уже обработана", show_alert=True)
        return

    if not verify_payreq(pr):
        await callback.answer("❌ Ошибка проверки подписи", show_alert=True)
        return

    await callback.answer("⏳ Обрабатываю...")

    log_payment_event(EVENT_PAYMENT_REJECTED, req_id=pr.req_id, tg_id=pr.tg_id, payload={"admin_id": callback.from_user.id})

    # Уведомление пользователю
    admin_username = getattr(settings, "ADMIN_SUPPORT_USERNAME", None) or "dcfrq"
    try:
        await callback.bot.send_message(
            chat_id=pr.tg_id,
            text=(
                "❌ <b>Платёж не подтверждён.</b>\n\n"
                "Пожалуйста, свяжитесь с администратором."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Написать администратору", url=f"https://t.me/{admin_username.replace('@', '')}")],
            ]),
            parse_mode="HTML"
        )
        log_payment_event(EVENT_USER_NOTIFIED, req_id=pr.req_id, tg_id=pr.tg_id, payload={"type": "rejected"})
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {pr.tg_id}: {e}")

    # Редактируем сообщение админа
    resolved_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_block = text.replace("status=NEW", f"status=REJECTED\nadmin_id={callback.from_user.id}\nresolved={resolved_ts}")
    await callback.message.edit_text(new_block, reply_markup=None)
    await callback.answer("✅ Заявка отклонена")


@router.callback_query(F.data == "get_subscription_link")
async def get_subscription_link(callback: types.CallbackQuery):
    """Получение ссылки подписки из Remnawave."""
    await callback.answer("⏳ Получаем ссылку...")

    try:
        from app.remnawave.client import RemnaClient
        from app.services.remna_service import ensure_user_in_remnawave

        telegram_id = callback.from_user.id
        remna_user_id = await ensure_user_in_remnawave(
            telegram_id=telegram_id,
            username=callback.from_user.username,
            name=f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip(),
        )

        if not remna_user_id:
            await callback.message.edit_text(
                "❌ Не удалось получить данные. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard(user_id=telegram_id)
            )
            return

        client = RemnaClient()
        try:
            subscription_url = await client.get_user_subscription_url(remna_user_id)
        finally:
            await client.close()

        if subscription_url and subscription_url.strip():
            from app.utils.html import escape_html
            message_text = (
                "🚀 <b>Ссылка для подключения VPN</b>\n\n"
                "Используйте эту ссылку для настройки VPN:\n\n"
                f"<code>{escape_html(subscription_url.strip())}</code>\n\n"
                "💡 Скопируйте ссылку и вставьте в VPN клиент."
            )
            from app.ui.callbacks import build_cb
            from app.ui.screens import ScreenID
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Открыть ссылку", url=subscription_url.strip())],
                [InlineKeyboardButton(text="⬅️ В главное меню", callback_data=build_cb(ScreenID.CONNECT, "back"))],
            ])
            await callback.message.edit_text(message_text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(
                "⚠️ <b>Ссылка недоступна</b>\n\n"
                "У вас нет активной подписки. Приобретите подписку.",
                reply_markup=get_main_menu_keyboard(user_id=telegram_id)
            )
    except Exception as e:
        logger.error(f"Ошибка получения ссылки: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при получении ссылки. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id)
        )
