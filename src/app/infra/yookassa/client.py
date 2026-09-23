"""Async YooKassa API v3 client (httpx). Owner: stream A (Money).

Replaces the synchronous ``yookassa`` SDK on the money path: the SDK blocked
the event loop on every call (APS defect D-1) and hid HTTP details.

Rules:
- Every POST carries an ``Idempotence-Key``. A retry after a network error or
  5xx reuses the same key, so YooKassa returns the same object instead of a
  second payment/refund. That is what makes retries here safe.
- HTTP 202 means "still processing": wait ``retry_after`` ms and repeat the
  same request with the same key (documented YooKassa behaviour).
- 404 on GET -> None (unknown id). Other 4xx -> YooKassaError(retryable=False).
  Network errors, 5xx, 429 after the retries -> YooKassaError(retryable=True).
- Nothing secret is logged: no auth header, no confirmation URL, no card data.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.logger import logger

API_BASE = "https://api.yookassa.ru/v3"
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_ATTEMPTS = 3
_MAX_RETRY_AFTER_S = 5.0


class YooKassaError(Exception):
    """API call failed. ``retryable`` says whether the same call may succeed later."""

    def __init__(self, message: str, *, status: Optional[int] = None, code: Optional[str] = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


class YooKassaClient:
    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        base_url: str = API_BASE,
        timeout: float = DEFAULT_TIMEOUT_S,
        attempts: int = DEFAULT_ATTEMPTS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep=asyncio.sleep,
    ):
        if not shop_id or not secret_key:
            raise ValueError("YooKassa shop id and secret key are required")
        self._auth = httpx.BasicAuth(str(shop_id).strip(), str(secret_key).strip())
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._attempts = max(1, int(attempts))
        self._transport = transport
        self._sleep = sleep
        self._http: Optional[httpx.AsyncClient] = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base, auth=self._auth, timeout=self._timeout, transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def _request(self, method: str, path: str, *, json: Any = None,
                       idempotence_key: Optional[str] = None) -> Optional[dict]:
        headers = {"Idempotence-Key": idempotence_key} if idempotence_key else None
        last: Optional[YooKassaError] = None
        for attempt in range(1, self._attempts + 1):
            try:
                resp = await self._client().request(method, path, json=json, headers=headers)
            except httpx.HTTPError as e:
                last = YooKassaError(f"{method} {path}: {type(e).__name__}", retryable=True)
                logger.warning(f"yookassa {method} {path}: network error {type(e).__name__} (attempt {attempt})")
                await self._backoff(attempt)
                continue
            if resp.status_code == 202:
                delay = _retry_after_s(resp)
                last = YooKassaError(f"{method} {path}: still processing", status=202, retryable=True)
                await self._sleep(delay)
                continue
            if resp.status_code == 404 and method == "GET":
                return None
            if resp.status_code >= 500 or resp.status_code == 429:
                last = YooKassaError(f"{method} {path}: HTTP {resp.status_code}", status=resp.status_code,
                                     retryable=True)
                logger.warning(f"yookassa {method} {path}: HTTP {resp.status_code} (attempt {attempt})")
                await self._backoff(attempt)
                continue
            if resp.status_code >= 400:
                code, desc = _error_details(resp)
                raise YooKassaError(f"{method} {path}: HTTP {resp.status_code} {code or ''} {desc or ''}".strip(),
                                    status=resp.status_code, code=code, retryable=False)
            try:
                data = resp.json()
            except ValueError as e:
                raise YooKassaError(f"{method} {path}: invalid JSON", status=resp.status_code,
                                    retryable=True) from e
            return data if isinstance(data, dict) else {}
        assert last is not None
        raise last

    async def _backoff(self, attempt: int) -> None:
        if attempt < self._attempts:
            await self._sleep(min(0.5 * (2 ** (attempt - 1)), 4.0))

    # --- payments -------------------------------------------------------------------------

    async def create_payment(self, body: dict, idempotence_key: str) -> dict:
        return await self._request("POST", "/payments", json=body, idempotence_key=idempotence_key) or {}

    async def get_payment(self, payment_id: str) -> Optional[dict]:
        return await self._request("GET", f"/payments/{payment_id}")

    # --- refunds --------------------------------------------------------------------------

    async def create_refund(self, body: dict, idempotence_key: str) -> dict:
        return await self._request("POST", "/refunds", json=body, idempotence_key=idempotence_key) or {}

    async def get_refund(self, refund_id: str) -> Optional[dict]:
        return await self._request("GET", f"/refunds/{refund_id}")


def _retry_after_s(resp: httpx.Response) -> float:
    try:
        ms = float((resp.json() or {}).get("retry_after") or 1000)
    except (ValueError, AttributeError):
        ms = 1000
    return min(max(ms / 1000.0, 0.1), _MAX_RETRY_AFTER_S)


def _error_details(resp: httpx.Response) -> tuple[Optional[str], Optional[str]]:
    try:
        data = resp.json() or {}
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    return data.get("code"), str(data.get("description") or "")[:200] or None
