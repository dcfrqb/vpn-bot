"""
Модуль для унифицированного отображения подписки
"""
from typing import Optional
from datetime import datetime
from app.utils.html import escape_html
from app.logger import logger


class SubscriptionViewModel:
    """ViewModel для данных подписки - единый источник истины для отображения"""
    
    def __init__(
        self,
        is_active: bool,
        expires_at: Optional[datetime] = None,
        days_left: Optional[int] = None,
        source: str = "unknown"  # "remna", "cache", "db"
    ):
        self.is_active = is_active
        self.expires_at = expires_at
        self.days_left = days_left
        self.source = source
        
        # Валидация: если подписка активна, но нет expires_at - это ошибка данных
        if is_active and expires_at is None:
            logger.warning(
                f"SubscriptionViewModel: is_active=True, но expires_at=None (source={source}). "
                "Это неполные данные - требуется force_remna для получения полной информации."
            )


def render_subscription_block(vm: SubscriptionViewModel) -> str:
    """
    Единая функция рендеринга подписки в HTML формате
    
    Формат для активной подписки:
    <b>✅ Подписка активна</b>
    📅 Действует до: {expires_at}
    Осталось (дней): {days_left}
    
    Args:
        vm: SubscriptionViewModel с данными подписки
        
    Returns:
        HTML-форматированный текст подписки
    """
    if vm.is_active:
        # Активная подписка - ВСЕГДА показываем полную информацию
        if vm.expires_at is None:
            # Критическая ошибка: активная подписка без даты
            logger.error(
                f"render_subscription_block: попытка отобразить активную подписку без expires_at "
                f"(source={vm.source}). Показываем предупреждение вместо данных."
            )
            return (
                "<b>✅ Подписка активна</b>\n"
                "<blockquote>⚠️ Данные о подписке неполные. Обновите информацию.</blockquote>"
            )
        
        # Форматируем дату
        try:
            expires_str = vm.expires_at.strftime("%d.%m.%Y %H:%M")
        except Exception as e:
            logger.error(f"Ошибка форматирования даты expires_at: {e}")
            expires_str = "Не указано"
        
        # Вычисляем days_left, если не передан
        days_left = vm.days_left
        if days_left is None and vm.expires_at:
            now = datetime.utcnow()
            time_diff = vm.expires_at - now
            days_left = time_diff.days
        
        # Формируем блок с blockquote
        subscription_text = "<b>✅ Подписка активна</b>\n"
        subscription_text += "<blockquote>"
        subscription_text += f"📅 Действует до: {escape_html(expires_str)}"
        
        if days_left is not None:
            subscription_text += f"\nОсталось (дней): {escape_html(str(days_left))}"
        else:
            logger.warning(
                f"render_subscription_block: days_left=None для активной подписки "
                f"(expires_at={vm.expires_at}, source={vm.source})"
            )
            subscription_text += "\nОсталось (дней): не указано"
        
        subscription_text += "</blockquote>"
        return subscription_text
        
    elif vm.expires_at is not None:
        # Истекшая подписка (есть дата, но не активна)
        try:
            expires_str = vm.expires_at.strftime("%d.%m.%Y %H:%M")
        except Exception as e:
            logger.error(f"Ошибка форматирования даты expires_at: {e}")
            expires_str = "Не указано"
        
        subscription_text = "<b>❌ Подписка истекла</b>\n"
        subscription_text += f"📅 Истекла: {escape_html(expires_str)}"
        return subscription_text
    else:
        # Подписки нет
        subscription_text = "<b>❌ Подписка не активна</b>"
        return subscription_text


def create_subscription_view_model(
    subscription_status: str,  # "active", "expired", "none"
    expires_at: Optional[datetime] = None,
    days_left: Optional[int] = None,
    source: str = "unknown"
) -> SubscriptionViewModel:
    """
    Создает SubscriptionViewModel из данных синхронизации
    
    Args:
        subscription_status: Статус подписки из SyncResult
        expires_at: Дата окончания подписки
        days_left: Остаток дней (опционально, будет вычислен если None)
        source: Источник данных ("remna", "cache", "db")
        
    Returns:
        SubscriptionViewModel
    """
    is_active = subscription_status == "active"
    
    # Вычисляем days_left, если не передан и есть expires_at
    if days_left is None and expires_at and is_active:
        now = datetime.utcnow()
        time_diff = expires_at - now
        days_left = time_diff.days
    
    return SubscriptionViewModel(
        is_active=is_active,
        expires_at=expires_at,
        days_left=days_left,
        source=source
    )
