"""Remnawave webhook events -> bot actions. Owner: C.

Called by api/routes/remnawave.py in the background after the signature,
replay window and dedupe checks. Every handler is idempotent enough for a
retry: user notices carry a Notifier dedup key, DB marks are conditional.

Handled (Remnawave 3.4.3, @remnawave/backend-contract EVENTS):
  user.expired              main row inactive, obhod disabled, grace (flag)
  user.not_connected        onboarding nudge with the connect screen
  user_hwid_devices.added   «новое устройство, N из M» + devices button
  user.modified (+ other user.*, hwid.*)  StatusService.invalidate
  node.connection_lost / node.connection_restored  admin notice (PANEL)
  user.limited on an obhod account  traffic package upsell
Everything else is acknowledged and ignored.

Payload secrets (vlessUuid, trojanPassword, ssPassword, subscriptionUrl) are
never logged: only event name, panel id and telegram id are.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from app.domain.models import AdminTopic, PanelUser
from app.domain.texts import notify as T
from app.logger import logger
from app.services.events_repo import EXPIRE_PAID_LATER, EventsRepo, SqlEventsRepo
from app.infra.remnawave.dto import panel_user_from_raw

OBHOD_USERNAME = re.compile(r"^tg_(\d+)_obhod$")

EV_USER_EXPIRED = "user.expired"
EV_USER_NOT_CONNECTED = "user.not_connected"
EV_USER_MODIFIED = "user.modified"
EV_USER_LIMITED = "user.limited"
EV_HWID_ADDED = "user_hwid_devices.added"
EV_NODE_LOST = "node.connection_lost"
EV_NODE_RESTORED = "node.connection_restored"

NODE_DEDUP_TTL = 600
DEVICE_DEDUP_TTL = 24 * 3600
NOT_CONNECTED_DEDUP_TTL = 7 * 24 * 3600
OBHOD_LIMIT_DEDUP_TTL = 35 * 24 * 3600


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PanelEvent:
    scope: str
    event: str
    timestamp: Optional[datetime]
    data: Mapping[str, Any] = field(default_factory=dict, repr=False)
    meta: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def user(self) -> Optional[PanelUser]:
        raw = self.data.get("user") if self.scope == "user_hwid_devices" else self.data
        if self.scope not in ("user", "user_hwid_devices") or not isinstance(raw, Mapping):
            return None
        if raw.get("id") in (None, ""):
            return None
        return panel_user_from_raw(raw)

    @property
    def device(self) -> Mapping[str, Any]:
        d = self.data.get("hwidUserDevice") if self.scope == "user_hwid_devices" else None
        return d if isinstance(d, Mapping) else {}

    @property
    def node(self) -> Mapping[str, Any]:
        return self.data if self.scope == "node" else {}


def parse_event(payload: Any) -> PanelEvent:
    """Webhook JSON -> PanelEvent. ValueError on a shape we cannot use."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload is not an object")
    scope, event = payload.get("scope"), payload.get("event")
    if not isinstance(scope, str) or not isinstance(event, str) or not event.startswith(scope + "."):
        raise ValueError("bad scope/event")
    data = payload.get("data")
    meta = payload.get("meta")
    return PanelEvent(
        scope=scope,
        event=event,
        timestamp=parse_ts(payload.get("timestamp")),
        data=data if isinstance(data, Mapping) else {},
        meta=meta if isinstance(meta, Mapping) else {},
    )


class PanelEventProcessor:
    def __init__(
        self,
        container: Any,
        repo: Optional[EventsRepo] = None,
        *,
        settings: Any = None,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self.c = container
        self.repo = repo if repo is not None else SqlEventsRepo()
        self._settings = settings if settings is not None else getattr(container, "settings", None)
        self.clock = clock

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    async def process(self, ev: PanelEvent) -> str:
        handler = {
            EV_USER_EXPIRED: self.on_expired,
            EV_USER_NOT_CONNECTED: self.on_not_connected,
            EV_HWID_ADDED: self.on_device_added,
            EV_USER_LIMITED: self.on_limited,
            EV_NODE_LOST: self.on_node,
            EV_NODE_RESTORED: self.on_node,
        }.get(ev.event)
        if handler is not None:
            outcome = await handler(ev)
        elif ev.scope in ("user", "user_hwid_devices"):
            outcome = await self.on_user_changed(ev)
        else:
            outcome = "ignored"
        logger.info(f"panel event {ev.event}: {outcome}")
        return outcome

    # ------------------------------------------------------------ helpers

    async def _invalidate(self, telegram_id: Optional[int]) -> None:
        if not telegram_id:
            return
        try:
            await self.c.status.invalidate(int(telegram_id))
        except Exception as e:  # noqa: BLE001 - cache only
            logger.debug(f"panel event: invalidate tg={telegram_id} failed ({type(e).__name__})")

    async def _owner_of(self, user: PanelUser) -> Optional[int]:
        """Telegram id of the person behind a panel account (main or obhod)."""
        if user.telegram_id:
            return int(user.telegram_id)
        owner = await self.repo.obhod_owner(user.id, user.uuid)
        if owner:
            return owner
        m = OBHOD_USERNAME.match(user.username or "")
        return int(m.group(1)) if m else None

    def _is_obhod(self, user: PanelUser) -> bool:
        from app.domain.plans import OBHOD_SQUAD_NAME

        return not user.telegram_id and (
            bool(OBHOD_USERNAME.match(user.username or "")) or OBHOD_SQUAD_NAME in user.squads
        )

    def _support(self) -> Optional[str]:
        handle = getattr(self.settings, "SUPPORT_HANDLE", None) or getattr(
            self.settings, "ADMIN_SUPPORT_USERNAME", None
        )
        if not handle:
            return None
        handle = str(handle).strip()
        return handle if handle.startswith("@") else f"@{handle}"

    # ------------------------------------------------------------ handlers

    async def on_user_changed(self, ev: PanelEvent) -> str:
        user = ev.user
        if user is None:
            return "no_user"
        await self._invalidate(await self._owner_of(user))
        return "invalidated"

    async def on_expired(self, ev: PanelEvent) -> str:
        user = ev.user
        if user is None:
            return "no_user"
        if self._is_obhod(user) or not user.telegram_id:
            await self._invalidate(await self._owner_of(user))
            return "not_main"
        tg = int(user.telegram_id)
        now = self.clock()
        state = await self.repo.mark_main_expired(tg, now)
        await self._invalidate(tg)
        if state == EXPIRE_PAID_LATER:
            return "paid_later"

        obhod_pid = await self.repo.obhod_panel_id(tg)
        if obhod_pid:
            if str(obhod_pid).isdigit():
                try:
                    await self.c.remna.disable_user(int(obhod_pid))
                except Exception as e:  # noqa: BLE001 - the row is still turned off
                    logger.warning(f"panel event: obhod disable tg={tg} failed ({type(e).__name__})")
            await self.repo.deactivate_obhod_row(tg)

        if not getattr(self.settings, "GRACE_ENABLED", False):
            return f"expired_{state}"
        from app.bot.views.notify import renew_kb
        from app.services.grace import GraceService

        grace = GraceService(self.c.remna, self.repo, provisioning=self.c.provisioning,
                             settings=self.settings, clock=self.clock)
        until = await grace.start(tg, user)
        if until is None:
            return f"expired_{state}"
        info = await self.repo.reminder_info(tg)
        await self.c.notifier.notify_user(
            tg,
            T.grace_started(int(getattr(self.settings, "GRACE_DAYS", 3) or 3), until,
                            int(getattr(self.settings, "GRACE_DAILY_GB", 5) or 5)),
            reply_markup=renew_kb(*await self._renew_target(tg, info)),
            dedup_key=f"grace_start:{tg}:{until.date().isoformat()}",
            dedup_ttl=4 * 24 * 3600,
        )
        await self.c.notifier.notify_admins(
            AdminTopic.PANEL, T.admin_grace_started(tg, until), disable_notification=True,
        )
        return "grace_started"

    async def _renew_target(self, tg: int, info) -> tuple[Optional[str], Optional[int]]:
        return await renew_target(self.c, tg, info)

    async def on_not_connected(self, ev: PanelEvent) -> str:
        user = ev.user
        if user is None or not user.telegram_id or self._is_obhod(user):
            return "skipped"
        if (user.status or "").upper() != "ACTIVE":
            return "not_active"
        from app.bot.views.notify import connect_kb

        hours = ev.meta.get("notConnectedAfterHours") or 0
        sent = await self.c.notifier.notify_user(
            int(user.telegram_id),
            T.NOT_CONNECTED,
            reply_markup=connect_kb(getattr(self.settings, "CONNECT_ARTICLE_URL", None)),
            dedup_key=f"rw:not_connected:{user.telegram_id}:{hours}",
            dedup_ttl=NOT_CONNECTED_DEDUP_TTL,
        )
        return "nudged" if sent else "deduped"

    async def on_device_added(self, ev: PanelEvent) -> str:
        user = ev.user
        if user is None:
            return "no_user"
        tg = await self._owner_of(user)
        await self._invalidate(tg)
        if not user.telegram_id or self._is_obhod(user):
            return "not_main"
        dev = ev.device
        hwid = str(dev.get("hwid") or "")
        model = dev.get("deviceModel") or dev.get("platform") or None
        used: Optional[int] = None
        try:
            used = len(await self.c.remna.list_devices(user.id))
        except Exception as e:  # noqa: BLE001 - count is optional
            logger.debug(f"panel event: device count unavailable ({type(e).__name__})")
        from app.bot.views.notify import devices_kb

        digest = hashlib.sha256(hwid.encode()).hexdigest()[:16]
        sent = await self.c.notifier.notify_user(
            int(user.telegram_id),
            T.device_added(str(model) if model else None, used, user.device_limit, self._support()),
            reply_markup=devices_kb(),
            dedup_key=f"rw:hwid_added:{user.id}:{digest}",
            dedup_ttl=DEVICE_DEDUP_TTL,
        )
        return "notified" if sent else "deduped"

    async def on_limited(self, ev: PanelEvent) -> str:
        user = ev.user
        if user is None:
            return "no_user"
        tg = await self._owner_of(user)
        await self._invalidate(tg)
        if not self._is_obhod(user) or not tg:
            return "not_obhod"
        from app.domain.plans import OBHOD_PACKAGE_CODES, is_obhod_package_purchasable

        can_buy = any(is_obhod_package_purchasable(c) for c in OBHOD_PACKAGE_CODES)
        from app.bot.views.notify import obhod_packages_kb

        month = self.clock().strftime("%Y-%m")
        sent = await self.c.notifier.notify_user(
            tg,
            T.obhod_limited(user.traffic_limit_bytes, can_buy),
            reply_markup=obhod_packages_kb() if can_buy else None,
            dedup_key=f"rw:obhod_limited:{tg}:{month}",
            dedup_ttl=OBHOD_LIMIT_DEDUP_TTL,
        )
        return "upsell" if sent else "deduped"

    async def on_node(self, ev: PanelEvent) -> str:
        node = ev.node
        name = str(node.get("name") or "?")
        address = str(node.get("address") or "?")
        uuid = str(node.get("uuid") or name)
        if ev.event == EV_NODE_LOST:
            text = T.admin_node_lost(name, address, node.get("lastStatusMessage") or None)
        else:
            text = T.admin_node_restored(name, address)
        n = await self.c.notifier.notify_admins(
            AdminTopic.PANEL, text, dedup_key=f"rw:{ev.event}:{uuid}", dedup_ttl=NODE_DEDUP_TTL,
        )
        return "notified" if n else "deduped"


async def renew_target(container: Any, telegram_id: int, info) -> tuple[Optional[str], Optional[int]]:
    """(plan, months) for the «Продлить» button: the last paid plan and period,
    only when checkout still sells it to this user (Quote from CheckoutService);
    (None, None) sends the user to the plan list instead."""
    plan = getattr(info, "last_plan_code", None)
    months = getattr(info, "last_months", None) or (1 if plan else None)
    if not plan:
        return None, None
    try:
        quote = await container.checkout.quote(int(telegram_id), plan, int(months))
    except Exception as e:  # noqa: BLE001 - fall back to the plan list
        logger.debug(f"renew_target: quote failed ({type(e).__name__})")
        return None, None
    if quote is None or not quote.sellable:
        return None, None
    return quote.plan_code, quote.months
