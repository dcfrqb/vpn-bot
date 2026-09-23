"""Panel -> DB reconciler over ALL users (stream B, review 06 M2).

Runs every RECONCILER_INTERVAL_S (job ``panel_sync``, TASK_PANEL_SYNC_ENABLED).
It only ever writes the DB, never the panel:

1. Pull every panel user once (GET /api/users, offset paging from 0). If the
   listing breaks half way, the pass stops before any DB write: a partial
   listing must not look like "user deleted".
2. Walk every ACTIVE main row (keyset paging by id, no fixed 50-row window):
   - rows in ``pending``/``failed`` belong to a grant or the 2.x resync: skipped;
   - panel date LATER than ``valid_until`` (manual extension, friend grant):
     pulled forward into ``valid_until`` (and ``is_lifetime`` for 2099);
   - panel account not live and both dates in the past: row deactivated
     (``active=false, provisioning_state='expired'``); the obhod lifecycle job
     then turns the obhod off;
   - panel date EARLIER than the DB (a shortfall): reported only. The panel
     date is never touched here (never shorten, never write squads/limits);
   - DISABLED in the panel, no panel account, legacy UUID id: counted only;
   - grace (stream C): rows with grace_state active/ended and users on the
     GRACE_SQUAD are skipped, so the grace end never becomes paid time.
3. One admin report when something changed or needs a look (dedup per day).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.domain.models import AdminTopic, PanelUser, SubKind
from app.logger import logger
from app.services.accounts import AccountsRepo, SqlAccountsRepo, iter_all_subscriptions, numeric_panel_id

TOLERANCE = timedelta(minutes=5)
GRACE_STATES = ("active", "ended")  # subscriptions.grace_state (stream C)
LIVE = ("ACTIVE", "LIMITED")
PAGE_SIZE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SyncReport:
    panel_users: int = 0
    rows: int = 0
    pulled_forward: int = 0
    deactivated: int = 0
    shortfall: int = 0
    disabled: int = 0
    missing: int = 0
    legacy_id: int = 0
    skipped_in_flight: int = 0
    grace: int = 0
    aborted: bool = False
    shortfall_ids: list = field(default_factory=list)

    def needs_admin(self) -> bool:
        return bool(self.pulled_forward or self.deactivated or self.shortfall or self.missing or self.aborted)

    def text(self) -> str:
        if self.aborted:
            return "Сверка с панелью прервана: панель не отдала полный список пользователей. БД не менялась."
        lines = [
            "Сверка БД с панелью",
            f"Пользователей в панели: {self.panel_users}, активных строк: {self.rows}",
            f"Дата подтянута из панели: {self.pulled_forward}",
            f"Истекшие строки закрыты: {self.deactivated}",
        ]
        if self.shortfall:
            ids = ", ".join(str(i) for i in self.shortfall_ids[:10])
            lines.append(f"В панели срок меньше, чем в БД (панель не трогаю): {self.shortfall} [{ids}]")
        if self.missing:
            lines.append(f"Нет аккаунта в панели: {self.missing}")
        if self.disabled:
            lines.append(f"Отключены в панели вручную: {self.disabled}")
        return "\n".join(lines)


class PanelSync:
    def __init__(self, remna, repo: Optional[AccountsRepo] = None, *, notifier: Any = None,
                 clock: Callable[[], datetime] = _now, settings: Any = None):
        self.remna = remna
        self.repo = repo or SqlAccountsRepo()
        self.notifier = notifier
        self.clock = clock
        self._settings = settings

    @property
    def grace_squad(self) -> Optional[str]:
        settings = self._settings
        if settings is None:
            from app.config import settings
        return (getattr(settings, "GRACE_SQUAD", None) or "").strip() or None

    async def _panel_index(self) -> dict[int, PanelUser]:
        index: dict[int, PanelUser] = {}
        async for u in self.remna.iter_users(page_size=PAGE_SIZE):
            if u.id:
                index[u.id] = u
        return index

    async def run(self) -> SyncReport:
        report = SyncReport()
        try:
            index = await self._panel_index()
        except Exception as e:  # noqa: BLE001
            report.aborted = True
            logger.warning(f"panel_sync: panel listing failed ({type(e).__name__}), no DB writes")
            await self._notify(report)
            return report
        report.panel_users = len(index)
        now = self.clock()
        grace_squad = self.grace_squad
        async for row in iter_all_subscriptions(self.repo, sub_kind=SubKind.MAIN, active=True):
            if row.sub_kind != SubKind.MAIN.value:
                continue
            report.rows += 1
            if row.provisioning_state in ("pending", "failed"):
                report.skipped_in_flight += 1
                continue
            if row.grace_state in GRACE_STATES:
                report.grace += 1  # the panel date is the grace end, not paid time
                continue
            pid = numeric_panel_id(row.remna_user_id)
            if pid is None:
                report.legacy_id += 1
                continue
            user = index.get(pid)
            if user is None:
                report.missing += 1
                continue
            if grace_squad and grace_squad in user.squads:
                report.grace += 1
                continue
            status = (user.status or "").upper()
            panel_exp = user.expire_at
            db_exp = row.valid_until
            if status == "DISABLED":
                report.disabled += 1
                continue
            live = status in LIVE and panel_exp is not None and panel_exp > now
            if live and panel_exp is not None and (db_exp is None or panel_exp > db_exp + TOLERANCE):
                await self.repo.save_subscription(replace(
                    row, valid_until=panel_exp, is_lifetime=panel_exp.year >= 2099,
                    remnawave_synced_at=now,
                ))
                report.pulled_forward += 1
                logger.info(
                    f"panel_sync: tg={row.telegram_user_id} valid_until "
                    f"{db_exp.isoformat() if db_exp else None} -> {panel_exp.isoformat()} (panel ahead)"
                )
                continue
            if not live:
                db_past = row.is_lifetime is False and (db_exp is None or db_exp <= now)
                if db_past:
                    await self.repo.save_subscription(replace(
                        row, active=False, provisioning_state="expired", remnawave_synced_at=now,
                    ))
                    report.deactivated += 1
                    logger.info(f"panel_sync: tg={row.telegram_user_id} expired in panel and DB, row closed")
                else:
                    report.shortfall += 1
                    report.shortfall_ids.append(row.telegram_user_id)
                continue
            if db_exp is not None and panel_exp is not None and panel_exp < db_exp - TOLERANCE:
                report.shortfall += 1
                report.shortfall_ids.append(row.telegram_user_id)
        logger.info(
            f"panel_sync: users={report.panel_users} rows={report.rows} pulled={report.pulled_forward} "
            f"closed={report.deactivated} shortfall={report.shortfall} missing={report.missing} "
            f"disabled={report.disabled} legacy={report.legacy_id} in_flight={report.skipped_in_flight} "
            f"grace={report.grace}"
        )
        await self._notify(report)
        return report

    async def _notify(self, report: SyncReport) -> None:
        if self.notifier is None or not report.needs_admin():
            return
        day = self.clock().date().isoformat()
        sig = f"{report.pulled_forward}:{report.deactivated}:{report.shortfall}:{report.missing}:{int(report.aborted)}"
        try:
            await self.notifier.notify_admins(
                AdminTopic.PANEL, report.text(), dedup_key=f"panel_sync:{day}:{sig}",
                dedup_ttl=24 * 3600, disable_notification=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"panel_sync: report failed ({type(e).__name__})")
