"""
LEGACY MENU BUILDER - DEPRECATED

DO NOT USE. UI must be rendered via ScreenManager.

This module is kept for backward compatibility.
The build_main_menu_text function is used as an adapter in ui/renderers/main_menu.py.

All new UI code MUST use:
- ui/screens/* for Screen classes
- ui/renderers/* for renderers
- ScreenManager.show_screen() for displaying UI

This module will be removed in a future version.
"""
import warnings
from typing import Optional
from datetime import datetime
from app.utils.html import escape_html, safe_format_user_name
from app.routers.subscription_view import SubscriptionViewModel, render_subscription_block

# Show deprecation warning
warnings.warn(
    "app.routers.menu_builder is deprecated. Use ui.renderers and ScreenManager instead.",
    DeprecationWarning,
    stacklevel=2
)


class MenuData:
    """DTO для данных главного меню"""
    def __init__(
        self,
        user_id: int,
        user_first_name: Optional[str] = None,
        user_last_name: Optional[str] = None,
        user_username: Optional[str] = None,
        subscription_view_model: Optional[SubscriptionViewModel] = None
    ):
        self.user_id = user_id
        self.user_first_name = user_first_name
        self.user_last_name = user_last_name
        self.user_username = user_username
        self.subscription_view_model = subscription_view_model


def build_main_menu_text(data: MenuData) -> str:
    """
    Строит текст главного меню с безопасным HTML форматированием
    Использует обычный HTML формат без <pre> блоков (как в remnashop)
    
    Args:
        data: Данные для меню
        
    Returns:
        HTML-форматированный текст меню
    """
    # Формируем профиль
    user_name = safe_format_user_name(
        data.user_first_name,
        data.user_last_name,
        data.user_username,
        data.user_id
    )
    
    # Профиль: заголовок жирным, поля на отдельных строках в blockquote
    profile_text = "👤 <b>Профиль:</b>\n"
    profile_text += "<blockquote>"
    profile_text += f"ID: {escape_html(str(data.user_id))}\n"
    profile_text += f"Имя: {escape_html(user_name)}"
    profile_text += "</blockquote>"
    
    # Формируем информацию о подписке через единую функцию рендеринга
    if data.subscription_view_model:
        subscription_text = render_subscription_block(data.subscription_view_model)
    else:
        # Fallback: если ViewModel не передан, создаем пустую подписку
        from app.routers.subscription_view import SubscriptionViewModel
        empty_vm = SubscriptionViewModel(is_active=False, source="unknown")
        subscription_text = render_subscription_block(empty_vm)
    
    # Объединяем текст с пустой строкой между профилем и подпиской
    return f"{profile_text}\n\n{subscription_text}"
