import json
from pathlib import Path

from aiogram import Router, types, F
from aiogram.filters import Command
from app.config import is_admin, settings
from app.utils.html import escape_html
from app.logger import logger
from app.services.stats import get_statistics, get_users_list, get_payments_list
from app.services.sync_service import SyncService, RemnaUnavailableError
from app.ui.screen_manager import get_screen_manager
# UI EXCEPTION: импорт ScreenID для передачи в ScreenManager
from app.ui.screens import ScreenID
from app.navigation.navigator import get_navigator
# UI EXCEPTION: импорт AdminPanelScreen для передачи в ScreenManager
from app.ui.screens.admin import (
    AdminPanelScreen,
    AdminStatsScreen,
    AdminUsersScreen,
    AdminPaymentsScreen
)

router = Router(name="admin")

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Панель администратора или промокод /admin (запрос на выдачу)"""
    if is_admin(message.from_user.id):
        # Админ — показываем панель
        logger.info(f"Администратор {message.from_user.id} открыл панель")
        stats = await get_statistics()
        screen = AdminPanelScreen()
        viewmodel = await screen.create_viewmodel(stats=stats)
        screen_manager = get_screen_manager()
        await screen_manager.show_screen(
            screen_id=ScreenID.ADMIN_PANEL,
            message_or_callback=message,
            viewmodel=viewmodel,
            edit=False,
            user_id=message.from_user.id
        )
        return

    # Не админ — промокод /admin (запрос на выдачу) — только если включён
    if not getattr(settings, "PROMO_ADMIN_ENABLED", True):
        await message.answer("❌ У вас нет прав администратора")
        return

    await _handle_admin_promo_request(message)


async def _handle_admin_promo_request(message: types.Message):
    """Промокод /admin: отправляет запрос админам с inline-клавиатурой."""
    user_id = message.from_user.id
    logger.info(f"Пользователь {user_id} вызвал промокод /admin")
    try:
        sync_service = SyncService()
        sync_result = await sync_service.sync_user_and_subscription(
            telegram_id=user_id,
            tg_username=message.from_user.username,
            tg_first_name=message.from_user.first_name,
            tg_last_name=message.from_user.last_name,
            use_fallback=False,
            use_cache=False,
            force_sync=True,
            force_remna=True,
        )
        if sync_result.subscription_status == "active":
            await message.answer("❌ У вас уже есть активная подписка.")
            return
    except RemnaUnavailableError:
        await message.answer("❌ Не удалось проверить статус подписки. Попробуйте позже.")
        return
    except Exception as e:
        logger.error(f"Ошибка /admin promo для {user_id}: {e}")
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return

    name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or message.from_user.username or f"User_{user_id}"
    admin_msg = (
        f"👤 <b>Запрос на доступ (промокод /admin)</b>\n\n"
        f"Имя: {name}\n"
        f"Username: @{message.from_user.username or 'не указан'}\n"
        f"Telegram ID: <code>{user_id}</code>\n\n"
        f"Выдайте Premium или отклоните запрос."
    )
    admin_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Выдать Premium на 1 месяц", callback_data=f"admin_promo_grant_1m_{user_id}")],
        [types.InlineKeyboardButton(text="Выдать Premium на 3 месяца", callback_data=f"admin_promo_grant_3m_{user_id}")],
        [types.InlineKeyboardButton(text="Выдать Premium навсегда", callback_data=f"admin_promo_grant_forever_{user_id}")],
        [types.InlineKeyboardButton(text="Отклонить", callback_data=f"admin_promo_reject_{user_id}")],
        [types.InlineKeyboardButton(text="📩 Написать пользователю", url=f"tg://user?id={user_id}")],
    ])
    for admin_id in (settings.ADMINS or []):
        if isinstance(admin_id, int):
            try:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=admin_msg,
                    reply_markup=admin_keyboard,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки /admin promo админу {admin_id}: {e}")
    await message.answer("⏳ Запрос отправлен администратору. Ожидайте ответа.")


@router.message(Command("payments_new"))
async def cmd_payments_new(message: types.Message):
    """Показывает новые заявки на оплату из logs/payments.jsonl"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав")
        return
    log_dir = Path(getattr(settings, "LOG_DIR", None) or Path(__file__).resolve().parents[3] / "logs")
    path = log_dir / "payments.jsonl"
    if not path.exists():
        await message.answer("📋 Лог платежей пуст")
        return
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("event") == "payment_request_created":
                    lines.append(rec)
            except json.JSONDecodeError:
                continue
    if not lines:
        await message.answer("📋 Новых заявок нет")
        return
    text = "📋 <b>Новые заявки на оплату</b>\n\n"
    for i, rec in enumerate(lines[-10:], 1):
        payload = rec.get("payload", {})
        req_id = escape_html(str(rec.get("req_id", "")))
        tg_id = escape_html(str(rec.get("tg_id", "")))
        amount = escape_html(str(payload.get("amount", "")))
        text += f"{i}. req_id={req_id} tg_id={tg_id} {amount} RUB\n"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("payment_find"))
async def cmd_payment_find(message: types.Message):
    """Ищет заявку по req_id в logs/payments.jsonl"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав")
        return
    parts = message.text.split(maxsplit=1)
    req_id = parts[1].strip() if len(parts) > 1 else None
    if not req_id:
        await message.answer("Использование: /payment_find PRQ-XXXXX")
        return
    log_dir = Path(getattr(settings, "LOG_DIR", None) or Path(__file__).resolve().parents[3] / "logs")
    path = log_dir / "payments.jsonl"
    if not path.exists():
        await message.answer("📋 Лог платежей пуст")
        return
    found = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("req_id") == req_id:
                    found.append(rec)
            except json.JSONDecodeError:
                continue
    if not found:
        await message.answer(f"Заявка {escape_html(req_id)} не найдена")
        return
    text = f"📋 <b>Заявка {escape_html(req_id)}</b>\n\n"
    for rec in found:
        ev = escape_html(str(rec.get("event", "")))
        ts = escape_html(str(rec.get("ts", "")))
        tg_id = escape_html(str(rec.get("tg_id", "")))
        payload_str = escape_html(str(rec.get("payload", {})))
        text += f"event={ev} ts={ts}\ntg_id={tg_id} payload={payload_str}\n\n"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("stats"))
async def admin_stats(message: types.Message):
    """Статистика для администратора"""
    if not is_admin(message.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await message.answer("❌ У вас нет прав администратора")
        return
    
    stats = await get_statistics()
    
    # UI EXCEPTION: прямой вызов UI метода
    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Пользователи:</b> {stats['total_users']}\n"
        f"💳 <b>Платежи:</b> {stats['total_payments']}\n"
        f"🔄 <b>Активные подписки:</b> {stats['active_subscriptions']}\n"
        f"💰 <b>Доход:</b> {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽"
    )

@router.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: types.CallbackQuery):
    """Список пользователей - использует ScreenManager через Navigator"""
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Используем ScreenManager для обработки действия через Navigator
    screen_manager = get_screen_manager()
    
    # Определяем текущий экран из Navigator
    navigator = get_navigator()
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.ADMIN_PANEL
    
    # Обрабатываем действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="users",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page_callback(callback: types.CallbackQuery):
    """Пагинация списка пользователей - использует ScreenManager"""
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    page = int(callback.data.split("_")[-1])
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()  # Быстрый ответ
    
    users_data = await get_users_list(page=page, page_size=10)
    
    screen = AdminUsersScreen()
    viewmodel = await screen.create_viewmodel(
        users=users_data["users"],
        page=users_data["page"],
        total_pages=users_data["total_pages"],
        total=users_data["total"]
    )
    
    screen_manager = get_screen_manager()
    await screen_manager.show_screen(
        screen_id=ScreenID.ADMIN_USERS,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data == "admin_payments")
async def admin_payments_callback(callback: types.CallbackQuery):
    """Список платежей - использует ScreenManager через Navigator"""
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Используем ScreenManager для обработки действия через Navigator
    screen_manager = get_screen_manager()
    
    # Определяем текущий экран из Navigator
    navigator = get_navigator()
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.ADMIN_PANEL
    
    # Обрабатываем действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="payments",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data.startswith("admin_payments_"))
async def admin_payments_filter_callback(callback: types.CallbackQuery):
    """Фильтрация и пагинация платежей"""
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    data_parts = callback.data.split("_")
    
    if len(data_parts) >= 4 and data_parts[2] == "page":
        page = int(data_parts[3])
        if len(data_parts) > 4:
            status_str = data_parts[4]
            status_map = {
                "all": None,
                "succeeded": "succeeded",
                "pending": "pending",
                "canceled": "canceled",
                "failed": "failed"
            }
            status = status_map.get(status_str, None)
        else:
            status = None
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer()  # Быстрый ответ
    else:
        filter_type = data_parts[2]
        status_map = {
            "all": None,
            "succeeded": "succeeded",
            "pending": "pending",
            "canceled": "canceled",
            "failed": "failed"
        }
        status = status_map.get(filter_type)
        page = 1
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer()  # Быстрый ответ
    
    payments_data = await get_payments_list(page=page, page_size=10, status=status)
    
    if not payments_data["payments"]:
        filter_text = {
            None: "Все",
            "succeeded": "Успешные",
            "pending": "Ожидающие",
            "canceled": "Отмененные",
            "failed": "Неудачные"
        }.get(status, "Все")
        
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            f"💳 <b>История платежей</b>\n\n"
            f"Фильтр: {filter_text}\n"
            f"Платежей не найдено.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
                [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
                [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
                [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
            ])
        )
        return
    
    filter_text = {
        None: "Все",
        "succeeded": "Успешные",
        "pending": "Ожидающие",
        "canceled": "Отмененные",
        "failed": "Неудачные"
    }.get(status, "Все")
    
    text = f"💳 <b>История платежей</b>\n\n"
    text += f"Фильтр: {filter_text}\n"
    text += f"Всего: {payments_data['total']}\n"
    text += f"Страница {payments_data['page']} из {payments_data['total_pages']}\n\n"
    
    for i, payment in enumerate(payments_data["payments"], 1):
        status_emoji = {
            "succeeded": "✅",
            "pending": "⏳",
            "canceled": "❌",
            "failed": "⚠️"
        }.get(payment["status"], "❓")
        
        text += (
            f"{i}. {status_emoji} {payment['amount']:.2f}{payment['currency']} - @{payment['username']}\n"
            f"   Статус: {payment['status']} | {payment['provider']}\n"
        )
    
    keyboard = []
    if payments_data["total_pages"] > 1:
        nav_buttons = []
        if payments_data["page"] > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", 
                callback_data=f"admin_payments_page_{payments_data['page'] - 1}_{status or 'all'}"
            ))
        if payments_data["page"] < payments_data["total_pages"]:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", 
                callback_data=f"admin_payments_page_{payments_data['page'] + 1}_{status or 'all'}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
        [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
        [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])
    
    # UI EXCEPTION: прямой вызов UI метода
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: types.CallbackQuery):
    """Возврат в админ-панель - использует ScreenManager через Navigator"""
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Используем ScreenManager для обработки BACK действия через Navigator
    screen_manager = get_screen_manager()
    
    # Определяем текущий экран из Navigator
    navigator = get_navigator()
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.ADMIN_PANEL
    
    # Обрабатываем BACK действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="back",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data.startswith("admin_grant_forever_"))
async def admin_grant_forever(callback: types.CallbackQuery):
    """Запросы обрабатываются вручную через Remnawave."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await callback.answer("Используйте Remnawave для выдачи доступа", show_alert=True)


@router.callback_query(F.data.startswith("admin_grant_"))
async def admin_grant_access(callback: types.CallbackQuery):
    """Запросы на доступ обрабатываются вручную через Remnawave."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await callback.answer("Используйте Remnawave для выдачи доступа", show_alert=True)


@router.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_access(callback: types.CallbackQuery):
    """Запросы обрабатываются вручную."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await callback.answer("Запросы обрабатываются вручную", show_alert=True)


# --- /friend handlers (NoDB subscription): Premium 1m, 3m, forever, reject ---

_FRIEND_PROCESSED_MARKER = "✅ Обработано"


def _parse_friend_user_id(callback_data: str, prefix: str) -> int | None:
    """Извлекает user_id из callback_data: friend_grant_1m_12345 -> 12345"""
    try:
        suffix = callback_data[len(prefix):]
        return int(suffix)
    except (ValueError, TypeError):
        return None


_FRIEND_GRANT_MAP = {
    "1m": ("premium_1", "1 месяц"),
    "3m": ("premium_3", "3 месяца"),
    "forever": ("premium_forever", "всегда"),
}


async def _handle_friend_grant(callback: types.CallbackQuery, key: str) -> bool:
    """Выдаёт Premium подписку через NoDB. key: 1m, 3m, forever."""
    if not callback.data or key not in _FRIEND_GRANT_MAP:
        return False
    tariff_code, period_label = _FRIEND_GRANT_MAP[key]
    prefix = f"friend_grant_{key}_"
    if not callback.data.startswith(prefix):
        return False
    user_id = _parse_friend_user_id(callback.data, prefix)
    if not user_id:
        await callback.answer("❌ Ошибка формата", show_alert=True)
        return False

    text = callback.message.text or ""
    if _FRIEND_PROCESSED_MARKER in text:
        await callback.answer("✅ Запрос уже обработан", show_alert=True)
        return False

    # M-3: Redis dedup prevents double-grant on concurrent clicks
    from app.services.cache import get_redis_client as _get_redis
    _redis = _get_redis()
    _dedup_key = f"grant_dedup:{callback.data}"
    if _redis:
        _acquired = await _redis.set(_dedup_key, "1", ex=300, nx=True)
        if not _acquired:
            await callback.answer("✅ Запрос уже обрабатывается", show_alert=True)
            return False

    from app.services.remna_service import provision_tariff
    from app.keyboards import get_subscription_link_keyboard

    success = await provision_tariff(user_id, tariff_code, req_id=f"friend_admin_{callback.from_user.id}")
    if not success:
        await callback.answer("❌ Ошибка выдачи доступа. Проверьте логи.", show_alert=True)
        return False

    try:
        await callback.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Вам выдан доступ!</b>\n\n"
                f"Premium на {period_label}. Нажмите «Получить ссылку» для настройки VPN."
            ),
            reply_markup=get_subscription_link_keyboard(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    new_text = text.split("\n\n")[0] + f"\n\n{_FRIEND_PROCESSED_MARKER}\n⭐ Premium на {period_label} выдан администратором."
    await callback.message.edit_text(new_text, reply_markup=None)
    await callback.answer(f"✅ Premium на {period_label} выдан")
    return True


async def _handle_friend_reject(callback: types.CallbackQuery) -> bool:
    """Отклоняет запрос /friend."""
    if not callback.data or not callback.data.startswith("friend_reject_"):
        return False
    user_id = _parse_friend_user_id(callback.data, "friend_reject_")
    if not user_id:
        await callback.answer("❌ Ошибка формата", show_alert=True)
        return False

    text = callback.message.text or ""
    if _FRIEND_PROCESSED_MARKER in text:
        await callback.answer("✅ Запрос уже обработан", show_alert=True)
        return False

    admin_username = getattr(settings, "ADMIN_SUPPORT_USERNAME", None) or "dcfrq"
    try:
        await callback.bot.send_message(
            chat_id=user_id,
            text="❌ Ваш запрос на доступ отклонён. Свяжитесь с администратором при необходимости.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✍️ Написать", url=f"https://t.me/{admin_username.replace('@', '')}")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    new_text = text.split("\n\n")[0] + f"\n\n{_FRIEND_PROCESSED_MARKER}\n❌ Запрос отклонён."
    await callback.message.edit_text(new_text, reply_markup=None)
    await callback.answer("✅ Запрос отклонён")
    return True


@router.callback_query(F.data.startswith("friend_grant_1m_"))
async def friend_grant_1m(callback: types.CallbackQuery):
    """Выдать Premium на 1 месяц."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_friend_grant(callback, "1m")


@router.callback_query(F.data.startswith("friend_grant_3m_"))
async def friend_grant_3m(callback: types.CallbackQuery):
    """Выдать Premium на 3 месяца."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_friend_grant(callback, "3m")


@router.callback_query(F.data.startswith("friend_grant_forever_"))
async def friend_grant_forever(callback: types.CallbackQuery):
    """Выдать Premium навсегда."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_friend_grant(callback, "forever")


@router.callback_query(F.data.startswith("friend_reject_"))
async def friend_reject(callback: types.CallbackQuery):
    """Отклонить запрос на доступ."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_friend_reject(callback)


# --- /admin promo handlers (admin_promo_grant_*, admin_promo_reject_*) ---

def _parse_admin_promo_user_id(callback_data: str, prefix: str) -> int | None:
    """Извлекает user_id из callback_data: admin_promo_grant_1m_12345 -> 12345"""
    return _parse_friend_user_id(callback_data, prefix)


async def _handle_admin_promo_grant(callback: types.CallbackQuery, key: str) -> bool:
    """Выдаёт Premium через промокод /admin. key: 1m, 3m, forever."""
    if key not in _FRIEND_GRANT_MAP:
        return False
    tariff_code, period_label = _FRIEND_GRANT_MAP[key]
    prefix = f"admin_promo_grant_{key}_"
    if not callback.data or not callback.data.startswith(prefix):
        return False
    user_id = _parse_admin_promo_user_id(callback.data, prefix)
    if not user_id:
        await callback.answer("❌ Ошибка формата", show_alert=True)
        return False

    text = callback.message.text or ""
    if _FRIEND_PROCESSED_MARKER in text:
        await callback.answer("✅ Запрос уже обработан", show_alert=True)
        return False

    # M-3: Redis dedup prevents double-grant on concurrent clicks
    from app.services.cache import get_redis_client as _get_redis
    _redis = _get_redis()
    _dedup_key = f"grant_dedup:{callback.data}"
    if _redis:
        _acquired = await _redis.set(_dedup_key, "1", ex=300, nx=True)
        if not _acquired:
            await callback.answer("✅ Запрос уже обрабатывается", show_alert=True)
            return False

    from app.services.remna_service import provision_tariff
    from app.keyboards import get_subscription_link_keyboard

    success = await provision_tariff(user_id, tariff_code, req_id=f"admin_promo_{callback.from_user.id}")
    if not success:
        await callback.answer("❌ Ошибка выдачи доступа. Проверьте логи.", show_alert=True)
        return False

    try:
        await callback.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Вам выдан доступ!</b>\n\n"
                f"Premium на {period_label}. Нажмите «Получить ссылку» для настройки VPN."
            ),
            reply_markup=get_subscription_link_keyboard(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    new_text = text.split("\n\n")[0] + f"\n\n{_FRIEND_PROCESSED_MARKER}\n⭐ Premium на {period_label} выдан администратором."
    await callback.message.edit_text(new_text, reply_markup=None)
    await callback.answer(f"✅ Premium на {period_label} выдан")
    return True


async def _handle_admin_promo_reject(callback: types.CallbackQuery) -> bool:
    """Отклоняет запрос промокода /admin."""
    if not callback.data or not callback.data.startswith("admin_promo_reject_"):
        return False
    user_id = _parse_admin_promo_user_id(callback.data, "admin_promo_reject_")
    if not user_id:
        await callback.answer("❌ Ошибка формата", show_alert=True)
        return False

    text = callback.message.text or ""
    if _FRIEND_PROCESSED_MARKER in text:
        await callback.answer("✅ Запрос уже обработан", show_alert=True)
        return False

    admin_username = getattr(settings, "ADMIN_SUPPORT_USERNAME", None) or "dcfrq"
    try:
        await callback.bot.send_message(
            chat_id=user_id,
            text="❌ Ваш запрос на доступ отклонён. Свяжитесь с администратором при необходимости.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✍️ Написать", url=f"https://t.me/{admin_username.replace('@', '')}")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    new_text = text.split("\n\n")[0] + f"\n\n{_FRIEND_PROCESSED_MARKER}\n❌ Запрос отклонён."
    await callback.message.edit_text(new_text, reply_markup=None)
    await callback.answer("✅ Запрос отклонён")
    return True


@router.callback_query(F.data.startswith("admin_promo_grant_1m_"))
async def admin_promo_grant_1m(callback: types.CallbackQuery):
    """Промокод /admin: выдать Premium на 1 месяц."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_admin_promo_grant(callback, "1m")


@router.callback_query(F.data.startswith("admin_promo_grant_3m_"))
async def admin_promo_grant_3m(callback: types.CallbackQuery):
    """Промокод /admin: выдать Premium на 3 месяца."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_admin_promo_grant(callback, "3m")


@router.callback_query(F.data.startswith("admin_promo_grant_forever_"))
async def admin_promo_grant_forever(callback: types.CallbackQuery):
    """Промокод /admin: выдать Premium навсегда."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_admin_promo_grant(callback, "forever")


@router.callback_query(F.data.startswith("admin_promo_reject_"))
async def admin_promo_reject(callback: types.CallbackQuery):
    """Промокод /admin: отклонить запрос."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return
    await _handle_admin_promo_reject(callback)


# === Обработчики для промокода /solokhin ===

@router.callback_query(F.data.startswith("promo_grant_"))
async def promo_grant_handler(callback: types.CallbackQuery):
    """Выдать промокод (например solokhin) — универсальный обработчик."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    # Формат: promo_grant_{promo_code}_{user_id}_{req_id}
    parts = callback.data.replace("promo_grant_", "").split("_")
    if len(parts) < 3:
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    promo_code = parts[0]
    try:
        user_id = int(parts[1])
        req_id = parts[2]
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    # Проверяем, не обработан ли уже
    text = callback.message.text or ""
    if "✅ Обработано" in text or "❌ Отклонено" in text:
        await callback.answer("✅ Заявка уже обработана", show_alert=True)
        return

    await callback.answer("⏳ Выдаю доступ...")

    # Provision
    from app.services.remna_service import provision_tariff
    tariff = "solokhin_10d"  # /solokhin всегда 10 дней
    success = await provision_tariff(user_id, tariff, req_id=req_id)

    if not success:
        await callback.answer("❌ Ошибка выдачи доступа", show_alert=True)
        return

    # Логируем
    try:
        from app.services.jsonl_logger import log_payment_event
        log_payment_event(
            event="promo_granted",
            req_id=req_id,
            tg_id=user_id,
            payload={"promo_code": promo_code, "tariff": tariff, "admin_id": callback.from_user.id},
        )
    except Exception as e:
        logger.error(f"promo_grant: ошибка логирования: {e}")

    # Уведомляем пользователя
    try:
        from app.keyboards import get_subscription_link_keyboard
        await callback.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Промокод активирован!</b>\n\n"
                "Вам выдан Premium на 10 дней. Нажмите «Получить ссылку» для настройки VPN."
            ),
            reply_markup=get_subscription_link_keyboard(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"promo_grant: ошибка уведомления пользователя {user_id}: {e}")

    # Обновляем сообщение админа
    new_text = text.split("<pre>")[0].strip() + f"\n\n✅ Обработано\nПремиум выдан администратором {callback.from_user.id}"
    try:
        await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("✅ Доступ выдан")


@router.callback_query(F.data.startswith("promo_reject_"))
async def promo_reject_handler(callback: types.CallbackQuery):
    """Отклонить промокод — универсальный обработчик."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    # Формат: promo_reject_{promo_code}_{user_id}_{req_id}
    parts = callback.data.replace("promo_reject_", "").split("_")
    if len(parts) < 3:
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    promo_code = parts[0]
    try:
        user_id = int(parts[1])
        req_id = parts[2]
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    # Проверяем, не обработан ли уже
    text = callback.message.text or ""
    if "✅ Обработано" in text or "❌ Отклонено" in text:
        await callback.answer("✅ Заявка уже обработана", show_alert=True)
        return

    await callback.answer("⏳ Отклоняю...")

    # Логируем
    try:
        from app.services.jsonl_logger import log_payment_event
        log_payment_event(
            event="promo_rejected",
            req_id=req_id,
            tg_id=user_id,
            payload={"promo_code": promo_code, "admin_id": callback.from_user.id},
        )
    except Exception as e:
        logger.error(f"promo_reject: ошибка логирования: {e}")

    # Уведомляем пользователя
    try:
        admin_username = getattr(settings, "ADMIN_SUPPORT_USERNAME", None) or "dcfrq"
        await callback.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Промокод отклонён.</b>\n\n"
                "Если считаете это ошибкой — напишите администратору."
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✍️ Написать", url=f"https://t.me/{admin_username.replace('@', '')}")]
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"promo_reject: ошибка уведомления пользователя {user_id}: {e}")

    # Обновляем сообщение админа
    new_text = text.split("<pre>")[0].strip() + f"\n\n❌ Отклонено администратором {callback.from_user.id}"
    try:
        await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("✅ Заявка отклонена")


# ========== Stage B: operational tools ==========

@router.message(Command("sync"))
async def cmd_sync(message: types.Message):
    """/sync <telegram_id> — форс-синк пользователя с Remnawave (только для админов)"""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer("Использование: /sync <telegram_id>")
        return
    target_id = int(parts[1].strip())
    await _do_sync(message, target_id)


@router.message(Command("syncme"))
async def cmd_syncme(message: types.Message):
    """/syncme — форс-синк самого администратора"""
    if not is_admin(message.from_user.id):
        return
    await _do_sync(message, message.from_user.id)


async def _do_sync(message: types.Message, target_id: int):
    """Выполняет форс-синк пользователя с Remnawave и отправляет результат."""
    await message.answer(f"⏳ Синхронизирую {target_id}...")
    try:
        from app.services.cache import invalidate_sync_cache
        await invalidate_sync_cache(target_id)
    except Exception:
        pass
    try:
        sync_service = SyncService()
        result = await sync_service.sync_user_and_subscription(
            telegram_id=target_id,
            use_cache=False,
            force_sync=True,
            force_remna=True,
        )
        status_map = {"active": "✅ активна", "expired": "⚠️ истекла", "none": "❌ нет подписки"}
        status_str = status_map.get(result.subscription_status, result.subscription_status)
        expires = result.expires_at.strftime("%Y-%m-%d %H:%M UTC") if result.expires_at else "—"
        remna_uuid = result.user_remna_uuid or "—"
        await message.answer(
            f"<b>Sync {target_id}</b>\n"
            f"Remna UUID: <code>{escape_html(remna_uuid)}</code>\n"
            f"Статус: {status_str}\n"
            f"Истекает: {escape_html(expires)}\n"
            f"Источник: {escape_html(result.source)}",
            parse_mode="HTML",
        )
        logger.info(f"admin sync: target={target_id} by={message.from_user.id} status={result.subscription_status}")
    except RemnaUnavailableError:
        await message.answer("❌ Remnawave недоступен. Попробуйте позже.")
    except Exception as e:
        logger.error(f"admin sync error: target={target_id} err={e}")
        await message.answer(f"❌ Ошибка синхронизации: {escape_html(str(e)[:200])}", parse_mode="HTML")


@router.message(Command("block"))
async def cmd_block(message: types.Message):
    """/block <telegram_id> — заблокировать пользователя (только для админов)"""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer("Использование: /block <telegram_id>")
        return
    target_id = int(parts[1].strip())
    if is_admin(target_id):
        await message.answer("❌ Нельзя заблокировать администратора.")
        return
    from app.middlewares.blocklist import block_user
    await block_user(target_id)
    await message.answer(f"✅ Пользователь <code>{target_id}</code> заблокирован.", parse_mode="HTML")


@router.message(Command("unblock"))
async def cmd_unblock(message: types.Message):
    """/unblock <telegram_id> — разблокировать пользователя (только для админов)"""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer("Использование: /unblock <telegram_id>")
        return
    target_id = int(parts[1].strip())
    from app.middlewares.blocklist import unblock_user
    await unblock_user(target_id)
    await message.answer(f"✅ Пользователь <code>{target_id}</code> разблокирован.", parse_mode="HTML")


@router.message(Command("whois"))
async def cmd_whois(message: types.Message):
    """/whois <telegram_id> — краткая диагностика пользователя (только для админов)"""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer("Использование: /whois <telegram_id>")
        return
    target_id = int(parts[1].strip())
    await message.answer(f"⏳ Запрашиваю данные {target_id}...")
    try:
        from app.middlewares.blocklist import is_blocked
        sync_service = SyncService()
        result = await sync_service.sync_user_and_subscription(
            telegram_id=target_id,
            use_cache=False,
            force_sync=True,
            force_remna=True,
        )
        status_map = {"active": "✅ активна", "expired": "⚠️ истекла", "none": "❌ нет подписки"}
        status_str = status_map.get(result.subscription_status, result.subscription_status)
        expires = result.expires_at.strftime("%Y-%m-%d %H:%M UTC") if result.expires_at else "—"
        blocked_str = "🚫 да" if is_blocked(target_id) else "нет"
        admin_str = "👑 да" if is_admin(target_id) else "нет"
        remna_uuid = result.user_remna_uuid or "—"
        await message.answer(
            f"<b>Whois {target_id}</b>\n"
            f"Remna UUID: <code>{escape_html(remna_uuid)}</code>\n"
            f"Статус подписки: {status_str}\n"
            f"Истекает: {escape_html(expires)}\n"
            f"Заблокирован: {blocked_str}\n"
            f"Администратор: {admin_str}",
            parse_mode="HTML",
        )
    except RemnaUnavailableError:
        await message.answer("❌ Remnawave недоступен. Попробуйте позже.")
    except Exception as e:
        logger.error(f"admin whois error: target={target_id} err={e}")
        await message.answer(f"❌ Ошибка: {escape_html(str(e)[:200])}", parse_mode="HTML")
