"""Кастомные ошибки provisioning.

ProvisioningPendingError — Phase B провалилась (Remnawave недоступен / не подтвердил
ожидаемое состояние). Локальная БД содержит intent (`provisioning_state='failed'`),
но реального синка нет. Webhook должен вернуть 5xx, чтобы YooKassa повторил;
reconciler страхует на случай, если YooKassa перестанет ретраить.
"""


# One class for the whole bot (review architecture R5): the webhook routes catch
# this name, and ProvisioningService raises the same class, so a failed grant is
# always a 503 for YooKassa, never a 500.
from app.services.provisioning_rules import ProvisioningError  # noqa: E402,F401


class ProvisioningPendingError(ProvisioningError):
    """Sync с Remnawave не удался; нужен retry (webhook → 503, reconciler → следующий цикл)."""


class WebhookRetryableError(ProvisioningError):
    """Вебхук не обработан по внешней причине (YooKassa API недоступен и т.п.);
    эндпоинт отвечает 503, чтобы YooKassa прислала его повторно."""
