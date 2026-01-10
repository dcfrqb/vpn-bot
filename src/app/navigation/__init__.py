"""
Navigation модули: навигация, роутинг, callback schema
"""
from app.navigation.callback_schema import (
    CallbackAction,
    CallbackSchema,
    build_cb,
    parse_cb,
    is_ui_callback
)
from app.navigation.rules import (
    can_navigate,
    get_allowed_navigations,
    UserRole,
    ADMIN_SCREENS,
    NAVIGATION_MAP
)
from app.navigation.navigator import (
    Navigator,
    NavigationResult,
    RenderMode,
    get_navigator
)

__all__ = [
    "CallbackAction",
    "CallbackSchema",
    "build_cb",
    "parse_cb",
    "is_ui_callback",
    "can_navigate",
    "get_allowed_navigations",
    "UserRole",
    "ADMIN_SCREENS",
    "NAVIGATION_MAP",
    "Navigator",
    "NavigationResult",
    "RenderMode",
    "get_navigator",
]
