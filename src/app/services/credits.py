"""Mixins of PanelProvisioningService (stream B), split out to keep modules small.

RevokeMixin.rollback / revoke: take the exact credited payment duration back
(or a full cut: expireAt = now + 5 min, row inactive, obhod off) under
the per-user provisioning lock; lifetime and manual-squad users are never cut.
CreditsMixin: add_days / add_traffic / add_devices for EXISTING panel
accounts. Never create an account, never lower anything, idempotent per
trace_id (Redis marker).

add_days marker ``credit:<trace>`` (review N-3): ``1`` = applied; a JSON
``{"s": "pending", "target", "base"}`` = the PATCH was sent but its outcome is
not known yet. A retry with a pending marker re-reads the panel: the expiry at
or past the recorded target means the days landed (nothing is written again),
otherwise it credits. A PATCH error re-reads the panel before the marker is
dropped, so a timed-out PATCH that landed is reported as applied.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.domain.models import Entitlement, EntitlementSource, SubKind
from app.logger import logger
from app.services.accounts import numeric_panel_id
from app.domain.plans import PLAN_CATALOG, get_plan_name, get_plan_squad, is_obhod_eligible_plan
from app.services.provisioning_rules import (
    CREDIT_MARKER_TTL_S,
    REVOKE_GRACE,
    CreditOutcomeUnknown,
    ProvisioningError,
    VERIFY_TOLERANCE,
    compute_target,
)
from app.services.remna_tariff import is_manual_squad_name, managed_tariff_squad_names, resolve_device_limit


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of ``rollback``: ``expired`` (full cut, expireAt = now + 5 min),
    ``shortened`` (the credited duration taken off, access stays), ``skipped``
    (lifetime/manual squad or missing payment evidence: admin alerted), ``no_account``."""

    action: str
    expire_at: Optional[datetime] = None

    @property
    def changed(self) -> bool:
        return self.action in ("expired", "shortened")


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rights_snapshot(user, names=None):
    """Only observed panel rights, never the optimistic subscription row."""
    names = list(names if names is not None else user.squads) if user else []
    managed = sorted(set(names) & managed_tariff_squad_names())
    plans = [p for p, meta in PLAN_CATALOG.items() if meta.get("squad") in managed and p != "trial"]
    return {"managed": managed, "plan": plans[0] if len(plans) == 1 else None,
            "device_limit": user.device_limit if user else None}


def _valid_rights(value):
    if not isinstance(value, dict) or not {"managed", "plan", "device_limit"} <= value.keys():
        return False
    managed, plan, cap = value["managed"], value["plan"], value["device_limit"]
    return (isinstance(managed, list) and all(isinstance(n, str) for n in managed)
            and set(managed) <= managed_tariff_squad_names() and len(set(managed)) == len(managed)
            and (plan is None or (isinstance(plan, str) and plan in PLAN_CATALOG
                                  and get_plan_squad(plan) in managed))
            and (cap is None or (type(cap) is int and cap >= 0)))


def _valid_rights_op(value):
    if not isinstance(value, dict) or not {"plan", "squad", "device_limit", "foreign"} <= value.keys():
        return False
    return (isinstance(value["plan"], str) and value["plan"] in PLAN_CATALOG
            and isinstance(value["squad"], str) and value["squad"] == get_plan_squad(value["plan"])
            and type(value["device_limit"]) is int and value["device_limit"] > 0
            and type(value["foreign"]) is bool)


def _rights_match(actual, expected):
    return (actual.get("managed") == expected.get("managed")
            and actual.get("device_limit") == expected.get("device_limit"))


def refund_rights(row, payment_id, observed):
    try:
        result = _replay_refund_rights(row, payment_id, observed)
        return result if _valid_rights(result) else None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        # Legacy/malformed JSON is evidence for manual review, never a basis
        # for a guessed downgrade (or an endlessly retrying webhook).
        return None


def _replay_refund_rights(row, payment_id, observed):
    """Replay a complete observed rights history without refunded operations.

    Device edits between grants (or after the last one) remain external floors.
    Only managed tariff squads are replayed; foreign squads are kept live.
    A bounded/truncated or unverifiable journal requires human review.
    """
    cfg = row.config_data or {}
    if not _valid_rights(observed) or (cfg.get("rights_checkpoint") is not None
                                       and not _valid_rights(cfg["rights_checkpoint"])):
        return None
    grants = cfg.get("grants") or {}
    paid = grants.get(f"pay:{payment_id}") or {}
    if cfg.get("rights_history_truncated") or not paid.get("rights_after"):
        return None
    records = sorted((r for r in grants.values() if r.get("rights_seq") is not None),
                     key=lambda r: r["rights_seq"])
    if not records or records[0]["rights_seq"] != 1:
        return None
    if [r["rights_seq"] for r in records] != list(range(1, cfg.get("rights_seq", 0) + 1)):
        return None
    refunded = {r.get("payment_id") for r in (cfg.get("rollbacks") or {}).values()
                if r.get("state") == "applied"}
    refunded.add(payment_id)
    result = dict(records[0].get("rights_before") or {})
    previous = result
    for r in records:
        before, after, op = r.get("rights_before"), r.get("rights_after"), r.get("rights_op")
        if (r.get("state") != "applied" or not _valid_rights(before) or not _valid_rights(after)
                or not _valid_rights_op(op) or type(r.get("rights_seq")) is not int):
            return None
        if r.get("rights_checkpoint_before") is not None and not _valid_rights(r["rights_checkpoint_before"]):
            return None
        known_before = [r.get("rights_checkpoint_before")]
        if before.get("managed") != previous.get("managed"):
            if not any(k and k.get("managed") == before.get("managed") for k in known_before):
                return None
        if before.get("device_limit") != previous.get("device_limit"):
            if not any(k and k.get("device_limit") == before.get("device_limit") for k in known_before):
                result["device_limit"] = before["device_limit"]
        if after.get("managed") != [op["squad"]]:
            return None
        actual_limit = resolve_device_limit(before.get("device_limit"), op["device_limit"], op["foreign"])
        actual_limit = before.get("device_limit") if actual_limit is None else actual_limit
        if r.get("payment_id") not in refunded or r.get("payment_id") is None:
            result.update(managed=[op["squad"]], plan=op["plan"])
            limit = resolve_device_limit(result.get("device_limit"), op["device_limit"], op["foreign"])
            if limit is not None:
                result["device_limit"] = limit
        if after.get("device_limit") != actual_limit:
            result["device_limit"] = after["device_limit"]
        previous = after
    if observed.get("managed") != previous.get("managed"):
        # A completed refund's target is also an authoritative observed state.
        known = [cfg.get("rights_checkpoint")]
        if not any(k and k.get("managed") == observed.get("managed") for k in known):
            return None
    if observed.get("device_limit") != previous.get("device_limit"):
        # Preserve a manual/custom current value unless a previous rollback
        # already produced it, in which case replay supplies the effective cap.
        known = [cfg.get("rights_checkpoint")]
        if not any(k and _rights_match(observed, k) for k in known):
            result["device_limit"] = observed["device_limit"]
    return result


class RevokeMixin:
    async def revoke(self, telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str,
                     trace_id: str, months: Optional[int] = None, payment_id: Optional[int] = None) -> bool:
        if SubKind(sub_kind) is SubKind.OBHOD:
            await self.obhod.on_main_revoked(int(telegram_id), trace_id)
            await self._invalidate(int(telegram_id))
            return True
        return (await self.rollback(telegram_id, months=months, reason=reason,
                                    trace_id=trace_id, payment_id=payment_id)).changed

    async def rollback(self, telegram_id: int, *, months: Optional[int], reason: str,
                       trace_id: str, payment_id: Optional[int] = None) -> RollbackResult:
        """Refund exact credited time and proven rights, under the grant lock."""
        tg = int(telegram_id)
        lock = await self._lock(tg)
        try:
            return await self._rollback_locked(tg, months=int(months or 0), reason=reason,
                                               trace_id=trace_id, payment_id=payment_id)
        finally:
            await self._unlock(lock)

    async def _refund_review(self, tg, payment_id, current, detail):
        await self._alert(
            "Возврат денег: доступ не изменен, нет точной записи выдачи или прав платежа.\n"
            f"Telegram ID: {tg}\nПлатеж: {payment_id}\nПричина: {detail}\nПроверьте вручную.",
            dedup_key=f"refund_review:{tg}:{payment_id}",
        )
        return RollbackResult("skipped", current)

    async def _rollback_sync(self, tg, row, key, trace_id):
        rec = (row.config_data or {}).get("rollbacks", {}).get(key, {}) if row else {}
        if rec.get("sync_state") == "applied":
            return
        # A later grant's Phase A row can be optimistic after a failed PATCH.
        # Synchronize only rights/date actually observed on the main account.
        user = await self.accounts.find_main(tg)
        observed = rights_snapshot(user, await self._squad_names(user) if user else [])
        full_cut = rec.get("action") == "expired" and user and user.expire_at == _parse_dt(rec.get("target"))
        active = bool(user and user.expire_at and user.expire_at > self.clock()
                      and (user.status or "").upper() not in ("DISABLED", "EXPIRED") and not full_cut)
        plan = observed.get("plan") if active else None
        if active and plan is None:
            raise ProvisioningError("refund obhod sync needs proven current main tariff")
        expiry = user.expire_at if user else None
        strict_sync = getattr(self.obhod, "on_main_refunded", None)
        if strict_sync is not None:
            confirmed = await strict_sync(tg, plan, expiry, trace_id)
            if confirmed is not True:
                await self._alert("Возврат: основной доступ изменен, синхронизация обхода требует проверки.\n"
                                  f"Telegram ID: {tg}", dedup_key=f"refund_obhod_pending:{tg}:{key}")
                raise ProvisioningError("refund obhod synchronization unconfirmed")
        elif not is_obhod_eligible_plan(plan):
            await self.obhod.on_main_revoked(tg, trace_id)
        else:
            await self.obhod.on_main_granted(tg, plan, expiry, trace_id)
        if row is not None:
            cfg = dict(row.config_data or {})
            rbs = dict(cfg.get("rollbacks") or {})
            rbs[key] = {**rbs[key], "sync_state": "applied"}
            cfg["rollbacks"] = rbs
            await self.repo.save_subscription(replace(row, config_data=cfg))
        await self._invalidate(tg)

    async def _rollback_locked(self, tg: int, *, months: int, reason: str, trace_id: str,
                               payment_id: Optional[int] = None) -> RollbackResult:
        user = await self.accounts.find_main(tg)
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        if user is None:
            return RollbackResult("no_account")
        rollbacks = (((row.config_data if row else {}) or {}).get("rollbacks") or {})
        key = next((k for k, v in rollbacks.items()
                    if payment_id is not None and v.get("payment_id") == payment_id), trace_id)
        rec = rollbacks.get(key)
        if rec and rec.get("state") == "applied":
            await self._rollback_sync(tg, row, key, trace_id)
            return RollbackResult(rec["action"], _parse_dt(rec.get("target")))
        names = await self._squad_names(user)
        if (user.expire_at and user.expire_at.year >= 2099) or any(is_manual_squad_name(n) for n in names):
            return await self._refund_review(tg, payment_id, user.expire_at, "lifetime/manual squad")
        now = self.clock()
        current = user.expire_at if user.expire_at and user.expire_at.year >= 2020 else None
        observed = rights_snapshot(user, names)
        settled = settle_landed_rollbacks(row, current, observed, exclude_key=key)
        if settled is not row:
            row = await self.repo.save_subscription(settled)
        cut = now + REVOKE_GRACE
        duration = _paid_duration(row, payment_id) if months > 0 else None
        if months > 0 and duration is None:
            return await self._refund_review(tg, payment_id, current, "missing paid interval")
        rights = None
        target = None
        if rec and rec.get("state") == "pending":
            base, recorded = _parse_dt(rec.get("base")), _parse_dt(rec.get("target"))
            if current == recorded:
                # Date equality alone does not prove a combined PATCH landed.
                rights = rec.get("rights_target")
                if rights is not None and (not _valid_rights(rights)
                                           or not _valid_rights(rec.get("rights_before"))):
                    return await self._refund_review(tg, payment_id, current, "malformed pending rights")
                if rights and not (_rights_match(observed, rights)
                                   or _rights_match(observed, rec.get("rights_before") or {})):
                    return await self._refund_review(tg, payment_id, current, "changed rights during refund")
                target = recorded
            elif current != base and not rec.get("not_landed_seen"):
                return await self._refund_review(tg, payment_id, current, "unknown previous PATCH outcome")
        if target is None:
            target = current - duration if current is not None and duration is not None else cut
        action = "shortened" if target > cut else "expired"
        if action == "expired":
            target = min(current, cut) if current is not None else cut
        # The gateway serializes expireAt to whole UTC seconds. Persist that
        # exact wire value so verification/retries recognize the same mutation.
        target = target.replace(microsecond=0)
        if months > 0 and rights is None:
            rights = refund_rights(row, payment_id, observed)
            if rights is None:
                return await self._refund_review(tg, payment_id, current, "missing/ambiguous rights history")
            if action == "shortened" and not rights.get("plan"):
                return await self._refund_review(tg, payment_id, current, "remaining compensation has no proven tariff")
        # A full cut of a newly created account need not invent a prior tariff.
        if rights and not rights.get("plan"):
            rights = observed
        rights = rights or observed
        if rights["device_limit"] is None and observed["device_limit"] is not None:
            return await self._refund_review(tg, payment_id, current, "cannot restore NULL device cap")
        if row is not None:
            cfg = dict(row.config_data or {})
            rbs = dict(cfg.get("rollbacks") or {})
            rbs[key] = {"state": "pending", "sync_state": "pending", "action": action,
                        "target": target.isoformat(), "base": _iso_or_none(current), "months": months,
                        "payment_id": payment_id, "at": now.isoformat(),
                        "rights_before": observed, "rights_target": rights}
            cfg["rollbacks"] = rbs
            row = await self.repo.save_subscription(replace(row, config_data=cfg))
        kwargs = {}
        if not _rights_match(observed, rights):
            kwargs["squads"] = [n for n in names if n not in managed_tariff_squad_names()] + rights["managed"]
            if rights["device_limit"] != observed["device_limit"]:
                if rights["device_limit"] is None:
                    return await self._refund_review(tg, payment_id, current, "cannot restore NULL device cap")
                kwargs["device_limit"] = rights["device_limit"]
                kwargs["refund_device_limit_from"] = observed["device_limit"]
        if current != target or kwargs:
            await self.remna.update_user(user.id, expire_at=target, current=user, **kwargs)
        # Always verify the complete combined mutation before marking applied.
        verified = await self.remna.get_user(user.id)
        if verified is None or verified.expire_at != target or not _rights_match(
                rights_snapshot(verified, await self._squad_names(verified)), rights):
            raise ProvisioningError("refund date/rights not verified")
        if row is not None:
            cfg = dict(row.config_data or {})
            rbs = dict(cfg.get("rollbacks") or {})
            rbs[key] = {**rbs[key], "state": "applied"}
            cfg["rollbacks"] = rbs
            cfg["rights_checkpoint"] = rights
            if action == "expired":
                cfg["revoked"] = {"at": now.isoformat(), "reason": reason[:200], "trace": trace_id}
            row = await self.repo.save_subscription(replace(
                row, active=action != "expired", provisioning_state="expired" if action == "expired" else "synced",
                valid_until=target, remnawave_expected_expire_at=target, config_data=cfg,
                plan_code=rights.get("plan") or row.plan_code,
                plan_name=get_plan_name(rights.get("plan") or row.plan_code)))
        await self._rollback_sync(tg, row, key, trace_id)
        return RollbackResult(action, target)


def _paid_duration(row, payment_id: Optional[int]) -> Optional[timedelta]:
    """Exact credited interval, including the max(now, previous expiry) rule."""
    if row is None or payment_id is None:
        return None
    rec = (row.config_data or {}).get("grants", {}).get(f"pay:{payment_id}")
    if not isinstance(rec, dict) or rec.get("state") != "applied" or rec.get("source") not in ("payment", "autorenew"):
        return None
    seconds = rec.get("credited_seconds")
    if seconds is not None:
        try:
            duration = timedelta(seconds=float(seconds))
        except (ValueError, TypeError, OverflowError):
            return None
        return duration if duration > timedelta(0) else None
    try:
        target, base, at = (_parse_dt(rec.get(k)) for k in ("target", "base", "at"))
    except (ValueError, TypeError):
        return None
    if target is None or at is None:
        return None
    start = max(base, at) if base is not None else at
    return target - start if target > start else None


def settle_landed_rollbacks(row, current: Optional[datetime], observed=None, *, exclude_key=None):
    """Observe the entire refund PATCH before another grant/credit can move it."""
    if row is None or current is None:
        return row
    cfg = dict(row.config_data or {})
    rollbacks = dict(cfg.get("rollbacks") or {})
    changed = False
    for key, rec in rollbacks.items():
        if key == exclude_key or rec.get("state") != "pending":
            continue
        target, base = _parse_dt(rec.get("target")), _parse_dt(rec.get("base"))
        if target == current:
            rights = rec.get("rights_target")
            if not _valid_rights(rights) or not _valid_rights(observed) or not _rights_match(observed, rights):
                raise ProvisioningError("pending refund date landed but rights are unverified")
            rollbacks[key] = {**rec, "state": "applied", "sync_state": "pending"}
            if rights:
                cfg["rights_checkpoint"] = rights
            if rights and rights.get("plan"):
                row = replace(row, plan_code=rights["plan"], plan_name=get_plan_name(rights["plan"]))
            row = replace(row, valid_until=current, remnawave_expected_expire_at=current,
                          active=rec.get("action") != "expired")
            changed = True
        elif base == current:
            rollbacks[key] = {**rec, "not_landed_seen": True}
            changed = True
        else:
            raise ProvisioningError("pending refund outcome unknown; resolve before changing access")
    if not changed:
        return row
    cfg["rollbacks"] = rollbacks
    return replace(row, config_data=cfg)


def _credit_key(trace_id: str) -> str:
    return f"credit:{trace_id}"


def _pending_marker(target: datetime, base: Optional[datetime]) -> str:
    return json.dumps({"s": "pending", "target": target.isoformat(), "base": _iso_or_none(base)})


def pending_credit(value: Optional[str]) -> Optional[dict]:
    """The pending record of a ``credit:<trace>`` marker value, None for an
    applied (``1``) or missing marker."""
    if not value or not value.startswith("{"):
        return None
    try:
        rec = json.loads(value)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) and rec.get("s") == "pending" else None


class CreditsMixin:
    async def _credit_once(self, trace_id: str) -> bool:
        from app.infra.redis.flags import set_once

        got = await set_once(_credit_key(trace_id), "1", ttl=CREDIT_MARKER_TTL_S)
        if got is False:
            logger.info(f"[{trace_id}] provisioning: credit already applied")
            return False
        return True

    async def add_days(self, telegram_id: int, days: int, *, trace_id: str, reason: str = "") -> Optional[datetime]:
        """Extend an EXISTING main account by ``days`` from max(now, expiry).
        Returns the new expiry; None when there is no account, a lifetime one
        or a DISABLED one (nothing credited). Runs under the per-user
        provisioning lock. Idempotent per ``trace_id`` also when the PATCH
        response is lost: the panel is re-read before a failure is declared
        (raises ProvisioningError-compatible errors: the original one when the
        days did not land, CreditOutcomeUnknown when the panel can not tell).
        Squads and limits are not touched."""
        if int(days) <= 0:
            raise ValueError("days must be positive")
        tg = int(telegram_id)
        lock = await self._lock(tg)  # same per-user lock as grant/revoke
        try:
            return await self._add_days_locked(tg, int(days), trace_id=trace_id, reason=reason)
        finally:
            await self._unlock(lock)

    async def _days_landed(self, panel_id: int, target: datetime) -> Optional[bool]:
        """Re-read the panel after a failed PATCH: True when the expiry reached
        ``target`` (within VERIFY_TOLERANCE), False when it did not, None when
        the panel can not be read."""
        delay = getattr(self, "late_patch_delay_s", 0) or 0
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            probe = await self.remna.get_user(panel_id)
        except Exception:  # noqa: BLE001
            return None
        if probe is None:
            return None
        exp = probe.expire_at
        return bool(exp is not None and exp >= target - VERIFY_TOLERANCE)

    async def _finish_credit(self, tg: int, trace_id: str, new_expiry: datetime) -> datetime:
        from app.infra.redis.flags import set_value

        await set_value(_credit_key(trace_id), "1", ttl=CREDIT_MARKER_TTL_S)
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        if row is not None and row.active:
            await self.repo.save_subscription(replace(row, valid_until=new_expiry,
                                                      remnawave_expected_expire_at=new_expiry))
        await self._invalidate(tg)
        return new_expiry

    async def _add_days_locked(self, tg: int, days: int, *, trace_id: str, reason: str) -> Optional[datetime]:
        from app.infra.redis.flags import delete_key, get_value, set_once, set_value

        user = await self.accounts.find_main(tg)
        if user is None:
            return None
        if user.expire_at and user.expire_at.year >= 2099:
            return None  # lifetime: nothing to add (callers count it as skipped)
        if (user.status or "").upper() == "DISABLED":
            return None
        now = self.clock()
        current = user.expire_at if (user.expire_at and user.expire_at.year >= 2020) else None
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        settled = settle_landed_rollbacks(row, current, rights_snapshot(user, await self._squad_names(user)))
        if settled is not row:
            await self.repo.save_subscription(settled)
        target = compute_target(Entitlement(plan_code="-", source=EntitlementSource.ADMIN, days=int(days)),
                                current, now)
        key = _credit_key(trace_id)
        got = await set_once(key, _pending_marker(target, current), ttl=CREDIT_MARKER_TTL_S)
        if got is False:
            rec = pending_credit(await get_value(key))
            if rec is None:
                logger.info(f"[{trace_id}] provisioning: credit already applied")
                return user.expire_at
            recorded = _parse_dt(rec.get("target"))
            if recorded is not None and current is not None and current >= recorded - VERIFY_TOLERANCE:
                # An earlier attempt of this trace timed out but its PATCH landed.
                logger.warning(f"[{trace_id}] provisioning: +{days}d tg={tg} found landed on retry, no write")
                return await self._finish_credit(tg, trace_id, current)
            await set_value(key, _pending_marker(target, current), ttl=CREDIT_MARKER_TTL_S)
        try:
            updated = await self.remna.update_user(user.id, expire_at=target, current=user)
        except Exception as e:
            landed = await self._days_landed(user.id, target)
            if landed is None:
                # Unknown: keep the pending marker, a retry of this trace re-reads the panel.
                logger.error(f"[{trace_id}] provisioning: +{days}d tg={tg} outcome unknown "
                             f"({type(e).__name__}, panel re-read failed)")
                raise CreditOutcomeUnknown(f"add_days outcome unknown: {type(e).__name__}") from e
            if not landed:
                # Not applied: drop the marker so a retry of the same trace_id credits.
                await delete_key(key)
                raise
            logger.warning(f"[{trace_id}] provisioning: +{days}d tg={tg} landed despite {type(e).__name__}")
            new_expiry = target
        else:
            new_expiry = updated.expire_at or target
        await self._finish_credit(tg, trace_id, new_expiry)
        logger.info(f"[{trace_id}] provisioning: +{days}d tg={tg} reason={reason[:80]!r}")
        return new_expiry

    async def add_traffic(self, telegram_id: int, extra_bytes: int, *, trace_id: str,
                          sub_kind: SubKind = SubKind.OBHOD) -> Optional[int]:
        """Raise the traffic cap of the obhod (default) or main account by
        ``extra_bytes``. 0 (unlimited) stays unlimited. Returns the new cap."""
        if int(extra_bytes) <= 0:
            raise ValueError("extra_bytes must be positive")
        tg = int(telegram_id)
        if SubKind(sub_kind) is SubKind.OBHOD:
            row = await self.repo.get_subscription(tg, SubKind.OBHOD)
            pid = numeric_panel_id(row.remna_user_id) if row and row.active else None
            user = await self.remna.get_user(pid) if pid else None
        else:
            user = await self.accounts.find_main(tg)
        if user is None:
            return None
        cur = int(user.traffic_limit_bytes or 0)
        if cur == 0:
            return 0
        if not await self._credit_once(trace_id):
            return cur
        new = cur + int(extra_bytes)
        await self.remna.update_user(user.id, traffic_limit_bytes=new, current=user)
        await self._invalidate(tg)
        return new

    async def add_devices(self, telegram_id: int, extra: int, *, trace_id: str) -> Optional[int]:
        """Raise the HWID limit of the main account by ``extra``. 0 (unlimited)
        and NULL (panel fallback) are left alone. Returns the new limit."""
        if int(extra) <= 0:
            raise ValueError("extra must be positive")
        tg = int(telegram_id)
        lock = await self._lock(tg)
        try:
            return await self._add_devices_locked(tg, extra, trace_id=trace_id)
        finally:
            await self._unlock(lock)

    async def _add_devices_locked(self, tg: int, extra: int, *, trace_id: str) -> Optional[int]:
        user = await self.accounts.find_main(tg)
        if user is None or user.device_limit in (None, 0):
            return user.device_limit if user else None
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        settled = settle_landed_rollbacks(row, user.expire_at,
                                         rights_snapshot(user, await self._squad_names(user)))
        if settled is not row:
            await self.repo.save_subscription(settled)
        if not await self._credit_once(trace_id):
            return user.device_limit
        new = int(user.device_limit) + int(extra)
        await self.remna.update_user(user.id, device_limit=new, current=user)
        await self._invalidate(tg)
        return new
