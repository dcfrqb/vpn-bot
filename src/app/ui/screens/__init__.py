"""
Экраны бота - каждый экран имеет один renderer, один keyboard builder и один ViewModel
"""
from enum import Enum

class ScreenID(Enum):
    """Идентификаторы экранов бота"""
    MAIN_MENU = "main_menu"
    SUBSCRIPTION = "subscription"
    SUBSCRIPTION_PLANS = "subscription_plans"
    SUBSCRIPTION_PLAN_DETAIL = "subscription_plan_detail"
    SUBSCRIPTION_PAYMENT = "subscription_payment"
    CONNECT = "connect"
    CONNECT_SUCCESS = "connect_success"  # DEPRECATED: используйте CONNECT со status="success"
    HELP = "help"
    ADMIN_PANEL = "admin_panel"
    ADMIN_STATS = "admin_stats"
    ADMIN_USERS = "admin_users"
    ADMIN_PAYMENTS = "admin_payments"
    ADMIN_GRANTS = "admin_grants"
    PROFILE = "profile"
    ERROR = "error"
    ACCESS_DENIED = "access_denied"
    REMNA_UNAVAILABLE = "remna_unavailable"