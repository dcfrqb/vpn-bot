"""Внутренний API для сайта (vpn.crs-projects.com): /internal/site/*.

Доступен только из docker-сети (сайт ходит на http://bot-internal-api:8001).
Nginx наружу /internal/ не пускает (location /internal/ { return 404; }).

Защита:
  - X-Internal-Token сверяется через hmac.compare_digest с BOT_INTERNAL_TOKEN;
    пустой BOT_INTERNAL_TOKEN = 503 на всех маршрутах, неверный токен = 403;
  - 120 запросов в минуту на все /internal/site/ (Redis, как у вебхука ЮKassa).

Маршруты тонкие: вся логика в app.services.site_profile. Токены и данные
профиля в лог не пишутся.
"""
import hmac
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.logger import logger

RATE_LIMIT_PER_MIN = 120
RATE_LIMIT_KEY_PREFIX = "rl:internal_site:"


class InternalApiError(Exception):
    def __init__(self, status_code: int, error: str):
        self.status_code = status_code
        self.error = error


async def internal_api_error_handler(request: Request, exc: InternalApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.error})


def _check_token(request: Request) -> None:
    expected = (settings.BOT_INTERNAL_TOKEN or "").strip()
    if not expected:
        raise InternalApiError(503, "disabled")
    got = request.headers.get("X-Internal-Token") or ""
    if not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
        logger.warning(f"internal site api: неверный или пустой токен, path={request.url.path}")
        raise InternalApiError(403, "forbidden")


async def _rate_limit_ok() -> bool:
    """Общий лимит на все /internal/site/: окно в одну минуту. Redis недоступен =
    пропускаем (как у вебхука): сеть внутренняя, а сайт не должен падать."""
    try:
        from app.services.cache import get_redis_client

        redis_client = get_redis_client()
        if not redis_client:
            return True
        key = f"{RATE_LIMIT_KEY_PREFIX}{int(time.time() // 60)}"
        count = await redis_client.incr(key)
        if int(count) == 1:
            await redis_client.expire(key, 120)
        return int(count) <= RATE_LIMIT_PER_MIN
    except Exception as e:
        logger.debug(f"internal site rate-limit soft-fail: {type(e).__name__}")
        return True


async def guard(request: Request) -> None:
    _check_token(request)
    if not await _rate_limit_ok():
        logger.warning("internal site api: rate limit exceeded")
        raise InternalApiError(429, "rate_limited")


router = APIRouter(prefix="/internal/site", dependencies=[Depends(guard)])


@router.get("/users/{telegram_id}/profile")
async def site_user_profile(telegram_id: int):
    from app.services.site_profile import ProfileDbUnavailable, get_profile

    if telegram_id <= 0:
        raise InternalApiError(404, "not_found")
    try:
        profile = await get_profile(telegram_id)
    except ProfileDbUnavailable:
        logger.error(f"internal site api: БД недоступна, profile tg_id={telegram_id}")
        raise InternalApiError(503, "db_unavailable")
    if profile is None:
        raise InternalApiError(404, "not_found")
    return profile


@router.get("/health")
async def site_health():
    from app.services.site_profile import db_is_alive

    db_ok = await db_is_alive()
    return {"ok": db_ok, "db": db_ok}
