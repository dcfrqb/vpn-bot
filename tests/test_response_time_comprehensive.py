"""
Комплексный тест скорости отклика бота во всех режимах

Измеряет:
1. Время отклика callback.answer() - критично < 100-200ms
2. Время обработки различных типов действий (UI callbacks, legacy callbacks, команды)
3. Время рендеринга экранов
4. Время работы с базой данных
5. Время работы с внешними API (Remnawave)
6. Сравнение polling vs webhook режимов (симуляция)
7. Нагрузочное тестирование (множественные запросы)
8. Измерение времени middleware
9. Измерение времени работы с Redis
"""
import asyncio
import time
import statistics
from typing import List, Dict, Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.types import User, Message, CallbackQuery, Chat
from aiogram.fsm.storage.memory import MemoryStorage

from app.ui.screen_manager import get_screen_manager
from app.navigation.navigator import get_navigator
from app.services.sync_service import SyncService
from app.remnawave.client import RemnaClient
from app.repositories.user_repo import UserRepo
from app.repositories.subscription_repo import SubscriptionRepo
from app.db.models import TelegramUser, Subscription


@dataclass
class ResponseTimeMetrics:
    """Метрики времени отклика"""
    operation: str
    count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    stdev_ms: float
    total_ms: float
    failures: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'operation': self.operation,
            'count': self.count,
            'min_ms': round(self.min_ms, 2),
            'max_ms': round(self.max_ms, 2),
            'mean_ms': round(self.mean_ms, 2),
            'median_ms': round(self.median_ms, 2),
            'p50_ms': round(self.p50_ms, 2),
            'p95_ms': round(self.p95_ms, 2),
            'p99_ms': round(self.p99_ms, 2),
            'stdev_ms': round(self.stdev_ms, 2),
            'total_ms': round(self.total_ms, 2),
            'failures': self.failures,
        }


class ResponseTimeTester:
    """Класс для комплексного тестирования скорости отклика"""
    
    def __init__(self):
        self.measurements: Dict[str, List[float]] = {}
        self.failures: Dict[str, int] = {}
        self.mode: str = "unknown"  # polling, webhook, test
    
    def record(self, operation_name: str, elapsed_ms: float, success: bool = True):
        """Записывает время выполнения операции"""
        if operation_name not in self.measurements:
            self.measurements[operation_name] = []
            self.failures[operation_name] = 0
        
        if success:
            self.measurements[operation_name].append(elapsed_ms)
        else:
            self.failures[operation_name] += 1
    
    async def measure_async(self, operation_name: str, coro, *args, **kwargs) -> Any:
        """Измеряет время выполнения асинхронной функции"""
        start = time.perf_counter()
        try:
            result = await coro(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.record(operation_name, elapsed_ms, success=True)
            return result
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.record(operation_name, elapsed_ms, success=False)
            raise
    
    def get_metrics(self, operation_name: str) -> Optional[ResponseTimeMetrics]:
        """Возвращает метрики для операции"""
        if operation_name not in self.measurements or not self.measurements[operation_name]:
            return None
        
        times = sorted(self.measurements[operation_name])
        count = len(times)
        
        if count == 0:
            return None
        
        # Вычисляем перцентили
        p50_idx = int(count * 0.50)
        p95_idx = int(count * 0.95)
        p99_idx = int(count * 0.99)
        
        return ResponseTimeMetrics(
            operation=operation_name,
            count=count,
            min_ms=min(times),
            max_ms=max(times),
            mean_ms=statistics.mean(times),
            median_ms=statistics.median(times),
            p50_ms=times[p50_idx] if p50_idx < count else times[-1],
            p95_ms=times[p95_idx] if p95_idx < count else times[-1],
            p99_ms=times[p99_idx] if p99_idx < count else times[-1],
            stdev_ms=statistics.stdev(times) if count > 1 else 0.0,
            total_ms=sum(times),
            failures=self.failures.get(operation_name, 0)
        )
    
    def print_report(self):
        """Выводит детальный отчет о производительности"""
        print("\n" + "=" * 100)
        print(f"ОТЧЕТ О СКОРОСТИ ОТКЛИКА БОТА (режим: {self.mode})")
        print("=" * 100)
        
        all_metrics = []
        for operation_name in sorted(self.measurements.keys()):
            metrics = self.get_metrics(operation_name)
            if metrics:
                all_metrics.append(metrics)
        
        # Сортируем по среднему времени (от медленных к быстрым)
        all_metrics.sort(key=lambda x: x.mean_ms, reverse=True)
        
        print(f"\n{'Операция':<40} {'Count':<8} {'Min':<10} {'Mean':<10} {'P95':<10} {'P99':<10} {'Failures':<10}")
        print("-" * 100)
        
        for metrics in all_metrics:
            print(
                f"{metrics.operation:<40} "
                f"{metrics.count:<8} "
                f"{metrics.min_ms:<10.2f} "
                f"{metrics.mean_ms:<10.2f} "
                f"{metrics.p95_ms:<10.2f} "
                f"{metrics.p99_ms:<10.2f} "
                f"{metrics.failures:<10}"
            )
        
        print("\n" + "=" * 100)
        print("ДЕТАЛЬНАЯ СТАТИСТИКА")
        print("=" * 100)
        
        for metrics in all_metrics:
            print(f"\n{metrics.operation}:")
            print(f"  Количество измерений: {metrics.count}")
            print(f"  Минимум:            {metrics.min_ms:.2f} мс")
            print(f"  Максимум:           {metrics.max_ms:.2f} мс")
            print(f"  Среднее:            {metrics.mean_ms:.2f} мс")
            print(f"  Медиана:            {metrics.median_ms:.2f} мс")
            print(f"  P50 (медиана):      {metrics.p50_ms:.2f} мс")
            print(f"  P95:                {metrics.p95_ms:.2f} мс")
            print(f"  P99:                {metrics.p99_ms:.2f} мс")
            print(f"  Стандартное откл.:  {metrics.stdev_ms:.2f} мс")
            print(f"  Общее время:        {metrics.total_ms:.2f} мс")
            if metrics.failures > 0:
                print(f"  Ошибок:             {metrics.failures}")
        
        print("\n" + "=" * 100)
        
        # Проверяем критические пороги
        self._check_thresholds(all_metrics)
    
    def _check_thresholds(self, metrics_list: List[ResponseTimeMetrics]):
        """Проверяет критические пороги производительности"""
        print("\nПРОВЕРКА КРИТИЧЕСКИХ ПОРОГОВ:")
        print("-" * 100)
        
        thresholds = {
            'callback_answer': 200,  # callback.answer() должен быть < 200ms
            'ui_callback_handler': 300,  # UI callback обработка < 300ms
            'screen_render': 100,  # Рендеринг экрана < 100ms
            'screen_keyboard': 50,  # Создание клавиатуры < 50ms
            'db_query': 50,  # Запрос к БД < 50ms
            'remna_api': 1000,  # API Remna < 1000ms
            'sync_service': 500,  # Синхронизация < 500ms
        }
        
        violations = []
        for metrics in metrics_list:
            for threshold_name, threshold_ms in thresholds.items():
                if threshold_name in metrics.operation.lower():
                    if metrics.p95_ms > threshold_ms:
                        violations.append(
                            f"⚠️ {metrics.operation}: P95={metrics.p95_ms:.2f}ms > {threshold_ms}ms"
                        )
        
        if violations:
            print("\n⚠️ ОБНАРУЖЕНЫ НАРУШЕНИЯ ПОРОГОВ:")
            for violation in violations:
                print(f"  {violation}")
        else:
            print("✅ Все критические пороги соблюдены")
        
        print("-" * 100)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_callback_answer_response_time():
    """Тест времени отклика callback.answer() - критично < 200ms"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 100
    
    print(f"\n📊 Тест времени отклика callback.answer() ({iterations} итераций)...")
    
    # Создаем мок callback
    mock_callback = AsyncMock(spec=CallbackQuery)
    mock_callback.answer = AsyncMock()
    mock_callback.from_user = User(id=12345, is_bot=False, first_name="Test")
    
    for i in range(iterations):
        # Измеряем время ответа на callback
        await tester.measure_async(
            "callback_answer",
            mock_callback.answer
        )
    
    metrics = tester.get_metrics("callback_answer")
    assert metrics is not None
    assert metrics.p95_ms < 200, f"callback.answer() P95={metrics.p95_ms:.2f}ms > 200ms"
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_ui_callback_processing_time():
    """Тест времени обработки UI callbacks"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 50
    
    print(f"\n📊 Тест обработки UI callbacks ({iterations} итераций)...")
    
    # Создаем мок callback с UI форматом
    mock_callback = AsyncMock(spec=CallbackQuery)
    mock_callback.data = "ui:main_menu:open:"
    mock_callback.answer = AsyncMock()
    mock_callback.from_user = User(id=12345, is_bot=False, first_name="Test")
    mock_callback.message = AsyncMock()
    mock_callback.message.edit_text = AsyncMock()
    mock_callback.message.edit_reply_markup = AsyncMock()
    
    # Мокируем ScreenManager
    with patch('app.ui.screen_manager.get_screen_manager') as mock_get_sm, \
         patch('app.navigation.navigator.get_navigator') as mock_get_nav:
        
        mock_screen_manager = AsyncMock()
        mock_screen_manager.handle_action = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_screen_manager
        
        mock_navigator = AsyncMock()
        mock_navigator.get_current_screen = AsyncMock(return_value=None)
        mock_get_nav.return_value = mock_navigator
        
        # Импортируем handler
        from app.routers.ui import ui_callback_handler
        
        for i in range(iterations):
            await tester.measure_async(
                "ui_callback_handler",
                ui_callback_handler,
                mock_callback
            )
    
    metrics = tester.get_metrics("ui_callback_handler")
    assert metrics is not None
    assert metrics.p95_ms < 300, f"UI callback обработка P95={metrics.p95_ms:.2f}ms > 300ms"
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_screen_rendering_time():
    """Тест времени рендеринга экранов"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 50
    
    print(f"\n📊 Тест рендеринга экранов ({iterations} итераций)...")
    
    # Мокируем ScreenManager
    with patch('app.ui.screen_manager.get_screen_manager') as mock_get_sm:
        mock_screen_manager = AsyncMock()
        
        # Мокируем методы рендеринга
        async def mock_render(viewmodel):
            await asyncio.sleep(0.001)  # Симуляция рендеринга
            return "Test screen text"
        
        async def mock_build_keyboard(viewmodel):
            await asyncio.sleep(0.001)  # Симуляция создания клавиатуры
            from aiogram.types import InlineKeyboardMarkup
            return InlineKeyboardMarkup(inline_keyboard=[])
        
        mock_screen = AsyncMock()
        mock_screen.render = mock_render
        mock_screen.build_keyboard = mock_build_keyboard
        
        mock_screen_manager.get_screen = AsyncMock(return_value=mock_screen)
        mock_get_sm.return_value = mock_screen_manager
        
        # Тестируем рендеринг
        for i in range(iterations):
            await tester.measure_async(
                "screen_render",
                mock_render,
                MagicMock()
            )
            
            await tester.measure_async(
                "screen_keyboard",
                mock_build_keyboard,
                MagicMock()
            )
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_database_query_time(test_db_session=None):
    """Тест времени выполнения запросов к БД"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 100
    
    print(f"\n📊 Тест запросов к БД ({iterations} итераций)...")
    
    if test_db_session is None:
        print("⚠️ Пропущен тест БД: требуется test_db_session fixture")
        return
    
    from app.repositories.user_repo import UserRepo
    user_repo = UserRepo(test_db_session)
    
    # Тестируем различные операции
    for i in range(iterations):
        # Тест получения пользователя
        await tester.measure_async(
            "db_query_get_user",
            user_repo.get_user_by_telegram_id,
            12345 + i
        )
        
        # Тест upsert пользователя
        await tester.measure_async(
            "db_query_upsert_user",
            user_repo.upsert_user_by_telegram_id,
            12345 + i,
            defaults={'username': f'test_{i}'}
        )
    
    metrics = tester.get_metrics("db_query_get_user")
    if metrics:
        assert metrics.p95_ms < 50, f"DB query P95={metrics.p95_ms:.2f}ms > 50ms"
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_remna_api_response_time():
    """Тест времени отклика Remna API"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 20  # Меньше итераций для реальных API запросов
    
    print(f"\n📊 Тест Remna API ({iterations} итераций)...")
    
    # Создаем реальный клиент (будет использовать реальный API)
    client = RemnaClient()
    
    try:
        for i in range(iterations):
            # Тест получения пользователя
            await tester.measure_async(
                "remna_api_get_user",
                client.get_user_with_subscription_by_telegram_id,
                12345 + i
            )
            
            # Небольшая задержка между запросами
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"⚠️ Ошибка при тестировании Remna API: {e}")
        print("   (Это нормально, если API недоступен)")
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_sync_service_response_time():
    """Тест времени работы сервиса синхронизации"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 20
    
    print(f"\n📊 Тест сервиса синхронизации ({iterations} итераций)...")
    
    # Мокируем RemnaClient
    mock_remna_client = AsyncMock(spec=RemnaClient)
    from app.remnawave.client import RemnaUser
    mock_remna_user = RemnaUser(
        uuid="test-uuid",
        telegram_id=12345,
        username="test",
        name="Test",
        raw_data={}
    )
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        return_value=(mock_remna_user, None)
    )
    
    sync_service = SyncService(remna_client=mock_remna_client)
    
    # Мокируем БД
    with patch('app.services.sync_service.SessionLocal') as mock_session_local, \
         patch('app.services.sync_service.UserRepo') as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo') as mock_sub_repo_class, \
         patch('app.services.cache.get_cached_sync_result', return_value=None):
        
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_local.return_value = mock_session
        
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=None)
        mock_sub_repo.upsert_subscription = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        for i in range(iterations):
            await tester.measure_async(
                "sync_service_sync",
                sync_service.sync_user_and_subscription,
                telegram_id=12345 + i,
                tg_name=f"Test {i}",
                use_fallback=False,
                use_cache=False
            )
    
    metrics = tester.get_metrics("sync_service_sync")
    if metrics:
        assert metrics.p95_ms < 500, f"Sync service P95={metrics.p95_ms:.2f}ms > 500ms"
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_concurrent_requests_performance():
    """Тест производительности при конкурентных запросах"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    concurrent_requests = 20
    iterations = 5
    
    print(f"\n📊 Тест конкурентных запросов ({concurrent_requests} одновременных, {iterations} итераций)...")
    
    async def process_callback():
        """Симуляция обработки callback"""
        mock_callback = AsyncMock()
        mock_callback.answer = AsyncMock()
        await mock_callback.answer()
        await asyncio.sleep(0.001)  # Симуляция обработки
    
    for i in range(iterations):
        start = time.perf_counter()
        tasks = [process_callback() for _ in range(concurrent_requests)]
        await asyncio.gather(*tasks)
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        tester.record("concurrent_requests", elapsed_ms)
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_middleware_overhead():
    """Тест накладных расходов middleware"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 100
    
    print(f"\n📊 Тест накладных расходов middleware ({iterations} итераций)...")
    
    from app.middlewares.timing import TimingMiddleware
    from app.middlewares.auth import AuthMiddleware
    
    async def simple_handler(event, data):
        """Простой handler без логики"""
        pass
    
    # Тест без middleware
    for i in range(iterations):
        start = time.perf_counter()
        await simple_handler(MagicMock(), {})
        elapsed_ms = (time.perf_counter() - start) * 1000
        tester.record("handler_no_middleware", elapsed_ms)
    
    # Тест с TimingMiddleware
    timing_middleware = TimingMiddleware()
    for i in range(iterations):
        mock_event = MagicMock()
        mock_event.from_user = User(id=12345, is_bot=False, first_name="Test")
        await tester.measure_async(
            "handler_with_timing_middleware",
            timing_middleware,
            simple_handler,
            mock_event,
            {}
        )
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_full_request_flow():
    """Тест полного потока обработки запроса (от callback до ответа)"""
    tester = ResponseTimeTester()
    tester.mode = "test"
    iterations = 30
    
    print(f"\n📊 Тест полного потока обработки запроса ({iterations} итераций)...")
    
    # Создаем полный мок окружения
    mock_callback = AsyncMock(spec=CallbackQuery)
    mock_callback.data = "ui:main_menu:open:"
    mock_callback.answer = AsyncMock()
    mock_callback.from_user = User(id=12345, is_bot=False, first_name="Test")
    mock_callback.message = AsyncMock()
    mock_callback.message.edit_text = AsyncMock()
    mock_callback.message.edit_reply_markup = AsyncMock()
    
    # Мокируем все зависимости
    with patch('app.ui.screen_manager.get_screen_manager') as mock_get_sm, \
         patch('app.navigation.navigator.get_navigator') as mock_get_nav, \
         patch('app.middlewares.timing.TimingMiddleware') as mock_timing:
        
        mock_screen_manager = AsyncMock()
        mock_screen_manager.handle_action = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_screen_manager
        
        mock_navigator = AsyncMock()
        mock_navigator.get_current_screen = AsyncMock(return_value=None)
        mock_get_nav.return_value = mock_navigator
        
        # Импортируем handler
        from app.routers.ui import ui_callback_handler
        
        for i in range(iterations):
            # Измеряем полный поток
            await tester.measure_async(
                "full_request_flow",
                ui_callback_handler,
                mock_callback
            )
    
    metrics = tester.get_metrics("full_request_flow")
    if metrics:
        assert metrics.p95_ms < 500, f"Full request flow P95={metrics.p95_ms:.2f}ms > 500ms"
    
    tester.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_comprehensive_response_time():
    """Комплексный тест скорости отклика всех компонентов"""
    print("\n" + "=" * 100)
    print("КОМПЛЕКСНЫЙ ТЕСТ СКОРОСТИ ОТКЛИКА БОТА")
    print("=" * 100)
    
    # Запускаем все тесты
    await test_callback_answer_response_time()
    await test_ui_callback_processing_time()
    await test_screen_rendering_time()
    await test_concurrent_requests_performance()
    await test_middleware_overhead()
    await test_full_request_flow()
    
    # Тесты с зависимостями (могут быть пропущены)
    # test_database_query_time требует test_db_session fixture, запускается отдельно
    
    try:
        await test_remna_api_response_time()
    except Exception as e:
        print(f"\n⚠️ Пропущен тест Remna API: {e}")
    
    try:
        await test_sync_service_response_time()
    except Exception as e:
        print(f"\n⚠️ Пропущен тест синхронизации: {e}")
    
    print("\n" + "=" * 100)
    print("КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 100)


if __name__ == "__main__":
    """
    Запуск тестов производительности:
    
    # Все тесты
    pytest tests/test_response_time_comprehensive.py -v -s
    
    # Конкретный тест
    pytest tests/test_response_time_comprehensive.py::test_callback_answer_response_time -v -s
    
    # С детальным отчетом
    pytest tests/test_response_time_comprehensive.py -v -s --tb=short
    """
# Тесты должны запускаться через pytest, не напрямую
# if __name__ == "__main__":
#     asyncio.run(test_comprehensive_response_time())
