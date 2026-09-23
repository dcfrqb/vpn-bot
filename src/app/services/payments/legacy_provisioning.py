"""ProvisioningService over the 2.x code (release 3.0, stream A).

The Foundation shim ``LegacyProvisioningService`` delegates here until stream B
wires its own ProvisioningService in the container. Deleted with the shims.

grant(): a paid period for ``entitlement.payment_id`` through the 2.x core
  (legacy_yookassa.provision_paid_period: Phase A/B/verify/C with the B1/B2
  fixes). Calendar months come from the payment row, exactly like 2.x. The
  target date is fixed per payment in ``payment_metadata.grant_target``: a
  retry never extends twice, and a stuck older payment never lends its target
  to a new one (review round 3, item 3d).
revoke(): cut the main access now (expireAt = now + 5 min, the panel moves the
  user to EXPIRED; never DISABLED, see review M1), obhod off, never touches a
  lifetime subscription.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from app.domain.models import Entitlement, EntitlementSource, SubKind, SubscriptionState
from app.logger import logger

REVOKE_GRACE = timedelta(minutes=5)


def _parse_target(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _months_from_note(note: str) -> Optional[int]:
    for part in (note or "").split(";"):
        key, _, value = part.partition("=")
        if key.strip() == "months":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def grant(telegram_id: int, entitlement: Entitlement, *, trace_id: str) -> SubscriptionState:
    from app.db.models import Payment as PaymentModel
    from app.db.session import SessionLocal
    from app.services.payments import legacy_yookassa as legacy

    if entitlement.sub_kind is not SubKind.MAIN or entitlement.source not in (
        EntitlementSource.PAYMENT, EntitlementSource.AUTORENEW,
    ) or not entitlement.payment_id:
        raise NotImplementedError("legacy grant: only paid main periods; the rest belongs to stream B")
    if SessionLocal is None:
        raise RuntimeError("database is not configured")

    async with SessionLocal() as session:
        payment = (await session.execute(
            select(PaymentModel).where(PaymentModel.id == int(entitlement.payment_id))
        )).scalar_one_or_none()
        if payment is None:
            raise LookupError(f"payment {entitlement.payment_id} not found")
        meta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
        months = (payment.period_months or _months_from_note(entitlement.note)
                  or (int(meta["period_months"]) if str(meta.get("period_months") or "").isdigit() else None))
        if months:
            delta = relativedelta(months=int(months))
        elif entitlement.days:
            delta = timedelta(days=int(entitlement.days))
        else:
            raise ValueError("legacy grant: neither months nor days")

        async def _record_target(target: datetime) -> None:
            fresh = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
            if fresh.get("grant_target"):
                return
            fresh["grant_target"] = target.isoformat()
            payment.payment_metadata = fresh
            # committed together with Phase A (same session)

        try:
            result = await legacy.provision_paid_period(
                session,
                payment=payment,
                telegram_user_id=int(telegram_id),
                plan_code=entitlement.plan_code,
                period_months=int(months) if months else None,
                delta=delta,
                review_approved=bool(meta.get("review_approved")),
                trace_id=trace_id,
                fixed_target=_parse_target(meta.get("grant_target")),
                on_target=_record_target,
                reuse_unfinished_target=False,
            )
        except legacy.PaymentUserMissingError as e:
            raise LookupError(f"telegram user {telegram_id} not found") from e

        sub = result.subscription
        return SubscriptionState(
            telegram_id=int(telegram_id),
            has_panel_user=bool(sub.remna_user_id),
            active=True,
            plan_code=sub.plan_code,
            expires_at=result.actual_expire_at or result.valid_until,
            is_lifetime=bool(sub.is_lifetime),
            fetched_at=datetime.now(timezone.utc),
        )


async def revoke(telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str, trace_id: str) -> bool:
    from app.db.models import Subscription, TelegramUser
    from app.db.session import SessionLocal
    from app.remnawave.client import RemnaClient, normalize_expire_at

    if sub_kind is not SubKind.MAIN:
        raise NotImplementedError("legacy revoke: main subscription only")
    if SessionLocal is None:
        raise RuntimeError("database is not configured")
    async with SessionLocal() as session:
        sub = (await session.execute(select(Subscription).where(
            Subscription.telegram_user_id == int(telegram_id), Subscription.sub_kind == "main",
        ).with_for_update())).scalar_one_or_none()
        if sub is None:
            return False
        if sub.is_lifetime:
            logger.warning(f"[{trace_id}] revoke refused: lifetime subscription tg_id={telegram_id}")
            return False
        remna_id = sub.remna_user_id
        if not remna_id:
            tg = (await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == int(telegram_id)))).scalar_one_or_none()
            remna_id = tg.remna_user_id if tg else None
        expire = datetime.now(timezone.utc) + REVOKE_GRACE
        if remna_id:
            client = RemnaClient()
            try:
                await client.update_user(str(remna_id), expire_at=normalize_expire_at(expire))
            finally:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass
        naive = expire.replace(tzinfo=None)
        sub.active = False
        sub.valid_until = naive
        sub.remnawave_expected_expire_at = naive
        sub.provisioning_state = "expired"
        sub.last_provisioning_error = reason[:500]
        sub.autorenew = False
        try:
            from app.services.obhod_service import deactivate_obhod

            await deactivate_obhod(session, int(telegram_id), trace_id=trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{trace_id}] revoke: obhod deactivate soft-fail: {type(e).__name__}")
        await session.commit()
    logger.warning(f"[{trace_id}] access revoked: tg_id={telegram_id} reason={reason}")
    try:
        from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
        from app.tasks.expiry_notifier import suppress_expiry_notices

        await invalidate_subscription_cache(int(telegram_id))
        await invalidate_sync_cache(int(telegram_id))
        await suppress_expiry_notices(int(telegram_id), expire)
    except Exception:  # noqa: BLE001
        pass
    return True


async def apply_obhod_package_for_payment(telegram_id: int, package_code: str, payment_id: int, trace_id: str) -> bool:
    """2.x obhod package application (stream B owns obhod; this only adapts the call)."""
    from app.db.session import SessionLocal
    from app.services.obhod_service import apply_obhod_package

    async with SessionLocal() as session:
        return bool(await apply_obhod_package(
            session=session, telegram_user_id=int(telegram_id), package_code=package_code,
            trace_id=trace_id, payment_id=int(payment_id),
        ))


async def referral_after_paid(payment_id: int, bot: Any) -> None:
    """/sun718 referral alerts of 2.x (soft-fail)."""
    try:
        from app.db.models import Payment as PaymentModel
        from app.db.session import SessionLocal
        from app.services.referral_tracker import notify_referral_payment_if_applicable

        async with SessionLocal() as session:
            payment = (await session.execute(
                select(PaymentModel).where(PaymentModel.id == int(payment_id)))).scalar_one_or_none()
            if payment is not None and bot is not None:
                await notify_referral_payment_if_applicable(bot, session, payment)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"referral hook soft-fail for payment {payment_id}: {type(e).__name__}")
