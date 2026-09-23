"""ProvisioningService: the ONE place that gives or takes access (stream B).

Replaces 2.x ``remna_service.provision_tariff`` (promo/admin/day grants) and
``yookassa.get_or_create_remna_user_and_get_subscription_url`` (payments);
both are deleted, this is the only grant path.
Callers build an ``Entitlement`` and call ``grant`` (alias ``provision``).

grant(telegram_id, entitlement, trace_id):
  0. per-user Redis lock (waits up to LOCK_WAIT_S; Redis down = no lock);
  1. telegram_users row first (FK invariant);
  2. idempotency: key = ``pay:<payment_id>`` or ``trace:<trace_id>``. The
     target date is computed ONCE and recorded in the main row
     (config_data.grants[key], with the panel date it was computed from as
     ``base``) before the panel is touched; a retry re-applies the same date
     only while the panel still sits on that base (or on the target, when the
     PATCH landed); if another grant, a credit or an admin moved the panel in
     between, the period is recomputed on top of the new date and never
     shortens it (review money B-1); an applied key returns without any write;
  3. main panel account: stored link / telegramId lookup; created here and
     only here (takeover-safe username policy of hotfix A-2);
  4. target expiry = max(now, current) + days | until | 2099-12-31, and never
     earlier than the current panel date (never shorten);
  5. ONE PATCH: expireAt + squads + device limit (+ traffic when given):
       squads: remove only the bot's tariff squads (and the grace squad when
       clear_grace), add the plan squad, keep everything else; manual squads
       (*-m, *-friend, arcadia) are never written (gateway raises);
       device limit: hotfix policy ``resolve_device_limit`` (never lower,
       0 and manual NULL left alone);
     DISABLED in the panel = switched off by an admin: refused
     (GrantRefused, one admin alert) unless ``enable_if_disabled=True``;
     grace (stream C, row grace_state active/ended, clear_grace=True): the
     term extends from the PAID valid_until (never the grace end), the grace
     squad and the daily cap go (traffic 0 / NO_RESET), a DISABLED/EXPIRED
     grace-end user is enabled, and the grace mark is cleared;
  6. verify by re-reading: live status, expireAt >= target - 5 min, the plan
     squad on the user. A failed/timed-out PATCH that actually landed counts;
     otherwise the row is marked failed and ProvisioningError is raised (the
     caller retries; the 2.x reconciler also re-pushes failed rows);
  7. DB: row active/synced, valid_until = the panel date, grace mark cleared;
     obhod follows Pro (ensure/deactivate); status cache invalidated.

revoke / rollback (refunds, under the same lock): take back exactly the
refunded months from the current expiry (review money M-1); if nothing would
be left, a full cut: expireAt = now + 5 min (the panel refuses past dates and
turns the user EXPIRED itself), row inactive, obhod off. Lifetime users and
users with manual squads are never cut (admin alert instead). Both the 24h
refund and the YooKassa refund.succeeded webhook go through here.

add_days / add_traffic / add_devices: credits for EXISTING accounts only
(no creation), never lowering, idempotent per trace_id (Redis marker).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Optional

from app.domain.models import AdminTopic, Entitlement, PanelUser, SubKind, SubscriptionState
from app.domain.plans import get_plan_device_limit, get_plan_name, get_plan_squad
from app.logger import logger
from app.services.accounts import AccountsRepo, PanelAccounts, SqlAccountsRepo, SubRow
from app.services.credits import CreditsMixin, RevokeMixin
from app.services.provisioning_rules import (  # noqa: F401 (re-exported)
    CREDIT_MARKER_TTL_S,
    DISABLED_ALERT_TTL_S,
    GRACE_STATES,
    LATE_PATCH_DELAY_S,
    LIFETIME,
    MAX_GRANT_RECORDS,
    REVOKE_GRACE,
    VERIFY_TOLERANCE,
    GrantRefused,
    LegacyObhodSync,
    ObhodSync,
    ProvisioningBusy,
    ProvisioningError,
    _now,
    _parse,
    _same_instant,
    compute_target,
    grant_key,
    landed_pending_keys,
    retry_target,
    target_squads,
)
from app.services.remna_tariff import is_manual_squad_name, managed_tariff_squad_names, resolve_device_limit

LOCK_TTL_S = 120
LOCK_WAIT_S = 30.0
LOCK_POLL_S = 0.5


@dataclass
class _Lock:
    key: str
    token: Optional[str]


class PanelProvisioningService(RevokeMixin, CreditsMixin):
    """ProvisioningService port (app.services.ports)."""

    def __init__(
        self,
        remna=None,
        repo: Optional[AccountsRepo] = None,
        *,
        notifier: Any = None,
        status: Any = None,
        obhod: Optional[ObhodSync] = None,
        clock: Callable[[], datetime] = _now,
        settings: Any = None,
        late_patch_delay_s: float = LATE_PATCH_DELAY_S,
    ):
        if remna is None:
            from app.infra.remnawave.gateway import HttpRemnaGateway

            remna = HttpRemnaGateway()
        self.remna = remna
        self.repo = repo or SqlAccountsRepo()
        self.accounts = PanelAccounts(remna, self.repo)
        self._notifier = notifier
        self._status = status
        self.obhod = obhod if obhod is not None else LegacyObhodSync()
        self.clock = clock
        self._settings = settings
        self.late_patch_delay_s = late_patch_delay_s

    # ------------------------------------------------------------ plumbing

    @property
    def settings(self):
        if self._settings is None:
            from app.config import settings

            return settings
        return self._settings

    @property
    def notifier(self):
        if self._notifier is not None:
            return self._notifier
        try:
            from app.container import get_container

            return get_container().notifier
        except RuntimeError:
            return None

    async def _alert(self, text: str, *, dedup_key: str, ttl: int = DISABLED_ALERT_TTL_S) -> None:
        n = self.notifier
        if n is None:
            logger.warning(f"provisioning: no notifier for admin alert {dedup_key}")
            return
        try:
            await n.notify_admins(AdminTopic.PANEL, text, dedup_key=dedup_key, dedup_ttl=ttl)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"provisioning: admin alert failed ({type(e).__name__})")

    async def _lock(self, tg: int) -> _Lock:
        import uuid

        from app.infra.redis.flags import set_once

        key = f"lock:provision:{int(tg)}"
        token = uuid.uuid4().hex
        waited = 0.0
        while True:
            got = await set_once(key, token, ttl=LOCK_TTL_S)
            if got is None:
                logger.warning(f"provisioning: Redis unavailable, grant for tg={tg} runs without the lock")
                return _Lock(key, None)
            if got:
                return _Lock(key, token)
            if waited >= LOCK_WAIT_S:
                raise ProvisioningBusy(f"another grant for tg={tg} is still running")
            await asyncio.sleep(LOCK_POLL_S)
            waited += LOCK_POLL_S

    async def _unlock(self, lock: _Lock) -> None:
        if lock.token:
            from app.infra.redis.flags import compare_and_delete

            await compare_and_delete(lock.key, lock.token)

    async def _invalidate(self, tg: int) -> None:
        if self._status is not None:
            try:
                await self._status.invalidate(tg)
                return
            except Exception as e:  # noqa: BLE001
                logger.debug(f"provisioning: status invalidate failed ({type(e).__name__})")
        from app.services.status import PanelStatusService

        try:
            await PanelStatusService(self.remna, self.repo).invalidate(tg)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"provisioning: status invalidate failed ({type(e).__name__})")
        try:
            from app.services.cache import invalidate_site_profile_cache

            await invalidate_site_profile_cache(tg)
        except Exception:  # noqa: BLE001
            pass

    async def _squad_names(self, user: PanelUser) -> list[str]:
        """Names of the user's squads; an unknown uuid stops the grant (we would
        otherwise drop it from the target list)."""
        if len(user.squads) == len(user.squad_uuids):
            return list(user.squads)
        by_uuid = {u: n for n, u in (await self.remna.list_squads()).items()}
        names = []
        for u in user.squad_uuids:
            if u not in by_uuid:
                raise ProvisioningError(f"panel user {user.id} has an unknown squad {u}")
            names.append(by_uuid[u])
        return names

    # -------------------------------------------------------------- grant

    async def provision(self, telegram_id: int, entitlement: Entitlement, *, trace_id: str,
                        **kwargs: Any) -> SubscriptionState:
        """Alias of ``grant`` (the name used in the 3.0 plan)."""
        return await self.grant(telegram_id, entitlement, trace_id=trace_id, **kwargs)

    async def grant(
        self,
        telegram_id: int,
        entitlement: Entitlement,
        *,
        trace_id: str,
        enable_if_disabled: bool = False,
        clear_grace: Optional[bool] = None,
        months: Optional[int] = None,
    ) -> SubscriptionState:
        """Optional kwargs (port extension, stream B): ``enable_if_disabled``
        (an admin approved a payment of a DISABLED user), ``clear_grace``
        (default True: a grant ends the grace period; Entitlement.clear_grace
        wins when the field exists), ``months`` (calendar months from
        max(now, current expiry), for payments; Entitlement.months when added)."""
        tg = int(telegram_id)
        ent = entitlement
        if SubKind(ent.sub_kind) is not SubKind.MAIN:
            raise GrantRefused("bad_entitlement", "obhod follows Pro; grant a main entitlement")
        plan_squad = ent.squad or get_plan_squad(ent.plan_code)
        if not plan_squad:
            raise GrantRefused("bad_plan", f"unknown plan_code={ent.plan_code!r}")
        if is_manual_squad_name(plan_squad):
            raise GrantRefused("bad_plan", f"refusing to grant manual squad {plan_squad!r}")
        if clear_grace is None:
            clear_grace = bool(getattr(ent, "clear_grace", True))
        if months is None:
            months = getattr(ent, "months", None)
        if not (ent.is_lifetime or months or ent.days or ent.until):
            raise GrantRefused("bad_entitlement", "entitlement needs months, days, until or is_lifetime")
        key = grant_key(ent, trace_id)

        lock = await self._lock(tg)
        try:
            return await self._grant_locked(tg, ent, trace_id, key, plan_squad, months=months,
                                            enable_if_disabled=enable_if_disabled, clear_grace=clear_grace)
        finally:
            await self._unlock(lock)

    async def _grant_locked(self, tg: int, ent: Entitlement, trace_id: str, key: str, plan_squad: str, *,
                            months: Optional[int], enable_if_disabled: bool, clear_grace: bool) -> SubscriptionState:
        from app.services.status import build_state

        now = self.clock()
        await self.repo.ensure_tg_user(tg)
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        record = ((row.config_data if row else {}) or {}).get("grants", {}).get(key)
        if record and record.get("state") == "applied":
            logger.info(f"[{trace_id}] provisioning: grant {key} already applied tg={tg}, no write")
            try:
                user = await self.accounts.find_main(tg)
            except Exception:  # noqa: BLE001 - already granted; the DB row is enough
                return build_state(tg, user=None, main_row=row, now=now, stale=True)
            return build_state(tg, user=user, main_row=row, now=now)

        try:
            user = await self.accounts.find_main(tg)
        except Exception as e:
            raise ProvisioningError(f"panel lookup failed: {type(e).__name__}") from e

        # Grace (stream C): the panel shows the grace squad, a daily cap and
        # expireAt = grace_until; a DISABLED/EXPIRED status there is the grace
        # end, not an admin decision. Paid time counts from valid_until.
        in_grace = bool(clear_grace and row is not None and row.grace_state in GRACE_STATES)
        if (user is not None and (user.status or "").upper() == "DISABLED"
                and not enable_if_disabled and not in_grace):
            await self._alert(
                "Выдача не выполнена: пользователь отключен вручную в панели (DISABLED).\n"
                f"Telegram ID: {tg}\nТариф: {ent.plan_code}\nИсточник: {ent.source.value} ({key})\n"
                "Бот сам не включает таких пользователей. Включите в панели и повторите выдачу.",
                dedup_key=f"grant_disabled:{tg}",
            )
            raise GrantRefused("disabled", f"panel user {user.id} is DISABLED")

        current = user.expire_at if user is not None else None
        if in_grace:
            current = row.valid_until  # never the grace end: grace is not paid time
        if current is not None and current.year < 2020:
            current = None  # 2000-01-01 sentinel of 2.x /start users
        grants_before = ((row.config_data if row else {}) or {}).get("grants", {})
        if record and record.get("target"):
            target = retry_target(record, ent, current, now, months=months)
        else:
            target = compute_target(ent, current, now, months=months)
        if current is not None and target < current:
            target = current  # never shorten, whatever the record says (review money B-1)

        # Phase A: the intent is recorded before the panel is touched.
        row = self._phase_a(row, tg, ent, key, target, now, base=current,
                            landed=landed_pending_keys(grants_before, key, current))
        row = await self.repo.save_subscription(row)

        try:
            user = await self._apply(tg, ent, user, target, plan_squad, trace_id,
                                     enable_if_disabled=enable_if_disabled or in_grace,
                                     clear_grace=clear_grace, in_grace=in_grace)
            verified = await self._verify(user.id, target, plan_squad)
        except GrantRefused:
            raise
        except Exception as e:
            # The PATCH/create may have landed although we saw an error (timeout,
            # lost response): re-read before declaring failure (review m3/N6).
            if self.late_patch_delay_s > 0:
                await asyncio.sleep(self.late_patch_delay_s)
            try:
                probe = user or await self.accounts.find_main(tg)
                landed = await self._verify(probe.id, target, plan_squad) if probe else None
            except Exception:  # noqa: BLE001
                landed = None
            if landed is None:
                await self._mark_failed(row, f"{type(e).__name__}: {str(e)[:300]}", now)
                raise ProvisioningError(f"grant not applied: {type(e).__name__}") from e
            logger.warning(f"[{trace_id}] provisioning: apply raised {type(e).__name__} but the grant landed")
            verified = landed

        row = await self._phase_c(row, tg, ent, key, verified, clear_grace, now)
        try:
            await self.repo.set_panel_link(tg, verified.id, username=verified.username, raw=dict(verified.raw))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{trace_id}] provisioning: panel link not stored ({type(e).__name__})")
        await self.obhod.on_main_granted(tg, ent.plan_code, verified.expire_at or row.valid_until, trace_id)
        await self._invalidate(tg)
        logger.info(
            f"[{trace_id}] provisioning: granted tg={tg} plan={ent.plan_code} source={ent.source.value} "
            f"key={key} panel_id={verified.id} expire={verified.expire_at.isoformat() if verified.expire_at else None}"
        )
        return build_state(tg, user=verified, main_row=row, now=self.clock())

    def _phase_a(self, row: Optional[SubRow], tg: int, ent: Entitlement, key: str, target: datetime,
                 now: datetime, *, base: Optional[datetime] = None, landed: tuple[str, ...] = ()) -> SubRow:
        if row is None:
            row = SubRow(telegram_user_id=tg, sub_kind=SubKind.MAIN.value, plan_code=ent.plan_code)
        cfg = dict(row.config_data or {})
        grants = dict(cfg.get("grants") or {})
        for other in landed:
            # An earlier pending grant whose PATCH did land (the panel sits on its
            # target): it is done, a later retry must not add its period again.
            grants[other] = {**grants[other], "state": "applied", "landed_seen_by": key}
        for other, orec in list(grants.items()):
            # Review round 2, N-1: another pending grant computed from the same
            # panel date did not land (the panel still sits on its base). If THIS
            # grant lands, a panel on the shared target proves only one of the
            # two, so the other one's retry must stack on the new date.
            if (other != key and other not in landed and isinstance(orec, dict)
                    and orec.get("state") == "pending" and "base" in orec and not orec.get("moved")
                    and _same_instant(_parse(orec.get("base")), base)):
                grants[other] = {**orec, "moved": True}
        grants[key] = {"target": target.isoformat(), "state": "pending", "plan": ent.plan_code,
                       "source": ent.source.value, "at": now.isoformat(),
                       "base": base.isoformat() if base is not None else None}
        if len(grants) > MAX_GRANT_RECORDS:
            for old in sorted(grants, key=lambda k: grants[k].get("at", ""))[: len(grants) - MAX_GRANT_RECORDS]:
                grants.pop(old, None)
        cfg["grants"] = grants
        # Same shape as 2.x Phase A: the row already carries the target, so the
        # 2.x reconciler re-pushes THIS date if the grant dies half way.
        return replace(
            row,
            plan_code=ent.plan_code,
            plan_name=get_plan_name(ent.plan_code),
            active=True,
            valid_until=target,
            is_lifetime=target.year >= 2099,
            config_data=cfg,
            remnawave_expected_expire_at=target,
            last_provisioning_attempt_at=now,
            provisioning_state="pending",
        )

    async def _phase_c(self, row: SubRow, tg: int, ent: Entitlement, key: str, user: PanelUser,
                       clear_grace: bool, now: datetime) -> SubRow:
        cfg = dict(row.config_data or {})
        grants = dict(cfg.get("grants") or {})
        rec = dict(grants.get(key) or {})
        rec["state"] = "applied"
        grants[key] = rec
        for other, orec in list(grants.items()):
            if other != key and isinstance(orec, dict) and orec.get("state") == "pending":
                # This grant moved the panel date past the other one's base: its
                # retry must stack on the new date (review money B-1).
                grants[other] = {**orec, "moved": True}
        cfg["grants"] = grants
        cfg["last_source"] = ent.source.value
        if ent.payment_id:
            cfg["last_payment_id"] = ent.payment_id
        url = user.subscription_url
        if url:
            from app.infra.remnawave.client import apply_subscription_domain

            cfg["subscription_url"] = apply_subscription_domain(url)
        updated = replace(
            row,
            plan_code=ent.plan_code,
            plan_name=get_plan_name(ent.plan_code),
            active=True,
            valid_until=user.expire_at,
            is_lifetime=bool(user.expire_at and user.expire_at.year >= 2099),
            remna_user_id=str(user.id),
            provisioning_state="synced",
            remnawave_synced_at=now,
            remnawave_expected_expire_at=user.expire_at,
            last_provisioning_error=None,
            config_data=cfg,
        )
        if clear_grace and (row.grace_state is not None or row.grace_until is not None):
            # Mark cleared (stream C: an empty grace_state makes the next expiry
            # eligible for grace again).
            updated = replace(updated, grace_until=None, grace_state=None)
        return await self.repo.save_subscription(updated)

    async def _mark_failed(self, row: SubRow, error: str, now: datetime) -> None:
        try:
            await self.repo.save_subscription(replace(
                row, provisioning_state="failed", last_provisioning_error=error[:500],
                last_provisioning_attempt_at=now,
            ))
        except Exception as e:  # noqa: BLE001
            logger.error(f"provisioning: could not mark row failed ({type(e).__name__})")

    async def _apply(self, tg: int, ent: Entitlement, user: Optional[PanelUser], target: datetime,
                     plan_squad: str, trace_id: str, *, enable_if_disabled: bool, clear_grace: bool,
                     in_grace: bool = False) -> PanelUser:
        plan_limit = int(ent.device_limit or get_plan_device_limit(ent.plan_code))
        grace_squad = getattr(self.settings, "GRACE_SQUAD", None)
        if user is None:
            user, adopted = await self._create(tg, target, plan_squad, plan_limit, trace_id)
            if not adopted:
                return user
        names = await self._squad_names(user)
        squads = target_squads(names, plan_squad, grace_squad=grace_squad, clear_grace=clear_grace)
        managed = managed_tariff_squad_names()
        foreign = [n for n in names if n not in managed]
        new_limit = resolve_device_limit(user.device_limit, plan_limit, has_foreign_squads=bool(foreign))
        was_disabled = (user.status or "").upper() == "DISABLED"
        traffic, strategy = ent.traffic_limit_bytes, None
        if in_grace and traffic is None:
            traffic, strategy = 0, "NO_RESET"  # drop the grace daily cap
        updated = await self.remna.update_user(
            user.id,
            expire_at=target,
            squads=squads,
            device_limit=new_limit,
            traffic_limit_bytes=traffic,
            traffic_limit_strategy=strategy,
            current=user,
        )
        status_after = (updated.status or "").upper()
        if enable_if_disabled and (was_disabled or (in_grace and status_after in ("DISABLED", "EXPIRED"))):
            await self.remna.enable_user(user.id)
            logger.warning(f"[{trace_id}] provisioning: panel user {user.id} was {user.status}, enabled "
                           f"({'grace end' if in_grace else 'approved'})")
        return updated

    async def _create(self, tg: int, target: datetime, plan_squad: str, plan_limit: int,
                      trace_id: str) -> tuple[PanelUser, bool]:
        from app.utils.remna_username import build_remna_display_name, build_remna_username

        row = await self.repo.get_tg_user(tg)
        username = build_remna_username(
            telegram_id=tg, username=row.username if row else None,
            first_name=row.first_name if row else None, last_name=row.last_name if row else None,
        )
        description = build_remna_display_name(
            telegram_id=tg, username=row.username if row else None,
            first_name=row.first_name if row else None, last_name=row.last_name if row else None,
        )
        creator = getattr(self.remna, "create_user_unique", None)
        if creator is not None:
            user, adopted = await creator(
                telegram_id=tg, base_username=username, known_panel_id=None, expire_at=target,
                squads=[plan_squad], device_limit=plan_limit, description=description,
            )
        else:  # a bare RemnaGateway: plain create (no username collision handling)
            user = await self.remna.create_user(username, telegram_id=tg, expire_at=target,
                                                squads=[plan_squad], device_limit=plan_limit)
            adopted = False
        logger.info(f"[{trace_id}] provisioning: panel account {'reused' if adopted else 'created'} "
                    f"tg={tg} panel_id={user.id}")
        return user, adopted

    async def _verify(self, panel_id: int, target: datetime, plan_squad: str) -> PanelUser:
        user = await self.remna.get_user(panel_id)
        if user is None:
            raise ProvisioningError(f"panel user {panel_id} vanished")
        status = (user.status or "").upper()
        if status in ("DISABLED", "EXPIRED"):
            raise ProvisioningError(f"panel status {status} after grant")
        if user.expire_at is None or user.expire_at < target - VERIFY_TOLERANCE:
            raise ProvisioningError("expireAt shortfall after grant")
        names = await self._squad_names(user)
        if plan_squad not in names:
            raise ProvisioningError(f"plan squad {plan_squad!r} missing after grant")
        return user
