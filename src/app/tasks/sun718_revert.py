"""
Sun718RevertTask — периодическая таска возврата squad после /sun718 для не-Pro юзеров.

Логика:
- При активации /sun718 для активного не-Pro юзера (lite/standard/premium/basic)
  ему выдаётся +5 дней Pro поверх expireAt + меняется squad на Pro.
  В promo Payment записывается metadata.revert_at = activated_at + 5d,
  metadata.pre_promo_plan = его исходный план.
- Эта таска раз в час сканирует такие Payment-записи и для каждой,
  у которой revert_at прошло и revert_completed != True:
    * Перепроверяет последний платный Payment юзера. Если он уже купил Pro
      за эти 5 дней — squad остаётся Pro (юзер сам апгрейднулся).
    * Иначе возвращает squad на pre_promo_plan (или его последний
      платный план если он другой), expireAt не трогается.
    * Помечает metadata.revert_completed=True.
- Уведомление админу шлётся на каждый успешный revert.

Регистрируется в app/main.py рядом с SubscriptionChecker.
"""
import asyncio
from datetime import datetime
from typing import Optional

from app.logger import logger


class Sun718RevertTask:
    """Раз в час возвращает squad юзерам с истёкшим Pro-бонусом от /sun718."""

    def __init__(self, bot, check_interval: int = 3600):
        self.bot = bot
        self.check_interval = check_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self.running = True
        self._task = asyncio.create_task(self._run())
        logger.info(
            f"Sun718RevertTask: started (interval={self.check_interval}s)"
        )

    def stop(self) -> None:
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        # Сначала прогон сразу — на случай если бот падал и пропустили revert
        await self._tick_safe(label="startup")
        while self.running:
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            if not self.running:
                break
            await self._tick_safe(label="periodic")

    async def _tick_safe(self, label: str) -> None:
        try:
            await self._tick(label)
        except Exception as e:
            logger.error(f"sun718_revert tick ({label}) failed: {e}")

    async def _tick(self, label: str) -> None:
        from app.db.session import SessionLocal
        from app.db.models import Payment as PaymentModel
        from sqlalchemy import select

        if not SessionLocal:
            return

        now = datetime.utcnow()
        pending_ids: list[int] = []

        async with SessionLocal() as session:
            res = await session.execute(
                select(PaymentModel)
                .where(PaymentModel.provider == "promo")
                .where(PaymentModel.external_id.like("promo_sun718_%"))
            )
            for p in res.scalars():
                meta = p.payment_metadata or {}
                if meta.get("revert_completed"):
                    continue
                revert_at_iso = meta.get("revert_at")
                if not revert_at_iso:
                    continue
                try:
                    dt = datetime.fromisoformat(revert_at_iso)
                except Exception:
                    logger.warning(
                        f"sun718_revert: bad revert_at={revert_at_iso!r} in payment id={p.id}"
                    )
                    continue
                if dt <= now:
                    pending_ids.append(p.id)

        if not pending_ids:
            logger.debug(f"sun718_revert ({label}): nothing to do")
            return

        logger.info(f"sun718_revert ({label}): {len(pending_ids)} reverts due")
        for payment_id in pending_ids:
            try:
                await self._revert_one(payment_id)
            except Exception as e:
                logger.error(f"sun718_revert: revert id={payment_id} failed: {e}")

    async def _revert_one(self, payment_id: int) -> None:
        from app.db.session import SessionLocal
        from app.db.models import Payment as PaymentModel, TelegramUser
        from app.remnawave.client import RemnaClient
        from app.core.plans import get_plan_squad
        from app.config import settings
        from app.routers.start import _get_last_paid_plan_code
        from sqlalchemy import select

        if not SessionLocal:
            return

        async with SessionLocal() as session:
            payment = await session.get(PaymentModel, payment_id)
            if not payment:
                logger.warning(f"sun718_revert: payment id={payment_id} disappeared")
                return
            meta = dict(payment.payment_metadata or {})
            if meta.get("revert_completed"):
                return  # already done (race)
            tg_id = payment.telegram_user_id
            pre_promo_plan = (meta.get("pre_promo_plan") or "basic").lower()

            res = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == tg_id)
            )
            tg_user = res.scalar_one_or_none()
            remna_user_id = tg_user.remna_user_id if tg_user else None

        if not remna_user_id:
            logger.warning(f"sun718_revert: tg={tg_id} no remna_user_id, skip")
            await self._notify(
                title="⚠️ SUN718 REVERT: пропущен (нет remna_user_id)",
                body=f"tg=<code>{tg_id}</code> payment_id={payment_id}",
            )
            await self._mark_completed(payment_id, reverted_to="skipped_no_remna")
            return

        # За 5 дней юзер мог докупить Pro — тогда squad оставляем pro
        current_plan = await _get_last_paid_plan_code(tg_id)
        target_plan = current_plan if (current_plan == "pro") else pre_promo_plan
        squad_name = get_plan_squad(target_plan) or "basic"

        client = RemnaClient()
        squad = await client.get_squad_by_name(squad_name)
        if not squad or not squad.get("uuid"):
            logger.error(
                f"sun718_revert: squad {squad_name!r} not found, tg={tg_id}"
            )
            await self._notify(
                title="❌ SUN718 REVERT: squad не найден",
                body=(f"tg=<code>{tg_id}</code> target_plan={target_plan} "
                      f"squad_name={squad_name}"),
            )
            return  # не маркируем completed — попробуем в следующем тике

        try:
            await client.update_user(
                remna_user_id, activeInternalSquads=[squad["uuid"]]
            )
        except Exception as e:
            logger.error(f"sun718_revert: update_user failed tg={tg_id}: {e}")
            await self._notify(
                title="❌ SUN718 REVERT: update_user упал",
                body=f"tg=<code>{tg_id}</code> err=<code>{str(e)[:200]}</code>",
            )
            return  # не маркируем — повторим

        # Инвалидируем кэш
        try:
            from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
            await invalidate_subscription_cache(tg_id)
            await invalidate_sync_cache(tg_id)
        except Exception as e:
            logger.debug(f"sun718_revert: cache invalidate fail {tg_id}: {e}")

        await self._mark_completed(payment_id, reverted_to=target_plan)
        logger.info(
            f"sun718_revert: tg={tg_id} → squad={target_plan} "
            f"(pre={pre_promo_plan}, current_last_paid={current_plan})"
        )
        await self._notify(
            title="🔄 SUN718 REVERT выполнен",
            body=(
                f"👤 <code>{tg_id}</code>\n"
                f"📦 Squad возвращён: <b>{pre_promo_plan}</b> → <b>{target_plan}</b>\n"
                + ("ℹ️ Юзер докупил Pro — squad остался Pro\n"
                   if (current_plan == "pro" and pre_promo_plan != "pro") else "")
                + f"🔗 Remnawave ID: <code>{remna_user_id}</code>"
            ),
        )

    async def _mark_completed(self, payment_id: int, *, reverted_to: str) -> None:
        from app.db.session import SessionLocal
        from app.db.models import Payment as PaymentModel

        if not SessionLocal:
            return
        try:
            async with SessionLocal() as session:
                p = await session.get(PaymentModel, payment_id)
                if not p:
                    return
                new_meta = dict(p.payment_metadata or {})
                new_meta["revert_completed"] = True
                new_meta["reverted_at"] = datetime.utcnow().isoformat()
                new_meta["reverted_to_plan"] = reverted_to
                p.payment_metadata = new_meta
                await session.commit()
        except Exception as e:
            logger.error(f"sun718_revert: _mark_completed id={payment_id} fail: {e}")

    async def _notify(self, *, title: str, body: str) -> None:
        from app.config import settings
        text = f"<b>{title}</b>\n\n{body}"
        for admin_id in (settings.ADMINS or []):
            if isinstance(admin_id, int):
                try:
                    await self.bot.send_message(
                        chat_id=admin_id, text=text, parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"sun718_revert admin notify {admin_id} fail: {e}")
