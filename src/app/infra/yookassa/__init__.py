"""YooKassa adapter. Owner stream: A (Money).

- ``client.YooKassaClient``: async httpx client (Idempotence-Key, 202/5xx retries).
- ``gateway.YooKassaGateway``: app.services.ports.PaymentGateway over it.

The synchronous ``yookassa`` SDK is no longer used on the money path.
"""
from app.infra.yookassa.client import YooKassaClient, YooKassaError
from app.infra.yookassa.gateway import YooKassaGateway, default_gateway, normalize_payment

__all__ = ["YooKassaClient", "YooKassaError", "YooKassaGateway", "default_gateway", "normalize_payment"]
