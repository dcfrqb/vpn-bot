from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import NullPool
from app.config import settings
from app.logger import logger

if settings.DATABASE_URL:
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=20,  # Увеличено для лучшей производительности
        max_overflow=30,  # Увеличено для пиковых нагрузок
        pool_recycle=3600,
        pool_timeout=30,  # Таймаут для получения соединения из пула
        poolclass=None,  # Используем стандартный пул для лучшей производительности
        connect_args={
            "server_settings": {
                "application_name": "crs_vpn_bot"
            },
            "command_timeout": 60,
            "prepared_statement_cache_size": 0  # Отключаем кэш prepared statements для обхода проблемы с кэшированием схемы asyncpg
        }
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    logger.info("База данных инициализирована")
else:
    engine = None
    SessionLocal = None
    logger.warning("DATABASE_URL не настроен, БД недоступна")