"""
Утилиты для работы с HTML в Telegram сообщениях
"""
import html
from typing import Optional


def escape_html(text: str) -> str:
    """
    Экранирует HTML-специальные символы в тексте
    
    Args:
        text: Текст для экранирования
        
    Returns:
        Экранированный текст
    """
    if text is None:
        return ""
    return html.escape(str(text))


def render_pre_block(content: str) -> str:
    """
    Создает безопасный HTML блок <pre> с экранированным содержимым
    
    Args:
        content: Содержимое блока (будет экранировано)
        
    Returns:
        HTML строка с тегом <pre>
    """
    if content is None:
        return "<pre></pre>"
    escaped = escape_html(str(content))
    return f"<pre>{escaped}</pre>"


def safe_format_user_name(first_name: Optional[str], last_name: Optional[str], username: Optional[str], user_id: int) -> str:
    """
    Безопасно форматирует имя пользователя с fallback значениями
    
    Args:
        first_name: Имя пользователя
        last_name: Фамилия пользователя
        username: Username пользователя
        user_id: ID пользователя (для fallback)
        
    Returns:
        Экранированное имя пользователя
    """
    name = f"{first_name or ''} {last_name or ''}".strip()
    if not name:
        name = username or f"User_{user_id}"
    return escape_html(name)
