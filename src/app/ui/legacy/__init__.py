"""
LEGACY UI MODULE - DEPRECATED

DO NOT USE. UI must be rendered via ScreenManager.

This module contains legacy keyboard builders and renderers that are kept
for backward compatibility with payment flows and other non-screen actions.

All new UI code MUST use:
- ui/screens/* for Screen classes
- ui/renderers/* for renderers
- ui/keyboards/* for keyboard builders
- ScreenManager.show_screen() for displaying UI

This module will be removed in a future version.
"""

# Re-export legacy functions with deprecation warnings
import warnings

# Legacy keyboard builders (used in payments, admin actions)
from app.keyboards import (
    get_period_keyboard,
    get_payment_method_keyboard,
    get_payment_keyboard,
    get_back_to_plans_keyboard,
    get_friend_request_keyboard,
    get_admin_access_request_keyboard,
    get_subscription_link_keyboard,
)

# Legacy renderer (used in main_menu renderer as adapter)
from app.routers.menu_builder import build_main_menu_text, MenuData


def _deprecation_warning():
    """Shows deprecation warning when legacy functions are imported"""
    warnings.warn(
        "Legacy UI functions are deprecated. Use ScreenManager and ui/screens/* instead.",
        DeprecationWarning,
        stacklevel=3
    )


# Wrap exports to show warnings
__all__ = [
    'get_period_keyboard',
    'get_payment_method_keyboard',
    'get_payment_keyboard',
    'get_back_to_plans_keyboard',
    'get_friend_request_keyboard',
    'get_admin_access_request_keyboard',
    'get_subscription_link_keyboard',
    'build_main_menu_text',
    'MenuData',
]