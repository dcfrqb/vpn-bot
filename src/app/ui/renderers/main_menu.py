"""
Renderer для главного меню
"""
from app.ui.viewmodels.main_menu import MainMenuViewModel
from app.ui.legacy import build_main_menu_text, MenuData


async def render_main_menu(viewmodel: MainMenuViewModel) -> str:
    """
    Рендерит текст главного меню
    
    Args:
        viewmodel: MainMenuViewModel с данными
        
    Returns:
        HTML-форматированный текст
    """
    # Используем существующую функцию build_main_menu_text
    menu_data = MenuData(
        user_id=viewmodel.user_id,
        user_first_name=viewmodel.user_first_name,
        user_last_name=viewmodel.user_last_name,
        user_username=viewmodel.user_username,
        subscription_view_model=viewmodel.subscription_view_model
    )
    
    return build_main_menu_text(menu_data)