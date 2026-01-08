"""
Скрипт для запуска FastAPI сервера
"""
import uvicorn
from app.config import settings
from app.logger import logger


def run_api_server():
    """Запускает FastAPI сервер для обработки webhook'ов"""
    host = "0.0.0.0"
    port = settings.WEBHOOK_API_PORT or 8001
    
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

