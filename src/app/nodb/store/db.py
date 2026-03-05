"""
SQLite store для no_db режима.
Заменяет JSONL как основное хранилище заявок и событий.
Легкое, встраиваемое решение без внешних зависимостей.
"""
import aiosqlite
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from app.config import settings
from app.logger import logger

_db_path: Optional[Path] = None
_initialized: bool = False

SCHEMA = """
-- Заявки на оплату (основная таблица)
CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    req_id TEXT UNIQUE NOT NULL,
    tg_id INTEGER NOT NULL,
    username TEXT,
    name TEXT,
    tariff TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT DEFAULT 'RUB',
    status TEXT DEFAULT 'NEW',
    signature TEXT NOT NULL,
    source TEXT DEFAULT 'card',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    admin_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_payment_requests_tg_id ON payment_requests(tg_id);
CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status);
CREATE INDEX IF NOT EXISTS idx_payment_requests_created_at ON payment_requests(created_at);

-- Аудит событий (append-only log)
CREATE TABLE IF NOT EXISTS payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    req_id TEXT,
    tg_id INTEGER,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_events_req_id ON payment_events(req_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_tg_id ON payment_events(tg_id);

-- Промо-активации (для /solokhin и др.)
CREATE TABLE IF NOT EXISTS promo_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    promo_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tg_id, promo_code)
);

-- Идемпотентность webhook'ов YooKassa
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT,
    tg_id INTEGER,
    amount REAL,
    processed INTEGER DEFAULT 0,
    provisioned INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(external_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_external_id ON webhook_events(external_id);
"""


def _get_db_path() -> Path:
    """Возвращает путь к SQLite файлу."""
    global _db_path
    if _db_path is not None:
        return _db_path

    log_dir = getattr(settings, "LOG_DIR", None)
    if log_dir:
        base = Path(log_dir)
    else:
        base = Path(__file__).resolve().parents[4] / "data"

    base.mkdir(parents=True, exist_ok=True)
    _db_path = base / "nodb_store.db"
    return _db_path


async def init_db() -> None:
    """Инициализирует SQLite базу данных."""
    global _initialized
    if _initialized:
        return

    db_path = _get_db_path()
    logger.info(f"Инициализация SQLite store: {db_path}")

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()

    _initialized = True
    logger.info("SQLite store инициализирован")


@asynccontextmanager
async def get_db():
    """Контекстный менеджер для получения соединения с БД."""
    if not _initialized:
        await init_db()

    db_path = _get_db_path()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def close_db() -> None:
    """Закрывает все соединения (для тестов)."""
    global _initialized
    _initialized = False
