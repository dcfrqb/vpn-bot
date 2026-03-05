"""
Сессия БД. Инициализируется только при DATABASE_URL.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings
from app.logger import logger

engine = None
SessionLocal = None

if settings.DATABASE_URL:
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=30,
        pool_recycle=3600,
        pool_timeout=30,
        poolclass=None,
        connect_args={
            "server_settings": {"application_name": "crs_vpn_bot"},
            "command_timeout": 60,
            "prepared_statement_cache_size": 0,
        },
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    logger.info("База данных инициализирована (legacy)")
else:
    logger.debug("DATABASE_URL не задан — БД недоступна")
