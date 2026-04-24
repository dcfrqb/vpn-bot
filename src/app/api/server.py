"""
Скрипт для запуска FastAPI сервера
"""
from app.utils.preflight import run_preflight_webhook_api

# Preflight: проверка обязательных env до старта
run_preflight_webhook_api()

import os

import uvicorn
from app.config import settings
from app.logger import logger


def run_api_server():
    """Запускает FastAPI сервер для обработки webhook'ов.

    Адрес прослушивания читается из env `WEBHOOK_API_BIND_HOST`.
    По умолчанию — `0.0.0.0` (ожидается, что docker-compose публикует порт на `127.0.0.1`).
    Чтобы закрыть от внешних соединений на уровне процесса — установите в `127.0.0.1`.
    """
    host = os.getenv("WEBHOOK_API_BIND_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_API_BIND_PORT") or settings.WEBHOOK_API_PORT or 8001)

    logger.info(f"Запуск FastAPI сервера на {host}:{port}")

    uvicorn.run(
        "app.api.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=False
    )


if __name__ == "__main__":
    run_api_server()

