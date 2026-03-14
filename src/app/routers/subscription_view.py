"""
Модуль для унифицированного отображения подписки
"""
from typing import Optional
from datetime import datetime
from app.utils.html import escape_html
from app.logger import logger

# Русские названия месяцев в родительном падеже
_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _format_expire_date(dt: datetime) -> str:
    """Форматирует дату окончания подписки: «12 апреля 2026»."""
    try:
        return f"{dt.day} {_MONTHS_RU[dt.month]} {dt.year}"
    except Exception:
        return dt.strftime("%d.%m.%Y")


def _days_word(n: int) -> str:
    """Возвращает 'день', 'дня' или 'дней' для числа n."""
    if 11 <= n % 100 <= 14:
        return "дней"
    r = n % 10
    if r == 1:
        return "день"
    if 2 <= r <= 4:
        return "дня"
    return "дней"


def _calc_days_left(expires_at: datetime) -> int:
    """Считает целые календарные дни до истечения (по дате, не по секундам)."""
    expire_date = expires_at.date() if hasattr(expires_at, "date") else expires_at
    today = datetime.utcnow().date()
    return max(0, (expire_date - today).days)


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
    Единая функция рендеринга подписки в HTML формате.

    Активная:
      🟢 Подписка активна
      📅 До: 12 апреля 2026
      ⏳ Осталось: 7 дней

    Истекшая:
      🔴 Подписка истекла
      📅 Истекла: 05 марта 2026
      💡 Нажмите «Подписка», чтобы продлить доступ.

    Нет подписки:
      💡 Подписка не оформлена
         Нажмите «Подписка» для активации VPN.
    """
    if vm.is_active:
        if vm.expires_at is None:
            logger.error(
                f"render_subscription_block: активная подписка без expires_at "
                f"(source={vm.source}). Показываем предупреждение."
            )
            return (
                "<b>🟢 Подписка активна</b>\n"
                "<blockquote>⚠️ Данные о подписке неполные. Нажмите «Обновить».</blockquote>"
            )

        expires_str = _format_expire_date(vm.expires_at)

        days_left = vm.days_left if vm.days_left is not None else _calc_days_left(vm.expires_at)

        text = "<b>🟢 Подписка активна</b>\n"
        text += "<blockquote>"
        text += f"📅 До: {escape_html(expires_str)}\n"
        if days_left > 0:
            text += f"⏳ Осталось: {days_left} {_days_word(days_left)}"
        else:
            text += "⏳ Истекает сегодня"
        text += "</blockquote>"
        return text

    elif vm.expires_at is not None:
        expires_str = _format_expire_date(vm.expires_at)
        text = "<b>🔴 Подписка истекла</b>\n"
        text += "<blockquote>"
        text += f"📅 Истекла: {escape_html(expires_str)}\n"
        text += "💡 Нажмите «Подписка», чтобы продлить доступ."
        text += "</blockquote>"
        return text

    else:
        return (
            "<b>💡 Подписка не оформлена</b>\n"
            "<blockquote>Нажмите «Подписка» для активации VPN.</blockquote>"
        )


def create_subscription_view_model(
    subscription_status: str,  # "active", "expired", "none"
    expires_at: Optional[datetime] = None,
    days_left: Optional[int] = None,
    source: str = "unknown"
) -> SubscriptionViewModel:
    """
    Создает SubscriptionViewModel из данных синхронизации.
    """
    is_active = subscription_status == "active"

    # Вычисляем days_left по календарным дням, если не передан
    if days_left is None and expires_at and is_active:
        days_left = _calc_days_left(expires_at)

    return SubscriptionViewModel(
        is_active=is_active,
        expires_at=expires_at,
        days_left=days_left,
        source=source
    )
