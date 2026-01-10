"""
ViewModels - данные для отображения экранов
"""
from app.ui.viewmodels.base import BaseViewModel
from app.ui.viewmodels.main_menu import MainMenuViewModel
from app.ui.viewmodels.subscription import SubscriptionViewModel as SubscriptionScreenViewModel
from app.ui.viewmodels.profile import ProfileViewModel

__all__ = [
    'BaseViewModel',
    'MainMenuViewModel',
    'SubscriptionScreenViewModel',
    'ProfileViewModel',
]