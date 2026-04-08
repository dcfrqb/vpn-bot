"""
ScreenManager - централизованная точка показа экранов
"""
import time
import uuid
import hashlib
import asyncio
from typing import Optional, Dict, Type, List, Tuple, Union
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from app.logger import logger
from app.ui.screens import ScreenID
from app.ui.screens.base import BaseScreen
from app.ui.viewmodels.base import BaseViewModel
from app.ui.navigation import can_navigate, UserRole
from app.navigation.navigator import get_navigator, NavigationResult, RenderMode
from app.navigation.callback_schema import CallbackAction, CallbackSchema
from app.ui.screen_registry import get_screen_registry
from app.ui.action_types import ActionType, get_action_type
from app.ui.action_map import ACTION_MAP, get_action_effect, is_action_allowed
from app.config import is_admin


class ScreenManager:
    """Менеджер экранов - централизованное управление отображением"""
    
    def __init__(self):
        # Используем реестр экранов вместо прямого хранения
        self._registry = get_screen_registry()
        self._screen_instances: Dict[ScreenID, BaseScreen] = {}
        # Backstack: user_id -> List[ScreenID] (стек экранов для каждого пользователя)
        self._backstacks: Dict[int, List[ScreenID]] = {}
        # Текущий экран пользователя: user_id -> ScreenID
        self._current_screens: Dict[int, ScreenID] = {}
        # Anchor screens для FLOW действий: user_id -> ScreenID (экран, к которому нужно вернуться)
        self._flow_anchors: Dict[int, ScreenID] = {}
        # Кэш последнего рендера для NO-OP: (user_id, message_id) -> (text_hash, keyboard_hash)
        self._last_render_cache: Dict[Tuple[int, int], Tuple[str, str]] = {}
        # Блокировки для защиты от гонок: user_id -> asyncio.Lock
        self._user_locks: Dict[int, 'asyncio.Lock'] = {}
        self._initialize_screens()
    
    def _initialize_screens(self):
        """Инициализирует все экраны из реестра"""
        # Реестр уже инициализирован при создании
        # Создаем экземпляры экранов из реестра
        for screen_id in self._registry.get_all_screen_ids():
            screen_class = self._registry.get_screen_class(screen_id)
            if screen_class:
                self._screen_instances[screen_id] = screen_class()
        
        logger.info(f"Инициализировано {len(self._screen_instances)} экранов из реестра")
        
        # Валидируем реестр
        errors = self._registry.validate()
        if errors:
            logger.error(f"Ошибки валидации реестра экранов: {errors}")
            raise ValueError(f"Реестр экранов невалиден: {errors}")
    
    def get_screen(self, screen_id: ScreenID) -> Optional[BaseScreen]:
        """
        Получает экран по ID из реестра
        
        Args:
            screen_id: ID экрана
            
        Returns:
            Экран или None, если не найден
        """
        # Проверяем реестр
        if not self._registry.is_registered(screen_id):
            logger.error(f"Экран {screen_id} не зарегистрирован в ScreenRegistry")
            return None
        
        # Возвращаем экземпляр экрана
        return self._screen_instances.get(screen_id)
    
    def _generate_request_id(self) -> str:
        """Генерирует короткий request_id"""
        return uuid.uuid4().hex[:8]
    
    def _get_user_lock(self, user_id: int) -> 'asyncio.Lock':
        """Получает или создает блокировку для пользователя (защита от гонок)"""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]
    
    def _hash_text(self, text: str) -> str:
        """Хэширует текст для сравнения"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()
    
    def _hash_keyboard(self, keyboard) -> str:
        """Хэширует клавиатуру для сравнения"""
        if keyboard is None:
            return "none"
        # Преобразуем InlineKeyboardMarkup в строку для сравнения
        try:
            import json
            # Получаем структуру клавиатуры
            if hasattr(keyboard, 'inline_keyboard'):
                kb_data = json.dumps(keyboard.inline_keyboard, sort_keys=True, default=str)
            else:
                kb_data = str(keyboard)
            return hashlib.md5(kb_data.encode('utf-8')).hexdigest()
        except Exception:
            return hashlib.md5(str(keyboard).encode('utf-8')).hexdigest()
    
    def _get_message_key(self, message_or_callback: Union[types.Message, types.CallbackQuery, dict]) -> Optional[Tuple[int, int]]:
        """Получает ключ для кэша рендера: (user_id, message_id)"""
        if isinstance(message_or_callback, types.CallbackQuery):
            if message_or_callback.message:
                return (message_or_callback.from_user.id, message_or_callback.message.message_id)
        elif isinstance(message_or_callback, types.Message):
            return (message_or_callback.from_user.id, message_or_callback.message_id)
        elif isinstance(message_or_callback, dict):
            user_id = message_or_callback.get('user_id')
            message_id = message_or_callback.get('message_id')
            if user_id and message_id:
                return (user_id, message_id)
        return None
    
    def _log_screen_action(
        self,
        request_id: str,
        telegram_id: Optional[int],
        screen_id: ScreenID,
        action: str,
        payload: str = "-",
        mode: str = "send",
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        action_type: Optional[ActionType] = None,
        backstack_size: Optional[int] = None,
        next_screen: Optional[ScreenID] = None
    ):
        """Логирует действие с экраном в structured формате"""
        log_data = {
            "request_id": request_id,
            "telegram_id": telegram_id,
            "screen_id": screen_id.value,
            "action": action,
            "payload": payload,
            "mode": mode,
        }
        
        if action_type:
            log_data["action_type"] = action_type.value
        
        if backstack_size is not None:
            log_data["backstack_size"] = backstack_size
        
        if next_screen:
            log_data["next_screen"] = next_screen.value
        
        if duration_ms is not None:
            log_data["duration_ms"] = round(duration_ms, 2)
        
        if error:
            log_data["error"] = error
            logger.error(f"Screen action failed: {log_data}")
        else:
            logger.info(f"Screen action: {log_data}")
    
    async def show_screen(
        self,
        screen_id: ScreenID,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        viewmodel: BaseViewModel,
        edit: bool = False,
        user_id: Optional[int] = None
    ) -> bool:
        """
        Показывает экран пользователю
        
        Args:
            screen_id: ID экрана для отображения
            message_or_callback: Message или CallbackQuery для отправки/редактирования
            viewmodel: ViewModel с данными для экрана
            edit: Если True, редактирует существующее сообщение, иначе отправляет новое
            user_id: ID пользователя (для проверки прав администратора)
            
        Returns:
            True, если успешно, False в случае ошибки
        """
        # Определяем user_id и роль
        if user_id is None:
            user_id = self._get_user_id(message_or_callback)
        
        role = self._get_user_role(user_id)
        
        # Проверка навигации (только если это новый экран, не редактирование)
        # ВАЖНО: Для редактирования (edit=True) не проверяем навигацию - это обновление существующего экрана
        if not edit and user_id:
            current_screen = self._get_current_screen(user_id)
            if current_screen and current_screen != screen_id and not can_navigate(current_screen, screen_id, role):
                logger.warning(
                    f"Переход запрещен: {current_screen} -> {screen_id} (role={role}, user_id={user_id})"
                )
                # Показываем ERROR экран (будет реализован в ШАГ 4)
                return False
        
        screen = self.get_screen(screen_id)
        if not screen:
            logger.error(f"Экран {screen_id} не найден в реестре")
            return False
        
        # Проверяем, что ViewModel соответствует экрану
        if viewmodel.screen_id != screen_id:
            logger.warning(
                f"Несоответствие ViewModel и экрана: "
                f"viewmodel.screen_id={viewmodel.screen_id}, screen_id={screen_id}"
            )
        
        # Генерируем request_id для отслеживания
        request_id = self._generate_request_id()
        start_time = time.monotonic()
        
        # Логируем показ экрана (DEBUG уровень для производительности)
        logger.debug(
            f"[UI SHOW] request_id={request_id}, screen_id={screen_id.value}, "
            f"edit={edit}, user_id={user_id}"
        )
        
        try:
            # Рендерим текст и клавиатуру с измерением времени
            render_start = time.monotonic()
            text = await screen.render(viewmodel)
            render_duration = (time.monotonic() - render_start) * 1000
            
            keyboard_start = time.monotonic()
            keyboard = await screen.build_keyboard(viewmodel)
            keyboard_duration = (time.monotonic() - keyboard_start) * 1000
            
            # NO-OP render: проверяем, изменился ли контент
            message_key = self._get_message_key(message_or_callback)
            text_hash = self._hash_text(text)
            keyboard_hash = self._hash_keyboard(keyboard)
            
            # Для edit операций проверяем, нужно ли обновлять
            if message_key and edit:
                last_text_hash, last_keyboard_hash = self._last_render_cache.get(message_key, (None, None))
                if last_text_hash == text_hash and last_keyboard_hash == keyboard_hash:
                    # Контент не изменился - NO-OP, не вызываем edit
                    logger.debug(
                        f"[NO-OP] request_id={request_id}, screen_id={screen_id.value}, "
                        f"user_id={user_id}, render={render_duration:.2f}ms, keyboard={keyboard_duration:.2f}ms"
                    )
                    return True
            
            # Отправляем или редактируем сообщение
            send_start = time.monotonic()
            tg_api_duration = 0
            
            # ВАЖНО: Для CallbackQuery ВСЕГДА редактируем сообщение (кроме явно указанного edit=False)
            # Для Message - всегда новое сообщение
            if isinstance(message_or_callback, types.CallbackQuery):
                # Для CallbackQuery по умолчанию edit=True (если не указано явно edit=False)
                should_edit = edit if edit is not None else True
                if should_edit:
                    try:
                        # Объединяем edit_text и edit_reply_markup в один вызов для производительности
                        tg_start = time.monotonic()
                        await message_or_callback.message.edit_text(
                            text,
                            reply_markup=keyboard,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                        tg_api_duration = (time.monotonic() - tg_start) * 1000
                        # Сохраняем хэши в кэш после успешного обновления
                        if message_key:
                            self._last_render_cache[message_key] = (text_hash, keyboard_hash)
                    except TelegramBadRequest as e:
                        # Если не удалось отредактировать (текст не изменился, сообщение слишком старое, или нет сообщения)
                        error_msg = str(e).lower()
                        if "message is not modified" in error_msg:
                            # Текст не изменился - проверяем, нужно ли обновить только клавиатуру
                            if message_key:
                                last_text_hash, last_keyboard_hash = self._last_render_cache.get(message_key, (None, None))
                                if last_keyboard_hash != keyboard_hash:
                                    # Клавиатура изменилась - обновляем только её
                                    try:
                                        await message_or_callback.message.edit_reply_markup(reply_markup=keyboard)
                                        self._last_render_cache[message_key] = (text_hash, keyboard_hash)
                                        logger.debug(f"Обновлена только клавиатура для экрана {screen_id}")
                                    except Exception as e2:
                                        logger.debug(f"Не удалось обновить клавиатуру для экрана {screen_id}: {e2}")
                                else:
                                    # Ничего не изменилось - NO-OP
                                    logger.debug(f"[NO-OP] Контент не изменился для экрана {screen_id}")
                        elif "message to edit not found" in error_msg or "message can't be edited" in error_msg or "bad request: message to edit not found" in error_msg:
                            # Сообщение слишком старое или удалено - отправляем новое ТОЛЬКО в этом случае
                            logger.warning(f"Сообщение нельзя отредактировать для экрана {screen_id}: {e}, отправляем новое")
                            await message_or_callback.message.answer(
                                text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            disable_web_page_preview=True
                            )
                            # Обновляем кэш для нового сообщения
                            if message_key:
                                self._last_render_cache[message_key] = (text_hash, keyboard_hash)
                        else:
                            # Другая ошибка - логируем, но НЕ отправляем новое сообщение
                            # Это предотвращает создание дублирующих сообщений при обычных ошибках
                            logger.warning(f"Не удалось отредактировать сообщение для экрана {screen_id}: {e}")
                    except Exception as e:
                        # Обрабатываем любые другие исключения (не только TelegramBadRequest)
                        # Это может быть, если сообщение было удалено или произошла другая ошибка
                        error_msg = str(e).lower()
                        if "message" in error_msg and ("not found" in error_msg or "can't be edited" in error_msg or "deleted" in error_msg):
                            # Сообщение удалено или недоступно - отправляем новое
                            logger.warning(f"Сообщение недоступно для редактирования для экрана {screen_id}: {e}, отправляем новое")
                            await message_or_callback.message.answer(
                                text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                            disable_web_page_preview=True
                            )
                            # Обновляем кэш для нового сообщения
                            if message_key:
                                self._last_render_cache[message_key] = (text_hash, keyboard_hash)
                        else:
                            # Другая ошибка - пробрасываем дальше
                            raise
                else:
                    # Явно указано edit=False - отправляем новое сообщение
                    await message_or_callback.message.answer(
                        text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    # Обновляем кэш для нового сообщения
                    if message_key:
                        self._last_render_cache[message_key] = (text_hash, keyboard_hash)
            elif isinstance(message_or_callback, dict):
                # Обновление по chat_id и message_id (для фоновых задач)
                chat_id = message_or_callback.get('chat_id')
                message_id = message_or_callback.get('message_id')
                bot = message_or_callback.get('bot')
                if chat_id and message_id and bot:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                else:
                    logger.error(f"Недостаточно данных для обновления сообщения: {message_or_callback}")
                    return False
            elif isinstance(message_or_callback, types.Message):
                # Для Message всегда отправляем новое сообщение
                tg_start = time.monotonic()
                await message_or_callback.answer(
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                tg_api_duration = (time.monotonic() - tg_start) * 1000
                # Обновляем кэш для нового сообщения
                if message_key:
                    self._last_render_cache[message_key] = (text_hash, keyboard_hash)
            
            send_duration = (time.monotonic() - send_start) * 1000
            total_duration = (time.monotonic() - start_time) * 1000
            
            # Детальное логирование производительности
            logger.info(
                f"[PERF] show_screen screen={screen_id.value} total={total_duration:.2f}ms "
                f"render={render_duration:.2f}ms keyboard={keyboard_duration:.2f}ms "
                f"tg_api={tg_api_duration:.2f}ms user_id={user_id}"
            )
            
            return True
            
        except Exception as e:
            # Любая ошибка при показе экрана
            total_duration = (time.monotonic() - start_time) * 1000
            error_msg = str(e)
            
            # Логируем ошибку
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action="show",
                payload="-",
                mode="edit" if edit else "send",
                duration_ms=total_duration,
                error=error_msg
            )
            
            logger.exception(
                f"Ошибка при показе экрана {screen_id}: request_id={request_id}, error={error_msg}"
            )
            
            # Показываем ERROR screen вместо fallback
            try:
                from app.ui.viewmodels.error import ErrorViewModel
                error_viewmodel = ErrorViewModel(
                    error_message="Произошла ошибка при отображении экрана",
                    request_id=request_id,
                    error_type="render_error"
                )
                # Рекурсивный вызов, но с флагом, чтобы избежать бесконечной рекурсии
                if screen_id != ScreenID.ERROR:
                    await self.show_screen(
                        screen_id=ScreenID.ERROR,
                        message_or_callback=message_or_callback,
                        viewmodel=error_viewmodel,
                        edit=edit,
                        user_id=user_id
                    )
            except Exception as fallback_error:
                logger.error(f"Не удалось показать ERROR screen: {fallback_error}")
            
            return False
        
        except TelegramBadRequest as e:
            # Ошибка форматирования HTML (legacy обработка)
            total_duration = (time.monotonic() - start_time) * 1000
            error_msg = str(e)
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action="show",
                payload="-",
                mode="edit" if edit else "send",
                duration_ms=total_duration,
                error=f"HTML formatting error: {error_msg}"
            )
            
            logger.exception(f"Ошибка форматирования HTML при показе экрана {screen_id}: {e}")
            
            # Fallback: отправляем без HTML
            try:
                import re
                text_plain = re.sub(r'<[^>]+>', '', text)
                
                if edit and isinstance(message_or_callback, types.CallbackQuery):
                    await message_or_callback.message.edit_text(
                        text_plain,
                        reply_markup=keyboard
                    )
                elif isinstance(message_or_callback, types.Message):
                    await message_or_callback.answer(
                        text_plain,
                        reply_markup=keyboard
                    )
                elif isinstance(message_or_callback, types.CallbackQuery):
                    await message_or_callback.message.answer(
                        text_plain,
                        reply_markup=keyboard
                    )
                
                return True
            except Exception as fallback_error:
                logger.exception(f"Ошибка при fallback отправке экрана {screen_id}: {fallback_error}")
                return False
                
        except Exception as e:
            logger.exception(f"Ошибка при показе экрана {screen_id}: {e}")
            return False
    
    def _get_user_id(self, message_or_callback: Union[types.Message, types.CallbackQuery, dict]) -> Optional[int]:
        """Извлекает user_id из message_or_callback"""
        if isinstance(message_or_callback, types.Message):
            return message_or_callback.from_user.id
        elif isinstance(message_or_callback, types.CallbackQuery):
            return message_or_callback.from_user.id
        elif isinstance(message_or_callback, dict):
            return message_or_callback.get('user_id')
        return None
    
    def _get_user_role(self, user_id: Optional[int]) -> UserRole:
        """Определяет роль пользователя"""
        if user_id and is_admin(user_id):
            return "admin"
        return "user"
    
    def _push_to_backstack(self, user_id: int, screen_id: ScreenID, from_screen_id: Optional[ScreenID] = None):
        """
        Добавляет текущий экран в backstack ПЕРЕД переходом на новый экран
        ВАЖНО: вызывается ТОЛЬКО для NAVIGATION действий
        
        Args:
            user_id: ID пользователя
            screen_id: Новый экран, на который переходим
            from_screen_id: Текущий экран (если не указан, берется из _current_screens)
        """
        if user_id not in self._backstacks:
            self._backstacks[user_id] = []
        
        backstack = self._backstacks[user_id]
        
        # Определяем текущий экран для добавления в стек
        current = from_screen_id
        if current is None:
            # Если не указан явно, берем из _current_screens
            current = self._current_screens.get(user_id)
        
        # Добавляем текущий экран в стек только если он отличается от нового и существует
        if current and current != screen_id:
            # Не добавляем дубликаты подряд
            if not backstack or backstack[-1] != current:
                backstack.append(current)
                logger.debug(f"[BACKSTACK] Added {current.value} to backstack for user {user_id} (next: {screen_id.value})")
        
        # Ограничиваем размер стека (максимум 10)
        if len(backstack) > 10:
            removed = backstack.pop(0)
            logger.debug(f"[BACKSTACK] Removed {removed.value} from backstack (max size)")
    
    def _pop_from_backstack(self, user_id: int) -> Optional[ScreenID]:
        """Извлекает предыдущий экран из backstack"""
        if user_id not in self._backstacks:
            return None
        
        backstack = self._backstacks[user_id]
        if not backstack:
            return None
        
        return backstack.pop()
    
    def _get_current_screen(self, user_id: int) -> Optional[ScreenID]:
        """Получает текущий экран пользователя"""
        return self._current_screens.get(user_id)
    
    def _set_current_screen(self, user_id: int, screen_id: ScreenID):
        """Устанавливает текущий экран пользователя"""
        self._current_screens[user_id] = screen_id
    
    async def navigate(
        self,
        from_screen_id: ScreenID,
        to_screen_id: ScreenID,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        viewmodel: BaseViewModel,
        edit: bool = True,
        user_id: Optional[int] = None,
        action_type: ActionType = ActionType.NAVIGATION
    ) -> bool:
        """
        Выполняет переход между экранами с проверкой навигации
        
        Args:
            from_screen_id: Исходный экран
            to_screen_id: Целевой экран
            message_or_callback: Message или CallbackQuery
            viewmodel: ViewModel для целевого экрана
            edit: Редактировать сообщение или отправлять новое (по умолчанию True для CallbackQuery)
            user_id: ID пользователя
            action_type: Тип действия (определяет, нужно ли пушить в backstack)
            
        Returns:
            True, если переход успешен
        """
        if user_id is None:
            user_id = self._get_user_id(message_or_callback)
        
        role = self._get_user_role(user_id)
        
        # Проверяем навигацию (если from != to, иначе это refresh)
        # ВАЖНО: Для refresh (from == to) не проверяем навигацию
        # ВАЖНО: Для BACK действия Navigator уже проверил навигацию
        if from_screen_id != to_screen_id and not can_navigate(from_screen_id, to_screen_id, role):
            logger.warning(
                f"[UI NAVIGATE] Переход запрещен: {from_screen_id.value} -> {to_screen_id.value} "
                f"(role={role}, user_id={user_id})"
            )
            return False
        
        # ВАЖНО: backstack управляется ТОЛЬКО через Navigator
        # ScreenManager НЕ должен управлять backstack напрямую
        # Для FLOW действий anchor устанавливается через Navigator
        if user_id and action_type == ActionType.FLOW and from_screen_id != to_screen_id:
            navigator = get_navigator()
            navigator.set_flow_anchor(user_id, from_screen_id)
            logger.debug(f"[FLOW] Set flow anchor {from_screen_id.value} for user {user_id}")
        
        # ВАЖНО: Для CallbackQuery ВСЕГДА редактируем (кроме явно указанного edit=False)
        # Для Message - всегда новое сообщение
        if edit is True and isinstance(message_or_callback, types.CallbackQuery):
            edit = True  # Явно устанавливаем для CallbackQuery
        elif edit is True and isinstance(message_or_callback, types.Message):
            edit = False  # Для Message всегда новое сообщение
        
        # Логируем переход (уменьшаем уровень логирования для производительности)
        logger.debug(
            f"[UI NAVIGATE] from={from_screen_id.value}, to={to_screen_id.value}, "
            f"edit={edit}, user_id={user_id}, action_type={action_type.value}"
        )
        
        success = await self.show_screen(
            screen_id=to_screen_id,
            message_or_callback=message_or_callback,
            viewmodel=viewmodel,
            edit=edit,
            user_id=user_id
        )
        
        # Устанавливаем новый текущий экран ТОЛЬКО после успешного показа
        # ВАЖНО: current_screen также синхронизируется с Navigator
        if success and user_id:
            self._set_current_screen(user_id, to_screen_id)
            # Синхронизируем с Navigator (Navigator уже обновил current_screen при успешной навигации)
            navigator = get_navigator()
            navigator_current = navigator.get_current_screen(user_id)
            if navigator_current:
                self._set_current_screen(user_id, navigator_current)
        
        return success
    
    async def handle_ui_action(
        self,
        screen_id: ScreenID,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int] = None
    ) -> bool:
        """
        Центральный dispatcher для UI действий - жёсткий ACTION → EFFECT pipeline
        
        Правила:
        1. ВСЕГДА логирует action
        2. ПРОВЕРЯЕТ action разрешён для screen_id (ACTION_MAP)
        3. ДЕЛЕГИРУЕТ по типу эффекта:
           - NAVIGATION → navigate()
           - STATE → show_screen(edit=True)
           - FLOW → await + show_screen(success/error)
        4. ЕСЛИ action не обработан → ERROR screen + лог
        
        Args:
            screen_id: ID экрана
            action: Действие
            payload: Дополнительные данные
            message_or_callback: Message или CallbackQuery
            user_id: ID пользователя
            
        Returns:
            True, если действие обработано успешно
        """
        request_id = self._generate_request_id()
        start_time = time.monotonic()
        
        if user_id is None:
            user_id = self._get_user_id(message_or_callback)
        
        # Получаем размер backstack для логирования
        backstack_size = len(self._backstacks.get(user_id, [])) if user_id else 0
        
        # ШАГ 1: ЛОГИРУЕМ action (ВСЕГДА)
        logger.info(
            f"[UI ACTION] request_id={request_id}, screen_id={screen_id.value}, "
            f"action={action}, payload={payload}, user_id={user_id}, backstack_size={backstack_size}"
        )
        
        # ШАГ 2: ПРОВЕРЯЕМ action разрешён для screen_id
        action_effect = get_action_effect(screen_id, action)
        if not action_effect:
            # Action не определён в ACTION_MAP → ERROR screen + лог
            duration = (time.monotonic() - start_time) * 1000
            error_msg = f"Action '{action}' not allowed for screen '{screen_id.value}'"
            logger.error(f"[UI ACTION FAILED] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=None,
                backstack_size=backstack_size
            )
            
            # Показываем ERROR screen
            from app.ui.viewmodels.error import ErrorViewModel
            error_viewmodel = ErrorViewModel(
                error_message=f"Действие '{action}' не поддерживается для этого экрана",
                request_id=request_id,
                error_type="action_not_allowed"
            )
            
            await self.show_screen(
                screen_id=ScreenID.ERROR,
                message_or_callback=message_or_callback,
                viewmodel=error_viewmodel,
                edit=isinstance(message_or_callback, types.CallbackQuery),
                user_id=user_id
            )
            return False
        
        effect_type, target_screen = action_effect
        
        # Определяем ActionType для логирования
        if effect_type == "NAVIGATION":
            action_type = ActionType.NAVIGATION
        elif effect_type == "STATE":
            action_type = ActionType.STATE
        elif effect_type == "FLOW":
            action_type = ActionType.FLOW
        else:
            action_type = ActionType.STATE
        
        # Логируем с action_type и next_screen
        logger.info(
            f"[UI ACTION] request_id={request_id}, effect_type={effect_type}, "
            f"target_screen={target_screen.value if target_screen else 'None'}, "
            f"action_type={action_type.value}"
        )
        
        role = self._get_user_role(user_id)
        
        # ШАГ 3: ДЕЛЕГИРУЕМ по типу эффекта
        try:
            if effect_type == "NAVIGATION":
                return await self._handle_navigation_action(
                    screen_id=screen_id,
                    action=action,
                    payload=payload,
                    message_or_callback=message_or_callback,
                    user_id=user_id,
                    target_screen=target_screen,
                    action_type=action_type,
                    request_id=request_id,
                    start_time=start_time,
                    backstack_size=backstack_size
                )
            elif effect_type == "STATE":
                return await self._handle_state_action(
                    screen_id=screen_id,
                    action=action,
                    payload=payload,
                    message_or_callback=message_or_callback,
                    user_id=user_id,
                    action_type=action_type,
                    request_id=request_id,
                    start_time=start_time,
                    backstack_size=backstack_size
                )
            elif effect_type == "FLOW":
                return await self._handle_flow_action(
                    screen_id=screen_id,
                    action=action,
                    payload=payload,
                    message_or_callback=message_or_callback,
                    user_id=user_id,
                    target_screen=target_screen,
                    action_type=action_type,
                    request_id=request_id,
                    start_time=start_time,
                    backstack_size=backstack_size
                )
            else:
                raise ValueError(f"Unknown effect_type: {effect_type}")
        except Exception as e:
            # Любая ошибка → ERROR screen + лог
            duration = (time.monotonic() - start_time) * 1000
            error_msg = f"Error handling action '{action}': {str(e)}"
            logger.exception(f"[UI ACTION ERROR] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=action_type,
                backstack_size=backstack_size
            )
            
            from app.ui.viewmodels.error import ErrorViewModel
            error_viewmodel = ErrorViewModel(
                error_message=f"Ошибка при обработке действия: {str(e)}",
                request_id=request_id,
                error_type="action_error"
            )
            
            await self.show_screen(
                screen_id=ScreenID.ERROR,
                message_or_callback=message_or_callback,
                viewmodel=error_viewmodel,
                edit=isinstance(message_or_callback, types.CallbackQuery),
                user_id=user_id
            )
            return False
    
    async def handle_action(
        self,
        screen_id: ScreenID,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int] = None
    ) -> bool:
        """
        Обрабатывает действие на экране (legacy метод, делегирует в handle_ui_action)
        """
        return await self.handle_ui_action(
            screen_id=screen_id,
            action=action,
            payload=payload,
            message_or_callback=message_or_callback,
            user_id=user_id
        )
    
    async def _handle_navigation_action(
        self,
        screen_id: ScreenID,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        target_screen: Optional[ScreenID],
        action_type: ActionType,
        request_id: str,
        start_time: float,
        backstack_size: int
    ) -> bool:
        """Обрабатывает NAVIGATION действие через Navigator"""
        if not user_id:
            return False
        
        # Если у экрана есть handle_action, сначала пробуем обработать через него
        # Это позволяет экрану самому управлять навигацией (например, создавать ViewModel для целевого экрана)
        screen = self.get_screen(screen_id)
        if screen and hasattr(screen, 'handle_action'):
            try:
                handled = await screen.handle_action(
                    action=action,
                    payload=payload,
                    message_or_callback=message_or_callback,
                    user_id=user_id
                )
                if handled:
                    # Если экран сам обработал действие (например, вызвал navigate), возвращаем успех
                    duration = (time.monotonic() - start_time) * 1000
                    logger.info(f"[UI NAVIGATION] action={action} handled by screen, duration={duration:.2f}ms")
                    return True
            except Exception as e:
                duration = (time.monotonic() - start_time) * 1000
                error_msg = f"Error in screen.handle_action: {e}"
                logger.error(f"[UI NAVIGATION ERROR] {error_msg}")
                import traceback
                traceback.print_exc()
        
        role = self._get_user_role(user_id)
        navigator = get_navigator()
        
        # Преобразуем action в CallbackAction
        try:
            callback_action = CallbackAction(action)
        except ValueError:
            # Для обратной совместимости
            if action == "back":
                callback_action = CallbackAction.BACK
            elif action == "refresh":
                callback_action = CallbackAction.REFRESH
            elif action == "open":
                callback_action = CallbackAction.OPEN
            else:
                logger.warning(f"[UI NAVIGATION] Unknown action: {action}")
                return False
        
        # Парсим payload в dict
        payload_dict = {}
        if payload and payload != "-":
            # Пытаемся распарсить как JSON (для Pagination и других сложных payload)
            try:
                import json
                payload_dict = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                # Если не JSON, используем как простую строку
                payload_dict = {"value": payload}
        
        # Если есть target_screen из ACTION_MAP, добавляем в payload
        if target_screen:
            payload_dict["target_screen"] = target_screen.value
        
        # Используем Navigator для обработки навигации
        nav_result = navigator.handle(
            action=callback_action,
            current_screen=screen_id,
            payload=payload_dict if payload_dict else None,
            user_id=user_id,
            user_role=role
        )
        
        # Синхронизируем backstack в ScreenManager из Navigator (только для логирования)
        # ВАЖНО: backstack управляется ТОЛЬКО через Navigator
        if nav_result.updated_backstack is not None:
            self._backstacks[user_id] = nav_result.updated_backstack.copy()
        
        # Синхронизируем current_screen из Navigator
        navigator_current = navigator.get_current_screen(user_id)
        if navigator_current:
            self._set_current_screen(user_id, navigator_current)
        
        # Обрабатываем ошибки
        if nav_result.error:
            duration = (time.monotonic() - start_time) * 1000
            logger.warning(f"[UI NAVIGATION] {nav_result.error}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=nav_result.error,
                action_type=action_type,
                backstack_size=len(self._backstacks.get(user_id, [])),
                next_screen=nav_result.target_screen
            )
            
            # Если refresh с ошибкой, просто обновляем текущий экран
            if nav_result.render_mode == RenderMode.REFRESH:
                viewmodel = await self._create_viewmodel_for_screen(
                    nav_result.target_screen, message_or_callback, user_id, payload
                )
                if viewmodel:
                    return await self.show_screen(
                        screen_id=nav_result.target_screen,
                        message_or_callback=message_or_callback,
                        viewmodel=viewmodel,
                        edit=True,
                        user_id=user_id
                    )
            return False
        
        # Создаём ViewModel для целевого экрана
        viewmodel = await self._create_viewmodel_for_screen(
            nav_result.target_screen, message_or_callback, user_id, payload
        )
        
        if not viewmodel:
            duration = (time.monotonic() - start_time) * 1000
            error_msg = f"Failed to create ViewModel for {nav_result.target_screen.value}"
            logger.error(f"[UI NAVIGATION ERROR] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=action_type,
                backstack_size=len(self._backstacks.get(user_id, [])),
                next_screen=nav_result.target_screen
            )
            return False
        
        # Определяем режим редактирования
        # ВАЖНО: Для CallbackQuery ВСЕГДА редактируем сообщение (кроме явно новых)
        # Для Message - всегда новое сообщение
        edit = isinstance(message_or_callback, types.CallbackQuery)
        
        # Выполняем навигацию
        if nav_result.render_mode == RenderMode.REFRESH:
            # Refresh - просто показываем экран с edit=True
            success = await self.show_screen(
                screen_id=nav_result.target_screen,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=edit,
                user_id=user_id
            )
        else:
            # OPEN или BACK - используем navigate
            from_screen = screen_id
            success = await self.navigate(
                from_screen_id=from_screen,
                to_screen_id=nav_result.target_screen,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=edit,
                action_type=action_type
            )
        
        duration = (time.monotonic() - start_time) * 1000
        new_backstack_size = len(self._backstacks.get(user_id, [])) if user_id else 0
        
        result = "OK" if success else "ERROR"
        logger.info(
            f"[UI NAVIGATION] result={result}, from={screen_id.value}, to={nav_result.target_screen.value}, "
            f"mode={nav_result.render_mode.value}, duration={duration:.2f}ms, backstack_size={new_backstack_size}"
        )
        
        self._log_screen_action(
            request_id=request_id,
            telegram_id=user_id,
            screen_id=screen_id,
            action=action,
            payload=payload,
            mode="action",
            duration_ms=duration,
            action_type=action_type,
            backstack_size=new_backstack_size,
            next_screen=nav_result.target_screen if success else None
        )
        
        return success
    
    async def _handle_state_action(
        self,
        screen_id: ScreenID,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        action_type: ActionType,
        request_id: str,
        start_time: float,
        backstack_size: int
    ) -> bool:
        """Обрабатывает STATE действие"""
        # Guard: refresh ТОЛЬКО из CallbackQuery
        if action == "refresh" and isinstance(message_or_callback, types.Message):
            duration = (time.monotonic() - start_time) * 1000
            error_msg = "Refresh from Message is forbidden"
            logger.warning(f"[UI STATE FORBIDDEN] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=action_type,
                backstack_size=backstack_size
            )
            return False
        
        # Делегируем в screen.handle_action для специфичных действий (page, filter, select)
        screen = self.get_screen(screen_id)
        if not screen:
            duration = (time.monotonic() - start_time) * 1000
            error_msg = f"Screen {screen_id.value} not found"
            logger.error(f"[UI STATE ERROR] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=action_type,
                backstack_size=backstack_size
            )
            return False
        
        # Если у экрана есть handle_action, делегируем
        if hasattr(screen, 'handle_action') and action in ("page", "filter", "select", "select_period"):
            try:
                # select_period не требует Navigator — делегируем напрямую в экран
                if action == "select_period":
                    result = await screen.handle_action(
                        action, payload, message_or_callback, user_id
                    )
                    duration = (time.monotonic() - start_time) * 1000
                    result_str = "OK" if result else "ERROR"
                    logger.info(f"[UI STATE] result={result_str}, action={action}, duration={duration:.2f}ms")
                    self._log_screen_action(
                        request_id=request_id,
                        telegram_id=user_id,
                        screen_id=screen_id,
                        action=action,
                        payload=payload,
                        mode="action",
                        duration_ms=duration,
                        action_type=action_type,
                        backstack_size=backstack_size
                    )
                    return result
                # Используем Navigator для STATE действий (page, filter, select)
                if user_id:
                    navigator = get_navigator()
                    try:
                        callback_action = CallbackAction(action.upper())
                    except ValueError:
                        # Для обратной совместимости
                        if action == "page":
                            callback_action = CallbackAction.PAGE
                        elif action == "filter":
                            callback_action = CallbackAction.FILTER
                        elif action == "select":
                            callback_action = CallbackAction.SELECT
                        else:
                            callback_action = CallbackAction.PAGE
                    
                    # Парсим payload
                    payload_dict = {}
                    if payload and payload != "-":
                        try:
                            import json
                            payload_dict = json.loads(payload)
                        except (json.JSONDecodeError, ValueError):
                            payload_dict = {"value": payload}
                    
                    # Используем Navigator для STATE действий
                    nav_result = navigator.handle(
                        action=callback_action,
                        current_screen=screen_id,
                        payload=payload_dict if payload_dict else None,
                        user_id=user_id,
                        user_role=self._get_user_role(user_id)
                    )
                    
                    # Обновляем payload для передачи в screen.handle_action
                    if nav_result.payload:
                        # Если payload содержит только {"value": ...}, извлекаем исходное значение
                        import json
                        if isinstance(nav_result.payload, dict):
                            # Если это dict с ключом "value", извлекаем исходное значение
                            if "value" in nav_result.payload and len(nav_result.payload) == 1:
                                payload = nav_result.payload["value"]
                            else:
                                # Иначе преобразуем в JSON строку
                                payload = json.dumps(nav_result.payload)
                        else:
                            payload = nav_result.payload
                
                result = await screen.handle_action(action, payload, message_or_callback, user_id, action_type)
                duration = (time.monotonic() - start_time) * 1000
                
                result_str = "OK" if result else "ERROR"
                logger.info(f"[UI STATE] result={result_str}, action={action}, duration={duration:.2f}ms")
                
                self._log_screen_action(
                    request_id=request_id,
                    telegram_id=user_id,
                    screen_id=screen_id,
                    action=action,
                    payload=payload,
                    mode="action",
                    duration_ms=duration,
                    action_type=action_type,
                    backstack_size=backstack_size
                )
                return result
            except Exception as e:
                duration = (time.monotonic() - start_time) * 1000
                error_msg = f"Error in screen.handle_action: {str(e)}"
                logger.exception(f"[UI STATE ERROR] {error_msg}")
                
                self._log_screen_action(
                    request_id=request_id,
                    telegram_id=user_id,
                    screen_id=screen_id,
                    action=action,
                    payload=payload,
                    mode="action",
                    duration_ms=duration,
                    error=error_msg,
                    action_type=action_type,
                    backstack_size=backstack_size
                )
                return False
        
        # Для refresh создаём ViewModel и показываем
        viewmodel = await self._create_viewmodel_for_screen(
            screen_id, message_or_callback, user_id, payload
        )
        
        if not viewmodel:
            duration = (time.monotonic() - start_time) * 1000
            error_msg = f"Failed to create ViewModel for {screen_id.value}"
            logger.error(f"[UI STATE ERROR] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=action_type,
                backstack_size=backstack_size
            )
            return False
        
        # STATE action - show_screen с edit=True, НЕ navigate, НЕ backstack
        success = await self.show_screen(
            screen_id=screen_id,
            message_or_callback=message_or_callback,
            viewmodel=viewmodel,
            edit=True,
            user_id=user_id
        )
        
        duration = (time.monotonic() - start_time) * 1000
        result_str = "OK" if success else "ERROR"
        logger.info(f"[UI STATE] result={result_str}, action={action}, duration={duration:.2f}ms")
        
        self._log_screen_action(
            request_id=request_id,
            telegram_id=user_id,
            screen_id=screen_id,
            action=action,
            payload=payload,
            mode="action",
            duration_ms=duration,
            action_type=action_type,
            backstack_size=backstack_size
        )
        
        return success
    
    async def _handle_flow_action(
        self,
        screen_id: ScreenID,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        target_screen: Optional[ScreenID],
        action_type: ActionType,
        request_id: str,
        start_time: float,
        backstack_size: int
    ) -> bool:
        """Обрабатывает FLOW действие (async flow с loading → success/error)"""
        # Сохраняем anchor screen через Navigator
        if user_id:
            current_screen = self._get_current_screen(user_id)
            if current_screen and current_screen != screen_id:
                navigator = get_navigator()
                navigator.set_flow_anchor(user_id, current_screen)
                logger.debug(f"[FLOW] Set flow anchor {current_screen.value} for user {user_id}")
        
        # Определяем целевой экран
        target = target_screen if target_screen else screen_id
        
        # Специальная обработка для CONNECT flow
        if screen_id == ScreenID.CONNECT and action == "open":
            return await self._handle_connect_flow(
                message_or_callback=message_or_callback,
                user_id=user_id,
                request_id=request_id,
                start_time=start_time,
                backstack_size=backstack_size
            )
        
        # Для остальных FLOW действий (HELP и т.д.)
        viewmodel = await self._create_viewmodel_for_screen(
            target, message_or_callback, user_id, payload
        )
        
        if not viewmodel:
            duration = (time.monotonic() - start_time) * 1000
            error_msg = f"Failed to create ViewModel for {target.value}"
            logger.error(f"[UI FLOW ERROR] {error_msg}")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=screen_id,
                action=action,
                payload=payload,
                mode="action",
                duration_ms=duration,
                error=error_msg,
                action_type=action_type,
                backstack_size=backstack_size,
                next_screen=target
            )
            return False
        
        # FLOW - navigate без backstack
        current_screen = self._get_current_screen(user_id) if user_id else None
        success = await self.navigate(
            from_screen_id=current_screen if current_screen else screen_id,
            to_screen_id=target,
            message_or_callback=message_or_callback,
            viewmodel=viewmodel,
            edit=current_screen is not None and isinstance(message_or_callback, types.CallbackQuery),
            action_type=action_type
        )
        
        duration = (time.monotonic() - start_time) * 1000
        result_str = "OK" if success else "ERROR"
        logger.info(f"[UI FLOW] result={result_str}, screen={target.value}, duration={duration:.2f}ms")
        
        self._log_screen_action(
            request_id=request_id,
            telegram_id=user_id,
            screen_id=screen_id,
            action=action,
            payload=payload,
            mode="action",
            duration_ms=duration,
            action_type=action_type,
            backstack_size=backstack_size,
            next_screen=target if success else None
        )
        
        return success
    
    async def _handle_connect_flow(
        self,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        request_id: str,
        start_time: float,
        backstack_size: int
    ) -> bool:
        """
        Обрабатывает CONNECT flow: loading → success/error
        ДОКРУЧИВАЕТ ДО КОНЦА - всегда заканчивается результатом
        """
        from app.services.connection import can_user_connect
        from app.ui.helpers import get_connect_viewmodel
        
        # ШАГ 1: Проверяем подписку с принудительной синхронизацией с Remna
        # Это гарантирует получение актуальных данных из панели
        has_subscription = await can_user_connect(user_id, force_remna=True) if user_id else False
        
        if not has_subscription:
            # Нет подписки → показываем ACCESS_DENIED
            from app.ui.viewmodels.error import AccessDeniedViewModel
            error_viewmodel = AccessDeniedViewModel(
                message="У вас нет активной подписки"
            )
            
            current_screen = self._get_current_screen(user_id) if user_id else None
            success = await self.navigate(
                from_screen_id=current_screen if current_screen else ScreenID.CONNECT,
                to_screen_id=ScreenID.ACCESS_DENIED,
                message_or_callback=message_or_callback,
                viewmodel=error_viewmodel,
                edit=current_screen is not None and isinstance(message_or_callback, types.CallbackQuery),
                action_type=ActionType.NAVIGATION
            )
            
            duration = (time.monotonic() - start_time) * 1000
            logger.info(f"[UI CONNECT FLOW] result=ACCESS_DENIED, duration={duration:.2f}ms")
            return success
        
        # ШАГ 2: Получаем ссылку подписки синхронно (БЕЗ loading экрана)
        try:
            from app.services.users import get_user_active_subscription
            from app.remnawave.client import RemnaClient

            subscription = await get_user_active_subscription(user_id, use_cache=False)
            subscription_url = None

            logger.info(
                f"[UI CONNECT FLOW] fetching link: user_id={user_id} "
                f"has_subscription={subscription is not None} "
                f"remna_user_id={getattr(subscription, 'remna_user_id', None)}"
            )

            if not subscription:
                # Нет подписки — повторная проверка дала тот же результат
                logger.warning(f"[UI CONNECT FLOW] no subscription found after can_user_connect passed: user_id={user_id}")
            elif subscription.remna_user_id:
                # Основной путь: получаем ссылку напрямую из Remnawave по remna_user_id
                try:
                    client = RemnaClient()
                    try:
                        subscription_url = await client.get_user_subscription_url(subscription.remna_user_id)
                    finally:
                        await client.close()
                    if subscription_url:
                        logger.info(
                            f"[UI CONNECT FLOW] subscription URL fetched: user_id={user_id} "
                            f"remna_user_id={subscription.remna_user_id}"
                        )
                    else:
                        logger.warning(
                            f"[UI CONNECT FLOW] empty URL from Remnawave: user_id={user_id} "
                            f"remna_user_id={subscription.remna_user_id}"
                        )
                except Exception as e:
                    logger.error(
                        f"[UI CONNECT FLOW] Remnawave API error: user_id={user_id} "
                        f"remna_user_id={subscription.remna_user_id} err={e}"
                    )
                    subscription_url = None
            else:
                logger.error(
                    f"[UI CONNECT FLOW] remna_user_id not set: user_id={user_id} — "
                    f"subscription exists but Remnawave user not provisioned yet"
                )
                subscription_url = None
            
            # ШАГ 4: Показываем результат (success или error)
            if subscription_url:
                success_viewmodel = await get_connect_viewmodel(
                    telegram_id=user_id,
                    subscription_url=subscription_url,
                    status="success"
                )
            else:
                success_viewmodel = await get_connect_viewmodel(
                    telegram_id=user_id,
                    status="error",
                    error_message="Не удалось получить ссылку подписки"
                )
            
            success = await self.show_screen(
                screen_id=ScreenID.CONNECT,
                message_or_callback=message_or_callback,
                viewmodel=success_viewmodel,
                edit=True,
                user_id=user_id
            )
            
            duration = (time.monotonic() - start_time) * 1000
            result_str = "SUCCESS" if subscription_url else "ERROR"
            logger.info(f"[UI CONNECT FLOW] result={result_str}, duration={duration:.2f}ms")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=ScreenID.CONNECT,
                action="open",
                payload="-",
                mode="action",
                duration_ms=duration,
                action_type=ActionType.FLOW,
                backstack_size=backstack_size,
                next_screen=ScreenID.CONNECT if success else None
            )
            
            return success
            
        except Exception as e:
            # Ошибка → показываем error экран
            logger.exception(f"[UI CONNECT FLOW ERROR] {e}")
            
            error_viewmodel = await get_connect_viewmodel(
                telegram_id=user_id,
                status="error",
                error_message=f"Ошибка: {str(e)}"
            )
            
            success = await self.show_screen(
                screen_id=ScreenID.CONNECT,
                message_or_callback=message_or_callback,
                viewmodel=error_viewmodel,
                edit=True,
                user_id=user_id
            )
            
            duration = (time.monotonic() - start_time) * 1000
            logger.error(f"[UI CONNECT FLOW] result=ERROR, error={str(e)}, duration={duration:.2f}ms")
            
            self._log_screen_action(
                request_id=request_id,
                telegram_id=user_id,
                screen_id=ScreenID.CONNECT,
                action="open",
                payload="-",
                mode="action",
                duration_ms=duration,
                error=str(e),
                action_type=ActionType.FLOW,
                backstack_size=backstack_size
            )
            
            return success
    
    async def _create_viewmodel_for_screen(
        self,
        screen_id: ScreenID,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int],
        payload: str = "-"
    ) -> Optional[BaseViewModel]:
        """
        Создаёт ViewModel для экрана
        Централизованная логика создания ViewModel
        
        ОПТИМИЗАЦИЯ: Логирует время создания ViewModel для выявления узких мест
        """
        vm_start = time.monotonic()
        screen = self.get_screen(screen_id)
        if not screen:
            return None
        
        try:
            if screen_id == ScreenID.MAIN_MENU:
                from app.ui.helpers import get_main_menu_viewmodel
                if isinstance(message_or_callback, types.CallbackQuery):
                    return await get_main_menu_viewmodel(
                        telegram_id=user_id,
                        first_name=message_or_callback.from_user.first_name,
                        last_name=message_or_callback.from_user.last_name,
                        username=message_or_callback.from_user.username
                    )
                elif isinstance(message_or_callback, types.Message):
                    return await get_main_menu_viewmodel(
                        telegram_id=user_id,
                        first_name=message_or_callback.from_user.first_name,
                        last_name=message_or_callback.from_user.last_name,
                        username=message_or_callback.from_user.username
                    )
                else:
                    return await get_main_menu_viewmodel(telegram_id=user_id)
            
            elif screen_id == ScreenID.CONNECT:
                from app.ui.helpers import get_connect_viewmodel
                return await get_connect_viewmodel(telegram_id=user_id, status="loading")
            
            elif screen_id == ScreenID.PROFILE:
                from app.ui.helpers import get_profile_viewmodel
                return await get_profile_viewmodel(telegram_id=user_id)
            
            elif screen_id == ScreenID.ADMIN_PANEL:
                from app.services.stats import get_statistics
                db_start = time.monotonic()
                stats = await get_statistics()
                db_duration = (time.monotonic() - db_start) * 1000
                logger.debug(f"[PERF] get_statistics duration={db_duration:.2f}ms")
                return await screen.create_viewmodel(stats=stats)
            
            elif screen_id == ScreenID.ADMIN_STATS:
                from app.services.stats import get_statistics
                db_start = time.monotonic()
                stats = await get_statistics()
                db_duration = (time.monotonic() - db_start) * 1000
                logger.debug(f"[PERF] get_statistics duration={db_duration:.2f}ms")
                return await screen.create_viewmodel(stats=stats)
            
            elif screen_id == ScreenID.ADMIN_USERS:
                try:
                    from app.services.stats import get_users_list
                    # Парсим page из payload если есть
                    page = 1
                    if payload and payload != "-":
                        try:
                            import json
                            payload_dict = json.loads(payload) if isinstance(payload, str) else payload
                            page = payload_dict.get("page", 1) if isinstance(payload_dict, dict) else 1
                        except:
                            pass
                    
                    db_start = time.monotonic()
                    users_data = await get_users_list(page=page, page_size=10)
                    db_duration = (time.monotonic() - db_start) * 1000
                    logger.debug(f"[PERF] get_users_list page={page} duration={db_duration:.2f}ms")
                    return await screen.create_viewmodel(
                        users=users_data.get("users", []),
                        page=users_data.get("page", 1),
                        total_pages=users_data.get("total_pages", 0),
                        total=users_data.get("total", 0)
                    )
                except Exception as e:
                    logger.exception(f"[VIEWMODEL ERROR] Ошибка создания ViewModel для ADMIN_USERS: {e}")
                    # Возвращаем пустой ViewModel в случае ошибки
                    return await screen.create_viewmodel(
                        users=[],
                        page=1,
                        total_pages=0,
                        total=0
                    )
            
            elif screen_id == ScreenID.ADMIN_PAYMENTS:
                from app.services.stats import get_payments_list
                # Парсим page и status из payload если есть
                page = 1
                status = None
                if payload and payload != "-":
                    try:
                        import json
                        payload_dict = json.loads(payload) if isinstance(payload, str) else payload
                        if isinstance(payload_dict, dict):
                            page = payload_dict.get("page", 1)
                            status = payload_dict.get("status")
                    except:
                        pass
                
                db_start = time.monotonic()
                payments_data = await get_payments_list(page=page, page_size=10, status=status)
                db_duration = (time.monotonic() - db_start) * 1000
                logger.debug(f"[PERF] get_payments_list page={page} status={status} duration={db_duration:.2f}ms")
                return await screen.create_viewmodel(
                    payments=payments_data["payments"],
                    page=payments_data["page"],
                    total_pages=payments_data["total_pages"],
                    total=payments_data["total"],
                    status_filter=status
                )
            
            elif screen_id == ScreenID.HELP:
                return await screen.create_viewmodel()
            
            elif screen_id == ScreenID.SUBSCRIPTION_PLANS:
                return await screen.create_viewmodel()
            
            elif screen_id == ScreenID.SUBSCRIPTION_PLAN_DETAIL:
                return await screen.create_viewmodel()
            
            elif screen_id == ScreenID.SUBSCRIPTION_PAYMENT:
                return await screen.create_viewmodel()
            
            else:
                # Для остальных экранов пытаемся создать базовый ViewModel
                try:
                    return await screen.create_viewmodel()
                except TypeError as e:
                    logger.error(f"Не удалось создать ViewModel для экрана {screen_id}: {e}")
                    return None
                    
        except Exception as e:
            vm_duration = (time.monotonic() - vm_start) * 1000
            logger.exception(f"Ошибка создания ViewModel для {screen_id}: {e} (duration={vm_duration:.2f}ms)")
            return None
        
        finally:
            vm_duration = (time.monotonic() - vm_start) * 1000
            if vm_duration > 100:  # Логируем только медленные ViewModel
                logger.debug(f"[PERF] _create_viewmodel screen={screen_id.value} duration={vm_duration:.2f}ms user_id={user_id}")


# Глобальный экземпляр ScreenManager
_screen_manager: Optional[ScreenManager] = None


def get_screen_manager() -> ScreenManager:
    """Получает глобальный экземпляр ScreenManager"""
    global _screen_manager
    if _screen_manager is None:
        _screen_manager = ScreenManager()
    return _screen_manager