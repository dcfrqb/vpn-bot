"""RemnawaveReconciler — фоновый воркер, добивающий sync БД↔Remnawave.

Закрывает дыру: webhook от YooKassa может провалить Phase B (Remnawave недоступен),
а ретраи YooKassa ограничены по времени. Reconciler страхует.

Два режима скана (внутри одного цикла):

1. **Shallow** (каждый interval): берёт active подписки с
   `provisioning_state IN ('pending', 'failed')` и пытается дотянуть sync через
   `resync_subscription_to_remnawave`. С backoff'ом по `last_provisioning_attempt_at`,
   чтобы не толкать одно и то же чаще раза в N минут.

2. **Deep** (раз в DEEP_SCAN_INTERVAL): берёт active+synced подписки, дёргает
   Remnawave батчами и сравнивает actual `expireAt` с `valid_until`. При расхождении
   > tolerance — сбрасывает `provisioning_state='failed'`, и shallow-скан подхватит
   на следующей итерации.

Не отправляет уведомления юзеру — только синкает Remnawave. При исчерпании
`MAX_ATTEMPTS` шлёт алерт админам в Telegram (rate-limited 1/24h на subscription).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from sqlalchemy import select, and_, or_

from app.config import settings
from app.db.session import SessionLocal
from app.db.models import Subscription
from app.logger import logger
from app.remnawave.client import RemnaClient


# Backoff: не пытаться синкать чаще, чем раз в N секунд
RESYNC_BACKOFF_SECONDS = 120

# Глубокий скан реже, чем shallow
SHALLOW_INTERVAL_SECONDS = 120
DEEP_SCAN_INTERVAL_SECONDS = 1800

# Сколько подписок обработать за один shallow-скан (rate limit Remnawave API)
SHALLOW_BATCH_SIZE = 20
DEEP_BATCH_SIZE = 50

# Максимум попыток per-subscription до алерта
MAX_RESYNC_ATTEMPTS = 5

# Допуск при сравнении expireAt в deep-скане
DEEP_SCAN_TOLERANCE_SECONDS = 300


class RemnawaveReconciler:
    """Периодически добивает sync local DB → Remnawave.

    Запускается в том же процессе, что бот (рядом с SubscriptionChecker).
    """

    def __init__(self, bot, shallow_interval: int = SHALLOW_INTERVAL_SECONDS):
        self.bot = bot
        self.shallow_interval = shallow_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._last_deep_scan_at: Optional[datetime] = None

    def start(self) -> None:
        if not SessionLocal:
            logger.info("RemnawaveReconciler: DB not configured, not starting")
            return
        self.running = True
        self._task = asyncio.create_task(self._run())
        logger.info(
            f"RemnawaveReconciler: started (shallow={self.shallow_interval}s, "
            f"deep={DEEP_SCAN_INTERVAL_SECONDS}s)"
        )

    def stop(self) -> None:
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        try:
            await asyncio.sleep(15)  # дать боту прогреться
        except asyncio.CancelledError:
            return
        while self.running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"RemnawaveReconciler: run_once error: {e}")
            try:
                await asyncio.sleep(self.shallow_interval)
            except asyncio.CancelledError:
                break

    async def run_once(self) -> Dict[str, Any]:
        """Один цикл reconciler'а. Возвращает счётчики (для тестов и метрик)."""
        result = {"shallow_found": 0, "shallow_synced": 0, "shallow_failed": 0,
                 "deep_scanned": 0, "deep_desynced": 0}

        result.update(await self._shallow_scan())

        now = datetime.utcnow()
        need_deep = (
            self._last_deep_scan_at is None
            or (now - self._last_deep_scan_at).total_seconds() >= DEEP_SCAN_INTERVAL_SECONDS
        )
        if need_deep:
            self._last_deep_scan_at = now
            try:
                deep = await self._deep_scan()
                result.update(deep)
            except Exception as e:
                logger.error(f"RemnawaveReconciler: deep_scan error: {e}")

        if any(v for k, v in result.items() if v):
            logger.info(f"reconciler_cycle: {result}")
        return result

    async def _shallow_scan(self) -> Dict[str, int]:
        """Найти pending/failed active-подписки и попытаться синкнуть."""
        from app.services.cache import acquire_provision_lock, release_provision_lock
        from app.services.payments.yookassa import resync_subscription_to_remnawave

        out = {"shallow_found": 0, "shallow_synced": 0, "shallow_failed": 0}

        if not SessionLocal:
            return out

        now = datetime.utcnow()
        backoff_threshold = now - timedelta(seconds=RESYNC_BACKOFF_SECONDS)

        async with SessionLocal() as session:
            # Не синкать подписки, у которых срок уже истёк: Remnawave всё равно
            # вернёт EXPIRED — verify провалится, попадём в alert-цикл. Истёкшие
            # подхватит deep_scan и переведёт в provisioning_state='expired'.
            stmt = (
                select(Subscription)
                .where(
                    Subscription.active == True,
                    Subscription.provisioning_state.in_(["pending", "failed"]),
                    or_(
                        Subscription.valid_until.is_(None),
                        Subscription.valid_until > now,
                    ),
                    or_(
                        Subscription.last_provisioning_attempt_at.is_(None),
                        Subscription.last_provisioning_attempt_at < backoff_threshold,
                    ),
                )
                .order_by(Subscription.last_provisioning_attempt_at.asc().nullsfirst())
                .limit(SHALLOW_BATCH_SIZE)
            )
            rows = await session.execute(stmt)
            subs = list(rows.scalars().all())

        out["shallow_found"] = len(subs)
        if not subs:
            return out

        for sub in subs:
            lock_key = f"sub_{sub.id}"
            ok = await acquire_provision_lock(lock_key)
            if not ok:
                continue
            try:
                synced = await resync_subscription_to_remnawave(sub.id)
                if synced:
                    out["shallow_synced"] += 1
                else:
                    out["shallow_failed"] += 1
                    await self._maybe_alert_exhausted(sub.id)
            except Exception as e:
                logger.error(
                    f"reconciler_resync_unhandled: subscription_id={sub.id} err={e}"
                )
                out["shallow_failed"] += 1
            finally:
                await release_provision_lock(lock_key)

        return out

    async def _deep_scan(self) -> Dict[str, int]:
        """Сверяет фактический Remnawave expireAt с локальным valid_until."""
        out = {"deep_scanned": 0, "deep_desynced": 0}
        if not SessionLocal:
            return out

        async with SessionLocal() as session:
            stmt = (
                select(Subscription)
                .where(
                    Subscription.active == True,
                    Subscription.provisioning_state == "synced",
                    Subscription.remna_user_id.isnot(None),
                    Subscription.valid_until.isnot(None),
                )
                .limit(DEEP_BATCH_SIZE)
            )
            rows = await session.execute(stmt)
            subs = list(rows.scalars().all())

        out["deep_scanned"] = len(subs)
        if not subs:
            return out

        client = RemnaClient()
        try:
            for sub in subs:
                if not sub.remna_user_id or not sub.valid_until:
                    continue
                try:
                    data = await client.get_user_by_id(str(sub.remna_user_id))
                except Exception as e:
                    logger.warning(
                        f"reconciler_deep_scan: get_user_by_id failed "
                        f"subscription_id={sub.id} err={e}"
                    )
                    continue
                raw = data.get("response", data) if isinstance(data, dict) else {}
                if not isinstance(raw, dict):
                    raw = {}
                status = raw.get("status")
                expire_raw = raw.get("expireAt")
                actual: Optional[datetime] = None
                if expire_raw:
                    try:
                        es = str(expire_raw).replace("Z", "+00:00")
                        actual = datetime.fromisoformat(es)
                        if actual.tzinfo is not None:
                            actual = actual.astimezone(timezone.utc).replace(tzinfo=None)
                    except Exception:
                        actual = None

                expected = sub.valid_until
                if expected.tzinfo is not None:
                    expected = expected.astimezone(timezone.utc).replace(tzinfo=None)

                now_naive = datetime.utcnow()

                # Честная истёкшая подписка: Remnawave EXPIRED + valid_until уже
                # в прошлом — юзер просто не продлил. Не desync, а нормальный
                # конец жизни. Снимаем active, чтобы реконсилер забыл про неё.
                if status == "EXPIRED" and expected and expected <= now_naive:
                    out["deep_desynced"] += 1
                    logger.info(
                        f"reconciler_natural_expiry: subscription_id={sub.id} "
                        f"tg_id={sub.telegram_user_id} valid_until={expected.isoformat()}"
                    )
                    await self._mark_naturally_expired(sub.id)
                    continue

                desync_reason = None
                if status == "EXPIRED":
                    desync_reason = "remnawave status=EXPIRED but local active=true"
                elif actual is None:
                    desync_reason = "remnawave expireAt missing"
                else:
                    # ВАЖНО: drift в сторону БОЛЬШЕ (actual > expected) — НЕ desync.
                    # Это означает, что юзер получил больше срока (ручное продление в
                    # панели, lifetime grant, или разные значения округления). Resync
                    # бы УКОРОТИЛ срок — деструктивно. Помечаем failed только если
                    # actual < expected (юзер недополучил).
                    delta = (actual - expected).total_seconds()
                    if delta < -DEEP_SCAN_TOLERANCE_SECONDS:
                        desync_reason = (
                            f"expireAt shortfall: actual={actual.isoformat()} "
                            f"expected={expected.isoformat()} delta={delta:.0f}s "
                            f"(remnawave behind local — needs resync)"
                        )

                if desync_reason:
                    out["deep_desynced"] += 1
                    logger.warning(
                        f"reconciler_desync_detected: subscription_id={sub.id} "
                        f"tg_id={sub.telegram_user_id} reason={desync_reason!r}"
                    )
                    await self._mark_failed_for_resync(sub.id, desync_reason)
        finally:
            try:
                await client.close()
            except Exception:
                pass

        return out

    async def _mark_naturally_expired(self, subscription_id: int) -> None:
        """Снимает active и переводит в provisioning_state='expired' для подписок,
        у которых valid_until прошёл и Remnawave честно показывает EXPIRED.

        После этого подписка выпадает из active-фильтров shallow/deep сканов,
        алертов не будет. Renewal-flow создаёт новую подписку штатно.
        """
        if not SessionLocal:
            return
        try:
            async with SessionLocal() as session:
                sub_r = await session.execute(
                    select(Subscription).where(Subscription.id == subscription_id)
                )
                sub = sub_r.scalar_one_or_none()
                if sub:
                    sub.active = False
                    sub.provisioning_state = "expired"
                    sub.last_provisioning_error = None
                    await session.commit()
        except Exception as e:
            logger.error(
                f"reconciler_mark_expired: subscription_id={subscription_id} err={e}"
            )

    async def _mark_failed_for_resync(self, subscription_id: int, reason: str) -> None:
        """Помечает подписку failed; shallow-скан подхватит на следующей итерации."""
        if not SessionLocal:
            return
        try:
            async with SessionLocal() as session:
                sub_r = await session.execute(
                    select(Subscription).where(Subscription.id == subscription_id)
                )
                sub = sub_r.scalar_one_or_none()
                if sub:
                    sub.provisioning_state = "failed"
                    sub.last_provisioning_error = f"deep_scan: {reason}"[:500]
                    # last_provisioning_attempt_at оставляем — backoff отработает
                    await session.commit()
        except Exception as e:
            logger.error(f"reconciler_mark_failed: subscription_id={subscription_id} err={e}")

    async def _maybe_alert_exhausted(self, subscription_id: int) -> None:
        """Шлёт алерт админам, если подписка не синкается > MAX_RESYNC_ATTEMPTS подряд.

        Состояние счётчика держим в Redis (rate-limit 1/24h per subscription).
        """
        if not getattr(settings, "ADMINS", None):
            return
        try:
            from app.services.cache import get_redis_client
            r = get_redis_client()
            if not r:
                return
            attempts_key = f"reconciler_attempts:sub:{subscription_id}"
            alert_key = f"reconciler_alert_sent:sub:{subscription_id}"

            attempts = await r.incr(attempts_key)
            await r.expire(attempts_key, 86400)
            if int(attempts) < MAX_RESYNC_ATTEMPTS:
                return
            already = await r.get(alert_key)
            if already:
                return
            await r.set(alert_key, "1", ex=86400)
        except Exception as e:
            logger.debug(f"reconciler_alert_state: {e}")
            return

        try:
            async with SessionLocal() as session:
                sub_r = await session.execute(
                    select(Subscription).where(Subscription.id == subscription_id)
                )
                sub = sub_r.scalar_one_or_none()
                if not sub:
                    return
                last_err = (sub.last_provisioning_error or "—")[:300]
                tg_id = sub.telegram_user_id
                remna_id = sub.remna_user_id or "—"
                valid_until = sub.valid_until.isoformat() if sub.valid_until else "—"
        except Exception:
            return

        text = (
            "⚠️ <b>Reconciler: подписка застряла</b>\n\n"
            f"subscription_id: <code>{subscription_id}</code>\n"
            f"tg_id: <code>{tg_id}</code>\n"
            f"remna_user_id: <code>{remna_id}</code>\n"
            f"valid_until: <code>{valid_until}</code>\n"
            f"last_error: <code>{last_err}</code>\n\n"
            f"Не синкается {MAX_RESYNC_ATTEMPTS}+ попыток подряд."
        )
        for admin_id in settings.ADMINS:
            try:
                await self.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"reconciler_alert_send_failed: admin={admin_id} err={e}")
