"""
Единый Navigator с backstack и строгой схемой callback_data
Единственная точка обработки переходов и callback-экшенов
"""
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from enum import Enum

from app.ui.screens import ScreenID
from app.navigation.callback_schema import CallbackAction
from app.navigation.rules import can_navigate, UserRole
from app.logger import logger
from app.core.errors import ValidationError


class RenderMode(Enum):
    """Режим отображения экрана"""
    OPEN = "open"      # Открыть новый экран
    BACK = "back"      # Вернуться назад
    REFRESH = "refresh"  # Обновить текущий экран
    STATE = "state"    # Изменить состояние (page, filter, select)


@dataclass
class NavigationResult:
    """Результат навигации"""
    target_screen: ScreenID
    render_mode: RenderMode
    updated_backstack: Optional[List[ScreenID]] = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class Navigator:
    """Единый навигатор с backstack"""
    
    def __init__(self):
        # Backstack: user_id -> List[ScreenID] (стек экранов)
        self._backstacks: Dict[int, List[ScreenID]] = {}
        # Текущий экран: user_id -> ScreenID
        self._current_screens: Dict[int, ScreenID] = {}
        # Anchor screens для FLOW: user_id -> ScreenID
        self._flow_anchors: Dict[int, ScreenID] = {}
        # Максимальный размер backstack
        self._max_backstack_size = 10
    
    def handle(
        self,
        action: CallbackAction,
        current_screen: ScreenID,
        payload: Optional[Dict[str, Any]],
        user_id: int,
        user_role: UserRole = "user"
    ) -> NavigationResult:
        """
        Единая точка обработки навигации
        
        Args:
            action: Действие (open, back, refresh, page, select, filter)
            current_screen: Текущий экран
            payload: Дополнительные данные (опционально)
            user_id: ID пользователя
            user_role: Роль пользователя
            
        Returns:
            NavigationResult с информацией о навигации
        """
        # Логируем входные данные (без секретов)
        payload_keys = list(payload.keys()) if payload else []
        logger.info(
            f"[NAVIGATOR] user_id={user_id}, action={action.value}, "
            f"current_screen={current_screen.value}, payload_keys={payload_keys}"
        )
        
        # Валидация payload
        if payload is not None and not isinstance(payload, dict):
            error_msg = f"Invalid payload type: expected dict, got {type(payload).__name__}"
            logger.warning(f"[NAVIGATOR] {error_msg}")
            return NavigationResult(
                target_screen=current_screen,
                render_mode=RenderMode.REFRESH,
                error=error_msg
            )
        
        # Валидация target_screen в payload (если есть)
        if payload and "target_screen" in payload:
            target_screen_str = payload.get("target_screen")
            if not isinstance(target_screen_str, str):
                error_msg = f"Invalid target_screen type: expected str, got {type(target_screen_str).__name__}"
                logger.warning(f"[NAVIGATOR] {error_msg}")
                return NavigationResult(
                    target_screen=current_screen,
                    render_mode=RenderMode.REFRESH,
                    error=error_msg
                )
        
        # Обрабатываем действие
        if action == CallbackAction.BACK:
            result = self._handle_back(current_screen, user_id, user_role, payload)
        elif action == CallbackAction.REFRESH:
            result = self._handle_refresh(current_screen, payload)
        elif action == CallbackAction.OPEN:
            result = self._handle_open(current_screen, payload, user_id, user_role)
        elif action in (CallbackAction.PAGE, CallbackAction.FILTER, CallbackAction.SELECT):
            result = self._handle_state(current_screen, action, payload)
        else:
            # Неизвестное действие
            error_msg = f"Unknown action: {action.value}"
            logger.warning(f"[NAVIGATOR] Unknown action: {action.value}")
            return NavigationResult(
                target_screen=current_screen,
                render_mode=RenderMode.REFRESH,
                error=error_msg
            )
        
        # Обновляем current_screen при успешной навигации
        # OPEN и BACK всегда обновляют current_screen (даже если render_mode == REFRESH)
        # REFRESH (не из OPEN) обновляет current_screen только если он еще не установлен (инициализация)
        # STATE не меняет текущий экран
        if result.error is None:
            if action == CallbackAction.OPEN:
                # OPEN всегда обновляет current_screen, даже если render_mode == REFRESH
                # (когда current_screen == target_screen)
                self._set_current_screen(user_id, result.target_screen)
            elif action == CallbackAction.BACK:
                # BACK всегда обновляет current_screen
                self._set_current_screen(user_id, result.target_screen)
            elif result.render_mode == RenderMode.REFRESH and action == CallbackAction.REFRESH:
                # REFRESH обновляет current_screen только если он еще не установлен (инициализация)
                if user_id not in self._current_screens:
                    self._set_current_screen(user_id, result.target_screen)
        
        return result
    
    def _handle_back(
        self,
        current_screen: ScreenID,
        user_id: int,
        user_role: UserRole,
        payload: Optional[Dict[str, Any]] = None
    ) -> NavigationResult:
        """Обрабатывает действие back"""
        # Если на MAIN_MENU, остаемся на нем (refresh)
        if current_screen == ScreenID.MAIN_MENU:
            logger.debug(f"[NAVIGATOR] Back на MAIN_MENU - refresh")
            return NavigationResult(
                target_screen=ScreenID.MAIN_MENU,
                render_mode=RenderMode.REFRESH,
                updated_backstack=self._backstacks.get(user_id, []).copy()
            )
        
        # Проверяем anchor для FLOW (используется ОДИН раз, затем очищается)
        anchor_screen = self._flow_anchors.get(user_id)
        if anchor_screen:
            logger.debug(f"[NAVIGATOR] Back к anchor экрану: {anchor_screen.value}")
            # Очищаем anchor сразу (используется один раз)
            del self._flow_anchors[user_id]
            # Проверяем навигацию
            if not can_navigate(current_screen, anchor_screen, user_role):
                error_msg = f"Navigation forbidden: {current_screen.value} -> {anchor_screen.value}"
                logger.warning(f"[NAVIGATOR] Forbidden navigation: {current_screen.value} -> {anchor_screen.value}")
                return NavigationResult(
                    target_screen=current_screen,
                    render_mode=RenderMode.REFRESH,
                    error=error_msg
                )
            return NavigationResult(
                target_screen=anchor_screen,
                render_mode=RenderMode.BACK,
                updated_backstack=self._backstacks.get(user_id, []).copy()
            )
        
        # Извлекаем из backstack
        target_screen = self._pop_from_backstack(user_id)
        
        # Если backstack пуст, используем target_screen из payload (если есть)
        if not target_screen:
            if payload and payload.get("target_screen"):
                try:
                    target_screen = ScreenID(payload["target_screen"])
                    logger.debug(f"[NAVIGATOR] Back: backstack пуст, используем target_screen из payload: {target_screen.value}")
                except ValueError:
                    logger.warning(f"[NAVIGATOR] Unknown target_screen из payload: {payload.get('target_screen')}")
                    target_screen = ScreenID.MAIN_MENU
            else:
                target_screen = ScreenID.MAIN_MENU
        
        # Проверяем навигацию
        if not can_navigate(current_screen, target_screen, user_role):
            error_msg = f"Navigation forbidden: {current_screen.value} -> {target_screen.value}"
            logger.warning(f"[NAVIGATOR] Forbidden navigation: {current_screen.value} -> {target_screen.value}")
            # Возвращаемся к MAIN_MENU если переход запрещен
            return NavigationResult(
                target_screen=ScreenID.MAIN_MENU,
                render_mode=RenderMode.BACK,
                updated_backstack=[]  # Очищаем backstack при ошибке
            )
        
        logger.info(
            f"[NAVIGATOR] Back: {current_screen.value} -> {target_screen.value}, "
            f"backstack_size={len(self._backstacks.get(user_id, []))}"
        )
        
        return NavigationResult(
            target_screen=target_screen,
            render_mode=RenderMode.BACK,
            updated_backstack=self._backstacks.get(user_id, []).copy()
        )
    
    def _handle_refresh(
        self,
        current_screen: ScreenID,
        payload: Optional[Dict[str, Any]]
    ) -> NavigationResult:
        """Обрабатывает действие refresh"""
        # Refresh НЕ меняет backstack и НЕ меняет screen
        logger.debug(f"[NAVIGATOR] Refresh экрана: {current_screen.value}")
        return NavigationResult(
            target_screen=current_screen,
            render_mode=RenderMode.REFRESH,
            payload=payload
        )
    
    def _handle_open(
        self,
        current_screen: ScreenID,
        payload: Optional[Dict[str, Any]],
        user_id: int,
        user_role: UserRole
    ) -> NavigationResult:
        """Обрабатывает действие open"""
        # Определяем целевой экран из payload или используем current_screen
        target_screen_str = payload.get("target_screen") if payload else None
        
        if target_screen_str:
            try:
                target_screen = ScreenID(target_screen_str)
            except ValueError:
                error_msg = f"Unknown target_screen: {target_screen_str}"
                logger.warning(f"[NAVIGATOR] {error_msg}")
                return NavigationResult(
                    target_screen=current_screen,
                    render_mode=RenderMode.REFRESH,
                    error=error_msg
                )
        else:
            # Если target_screen не указан, открываем текущий экран (refresh)
            target_screen = current_screen
        
        # Проверяем навигацию
        if current_screen != target_screen:
            if not can_navigate(current_screen, target_screen, user_role):
                error_msg = f"Navigation forbidden: {current_screen.value} -> {target_screen.value}"
                logger.warning(f"[NAVIGATOR] Forbidden navigation: {current_screen.value} -> {target_screen.value}")
                return NavigationResult(
                    target_screen=current_screen,
                    render_mode=RenderMode.REFRESH,
                    error=error_msg
                )
            
            # Добавляем текущий экран в backstack (если не MAIN_MENU)
            if current_screen != ScreenID.MAIN_MENU:
                self._push_to_backstack(user_id, current_screen)
            
            # Очищаем flow anchor при успешном OPEN (если был установлен)
            if user_id in self._flow_anchors:
                logger.debug(f"[NAVIGATOR] Cleared flow anchor for user_id={user_id} on OPEN")
                del self._flow_anchors[user_id]
        
        logger.info(
            f"[NAVIGATOR] Open: {current_screen.value} -> {target_screen.value}, "
            f"backstack_size={len(self._backstacks.get(user_id, []))}"
        )
        
        return NavigationResult(
            target_screen=target_screen,
            render_mode=RenderMode.OPEN if current_screen != target_screen else RenderMode.REFRESH,
            updated_backstack=self._backstacks.get(user_id, []).copy(),
            payload=payload
        )
    
    def _handle_state(
        self,
        current_screen: ScreenID,
        action: CallbackAction,
        payload: Optional[Dict[str, Any]]
    ) -> NavigationResult:
        """Обрабатывает действия изменения состояния (page, filter, select)"""
        # STATE действия НЕ меняют backstack и НЕ меняют screen
        logger.debug(f"[NAVIGATOR] State action: {action.value} на экране {current_screen.value}")
        return NavigationResult(
            target_screen=current_screen,
            render_mode=RenderMode.STATE,
            payload=payload
        )
    
    def _push_to_backstack(self, user_id: int, screen_id: ScreenID):
        """Добавляет экран в backstack"""
        if user_id not in self._backstacks:
            self._backstacks[user_id] = []
        
        stack = self._backstacks[user_id]
        
        # Не добавляем MAIN_MENU в backstack
        if screen_id == ScreenID.MAIN_MENU:
            return
        
        # Не добавляем дубликаты подряд
        if stack and stack[-1] == screen_id:
            return
        
        stack.append(screen_id)
        
        # Ограничиваем размер backstack
        if len(stack) > self._max_backstack_size:
            stack.pop(0)
    
    def _pop_from_backstack(self, user_id: int) -> Optional[ScreenID]:
        """Извлекает экран из backstack"""
        if user_id not in self._backstacks:
            return None
        
        stack = self._backstacks[user_id]
        if not stack:
            return None
        
        return stack.pop()
    
    def get_current_screen(self, user_id: int) -> Optional[ScreenID]:
        """Получает текущий экран пользователя"""
        return self._current_screens.get(user_id)
    
    def _get_current_screen(self, user_id: int) -> Optional[ScreenID]:
        """Приватный метод для внутреннего использования"""
        return self._current_screens.get(user_id)
    
    def _set_current_screen(self, user_id: int, screen_id: ScreenID):
        """Устанавливает текущий экран пользователя"""
        self._current_screens[user_id] = screen_id
    
    def set_flow_anchor(self, user_id: int, screen_id: ScreenID):
        """
        Устанавливает anchor экран для FLOW действий
        
        ВАЖНО: anchor используется ОДИН раз и автоматически очищается при:
        - BACK к anchor экрану
        - Успешном OPEN на другой экран
        """
        self._flow_anchors[user_id] = screen_id
        logger.debug(f"[NAVIGATOR] Set flow anchor для user_id={user_id}: {screen_id.value}")
    
    def clear_backstack(self, user_id: int):
        """Очищает backstack пользователя"""
        if user_id in self._backstacks:
            del self._backstacks[user_id]
        logger.debug(f"[NAVIGATOR] Cleared backstack для user_id={user_id}")
    
    def clear_flow_anchor(self, user_id: int):
        """Очищает flow anchor пользователя"""
        if user_id in self._flow_anchors:
            del self._flow_anchors[user_id]
        logger.debug(f"[NAVIGATOR] Cleared flow anchor для user_id={user_id}")
    
    def get_backstack(self, user_id: int) -> List[ScreenID]:
        """Получает копию backstack пользователя"""
        return self._backstacks.get(user_id, []).copy()


# Singleton instance
_navigator: Optional[Navigator] = None


def get_navigator() -> Navigator:
    """Получает singleton экземпляр Navigator"""
    global _navigator
    if _navigator is None:
        _navigator = Navigator()
    return _navigator
