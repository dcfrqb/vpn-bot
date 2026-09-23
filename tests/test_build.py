"""
Тесты сборки и импорта проекта.

Проверяют:
- проект собирается без ошибок
- все модули импортируются корректно
- env-конфигурация валидна
- нет циклических зависимостей
"""
import pytest
import sys
import importlib
from pathlib import Path


_SUBPROCESS_ENV = {
    # Фиктивные значения только для прохождения preflight при импорте app.main
    # в отдельном интерпретаторе (без сети: engine создается, но не коннектится).
    "BOT_TOKEN": "123456:TEST",
    "REMNA_API_BASE": "https://panel.invalid",
    "REMNA_API_KEY": "test",
    "DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db",
    "YOOKASSA_WEBHOOK_SECRET": "test",
    "REDIS_URL": "redis://127.0.0.1:1/0",
}


def _import_in_subprocess(code: str) -> None:
    """Импорт в чистом интерпретаторе: не загрязняет sys.modules текущей сессии
    (переимпорт app.config создавал бы второй объект settings)."""
    import os
    import subprocess

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update(_SUBPROCESS_ENV)
    env["PYTHONPATH"] = str(root / "src")
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(root), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]


def test_import_bot_modules():
    """Тест: импорт ключевых модулей бота (включая app.main с preflight)"""
    _import_in_subprocess(
        "from app.main import Bot, Dispatcher, setup_dispatcher, run_polling, run_webhook"
    )


def test_import_sync_service():
    """Тест: импорт SyncService"""
    try:
        from app.services.sync_service import SyncService, SyncResult, RemnaUnavailableError
        assert SyncService is not None
        assert SyncResult is not None
        assert RemnaUnavailableError is not None
    except ImportError as e:
        pytest.fail(f"Не удалось импортировать SyncService: {e}")


def test_import_remna_client():
    """Тест: импорт RemnaClient"""
    try:
        from app.remnawave.client import RemnaClient, RemnaUser, RemnaSubscription
        assert RemnaClient is not None
        assert RemnaUser is not None
        assert RemnaSubscription is not None
    except ImportError as e:
        pytest.fail(f"Не удалось импортировать RemnaClient: {e}")


def test_import_cache_layer():
    """Тест: импорт cache layer"""
    try:
        from app.services.cache import (
            get_cached_sync_result,
            set_cached_sync_result,
            invalidate_sync_cache,
            get_redis_client
        )
        assert get_cached_sync_result is not None
        assert set_cached_sync_result is not None
        assert invalidate_sync_cache is not None
    except ImportError as e:
        pytest.fail(f"Не удалось импортировать cache layer: {e}")


def test_import_handlers():
    """Тест: импорт handlers"""
    try:
        from app.routers.start import router as start_router
        from app.routers.admin import router as admin_router
        from app.legacy.routers.payments import router as payments_router
        assert start_router is not None
        assert admin_router is not None
        assert payments_router is not None
    except ImportError as e:
        pytest.fail(f"Не удалось импортировать handlers: {e}")


def test_import_models():
    """Тест: импорт моделей БД"""
    try:
        from app.db.models import Base, TelegramUser, Subscription, Payment
        assert Base is not None
        assert TelegramUser is not None
        assert Subscription is not None
        assert Payment is not None
    except ImportError as e:
        pytest.fail(f"Не удалось импортировать модели БД: {e}")


def test_import_config():
    """Тест: импорт конфигурации"""
    try:
        from app.config import settings, is_admin
        assert settings is not None
        assert is_admin is not None
    except ImportError as e:
        pytest.fail(f"Не удалось импортировать конфигурацию: {e}")


def test_config_validation():
    """Тест: валидация конфигурации (без подключения к реальным сервисам)"""
    from app.config import settings
    
    # Проверяем, что settings объект создан
    assert settings is not None
    
    # Проверяем, что можно получить доступ к настройкам (даже если они None)
    # Это проверяет, что конфигурация не падает при инициализации
    _ = settings.BOT_TOKEN
    _ = settings.DATABASE_URL
    _ = settings.REDIS_URL
    _ = settings.REMNA_API_BASE
    _ = settings.ADMINS


def test_no_circular_imports():
    """Тест: основные модули импортируются в чистом интерпретаторе в разном порядке"""
    modules = [
        "app.config",
        "app.remnawave.client",
        "app.services.cache",
        "app.services.sync_service",
        "app.routers.start",
        "app.api.main",
        "app.main",
    ]
    for order in (modules, list(reversed(modules))):
        _import_in_subprocess("import importlib\n" + "\n".join(
            f"importlib.import_module({m!r})" for m in order
        ))


def test_project_structure():
    """Тест: проверка структуры проекта"""
    project_root = Path(__file__).parent.parent
    
    # Проверяем наличие ключевых директорий
    assert (project_root / "src" / "app").exists(), "Директория src/app должна существовать"
    assert (project_root / "src" / "app" / "services").exists(), "Директория services должна существовать"
    assert (project_root / "src" / "app" / "routers").exists(), "Директория routers должна существовать"
    assert (project_root / "src" / "app" / "db").exists(), "Директория db должна существовать"
    assert (project_root / "tests").exists(), "Директория tests должна существовать"


def test_import_without_services():
    """Тест: импорт модулей без подключения к реальным сервисам"""
    # Этот тест проверяет, что модули можно импортировать
    # даже если Redis, PostgreSQL и Remna API недоступны
    
    try:
        # Импортируем модули, которые могут пытаться подключиться к сервисам
        from app.services.cache import get_redis_client
        from app.db.session import SessionLocal
        from app.remnawave.client import RemnaClient
        
        # Проверяем, что объекты создаются (даже если сервисы недоступны)
        # Это не должно вызывать исключений при импорте
        assert get_redis_client is not None
        assert RemnaClient is not None
        
    except Exception as e:
        # Если есть ошибка при импорте (не при подключении), это проблема
        pytest.fail(f"Ошибка импорта модулей (не подключения): {e}")
