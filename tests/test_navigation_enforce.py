"""
Тесты для enforce навигации
"""
import pytest
from app.ui.navigation import can_navigate, get_allowed_navigations, ADMIN_SCREENS
from app.ui.screens import ScreenID


class TestCanNavigate:
    """Тесты для can_navigate"""
    
    def test_allowed_navigation_user(self):
        """Тест: разрешенный переход для обычного пользователя"""
        assert can_navigate(ScreenID.MAIN_MENU, ScreenID.CONNECT, "user") is True
        assert can_navigate(ScreenID.MAIN_MENU, ScreenID.HELP, "user") is True
        assert can_navigate(ScreenID.HELP, ScreenID.MAIN_MENU, "user") is True
    
    def test_forbidden_admin_screen_user(self):
        """Тест: запрещенный переход в админский экран для обычного пользователя"""
        assert can_navigate(ScreenID.MAIN_MENU, ScreenID.ADMIN_PANEL, "user") is False
        assert can_navigate(ScreenID.MAIN_MENU, ScreenID.ADMIN_STATS, "user") is False
    
    def test_allowed_admin_screen_admin(self):
        """Тест: разрешенный переход в админский экран для администратора"""
        assert can_navigate(ScreenID.MAIN_MENU, ScreenID.ADMIN_PANEL, "admin") is True
        assert can_navigate(ScreenID.ADMIN_PANEL, ScreenID.ADMIN_STATS, "admin") is True
    
    def test_forbidden_navigation(self):
        """Тест: запрещенный переход (не в таблице навигации)"""
        assert can_navigate(ScreenID.CONNECT, ScreenID.ADMIN_PANEL, "user") is False
        assert can_navigate(ScreenID.HELP, ScreenID.CONNECT, "user") is False
    
    def test_error_screens_always_accessible(self):
        """Тест: ERROR экраны доступны из любого места"""
        assert can_navigate(ScreenID.MAIN_MENU, ScreenID.ERROR, "user") is True
        assert can_navigate(ScreenID.CONNECT, ScreenID.ERROR, "user") is True
        assert can_navigate(ScreenID.ADMIN_PANEL, ScreenID.ERROR, "admin") is True


class TestGetAllowedNavigations:
    """Тесты для get_allowed_navigations"""
    
    def test_allowed_navigations_user(self):
        """Тест: разрешенные переходы для обычного пользователя"""
        allowed = get_allowed_navigations(ScreenID.MAIN_MENU, "user")
        assert ScreenID.CONNECT in allowed
        assert ScreenID.HELP in allowed
        assert ScreenID.SUBSCRIPTION_PLANS in allowed
        # Админские экраны не должны быть в списке
        assert ScreenID.ADMIN_PANEL not in allowed
    
    def test_allowed_navigations_admin(self):
        """Тест: разрешенные переходы для администратора"""
        allowed = get_allowed_navigations(ScreenID.MAIN_MENU, "admin")
        assert ScreenID.CONNECT in allowed
        assert ScreenID.HELP in allowed
        # Админские экраны должны быть в списке
        assert ScreenID.ADMIN_PANEL in allowed


class TestAdminScreens:
    """Тесты для админских экранов"""
    
    def test_admin_screens_list(self):
        """Тест: список админских экранов"""
        assert ScreenID.ADMIN_PANEL in ADMIN_SCREENS
        assert ScreenID.ADMIN_STATS in ADMIN_SCREENS
        assert ScreenID.ADMIN_USERS in ADMIN_SCREENS
        assert ScreenID.ADMIN_PAYMENTS in ADMIN_SCREENS
        assert ScreenID.MAIN_MENU not in ADMIN_SCREENS