"""POST /webhook/remnawave - Remnawave panel webhooks. Owner stream: C.

Foundation stub: always 501, nothing is processed. Stream C implements HMAC
check (PANEL_WEBHOOK_SECRET), replay window, dedupe (infra.redis.flags
acquire_marker) and async processing. nginx does not route this path yet
(owner prerequisite 3).
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/webhook/remnawave")
async def remnawave_webhook() -> JSONResponse:
    return JSONResponse(status_code=501, content={"status": "not_implemented"})
