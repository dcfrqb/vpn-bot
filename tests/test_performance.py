"""
Комплексный тест производительности бота

Измеряет:
- Скорость API запросов (Remna)
- Скорость создания клавиатур
- Скорость работы с БД
- Скорость синхронизации
- Общее время обработки команд
"""
import asyncio
import time
import statistics
from typing import List, Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram import Bot, types
from aiogram.types import User, Message, CallbackQuery

from app.keyboards import (
    get_main_menu_keyboard,
    get_plans_keyboard,
    get_period_keyboard,
    get_payment_method_keyboard,
    get_subscription_info_keyboard,
    get_admin_panel_keyboard,
    get_inactive_subscription_keyboard,
)
from app.services.sync_service import SyncService
from app.remnawave.client import RemnaClient
from app.repositories.user_repo import UserRepo
from app.repositories.subscription_repo import SubscriptionRepo


class PerformanceTimer:
    """Класс для измерения времени выполнения операций"""
    
    def __init__(self):
        self.measurements: Dict[str, List[float]] = {}
    
    def measure(self, operation_name: str, func, *args, **kwargs):
        """Измеряет время выполнения функции"""
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        
        if operation_name not in self.measurements:
            self.measurements[operation_name] = []
        self.measurements[operation_name].append(elapsed)
        
        return result
    
    async def measure_async(self, operation_name: str, coro, *args, **kwargs):
        """Измеряет время выполнения асинхронной функции"""
        start = time.perf_counter()
        result = await coro(*args, **kwargs)
        elapsed = time.perf_counter() - start
        
        if operation_name not in self.measurements:
            self.measurements[operation_name] = []
        self.measurements[operation_name].append(elapsed)
        
        return result
    
    def get_stats(self, operation_name: str) -> Dict[str, float]:
        """Возвращает статистику для операции"""
        if operation_name not in self.measurements:
            return {}
        
        times = self.measurements[operation_name]
        return {
            'count': len(times),
            'min': min(times),
            'max': max(times),
            'mean': statistics.mean(times),
            'median': statistics.median(times),
            'stdev': statistics.stdev(times) if len(times) > 1 else 0.0,
            'total': sum(times),
        }
    
    def print_report(self):
        """Выводит отчет о производительности"""
        print("\n" + "=" * 80)
        print("ОТЧЕТ О ПРОИЗВОДИТЕЛЬНОСТИ")
        print("=" * 80)
        
        for operation_name in sorted(self.measurements.keys()):
            stats = self.get_stats(operation_name)
            if stats:
                print(f"\n{operation_name}:")
                print(f"  Количество измерений: {stats['count']}")
                print(f"  Минимум:            {stats['min']*1000:.2f} мс")
                print(f"  Максимум:           {stats['max']*1000:.2f} мс")
                print(f"  Среднее:            {stats['mean']*1000:.2f} мс")
                print(f"  Медиана:            {stats['median']*1000:.2f} мс")
                print(f"  Стандартное откл.:  {stats['stdev']*1000:.2f} мс")
                print(f"  Общее время:        {stats['total']*1000:.2f} мс")
        
        print("\n" + "=" * 80)


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.slow
async def test_keyboard_creation_performance():
    """Тест производительности создания клавиатур"""
    timer = PerformanceTimer()
    iterations = 100
    
    print(f"\nТестирование создания клавиатур ({iterations} итераций)...")
    
    # Тест всех типов клавиатур
    for _ in range(iterations):
        timer.measure("keyboard_main_menu", get_main_menu_keyboard, user_id=12345)
        timer.measure("keyboard_plans", get_plans_keyboard)
        timer.measure("keyboard_period_basic", get_period_keyboard, "basic")
        timer.measure("keyboard_period_premium", get_period_keyboard, "premium")
        timer.measure("keyboard_payment_method", get_payment_method_keyboard, "basic", 1, 99)
        timer.measure("keyboard_subscription_info", get_subscription_info_keyboard, True)
        timer.measure("keyboard_admin_panel", get_admin_panel_keyboard)
        timer.measure("keyboard_inactive_subscription", get_inactive_subscription_keyboard)
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.slow
async def test_remna_api_performance():
    """Тест производительности API запросов к Remna"""
    timer = PerformanceTimer()
    iterations = 10  # Меньше итераций для API, т.к. это реальные запросы
    
    # Создаем реальный клиент (будет использовать реальный API)
    client = RemnaClient()
    
    print(f"\nТестирование API запросов к Remna ({iterations} итераций)...")
    
    # Тест различных API методов
    for i in range(iterations):
        try:
            # Тест получения списка пользователей
            await timer.measure_async(
                "api_get_users",
                client.get_users,
                size=10,
                start=1
            )
        except Exception as e:
            print(f"  Ошибка при тесте get_users: {e}")
        
        # Небольшая задержка между запросами
        await asyncio.sleep(0.1)
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.slow
async def test_sync_service_performance():
    """Тест производительности сервиса синхронизации"""
    timer = PerformanceTimer()
    iterations = 5  # Меньше итераций, т.к. это сложная операция
    
    # Создаем моки для тестирования
    mock_remna_client = MagicMock()
    mock_remna_user = MagicMock()
    mock_remna_user.uuid = "test-uuid-123"
    mock_remna_user.telegram_id = 12345
    mock_remna_user.username = "test_user"
    mock_remna_user.name = "Test User"
    mock_remna_user.raw_data = {}
    
    # Мокируем методы RemnaClient
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        return_value=(mock_remna_user, None)
    )
    
    sync_service = SyncService(remna_client=mock_remna_client)
    
    print(f"\nТестирование сервиса синхронизации ({iterations} итераций)...")
    
    # Мокируем SessionLocal и репозитории
    with patch('app.services.sync_service.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_local.return_value = mock_session
        
        # Мокируем репозитории
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=None)
        
        with patch('app.services.sync_service.UserRepo', return_value=mock_user_repo), \
             patch('app.services.sync_service.SubscriptionRepo', return_value=mock_sub_repo):
            
            for i in range(iterations):
                try:
                    await timer.measure_async(
                        "sync_service_sync_user",
                        sync_service.sync_user_and_subscription,
                        telegram_id=12345 + i,
                        tg_name=f"Test User {i}",
                        use_fallback=False
                    )
                except Exception as e:
                    print(f"  Ошибка при синхронизации: {e}")
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_database_operations_performance():
    """Тест производительности операций с БД"""
    timer = PerformanceTimer()
    iterations = 50
    
    print(f"\nТестирование операций с БД ({iterations} итераций)...")
    
    # Мокируем сессию БД
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.scalar_one_or_none = MagicMock(return_value=None)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.flush = AsyncMock()
    
    # Мокируем UserRepo
    user_repo = UserRepo(mock_session)
    
    for i in range(iterations):
        try:
            # Тест upsert пользователя
            await timer.measure_async(
                "db_upsert_user",
                user_repo.upsert_user_by_telegram_id,
                telegram_id=12345 + i,
                defaults={
                    'username': f"test_user_{i}",
                    'first_name': f"Test {i}",
                    'last_activity_at': None
                }
            )
        except Exception as e:
            print(f"  Ошибка при upsert пользователя: {e}")
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_message_sending_performance():
    """Тест производительности отправки сообщений (моки)"""
    timer = PerformanceTimer()
    iterations = 100
    
    print(f"\nТестирование отправки сообщений ({iterations} итераций)...")
    
    # Создаем мок бота
    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message = AsyncMock()
    
    # Тест отправки различных типов сообщений
    for i in range(iterations):
        # Простое текстовое сообщение
        await timer.measure_async(
            "message_send_text",
            mock_bot.send_message,
            chat_id=12345,
            text=f"Test message {i}"
        )
        
        # Сообщение с клавиатурой
        keyboard = get_main_menu_keyboard(user_id=12345)
        await timer.measure_async(
            "message_send_with_keyboard",
            mock_bot.send_message,
            chat_id=12345,
            text=f"Test message with keyboard {i}",
            reply_markup=keyboard
        )
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_full_command_processing_performance():
    """Тест производительности полной обработки команды /start"""
    timer = PerformanceTimer()
    iterations = 5
    
    print(f"\nТестирование полной обработки команды /start ({iterations} итераций)...")
    
    # Создаем моки
    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message = AsyncMock()
    
    mock_user = User(
        id=12345,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser"
    )
    
    mock_message = Message(
        message_id=1,
        date=time.time(),
        chat=types.Chat(id=12345, type="private"),
        from_user=mock_user,
        text="/start"
    )
    mock_message.bot = mock_bot
    
    # Мокируем все зависимости
    mock_remna_client = MagicMock()
    mock_remna_user = MagicMock()
    mock_remna_user.uuid = "test-uuid-123"
    mock_remna_user.telegram_id = 12345
    mock_remna_user.username = "test_user"
    mock_remna_user.name = "Test User"
    mock_remna_user.raw_data = {}
    
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        return_value=(mock_remna_user, None)
    )
    
    for i in range(iterations):
        try:
            # Импортируем handler
            from app.routers.start import cmd_start
            
            # Мокируем все зависимости
            with patch('app.routers.start.SyncService') as mock_sync_service_class, \
                 patch('app.routers.start.get_user_active_subscription') as mock_get_subscription, \
                 patch('app.routers.start.get_main_menu_keyboard') as mock_get_keyboard:
                
                # Настраиваем моки
                mock_sync_result = MagicMock()
                mock_sync_result.subscription_status = "none"
                mock_sync_result.expires_at = None
                mock_sync_result.is_new_user_created = False
                mock_sync_result.user_remna_uuid = "test-uuid-123"
                
                mock_sync_service = AsyncMock()
                mock_sync_service.sync_user_and_subscription = AsyncMock(
                    return_value=mock_sync_result
                )
                mock_sync_service_class.return_value = mock_sync_service
                
                mock_get_subscription.return_value = None
                mock_get_keyboard.return_value = get_main_menu_keyboard(user_id=12345)
                
                # Измеряем время обработки команды
                await timer.measure_async(
                    "command_start_full",
                    cmd_start,
                    mock_message
                )
        except Exception as e:
            print(f"  Ошибка при обработке команды: {e}")
            import traceback
            traceback.print_exc()
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_concurrent_operations_performance():
    """Тест производительности при конкурентных операциях"""
    timer = PerformanceTimer()
    concurrent_requests = 10
    
    print(f"\nТестирование конкурентных операций ({concurrent_requests} одновременных запросов)...")
    
    async def create_keyboard_async():
        """Асинхронная функция создания клавиатуры"""
        return get_main_menu_keyboard(user_id=12345)
    
    async def concurrent_keyboard_creation():
        """Создание клавиатур конкурентно"""
        tasks = [create_keyboard_async() for _ in range(concurrent_requests)]
        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start
        return elapsed, results
    
    # Выполняем несколько раз
    for i in range(5):
        elapsed, _ = await concurrent_keyboard_creation()
        timer.measurements.setdefault("concurrent_keyboard_creation", []).append(elapsed)
    
    timer.print_report()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_comprehensive_performance():
    """Комплексный тест производительности всех компонентов"""
    print("\n" + "=" * 80)
    print("КОМПЛЕКСНЫЙ ТЕСТ ПРОИЗВОДИТЕЛЬНОСТИ")
    print("=" * 80)
    
    # Запускаем все тесты
    await test_keyboard_creation_performance()
    await test_database_operations_performance()
    await test_message_sending_performance()
    await test_concurrent_operations_performance()
    
    # API и синхронизация - только если доступны
    try:
        await test_remna_api_performance()
    except Exception as e:
        print(f"\n⚠️ Пропущен тест API (требуется доступ к Remna): {e}")
    
    try:
        await test_sync_service_performance()
    except Exception as e:
        print(f"\n⚠️ Пропущен тест синхронизации: {e}")
    
    try:
        await test_full_command_processing_performance()
    except Exception as e:
        print(f"\n⚠️ Пропущен тест обработки команд: {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 80)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_sync_service_performance_without_remna():
    """
    Регрессионный тест: SyncService без Remna должен выполняться < 5 мс.
    
    Цель: ловить деградацию скорости при будущих изменениях.
    """
    from app.services.sync_service import SyncService
    from app.remnawave.client import RemnaClient, RemnaUser
    
    # Создаем мок RemnaClient, который не делает реальных запросов
    mock_remna_client = AsyncMock(spec=RemnaClient)
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
    
    # Мокируем все зависимости для быстрого выполнения
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
        
        # Измеряем время выполнения
        start = time.monotonic()
        result = await sync_service.sync_user_and_subscription(
            telegram_id=12345,
            tg_name="Test User",
            use_fallback=False,
            use_cache=False
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        
        # Проверяем порог производительности
        assert elapsed_ms < 5.0, f"SyncService выполнился за {elapsed_ms:.2f}мс, ожидалось < 5мс"
        assert result is not None


@pytest.mark.asyncio
@pytest.mark.slow
async def test_start_handler_performance_cache_hit():
    """
    Регрессионный тест: /start handler при cache hit должен выполняться < 100 мс.
    
    Цель: ловить деградацию скорости при будущих изменениях.
    """
    from app.routers.start import cmd_start
    from aiogram import types
    from datetime import datetime
    
    # Создаем мок сообщения
    user = types.User(
        id=12345,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser"
    )
    message = MagicMock(spec=types.Message)
    message.from_user = user
    message.text = "/start"
    message.answer = AsyncMock()
    message.bot = AsyncMock()
    
    # Мокируем кэш (cache hit)
    cached_data = {
        'status': 'none',
        'remna_uuid': 'remna-uuid-123',
        'expires_at': None,
        'source': 'cache'
    }
    
    with patch('app.routers.start.get_cached_sync_result', return_value=cached_data), \
         patch('app.routers.start.get_user_active_subscription', return_value=None), \
         patch('app.routers.start.get_main_menu_keyboard') as mock_keyboard:
        
        mock_keyboard.return_value = MagicMock()
        
        # Измеряем время выполнения
        start = time.monotonic()
        await cmd_start(message)
        elapsed_ms = (time.monotonic() - start) * 1000
        
        # Проверяем порог производительности
        assert elapsed_ms < 100.0, f"/start handler выполнился за {elapsed_ms:.2f}мс, ожидалось < 100мс"
        message.answer.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_keyboard_creation_performance_threshold():
    """
    Регрессионный тест: создание клавиатур не должно превышать заданный порог.
    
    Цель: ловить деградацию скорости при будущих изменениях.
    """
    iterations = 100
    max_time_per_keyboard_ms = 1.0  # Максимальное время на одну клавиатуру
    
    # Тестируем создание различных клавиатур
    keyboards_to_test = [
        ("main_menu", lambda: get_main_menu_keyboard(user_id=12345)),
        ("plans", lambda: get_plans_keyboard()),
        ("period_basic", lambda: get_period_keyboard("basic")),
        ("period_premium", lambda: get_period_keyboard("premium")),
        ("payment_method", lambda: get_payment_method_keyboard("basic", 1, 99)),
        ("subscription_info", lambda: get_subscription_info_keyboard(True)),
        ("admin_panel", lambda: get_admin_panel_keyboard()),
        ("inactive_subscription", lambda: get_inactive_subscription_keyboard()),
    ]
    
    for keyboard_name, keyboard_func in keyboards_to_test:
        times = []
        for _ in range(iterations):
            start = time.monotonic()
            keyboard_func()
            elapsed_ms = (time.monotonic() - start) * 1000
            times.append(elapsed_ms)
        
        avg_time_ms = sum(times) / len(times)
        max_time_ms = max(times)
        
        # Проверяем, что среднее время не превышает порог
        assert avg_time_ms < max_time_per_keyboard_ms, \
            f"Клавиатура {keyboard_name}: среднее время {avg_time_ms:.2f}мс, ожидалось < {max_time_per_keyboard_ms}мс"
        
        # Проверяем, что максимальное время не слишком большое (не более чем в 2 раза от среднего)
        assert max_time_ms < max_time_per_keyboard_ms * 2, \
            f"Клавиатура {keyboard_name}: максимальное время {max_time_ms:.2f}мс слишком большое"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_sync_service_fallback_performance():
    """
    Регрессионный тест: SyncService fallback должен выполняться быстро.
    
    Цель: ловить деградацию скорости при будущих изменениях.
    """
    from app.services.sync_service import SyncService
    from app.remnawave.client import RemnaClient
    from app.db.models import TelegramUser, Subscription
    from datetime import datetime, timedelta
    
    # Создаем мок RemnaClient с ошибкой сети
    mock_remna_client = AsyncMock(spec=RemnaClient)
    from httpx import RequestError
    network_error = RequestError("Connection timeout", request=MagicMock())
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        side_effect=network_error
    )
    
    sync_service = SyncService(remna_client=mock_remna_client)
    
    # Мокируем БД для fallback
    with patch('app.services.sync_service.SessionLocal') as mock_session_local, \
         patch('app.services.sync_service.UserRepo') as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo') as mock_sub_repo_class:
        
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_local.return_value = mock_session
        
        # Мок пользователя в БД
        mock_telegram_user = MagicMock(spec=TelegramUser)
        mock_telegram_user.remna_user_id = "remna-uuid-from-db"
        
        mock_subscription = MagicMock(spec=Subscription)
        mock_subscription.active = True
        mock_subscription.valid_until = datetime.utcnow() + timedelta(days=10)
        
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_telegram_id = AsyncMock(return_value=mock_telegram_user)
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=mock_subscription)
        mock_sub_repo_class.return_value = mock_sub_repo
        
        # Измеряем время выполнения fallback
        start = time.monotonic()
        result = await sync_service.sync_user_and_subscription(
            telegram_id=12345,
            tg_name="Test User",
            use_fallback=True,
            use_cache=False
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        
        # Fallback должен быть быстрым (< 10мс, т.к. только чтение из БД)
        assert elapsed_ms < 10.0, f"SyncService fallback выполнился за {elapsed_ms:.2f}мс, ожидалось < 10мс"
        assert result.source == "db_fallback"


# Тесты должны запускаться через pytest, не напрямую
# if __name__ == "__main__":
#     asyncio.run(test_comprehensive_performance())
