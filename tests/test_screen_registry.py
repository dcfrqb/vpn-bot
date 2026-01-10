"""
Тесты для реестра экранов
"""
import pytest
from app.ui.screen_registry import get_screen_registry
from app.ui.screens import ScreenID


class TestScreenRegistry:
    """Тесты реестра экранов"""
    
    def test_all_screen_ids_registered(self):
        """Тест: все ScreenID зарегистрированы"""
        registry = get_screen_registry()
        
        # Экраны, которые не требуют регистрации (deprecated или не реализованы)
        OPTIONAL_SCREENS = {
            ScreenID.SUBSCRIPTION,  # Не используется, заменен на SUBSCRIPTION_PLANS
            ScreenID.ADMIN_GRANTS,  # Пока не реализован
            ScreenID.CONNECT_SUCCESS,  # DEPRECATED, заменен на CONNECT со status="success"
        }
        
        all_screen_ids = set(ScreenID) - OPTIONAL_SCREENS
        registered_ids = registry.get_all_screen_ids()
        
        missing = all_screen_ids - registered_ids
        if missing:
            pytest.fail(f"Не зарегистрированы экраны: {missing}")
    
    def test_no_duplicate_registrations(self):
        """Тест: нет дубликатов в реестре"""
        registry = get_screen_registry()
        
        errors = registry.validate()
        if errors:
            pytest.fail(f"Ошибки валидации реестра: {errors}")
    
    def test_get_screen_class(self):
        """Тест: получение класса экрана"""
        registry = get_screen_registry()
        
        screen_class = registry.get_screen_class(ScreenID.MAIN_MENU)
        assert screen_class is not None
        assert screen_class.__name__ == "MainMenuScreen"
    
    def test_is_registered(self):
        """Тест: проверка регистрации экрана"""
        registry = get_screen_registry()
        
        assert registry.is_registered(ScreenID.MAIN_MENU) is True
        assert registry.is_registered(ScreenID.ERROR) is True
        assert registry.is_registered(ScreenID.ACCESS_DENIED) is True