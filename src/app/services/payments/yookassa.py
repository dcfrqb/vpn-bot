"""Re-export shim (release 3.0, stream A). Used by the 2.x reconciler,
refunds.py and obhod_service; removed in 3.0.1 with them.

2.x imported everything money-related from here. In 3.0 the money path is
split into services:

  app.infra.yookassa            async YooKassa client + PaymentGateway
  app.infra.telegram_stars      StarsGateway
  app.services.checkout         CheckoutService (quote, start, check)
  app.services.fulfillment      paid -> price gate -> grant -> notify
  app.services.payments.webhook POST /webhook/yookassa body
  app.services.payments.store   payments / refund_requests / payment_methods SQL

The remaining 2.x code (Phase A/B/C provisioning, used by the Foundation
ProvisioningService shim and the reconciler until stream B replaces it) lives
in app.services.payments.legacy_yookassa. This module IS that module object
(same trick as core/plans.py), so ``patch("app.services.payments.yookassa.X")``
and imports through the old path keep hitting the same code.
"""
import sys

from app.services.payments import legacy_yookassa as _legacy

sys.modules[__name__] = _legacy
