"""Кастомные ошибки provisioning.

ProvisioningPendingError — Phase B провалилась (Remnawave недоступен / не подтвердил
ожидаемое состояние). Локальная БД содержит intent (`provisioning_state='failed'`),
но реального синка нет. Webhook должен вернуть 5xx, чтобы YooKassa повторил;
reconciler страхует на случай, если YooKassa перестанет ретраить.
"""


class ProvisioningError(Exception):
    """Базовая ошибка provisioning."""


class ProvisioningPendingError(ProvisioningError):
    """Sync с Remnawave не удался; нужен retry (webhook → 503, reconciler → следующий цикл)."""
