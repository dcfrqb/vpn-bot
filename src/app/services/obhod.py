"""Obhod lifecycle against the live panel (stream B, review 06 H4).

The obhod account (separate panel user, squad ``obhod``, monthly traffic cap,
no telegramId) lives as long as the main Pro. 2.x only changed it from
main-row events, so orphans stayed active forever and paid traffic packages
never expired. This module adds:

``package_gate(tg)``: may the user buy an obhod traffic package right now?
  The DB row must be active AND the obhod panel user must be live (ACTIVE or
  LIMITED, expireAt in the future). LIMITED is the normal case for buying a
  package (the cap is used up). Panel down -> False (no payment into nowhere).

``ObhodLifecycle.run``: the daily job body, for every active obhod row:
  - ORPHAN = the owner has no live main Pro: main row missing, main row
    inactive/expired while the main panel account is not live (or is in
    grace), or the main plan is not Pro. Owner decision 23.09.2026: orphans
    are left exactly as they are. The job only counts them (log + one admin
    summary with the counts, deduplicated); no DB or panel write. Only with
    OBHOD_ORPHAN_DEACTIVATE_ENABLED=true (default false) are they turned off;
  - main row inactive but the main panel account live (manual extension) ->
    kept, admin alert once;
  - owner with a live main Pro: panel obhod user gone / EXPIRED / DISABLED /
    past expireAt -> row inactive (the panel user is disabled only if it is
    still live); traffic package past ``package_until`` -> cap back to the
    base (trafficLimitBytes only), package fields cleared.
Squads and device limits are never written here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.domain.models import AdminTopic, PanelUser, SubKind
from app.domain.plans import OBHOD_TRAFFIC_LIMIT_STRATEGY, is_obhod_eligible_plan, obhod_base_limit_bytes
from app.logger import logger
from app.services.accounts import AccountsRepo, SqlAccountsRepo, SubRow, iter_all_subscriptions, numeric_panel_id

LIVE = ("ACTIVE", "LIMITED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _live(user: Optional[PanelUser], now: datetime) -> bool:
    return bool(user and (user.status or "").upper() in LIVE and user.expire_at and user.expire_at > now)


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass
class ObhodReport:
    scanned: int = 0
    deactivated: int = 0
    packages_expired: int = 0
    orphans: int = 0
    orphans_deactivated: int = 0
    orphan_reasons: dict = field(default_factory=dict)
    kept_manual: int = 0
    errors: int = 0
    details: list = field(default_factory=list)

    def changed(self) -> bool:
        return bool(self.deactivated or self.packages_expired)

    def text(self) -> str:
        lines = [
            "Обход: ежедневная проверка",
            f"Активных строк: {self.scanned}",
            f"Выключено: {self.deactivated}",
            f"Пакетов трафика истекло: {self.packages_expired}",
        ]
        if self.orphans:
            why = ", ".join(f"{k}: {v}" for k, v in sorted(self.orphan_reasons.items()))
            if self.orphans_deactivated:
                lines.append(f"Обход без основного Pro, выключено по флагу: {self.orphans_deactivated} ({why})")
            else:
                lines.append(f"Обход без основного Pro, не трогаю (решить вручную): {self.orphans} ({why})")
        if self.errors:
            lines.append(f"Ошибок панели: {self.errors}")
        return "\n".join(lines)


class ObhodLifecycle:
    def __init__(self, remna=None, repo: Optional[AccountsRepo] = None, *, notifier: Any = None,
                 clock: Callable[[], datetime] = _now, deactivate_orphans: Optional[bool] = None):
        if remna is None:
            from app.infra.remnawave.gateway import HttpRemnaGateway

            remna = HttpRemnaGateway()
        self.remna = remna
        self.repo = repo or SqlAccountsRepo()
        self.notifier = notifier
        self.clock = clock
        if deactivate_orphans is None:
            from app.config import settings

            deactivate_orphans = bool(getattr(settings, "OBHOD_ORPHAN_DEACTIVATE_ENABLED", False))
        self.deactivate_orphans = deactivate_orphans

    async def package_gate(self, telegram_id: int) -> bool:
        row = await self.repo.get_subscription(int(telegram_id), SubKind.OBHOD)
        pid = numeric_panel_id(row.remna_user_id) if row and row.active else None
        if not pid:
            return False
        try:
            user = await self.remna.get_user(pid)
        except Exception as e:  # noqa: BLE001 - refuse when we cannot check
            logger.warning(f"obhod gate: panel unreachable tg={telegram_id} ({type(e).__name__})")
            return False
        return _live(user, self.clock())

    async def _alert(self, text: str, key: str) -> None:
        if self.notifier is None:
            return
        try:
            await self.notifier.notify_admins(AdminTopic.PANEL, text, dedup_key=key, dedup_ttl=7 * 24 * 3600)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"obhod lifecycle: alert failed ({type(e).__name__})")

    async def _deactivate(self, row: SubRow, user: Optional[PanelUser], reason: str, report: ObhodReport) -> None:
        if user is not None and _live(user, self.clock()):
            try:
                await self.remna.disable_user(user.id)
            except Exception as e:  # noqa: BLE001 - the row still goes inactive; the panel date ends it anyway
                logger.warning(f"obhod lifecycle: disable failed panel_id={user.id} ({type(e).__name__})")
        cfg = dict(row.config_data or {})
        cfg["deactivated"] = {"at": self.clock().isoformat(), "reason": reason}
        await self.repo.save_subscription(replace(row, active=False, provisioning_state="expired", config_data=cfg))
        report.deactivated += 1
        report.details.append((row.telegram_user_id, reason))
        logger.info(f"obhod lifecycle: deactivated tg={row.telegram_user_id} reason={reason}")

    async def _main_live(self, main: SubRow, now: datetime) -> bool:
        if main.grace_state in ("active", "ended"):
            return False  # grace access (stream C) is not a paid Pro
        pid = numeric_panel_id(main.remna_user_id)
        if not pid:
            return False
        return _live(await self.remna.get_user(pid), now)

    async def _expire_package(self, row: SubRow, user: PanelUser, now: datetime, report: ObhodReport) -> SubRow:
        cfg = dict(row.config_data or {})
        until = _parse(cfg.get("package_until"))
        if not cfg.get("package") or until is None or until > now:
            return row
        base = obhod_base_limit_bytes()
        await self.remna.update_user(user.id, traffic_limit_bytes=base,
                                     traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY, current=user)
        history = list(cfg.get("package_history") or [])
        history.append({"package": cfg.get("package"), "until": cfg.get("package_until"), "ended_at": now.isoformat()})
        for k in ("package", "package_until", "package_limit_bytes"):
            cfg.pop(k, None)
        cfg["package_history"] = history[-10:]
        report.packages_expired += 1
        logger.info(f"obhod lifecycle: package expired tg={row.telegram_user_id}, cap back to base")
        return await self.repo.save_subscription(replace(row, config_data=cfg))

    async def _orphan_reason(self, tg: int, now: datetime, report: ObhodReport) -> Optional[str]:
        """Why this obhod has no live main Pro (None = it has one)."""
        main = await self.repo.get_subscription(tg, SubKind.MAIN)
        if main is None:
            return "no_main"
        if not main.active or (not main.is_lifetime and main.valid_until and main.valid_until <= now):
            if await self._main_live(main, now):
                report.kept_manual += 1
                await self._alert(
                    "Обход оставлен: основная строка в БД неактивна, но аккаунт в панели живой "
                    f"(ручное продление?).\nTelegram ID: {tg}",
                    key=f"obhod_main_manual:{tg}",
                )
                return None
            return "main_inactive"
        if main.plan_code and not is_obhod_eligible_plan(main.plan_code):
            return "main_not_pro"
        return None

    async def _one(self, row: SubRow, now: datetime, report: ObhodReport) -> None:
        tg = row.telegram_user_id
        pid = numeric_panel_id(row.remna_user_id)
        orphan = await self._orphan_reason(tg, now, report)
        if orphan is not None:
            report.orphans += 1
            report.orphan_reasons[orphan] = report.orphan_reasons.get(orphan, 0) + 1
            report.details.append((tg, f"orphan:{orphan}"))
            if not self.deactivate_orphans:
                logger.info(f"obhod lifecycle: orphan tg={tg} reason={orphan}, left as is (report only)")
                return
            user = await self.remna.get_user(pid) if pid else None
            await self._deactivate(row, user, f"orphan:{orphan}", report)
            report.orphans_deactivated += 1
            return
        user = await self.remna.get_user(pid) if pid else None
        if user is None or not _live(user, now):
            status = (user.status if user else "missing") or "unknown"
            await self._deactivate(row, user, f"panel:{status}", report)
            return
        await self._expire_package(row, user, now, report)

    async def run(self) -> ObhodReport:
        report = ObhodReport()
        now = self.clock()
        async for row in iter_all_subscriptions(self.repo, sub_kind=SubKind.OBHOD, active=True):
            if row.sub_kind != SubKind.OBHOD.value:
                continue
            report.scanned += 1
            try:
                await self._one(row, now, report)
            except Exception as e:  # noqa: BLE001 - one row must not stop the pass
                report.errors += 1
                logger.warning(f"obhod lifecycle: row {row.id} failed ({type(e).__name__})")
        logger.info(
            f"obhod lifecycle: scanned={report.scanned} deactivated={report.deactivated} "
            f"packages_expired={report.packages_expired} orphans={report.orphans} errors={report.errors}"
        )
        if self.notifier is not None and (report.changed() or report.errors or report.orphans):
            # one summary per distinct result (a daily report-only orphan count repeats for a week at most)
            key = (f"obhod_report:{report.deactivated}:{report.packages_expired}:{report.orphans}:"
                   f"{report.orphans_deactivated}:{report.errors}")
            try:
                await self.notifier.notify_admins(AdminTopic.PANEL, report.text(), disable_notification=True,
                                                  dedup_key=key, dedup_ttl=7 * 24 * 3600)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"obhod lifecycle: report failed ({type(e).__name__})")
        return report
