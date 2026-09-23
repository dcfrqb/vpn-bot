"""GET / и GET /health webhook-API (перенесено из app/api/main.py без изменений)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.logger import logger

router = APIRouter()


def _bot_instance():
    from app.api.routes import yookassa

    return yookassa.bot_instance


@router.get("/")
async def root():
    """Корневой эндпоинт для проверки работы API"""
    return {
        "status": "ok",
        "service": "CRS VPN Webhook API",
        "version": "2.0.0",
        "mode": "legacy"
    }


@router.get("/health")
async def health_check():
    """Health check: бот + БД + Redis.

    Liveness-часть — факт инициализации бота.
    Readiness-часть — SELECT 1 на Postgres и PING на Redis.
    Если DB или Redis не отвечают — возвращает 503, чтобы orchestrator
    мог вывести контейнер из балансировки.
    """
    status_parts: dict[str, str] = {
        "bot": "ok" if _bot_instance() is not None else "down",
    }
    overall_ok = _bot_instance() is not None

    try:
        from sqlalchemy import text as _sa_text

        from app.db.session import SessionLocal
        if SessionLocal:
            async with SessionLocal() as session:
                await session.execute(_sa_text("SELECT 1"))
                # Доп. сигнал: сколько подписок застряли в pending/failed.
                # Не делаем degraded — health должен оставаться 200, чтобы балансер не
                # вырубил веб-хук API. Просто публикуем число для мониторинга.
                try:
                    pending_row = await session.execute(
                        _sa_text(
                            "SELECT COUNT(*) FROM subscriptions "
                            "WHERE active = true "
                            "AND provisioning_state IN ('pending','failed')"
                        )
                    )
                    status_parts["pending_subscriptions"] = str(int(pending_row.scalar() or 0))
                except Exception:
                    # Колонка может еще не существовать (миграция не накатана).
                    pass
            status_parts["db"] = "ok"
        else:
            status_parts["db"] = "not_configured"
    except Exception as e:
        # Текст ошибки только в лог: /health доступен снаружи.
        logger.error(f"health: db check failed: {e}")
        status_parts["db"] = "down"
        overall_ok = False

    try:
        from app.services.cache import get_redis_client
        redis_client = get_redis_client()
        if redis_client:
            await redis_client.ping()
            status_parts["redis"] = "ok"
        else:
            status_parts["redis"] = "not_configured"
    except Exception as e:
        logger.error(f"health: redis check failed: {e}")
        status_parts["redis"] = "down"
        overall_ok = False

    body = {
        "status": "healthy" if overall_ok else "degraded",
        "components": status_parts,
        "mode": "legacy",
    }
    if not overall_ok:
        return JSONResponse(status_code=503, content=body)
    return body


