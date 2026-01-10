"""
Renderers для экранов ошибок
"""
from app.ui.viewmodels.error import (
    ErrorViewModel,
    AccessDeniedViewModel,
    RemnaUnavailableViewModel
)
from app.utils.html import escape_html


async def render_error(viewmodel: ErrorViewModel) -> str:
    """Рендерит экран ошибки"""
    text = "<b>❌ Ошибка</b>\n\n"
    text += f"{escape_html(viewmodel.error_message)}\n\n"
    
    if viewmodel.request_id:
        text += f"<i>ID запроса: {escape_html(viewmodel.request_id)}</i>\n"
        text += "Сообщите этот ID администратору для решения проблемы."
    
    return text


async def render_access_denied(viewmodel: AccessDeniedViewModel) -> str:
    """Рендерит экран отказа в доступе"""
    text = "<b>🚫 Доступ запрещен</b>\n\n"
    text += f"{escape_html(viewmodel.reason)}\n\n"
    text += "Если вы считаете, что это ошибка, обратитесь к администратору."
    
    return text


async def render_remna_unavailable(viewmodel: RemnaUnavailableViewModel) -> str:
    """Рендерит экран недоступности Remna"""
    text = "<b>⚠️ Сервис временно недоступен</b>\n\n"
    text += f"{escape_html(viewmodel.message)}\n\n"
    text += "Попробуйте позже или обратитесь к администратору."
    
    return text