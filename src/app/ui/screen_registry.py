"""
Реестр экранов - единый источник правды

Регистрирует все ScreenID → Screen class, renderer, keyboard mappings.
ScreenManager использует этот реестр для получения экранов.
"""
from typing import Dict, Type, Callable, Optional
from app.ui.screens import ScreenID
from app.ui.screens.base import BaseScreen
from app.ui.viewmodels.base import BaseViewModel
from app.logger import logger


class ScreenRegistry:
    """Реестр экранов"""
    
    def __init__(self):
        self._screens: Dict[ScreenID, Type[BaseScreen]] = {}
        self._renderers: Dict[ScreenID, Callable] = {}
        self._keyboards: Dict[ScreenID, Callable] = {}
        self._initialized = False
    
    def register(
        self,
        screen_id: ScreenID,
        screen_class: Type[BaseScreen],
        renderer: Optional[Callable] = None,
        keyboard_builder: Optional[Callable] = None
    ):
        """
        Регистрирует экран в реестре
        
        Args:
            screen_id: ID экрана
            screen_class: Класс экрана (должен быть подклассом BaseScreen)
            renderer: Функция рендеринга (опционально, берется из screen_class)
            keyboard_builder: Функция построения клавиатуры (опционально, берется из screen_class)
        """
        if screen_id in self._screens:
            logger.warning(f"Экран {screen_id} уже зарегистрирован. Перезаписываем.")
        
        if not issubclass(screen_class, BaseScreen):
            raise ValueError(f"screen_class должен быть подклассом BaseScreen, получен {screen_class}")
        
        self._screens[screen_id] = screen_class
        
        if renderer:
            self._renderers[screen_id] = renderer
        
        if keyboard_builder:
            self._keyboards[screen_id] = keyboard_builder
    
    def get_screen_class(self, screen_id: ScreenID) -> Optional[Type[BaseScreen]]:
        """Получает класс экрана по ID"""
        return self._screens.get(screen_id)
    
    def get_renderer(self, screen_id: ScreenID) -> Optional[Callable]:
        """Получает renderer для экрана"""
        return self._renderers.get(screen_id)
    
    def get_keyboard_builder(self, screen_id: ScreenID) -> Optional[Callable]:
        """Получает keyboard builder для экрана"""
        return self._keyboards.get(screen_id)
    
    def is_registered(self, screen_id: ScreenID) -> bool:
        """Проверяет, зарегистрирован ли экран"""
        return screen_id in self._screens
    
    def get_all_screen_ids(self) -> set[ScreenID]:
        """Возвращает множество всех зарегистрированных ScreenID"""
        return set(self._screens.keys())
    
    def validate(self) -> list[str]:
        """
        Валидирует реестр: проверяет, что все ScreenID зарегистрированы
        
        Returns:
            Список ошибок валидации (пустой, если все OK)
        """
        errors = []
        
        # Экраны, которые не требуют регистрации (deprecated или не реализованы)
        OPTIONAL_SCREENS = {
            ScreenID.SUBSCRIPTION,  # Не используется, заменен на SUBSCRIPTION_PLANS
            ScreenID.ADMIN_GRANTS,  # Пока не реализован
            ScreenID.CONNECT_SUCCESS,  # DEPRECATED, заменен на CONNECT со status="success"
        }
        
        # Проверяем, что все ScreenID зарегистрированы (кроме optional)
        all_screen_ids = set(ScreenID)
        registered_ids = self.get_all_screen_ids()
        
        missing = all_screen_ids - registered_ids - OPTIONAL_SCREENS
        if missing:
            errors.append(f"Не зарегистрированы экраны: {missing}")
        
        # Проверяем дубликаты (не должно быть)
        if len(self._screens) != len(set(self._screens.keys())):
            errors.append("Найдены дубликаты ScreenID в реестре")
        
        return errors
    
    def initialize(self):
        """Инициализирует реестр, регистрируя все экраны"""
        if self._initialized:
            return
        
        # Импортируем экраны здесь, чтобы избежать циклических зависимостей
        from app.ui.screens.main_menu import MainMenuScreen
        from app.ui.screens.subscription import (
            SubscriptionPlansScreen,
            SubscriptionPlanDetailScreen,
            SubscriptionPaymentScreen
        )
        from app.ui.screens.help import HelpScreen
        from app.ui.screens.connect import ConnectScreen
        from app.ui.screens.profile import ProfileScreen
        from app.ui.screens.admin import (
            AdminPanelScreen,
            AdminStatsScreen,
            AdminUsersScreen,
            AdminPaymentsScreen
        )
        from app.ui.screens.error import (
            ErrorScreen,
            AccessDeniedScreen,
            RemnaUnavailableScreen
        )
        
        # Регистрируем экраны
        registry = {
            ScreenID.MAIN_MENU: MainMenuScreen,
            ScreenID.SUBSCRIPTION_PLANS: SubscriptionPlansScreen,
            ScreenID.SUBSCRIPTION_PLAN_DETAIL: SubscriptionPlanDetailScreen,
            ScreenID.SUBSCRIPTION_PAYMENT: SubscriptionPaymentScreen,
            ScreenID.HELP: HelpScreen,
            ScreenID.CONNECT: ConnectScreen,
            ScreenID.PROFILE: ProfileScreen,
            ScreenID.ADMIN_PANEL: AdminPanelScreen,
            ScreenID.ADMIN_STATS: AdminStatsScreen,
            ScreenID.ADMIN_USERS: AdminUsersScreen,
            ScreenID.ADMIN_PAYMENTS: AdminPaymentsScreen,
            ScreenID.ERROR: ErrorScreen,
            ScreenID.ACCESS_DENIED: AccessDeniedScreen,
            ScreenID.REMNA_UNAVAILABLE: RemnaUnavailableScreen,
        }
        
        for screen_id, screen_class in registry.items():
            self.register(screen_id, screen_class)
        
        self._initialized = True
        logger.info(f"ScreenRegistry инициализирован: зарегистрировано {len(self._screens)} экранов")


# Глобальный экземпляр реестра
_registry: Optional[ScreenRegistry] = None


def get_screen_registry() -> ScreenRegistry:
    """Получает глобальный экземпляр ScreenRegistry"""
    global _registry
    if _registry is None:
        _registry = ScreenRegistry()
        _registry.initialize()
    return _registry