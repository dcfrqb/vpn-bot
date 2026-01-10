"""
Админские экраны
"""
from typing import Optional, List, Dict, Any, Union
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.screens import ScreenID
from app.ui.viewmodels.admin import (
    AdminPanelViewModel,
    AdminStatsViewModel,
    AdminUsersViewModel,
    AdminPaymentsViewModel
)
from app.ui.renderers.admin import (
    render_admin_panel,
    render_admin_stats,
    render_admin_users,
    render_admin_payments
)
from app.ui.keyboards.admin import (
    build_admin_panel_keyboard,
    build_admin_stats_keyboard,
    build_admin_users_keyboard,
    build_admin_payments_keyboard
)
from app.config import is_admin
from app.logger import logger
from app.ui.screen_manager import get_screen_manager


class AdminPanelScreen(BaseScreen):
    """Главная панель администратора"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_PANEL
    
    async def render(self, viewmodel: AdminPanelViewModel) -> str:
        return await render_admin_panel(viewmodel)
    
    async def build_keyboard(self, viewmodel: AdminPanelViewModel) -> types.InlineKeyboardMarkup:
        return await build_admin_panel_keyboard(viewmodel)
    
    async def create_viewmodel(self, stats: Dict[str, Any] = None) -> AdminPanelViewModel:
        if stats is None:
            from app.services.stats import get_statistics
            stats = await get_statistics()
        return AdminPanelViewModel(stats=stats)


class AdminStatsScreen(BaseScreen):
    """Экран статистики администратора"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_STATS
    
    async def render(self, viewmodel: AdminStatsViewModel) -> str:
        return await render_admin_stats(viewmodel)
    
    async def build_keyboard(self, viewmodel: AdminStatsViewModel) -> types.InlineKeyboardMarkup:
        return await build_admin_stats_keyboard(viewmodel)
    
    async def create_viewmodel(self, stats: Dict[str, Any] = None) -> AdminStatsViewModel:
        if stats is None:
            from app.services.stats import get_statistics
            stats = await get_statistics()
        return AdminStatsViewModel(stats=stats)
    
    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int]
    ) -> bool:
        """Обрабатывает действия экрана (refresh - обновление статистики)"""
        from app.ui.screen_manager import get_screen_manager
        from app.services.stats import get_statistics
        
        if action == "refresh":
            stats = await get_statistics()
            viewmodel = await self.create_viewmodel(stats=stats)
            
            # Используем navigate для обновления экрана
            screen_manager = get_screen_manager()
            current_screen = screen_manager._get_current_screen(user_id) if user_id else ScreenID.ADMIN_STATS
            return await screen_manager.navigate(
                from_screen_id=current_screen,
                to_screen_id=ScreenID.ADMIN_STATS,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True
            )
        
        return False


class AdminUsersScreen(BaseScreen):
    """Экран списка пользователей"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_USERS
    
    async def render(self, viewmodel: AdminUsersViewModel) -> str:
        return await render_admin_users(viewmodel)
    
    async def build_keyboard(self, viewmodel: AdminUsersViewModel) -> types.InlineKeyboardMarkup:
        return await build_admin_users_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        users: List[Dict[str, Any]] = None,
        page: int = 1,
        total_pages: int = 1,
        total: int = 0
    ) -> AdminUsersViewModel:
        # Если данные не переданы - загружаем их
        if users is None:
            from app.services.stats import get_users_list
            users_data = await get_users_list(page=page, page_size=10)
            users = users_data["users"]
            page = users_data["page"]
            total_pages = users_data["total_pages"]
            total = users_data["total"]
        
        return AdminUsersViewModel(
            users=users,
            page=page,
            total_pages=total_pages,
            total=total
        )
    
    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        action_type=None
    ) -> bool:
        """Обрабатывает действия экрана (page - пагинация с использованием Pagination)"""
        from app.ui.screen_manager import get_screen_manager
        from app.services.stats import get_users_list
        from app.core.pagination import Pagination
        import json
        
        if action == "page":
            # STATE action: пагинация с использованием Pagination
            try:
                # Если payload обернут в JSON как {"value": "p1s10"}, извлекаем значение
                import json
                if payload.startswith("{") and payload.endswith("}"):
                    try:
                        payload_dict = json.loads(payload)
                        if "value" in payload_dict:
                            payload = payload_dict["value"]
                    except (json.JSONDecodeError, KeyError):
                        pass
                
                # Пытаемся распарсить payload как Pagination
                pagination = Pagination.from_payload(payload)
                
                # Проверяем, что payload был успешно распарсен
                # Если payload начинается с "p" и содержит "s", значит это правильный формат
                # Если нет, пытаемся как просто номер страницы (обратная совместимость)
                if not (payload.startswith("p") and "s" in payload):
                    # Это не новый формат, пытаемся как просто номер страницы
                    try:
                        page_num = int(payload)
                        pagination = Pagination(page=page_num, page_size=10)
                    except ValueError:
                        logger.error(f"Неверный формат payload для пагинации: {payload}")
                        return False
                
                # Загружаем данные с учетом пагинации
                users_data = await get_users_list(page=pagination.page, page_size=pagination.page_size)
                
                # Обновляем total в pagination
                pagination.update_total(users_data["total"])
                
                viewmodel = await self.create_viewmodel(
                    users=users_data["users"],
                    page=pagination.page,
                    total_pages=pagination.total_pages,
                    total=pagination.total
                )
                
                # STATE action - show_screen с edit=True, НЕ navigate, НЕ backstack
                screen_manager = get_screen_manager()
                return await screen_manager.show_screen(
                    screen_id=ScreenID.ADMIN_USERS,
                    message_or_callback=message_or_callback,
                    viewmodel=viewmodel,
                    edit=True,
                    user_id=user_id
                )
            except Exception as e:
                logger.error(f"Ошибка при обработке пагинации: {e}")
                return False
        
        return False


class AdminPaymentsScreen(BaseScreen):
    """Экран списка платежей"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_PAYMENTS
    
    async def render(self, viewmodel: AdminPaymentsViewModel) -> str:
        return await render_admin_payments(viewmodel)
    
    async def build_keyboard(self, viewmodel: AdminPaymentsViewModel) -> types.InlineKeyboardMarkup:
        return await build_admin_payments_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        payments: List[Dict[str, Any]] = None,
        page: int = 1,
        total_pages: int = 1,
        total: int = 0,
        status_filter: Optional[str] = None
    ) -> AdminPaymentsViewModel:
        # Если данные не переданы - загружаем их
        if payments is None:
            from app.services.stats import get_payments_list
            payments_data = await get_payments_list(page=page, page_size=10, status=status_filter)
            payments = payments_data["payments"]
            page = payments_data["page"]
            total_pages = payments_data["total_pages"]
            total = payments_data["total"]
        
        return AdminPaymentsViewModel(
            payments=payments,
            page=page,
            total_pages=total_pages,
            total=total,
            status_filter=status_filter
        )
    
    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        action_type=None
    ) -> bool:
        """Обрабатывает действия экрана (page - пагинация, filter - фильтрация с использованием Pagination)"""
        from app.ui.screen_manager import get_screen_manager
        from app.services.stats import get_payments_list
        from app.core.pagination import Pagination
        import json
        
        if action == "page":
            # STATE action: пагинация с использованием Pagination
            try:
                # Поддержка нового компактного формата: p2s10f{filter}
                # Или старого JSON формата для обратной совместимости
                status_filter = None
                pagination = None
                
                # Пытаемся распарсить новый компактный формат: p2s10f{filter}
                if payload.startswith("p") and "s" in payload:
                    # Извлекаем фильтр, если есть (формат: p2s10f{filter})
                    if "f" in payload:
                        parts = payload.split("f", 1)
                        pagination = Pagination.from_payload(parts[0])
                        filter_str = parts[1] if len(parts) > 1 else "all"
                    else:
                        pagination = Pagination.from_payload(payload)
                        filter_str = "all"
                    
                    # Маппинг строки фильтра в значение
                    status_map = {
                        "all": None,
                        "succeeded": "succeeded",
                        "suc": "succeeded",
                        "pending": "pending",
                        "pen": "pending",
                        "canceled": "canceled",
                        "can": "canceled",
                        "failed": "failed",
                        "fail": "failed"
                    }
                    status_filter = status_map.get(filter_str, None)
                else:
                    # Старый формат: JSON или page&filter
                    payload_dict = {}
                    try:
                        payload_dict = json.loads(payload)
                    except (json.JSONDecodeError, ValueError):
                        # Обратная совместимость: page&filter
                        parts = payload.split("&")
                        if len(parts) >= 1:
                            try:
                                page_num = int(parts[0])
                                payload_dict = {"page": page_num, "page_size": 10}
                                if len(parts) > 1:
                                    payload_dict["status_filter"] = parts[1]
                            except ValueError:
                                logger.error(f"Неверный формат payload: {payload}")
                                return False
                    
                    # Извлекаем pagination и filter (поддержка сжатых ключей: p/s/f)
                    pagination = Pagination.from_dict(payload_dict) if ("p" in payload_dict or "page" in payload_dict) else Pagination()
                    # Поддержка сжатого ключа f и старого status_filter
                    status_filter = payload_dict.get("f") or payload_dict.get("status_filter")
                    
                    # Маппинг строки фильтра в значение (если это строка)
                    if isinstance(status_filter, str):
                        status_map = {
                            "all": None,
                            "succeeded": "succeeded",
                            "suc": "succeeded",
                            "pending": "pending",
                            "pen": "pending",
                            "canceled": "canceled",
                            "can": "canceled",
                            "failed": "failed",
                            "fail": "failed"
                        }
                        status_filter = status_map.get(status_filter, None)
                
                if pagination is None:
                    pagination = Pagination()
                
                # Загружаем данные
                payments_data = await get_payments_list(
                    page=pagination.page,
                    page_size=pagination.page_size,
                    status=status_filter
                )
                
                # Обновляем total в pagination
                pagination.update_total(payments_data["total"])
                
                viewmodel = await self.create_viewmodel(
                    payments=payments_data["payments"],
                    page=pagination.page,
                    total_pages=pagination.total_pages,
                    total=pagination.total,
                    status_filter=status_filter
                )
                
                # STATE action - show_screen с edit=True, НЕ navigate, НЕ backstack
                screen_manager = get_screen_manager()
                return await screen_manager.show_screen(
                    screen_id=ScreenID.ADMIN_PAYMENTS,
                    message_or_callback=message_or_callback,
                    viewmodel=viewmodel,
                    edit=True,
                    user_id=user_id
                )
            except Exception as e:
                logger.error(f"Ошибка при обработке пагинации: {e}")
                return False
        
        elif action == "filter":
            # STATE action: фильтрация (сбрасываем на страницу 1)
            try:
                status_map = {
                    "all": None,
                    "succeeded": "succeeded",
                    "pending": "pending",
                    "canceled": "canceled",
                    "failed": "failed"
                }
                status_filter = status_map.get(payload)
                
                # Создаем новую pagination с первой страницей
                pagination = Pagination(page=1, page_size=10)
                
                payments_data = await get_payments_list(
                    page=pagination.page,
                    page_size=pagination.page_size,
                    status=status_filter
                )
                
                # Обновляем total в pagination
                pagination.update_total(payments_data["total"])
                
                viewmodel = await self.create_viewmodel(
                    payments=payments_data["payments"],
                    page=pagination.page,
                    total_pages=pagination.total_pages,
                    total=pagination.total,
                    status_filter=status_filter
                )
                
                # STATE action - show_screen с edit=True, НЕ navigate, НЕ backstack
                screen_manager = get_screen_manager()
                return await screen_manager.show_screen(
                    screen_id=ScreenID.ADMIN_PAYMENTS,
                    message_or_callback=message_or_callback,
                    viewmodel=viewmodel,
                    edit=True,
                    user_id=user_id
                )
            except Exception as e:
                logger.error(f"Ошибка при обработке фильтра: {e}")
                return False
        
        return False