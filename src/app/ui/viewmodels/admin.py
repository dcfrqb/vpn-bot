"""
ViewModels для админских экранов
"""
from typing import Optional, List, Dict, Any
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID


class AdminPanelViewModel(BaseViewModel):
    """ViewModel для главной панели администратора"""
    
    def __init__(self, stats: Dict[str, Any]):
        self.stats = stats
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_PANEL


class AdminStatsViewModel(BaseViewModel):
    """ViewModel для экрана статистики"""
    
    def __init__(self, stats: Dict[str, Any]):
        self.stats = stats
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_STATS


class AdminUsersViewModel(BaseViewModel):
    """ViewModel для экрана списка пользователей"""
    
    def __init__(
        self,
        users: List[Dict[str, Any]],
        page: int,
        total_pages: int,
        total: int
    ):
        self.users = users
        self.page = page
        self.total_pages = total_pages
        self.total = total
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_USERS


class AdminPaymentsViewModel(BaseViewModel):
    """ViewModel для экрана списка платежей"""
    
    def __init__(
        self,
        payments: List[Dict[str, Any]],
        page: int,
        total_pages: int,
        total: int,
        status_filter: Optional[str] = None
    ):
        self.payments = payments
        self.page = page
        self.total_pages = total_pages
        self.total = total
        self.status_filter = status_filter
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ADMIN_PAYMENTS