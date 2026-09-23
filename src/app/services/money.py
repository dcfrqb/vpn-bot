"""Assembly of the money services (release 3.0, stream A).

The container (frozen) exposes ports; the money services are built on top of
them once per container and cached on it:

    m = money(container)          # MoneyServices
    await m.checkout.start(...)   # CheckoutService port implementation
    await m.fulfillment.process(payment_id, source="webhook")
    await m.refunds.request(...)  # 24h refund requests
    await m.autopay.run_once()    # worker job body

Tests pass their own store/ui/hooks: ``money(container, store=InMemoryPaymentStore())``.

Until the orchestrator rewires the container (requests/A.md), placeholders
are replaced here: DisabledStarsGateway -> TelegramStarsGateway(bot).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.services.payments.store import PaymentStore
from app.services.payments.ui import MoneyUi


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LegacyHooks:
    """2.x side effects the new flow still needs. One place, replaced in tests."""

    async def apply_obhod_package(self, telegram_id: int, package_code: str, payment_id: int, trace_id: str) -> bool:
        """2.x obhod package (stream B owns obhod; this only adapts the call).
        apply_obhod_package refuses without a live obhod account."""
        from app.db import session as db_session
        from app.services.obhod_service import apply_obhod_package

        async with db_session.SessionLocal() as session:
            return bool(await apply_obhod_package(
                session=session, telegram_user_id=int(telegram_id), package_code=package_code,
                trace_id=trace_id, payment_id=int(payment_id),
            ))

    async def after_paid(self, payment_id: int, bot: Any) -> None:
        """Referral tracker (/sun718 alerts), soft-fail."""
        try:
            from sqlalchemy import select

            from app.db import session as db_session
            from app.db.models import Payment as PaymentModel
            from app.services.referral_tracker import notify_referral_payment_if_applicable

            async with db_session.SessionLocal() as session:
                payment = (await session.execute(
                    select(PaymentModel).where(PaymentModel.id == int(payment_id)))).scalar_one_or_none()
                if payment is not None and bot is not None:
                    await notify_referral_payment_if_applicable(bot, session, payment)
        except Exception as e:  # noqa: BLE001
            from app.logger import logger

            logger.warning(f"referral hook soft-fail for payment {payment_id}: {type(e).__name__}")

    async def invalidate_caches(self, telegram_id: int) -> None:
        try:
            from app.services.cache import (
                invalidate_site_profile_cache,
                invalidate_subscription_cache,
                invalidate_sync_cache,
            )
            from app.services.users import invalidate_last_plan_cache

            await invalidate_subscription_cache(telegram_id)
            await invalidate_sync_cache(telegram_id)
            await invalidate_site_profile_cache(telegram_id)
            await invalidate_last_plan_cache(telegram_id)
        except Exception:  # noqa: BLE001 - caches are best effort
            pass

    async def user_block_reason(self, telegram_id: int) -> Optional[str]:
        from app.services.blocklist import get_user_block_reason

        return await get_user_block_reason(telegram_id)

    async def card_block_reason(self, fingerprint: Optional[str]) -> Optional[str]:
        from app.services.blocklist import get_card_block_reason

        return await get_card_block_reason(fingerprint)

    async def last_plan(self, telegram_id: int) -> Optional[str]:
        from app.services.users import get_user_last_plan

        return await get_user_last_plan(int(telegram_id))

    async def suppress_expiry_notices(self, telegram_id: int, expires_at: datetime) -> None:
        try:
            from app.tasks.expiry_notifier import suppress_expiry_notices

            await suppress_expiry_notices(telegram_id, expires_at)
        except Exception:  # noqa: BLE001
            pass


@dataclass
class MoneyDeps:
    payments: Any
    stars: Any
    provisioning: Any
    notifier: Any
    promo: Any
    store: PaymentStore
    settings: Any
    ui: MoneyUi
    hooks: LegacyHooks
    bot: Any = None
    clock: Callable[[], datetime] = utcnow

    async def bot_username(self) -> str:
        if self.bot is None:
            return ""
        try:
            me = await self.bot.me()
            return me.username or ""
        except Exception:  # noqa: BLE001
            return ""


@dataclass
class MoneyServices:
    deps: MoneyDeps
    checkout: Any
    fulfillment: Any
    refunds: Any
    autopay: Any


def build_money(deps: MoneyDeps) -> MoneyServices:
    from app.services.autopay import AutopayService
    from app.services.checkout import CheckoutServiceImpl
    from app.services.fulfillment import Fulfillment
    from app.services.payments.refund_requests import RefundRequests

    fulfillment = Fulfillment(deps)
    return MoneyServices(
        deps=deps,
        checkout=CheckoutServiceImpl(deps, fulfillment),
        fulfillment=fulfillment,
        refunds=RefundRequests(deps),
        autopay=AutopayService(deps, fulfillment),
    )


def money_deps(container: Any, *, store: Optional[PaymentStore] = None, ui: Optional[MoneyUi] = None,
               hooks: Optional[LegacyHooks] = None, clock: Optional[Callable[[], datetime]] = None) -> MoneyDeps:
    from app.infra.telegram_stars import TelegramStarsGateway
    from app.services.payments.sql_store import SqlPaymentStore
    from app.services.payments.ui import default_ui
    from app.services.shims import DisabledStarsGateway

    stars = container.stars
    if isinstance(stars, DisabledStarsGateway):
        stars = TelegramStarsGateway(container.bot)
    return MoneyDeps(
        payments=container.payments,
        stars=stars,
        provisioning=container.provisioning,
        notifier=container.notifier,
        promo=container.promo,
        store=store or SqlPaymentStore(),
        settings=container.settings,
        ui=ui or default_ui(),
        hooks=hooks or LegacyHooks(),
        bot=container.bot,
        clock=clock or utcnow,
    )


_ATTR = "_money_services"


def money(container: Any = None, **overrides: Any) -> MoneyServices:
    """MoneyServices of ``container`` (the process container when None), built once.

    ``overrides`` (store, ui, hooks, clock) rebuild and re-cache: tests use it."""
    if container is None:
        from app.container import get_container

        container = get_container()
    cached = getattr(container, _ATTR, None)
    if cached is not None and not overrides:
        return cached
    services = build_money(money_deps(container, **overrides))
    setattr(container, _ATTR, services)
    return services
