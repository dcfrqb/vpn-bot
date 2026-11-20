"""Website checkout on the existing shop, with UUID ownership and durable grants.

Telegram customers use the existing money/fulfillment path. Website-only
customers have separate rows and panel users; no fictitious Telegram IDs.
Provider data is always re-read; target expiry is committed before panel writes.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID

from app.domain.models import Entitlement, EntitlementSource
from app.domain.plans import (
    MENU_PLAN_CODES,
    PLAN_CATALOG,
    get_plan_device_limit,
    get_plan_squad,
)
from app.logger import logger
from app.services.provisioning_rules import compute_target
from app.services.site_store import SqlSiteStore, SiteBusy

TERMINAL = {"canceled", "failed", "refunded", "held"}
PAID = {"paid", "provisioning", "fulfilled", "refunding", "refunded"}


def utcnow():
    return datetime.now(timezone.utc)


def date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class SiteCommerceError(Exception):
    def __init__(self, code, status=503, **extra):
        self.code, self.status, self.extra = code, status, extra


class SiteCommerce:
    def __init__(
        self, container, *, store=None, provider=None, panel=None, clock=utcnow
    ):
        self.container = container
        self.settings = container.settings
        self.store = store or SqlSiteStore()
        self._provider = provider
        if panel is None:
            from app.infra.remnawave.gateway import HttpRemnaGateway

            panel = HttpRemnaGateway()
        self.panel, self.clock = panel, clock

    @property
    def provider(self):
        if self._provider is None:
            self._provider = self.container.payments.client()
        return self._provider

    def enabled(self):
        return bool(getattr(self.settings, "SITE_CHECKOUT_ENABLED", False))

    def origin(self):
        origin = self.settings.SITE_PUBLIC_ORIGIN.rstrip("/")
        url = urlsplit(origin)
        local = self.settings.SITE_PAYMENT_TEST_MODE and url.hostname in {
            "localhost",
            "127.0.0.1",
        }
        if url.username or url.password or url.path or url.query or url.fragment:
            raise SiteCommerceError("checkout_unavailable")
        if url.scheme != "https" and not (local and url.scheme == "http"):
            raise SiteCommerceError("checkout_unavailable")
        return origin

    async def quote(self, account_id, telegram_id, code, months):
        customer = await self.store.customer(str(account_id))
        if (
            customer
            and customer.get("telegram_id")
            and telegram_id not in (None, customer["telegram_id"])
        ):
            raise SiteCommerceError("account_conflict", 409)
        tg = (
            customer.get("telegram_id")
            if customer and customer["backend"] == "telegram"
            else telegram_id
            if not customer
            else None
        )
        if tg:
            from app.services.money import money

            quote = await money(self.container).checkout.quote(tg, code, months)
            if quote is None:
                raise SiteCommerceError("plan_unavailable", 400)
            amount, title = quote.amount_rub, quote.title
        else:
            if (
                code not in MENU_PLAN_CODES
                or months not in PLAN_CATALOG[code]["prices"]
            ):
                raise SiteCommerceError("plan_unavailable", 400)
            amount, title = (
                PLAN_CATALOG[code]["prices"][months],
                PLAN_CATALOG[code]["display"],
            )
        return {
            "plan_code": code,
            "months": months,
            "amount_rub": amount,
            "title": title,
            "currency": "RUB",
            "enabled": self.enabled(),
        }

    async def start(
        self, account_id, telegram_id, code, months, order_id, expected_amount=None
    ):
        if not self.enabled():
            raise SiteCommerceError("checkout_unavailable")
        key = str(getattr(self.settings, "YOOKASSA_API_KEY", "") or "")
        if self.settings.SITE_PAYMENT_TEST_MODE and not key.startswith("test_"):
            raise SiteCommerceError("checkout_unavailable")
        if not self.settings.SITE_PAYMENT_TEST_MODE and key.startswith("test_"):
            raise SiteCommerceError("checkout_unavailable")
        account_id, order_id = str(UUID(str(account_id))), str(UUID(str(order_id)))
        async with self.store.lock(account_id):
            quote = await self.quote(account_id, telegram_id, code, months)
            if expected_amount is not None and expected_amount != quote["amount_rub"]:
                raise SiteCommerceError("price_changed", 409)
            customer = await self.store.customer(account_id)
            if customer is None:
                customer = {
                    "account_id": account_id,
                    "telegram_id": telegram_id,
                    "backend": "telegram" if telegram_id else "web",
                    "owner_tag": secrets.token_hex(16),
                }
                await self.store.save_customer(customer)
            order = await self.store.order(order_id)
            if order:
                if (
                    order["account_id"] != account_id
                    or order["plan_code"] != code
                    or order["months"] != months
                ):
                    raise SiteCommerceError("order_conflict", 409)
            else:
                recent = await self.store.orders(account_id)
                order = next(
                    (
                        o
                        for o in recent
                        if o["state"] in {"creating", "pending"}
                        and o["plan_code"] == code
                        and o["months"] == months
                        and date(o["created_at"]) > self.clock() - timedelta(minutes=15)
                    ),
                    None,
                )
                if order is None:
                    unresolved = next(
                        (
                            o
                            for o in recent
                            if o["state"]
                            in {
                                "creating",
                                "pending",
                                "paid",
                                "provisioning",
                                "refunding",
                                "held",
                            }
                        ),
                        None,
                    )
                    if unresolved:
                        raise SiteCommerceError(
                            "order_in_progress", 409, order_id=unresolved["id"]
                        )
                    customer["order_sequence"] = customer.get("order_sequence", 0) + 1
                    await self.store.save_customer(customer)
                    order = {
                        "id": order_id,
                        "account_id": account_id,
                        "state": "creating",
                        "provider_id": None,
                        "created_at": self.clock(),
                        "sequence": customer["order_sequence"],
                        **quote,
                    }
                    order.pop("enabled", None)
                    # Durable order exists before a provider request can create a charge.
                    await self.store.save_order(order)
            if order["state"] == "creating":
                await self._create(customer, order)
            return self.public(order)

    async def _create(self, customer, order):
        if self.clock() - date(order["created_at"]) > timedelta(hours=1):
            # Do not recreate an uncertain payment after the provider's idempotency window.
            order["state"] = "held"
            await self.store.save_order(order)
            return
        if customer["backend"] == "telegram":
            from app.services.money import money

            checkout = money(self.container).checkout
            quote = await checkout.quote(
                customer["telegram_id"], order["plan_code"], order["months"]
            )
            if quote is None or quote.amount_rub != order["amount_rub"]:
                order["state"] = "failed"
                await self.store.save_order(order)
                return
            result = await checkout.start_checkout(
                customer["telegram_id"], quote, site_order_id=order["id"]
            )
            if not result.ok:
                raise SiteCommerceError("checkout_unavailable")
            intent = result.intent
            order.update(
                provider_id=intent.external_id,
                confirmation_url=intent.confirmation_url,
                telegram_payment_id=intent.payment_id,
                state="pending",
            )
        else:
            raw = await self.provider.create_payment(
                {
                    "amount": {"value": f"{order['amount_rub']}.00", "currency": "RUB"},
                    "capture": True,
                    "confirmation": {
                        "type": "redirect",
                        "return_url": self.origin() + "/checkout/" + order["id"],
                    },
                    "description": f"CRS VPN {order['title']}, {order['months']} мес",
                    "metadata": {
                        "site_order_id": order["id"],
                        "site_account_id": order["account_id"],
                        "source": "website",
                        "plan_code": order["plan_code"],
                        "period_months": str(order["months"]),
                    },
                },
                "site:" + order["id"],
            )
            self._validate(raw, order)
            order.update(
                provider_id=raw["id"],
                confirmation_url=(raw.get("confirmation") or {}).get(
                    "confirmation_url"
                ),
                state="pending",
            )
        url = urlsplit(order.get("confirmation_url") or "")
        if url.scheme != "https" or url.hostname not in {"yoomoney.ru", "yookassa.ru"}:
            raise SiteCommerceError("checkout_unavailable")
        await self.store.save_order(order)

    def _validate(self, raw, order):
        if not raw or not raw.get("id"):
            raise SiteCommerceError("payment_unavailable")
        try:
            exact_amount = Decimal(str((raw.get("amount") or {}).get("value")))
        except (InvalidOperation, TypeError):
            raise SiteCommerceError("payment_mismatch", 409) from None
        metadata = raw.get("metadata") or {}
        recipient = str((raw.get("recipient") or {}).get("account_id") or "")
        if (
            exact_amount != Decimal(order["amount_rub"])
            or (raw.get("amount") or {}).get("currency") != "RUB"
            or metadata.get("site_order_id") != order["id"]
            or metadata.get("site_account_id") != order["account_id"]
            or raw.get("test") is not bool(self.settings.SITE_PAYMENT_TEST_MODE)
            or (recipient and recipient != str(self.settings.YOOKASSA_SHOP_ID))
            or (order.get("provider_id") and raw["id"] != order["provider_id"])
        ):
            raise SiteCommerceError("payment_mismatch", 409)

    async def refresh(self, account_id, order_id):
        order = await self.store.order(str(order_id))
        if order is None or order["account_id"] != str(account_id):
            raise SiteCommerceError("order_not_found", 404)
        async with self.store.lock(str(account_id)):
            order = await self.store.order(str(order_id))
            customer = await self.store.customer(str(account_id))
            if order["state"] in TERMINAL:
                return self.public(order)
            if order["state"] == "creating":
                await self._create(customer, order)
            if order["state"] in TERMINAL:
                return self.public(order)
            if customer["backend"] == "telegram":
                from app.services.money import money

                result = await money(self.container).checkout.check_result(
                    customer["telegram_id"], order["telegram_payment_id"]
                )
                value = result.outcome.value
                mapping = {
                    "fulfilled": "fulfilled",
                    "already": "fulfilled",
                    "canceled": "canceled",
                    "held": "held",
                    "rejected": "held",
                    "refunded": "refunded",
                }
                if value in mapping:
                    order["state"] = mapping[value]
                if order["state"] == "fulfilled":
                    order.setdefault("paid_at", self.clock().isoformat())
            else:
                raw = await self.provider.get_payment(order["provider_id"])
                self._validate(raw, order)
                refunded = Decimal(
                    str((raw.get("refunded_amount") or {}).get("value", "0"))
                )
                if refunded > 0:
                    # Partial refunds require an explicit decision about retained rights.
                    if refunded != Decimal(order["amount_rub"]):
                        order["state"] = "held"
                    else:
                        await self._refund(customer, order)
                elif raw.get("status") == "canceled":
                    order["state"] = "canceled"
                elif raw.get("status") == "succeeded" and raw.get("paid") is True:
                    paid_at = date(
                        raw.get("captured_at") or raw.get("created_at")
                    ) or date(order["created_at"])
                    order.setdefault("paid_at", paid_at.isoformat())
                    if order["state"] != "fulfilled":
                        order["state"] = "paid"
                        await self.store.save_order(order)
                        try:
                            await self._grant(customer, order)
                        except Exception as exc:
                            # Payment is durable. Show the saved paid/provisioning state,
                            # so a panel outage never suggests making another payment.
                            logger.warning(
                                "site grant order={} error={}",
                                order["id"],
                                type(exc).__name__,
                            )
                            return self.public(await self.store.order(order["id"]))
                elif order["state"] in PAID:
                    raise SiteCommerceError("payment_mismatch", 409)
            await self.store.save_order(order)
            return self.public(order)

    async def _user(self, customer, kind):
        panel_id = customer.get(kind + "_panel_id")
        username = ("wo_" if kind == "obhod" else "w_") + UUID(
            customer["account_id"]
        ).hex
        if panel_id:
            user = await self.panel.get_user(int(panel_id))
            if user is None:
                raise SiteCommerceError("subscription_needs_review", 409)
        else:
            # Recover a create whose HTTP response was lost, without adopting another user.
            user = await self.panel.find_user_by_username(username)
        if user is not None:
            marker = "crs-site:" + customer["account_id"] + ":" + customer["owner_tag"]
            if (
                user.username != username
                or user.telegram_id is not None
                or user.raw.get("description") != marker
            ):
                raise SiteCommerceError("subscription_needs_review", 409)
        return user, username

    async def _apply(self, customer, kind, target, plan):
        user, username = await self._user(customer, kind)
        squad = "obhod" if kind == "obhod" else get_plan_squad(plan)
        limit = PLAN_CATALOG["pro"]["obhod_gb"] * 1024**3 if kind == "obhod" else 0
        strategy = "MONTH" if kind == "obhod" else "NO_RESET"
        if user and user.status == "DISABLED":
            if kind == "obhod" and customer.get("obhod_disabled_by_site"):
                await self.panel.enable_user(user.id)
                customer["obhod_disabled_by_site"] = False
                await self.store.save_customer(customer)
            else:
                raise SiteCommerceError("subscription_needs_review", 409)
        expiry = max(target, user.expire_at) if user and user.expire_at else target
        if user:
            # Preserve extra/manual squads and never reduce device caps or expiry.
            from app.services.provisioning_rules import target_squads

            squads = [squad] if kind == "obhod" else target_squads(user.squads, squad)
            await self.panel.update_user(
                user.id,
                current=user,
                expire_at=expiry,
                squads=squads,
                device_limit=get_plan_device_limit(plan),
                traffic_limit_bytes=limit,
                traffic_limit_strategy=strategy,
            )
            verified = await self.panel.get_user(user.id)
        else:
            marker = "crs-site:" + customer["account_id"] + ":" + customer["owner_tag"]
            verified = await self.panel.create_user(
                username,
                telegram_id=None,
                expire_at=expiry,
                squads=[squad],
                device_limit=get_plan_device_limit(plan),
                traffic_limit_bytes=limit,
                traffic_limit_strategy=strategy,
                description=marker,
            )
        if verified is None or not verified.expire_at or verified.expire_at < target:
            raise SiteCommerceError("provisioning_pending")
        customer[kind + "_panel_id"] = verified.id
        await self.store.save_customer(customer)

    async def _grant(self, customer, order):
        try:
            user, _ = await self._user(customer, "main")
            if user and (
                user.status == "DISABLED"
                or (user.expire_at and user.expire_at.year >= 2099)
            ):
                order["state"] = "held"
                await self.store.save_order(order)
                return
            if not order.get("target_expire"):
                base = user.expire_at if user else None
                target = compute_target(
                    Entitlement(
                        plan_code=order["plan_code"], source=EntitlementSource.PAYMENT
                    ),
                    base,
                    self.clock(),
                    months=order["months"],
                )
                order["base_expire"] = (
                    max(base, self.clock()).isoformat()
                    if base
                    else self.clock().isoformat()
                )
                order["target_expire"] = target.isoformat()
                order["state"] = "provisioning"
                # This absolute target is reused after every crash, not recalculated.
                await self.store.save_order(order)
            target = date(order["target_expire"])
            latest = (
                customer.get("latest_payment_at", ""),
                customer.get("latest_payment_sequence", 0),
            )
            current = (order["paid_at"], order.get("sequence", 0))
            effective_plan = (
                customer.get("plan_code") if latest > current else order["plan_code"]
            )
            await self._apply(customer, "main", target, effective_plan)
            order["main_applied"] = True
            await self.store.save_order(order)
            if effective_plan == "pro":
                await self._apply(customer, "obhod", target, "pro")
            elif customer.get("obhod_panel_id"):
                await self.panel.disable_user(customer["obhod_panel_id"])
                customer["obhod_disabled_by_site"] = True
            customer["plan_code"] = effective_plan
            if current >= latest:
                customer.update(
                    latest_payment_at=order["paid_at"],
                    latest_order_id=order["id"],
                    latest_payment_sequence=order.get("sequence", 0),
                )
            await self.store.save_customer(customer)
            order["state"] = "fulfilled"
            await self.store.save_order(order)
        except SiteCommerceError as exc:
            if exc.status == 409:
                order["state"] = "held"
                await self.store.save_order(order)
                return
            raise

    async def _refund(self, customer, order):
        if not order.get("target_expire"):
            order["state"] = "refunded"
            return
        user, _ = await self._user(customer, "main")
        from app.services.remna_tariff import is_manual_squad_name

        if (
            user is None
            or user.status == "DISABLED"
            or (user.expire_at and user.expire_at.year >= 2099)
            or any(is_manual_squad_name(name) for name in user.squads)
        ):
            order["state"] = "held"
            return
        if not order.get("main_applied"):
            order["state"] = (
                "held" if user.expire_at >= date(order["target_expire"]) else "refunded"
            )
            return
        if not order.get("refund_target"):
            duration = date(order["target_expire"]) - date(order["base_expire"])
            target = max(self.clock() + timedelta(minutes=5), user.expire_at - duration)
            order.update(refund_target=target.isoformat(), state="refunding")
            order["refund_base_expire"] = user.expire_at.isoformat()
            await self.store.save_order(order)
        target = date(order["refund_target"])
        if user.expire_at not in (date(order["refund_base_expire"]), target):
            order["state"] = "held"
            return
        obhod = None
        if customer.get("obhod_panel_id"):
            obhod, _ = await self._user(customer, "obhod")
            if obhod and obhod.expire_at not in (
                date(order["refund_base_expire"]),
                target,
            ):
                order["state"] = "held"
                return
        await self.panel.update_user(user.id, current=user, expire_at=target)
        if obhod:
            await self.panel.update_user(obhod.id, current=obhod, expire_at=target)
        if customer.get("latest_order_id") == order["id"]:
            previous = [
                o
                for o in await self.store.orders(customer["account_id"])
                if o["id"] != order["id"] and o["state"] == "fulfilled"
            ]
            if previous:
                last = max(
                    previous, key=lambda o: (o.get("paid_at", ""), o.get("sequence", 0))
                )
                code = last["plan_code"]
                await self._apply(customer, "main", target, code)
                if code == "pro":
                    await self._apply(customer, "obhod", target, code)
                elif customer.get("obhod_panel_id"):
                    await self.panel.disable_user(customer["obhod_panel_id"])
                    customer["obhod_disabled_by_site"] = True
                customer.update(
                    plan_code=code,
                    latest_order_id=last["id"],
                    latest_payment_at=last["paid_at"],
                    latest_payment_sequence=last.get("sequence", 0),
                )
                await self.store.save_customer(customer)
        order["state"] = "refunded"

    async def recover(self):
        for order in await self.store.recovery():
            try:
                await self.refresh(order["account_id"], order["id"])
            except (SiteBusy, SiteCommerceError):
                continue
            except Exception as exc:
                logger.warning(
                    "site recovery order={} error={}", order["id"], type(exc).__name__
                )

    @staticmethod
    def public(order):
        return {
            k: order.get(k)
            for k in (
                "id",
                "state",
                "plan_code",
                "title",
                "months",
                "amount_rub",
                "currency",
                "created_at",
                "confirmation_url",
            )
        }


_services = {}


def commerce(container=None):
    if container is None:
        from app.container import get_container

        container = get_container()
    key = id(container)
    if key not in _services:
        _services[key] = SiteCommerce(container)
    return _services[key]


async def recovery_loop(service):
    while True:
        try:
            await service.recover()
        except Exception as exc:
            logger.warning("site recovery error={}", type(exc).__name__)
        await asyncio.sleep(20)


async def handle_site_notification(data, *, service=None):
    from app.config import settings

    if service is None and not (
        settings.SITE_CHECKOUT_ENABLED or settings.SITE_COMMERCE_RECOVERY_ENABLED
    ):
        return None
    service = service or commerce()
    obj = data.get("object") or {}
    if not isinstance(obj, dict):
        return None
    event = data.get("event", "")
    provider_id = (
        obj.get("payment_id") if event == "refund.succeeded" else obj.get("id")
    )
    if not isinstance(provider_id, str):
        return None
    order = await service.store.external(provider_id)
    if order is not None and event == "refund.succeeded":
        customer = await service.store.customer(order["account_id"])
        if customer["backend"] == "telegram":
            # Existing refund journal/rollback/alerts own Telegram payments.
            # The website ledger observes the result on its next refresh.
            return None
    if order is None:
        candidate = (obj.get("metadata") or {}).get("site_order_id")
        try:
            candidate = str(UUID(str(candidate)))
        except (TypeError, ValueError, AttributeError):
            return None
        order = await service.store.order(candidate)
        if order is None:
            return None
        customer = await service.store.customer(order["account_id"])
        if customer["backend"] == "web":
            raw = await service.provider.get_payment(provider_id)
            # Payload metadata is only a lookup hint. Verify actual provider ownership.
            service._validate(raw, order)
            async with service.store.lock(order["account_id"]):
                order = await service.store.order(order["id"])
                if order.get("provider_id") not in (None, provider_id):
                    raise SiteCommerceError("payment_mismatch", 409)
                order.update(provider_id=provider_id, state="pending")
                await service.store.save_order(order)
        else:
            # Reuse the provider idempotency key and existing bot store after a lost response.
            async with service.store.lock(order["account_id"]):
                await service._create(customer, order)
    await service.refresh(order["account_id"], order["id"])
    return True
