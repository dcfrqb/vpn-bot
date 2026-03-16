"""
Expiry notification system — Stage C.

Runs inside SubscriptionChecker._run_once() every 15 minutes.
Paginates all Remnawave users, detects subscriptions expiring in 3 days
or on the expiration day, and sends a Telegram notification.

Dedup: Redis keys with TTL prevent duplicate sends across restarts.
  expiry_notice:3d:<telegram_id>:<yyyy-mm-dd>  TTL=4 days
  expiry_notice:0d:<telegram_id>:<yyyy-mm-dd>  TTL=2 days
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.logger import logger

# Notification windows (inclusive, whole UTC days from now)
WINDOW_SOON_MIN = 1   # at least 1 day away
WINDOW_SOON_MAX = 3   # at most 3 days away
WINDOW_TODAY = 0      # expires today (0 full days remaining)

# Redis TTLs for dedup keys
TTL_3D = 4 * 24 * 3600   # 4 days — covers the 3-day window + buffer
TTL_0D = 2 * 24 * 3600   # 2 days — covers same-day window + buffer

# Fetch users in batches from Remnawave
_PAGE_SIZE = 100

# Small delay between bot.send_message calls to avoid Telegram rate limits
_SEND_DELAY = 0.1  # seconds


def _build_notify_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="buy_subscription")]
    ])


def _parse_expire_dt(expire_at_raw: Any) -> Optional[datetime]:
    """Parse expireAt value from Remnawave into a timezone-aware UTC datetime."""
    try:
        if isinstance(expire_at_raw, (int, float)):
            return datetime.fromtimestamp(expire_at_raw, tz=timezone.utc)
        if isinstance(expire_at_raw, datetime):
            if expire_at_raw.tzinfo is None:
                return expire_at_raw.replace(tzinfo=timezone.utc)
            return expire_at_raw.astimezone(timezone.utc)
        if isinstance(expire_at_raw, str):
            s = expire_at_raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None


async def _set_dedup(redis_client, key: str, ttl: int) -> bool:
    """Set dedup key with NX (only if not exists). Returns True if key was set (not deduped)."""
    try:
        ok = await redis_client.set(key, "1", ex=ttl, nx=True)
        return bool(ok)
    except Exception as e:
        logger.debug(f"expiry_notifier: dedup set failed for {key}: {e}")
        return False


async def _fetch_all_remna_users(client) -> list:
    """Paginate through Remnawave get_users() and return all user dicts."""
    all_users = []
    start = 1
    total = None  # filled from first page
    while True:
        try:
            data = await client.get_users(size=_PAGE_SIZE, start=start)
        except Exception as e:
            logger.warning(f"expiry_notifier: get_users(start={start}) failed: {e}")
            break

        # Remnawave returns {"response": {"total": N, "users": [...]}}
        # Older versions may return {"response": [...]} or a bare list.
        if isinstance(data, dict):
            response = data.get("response", [])
            if isinstance(response, dict):
                # New Remnawave format: {"response": {"total": N, "users": [...]}}
                if total is None:
                    total = response.get("total", 0)
                users = response.get("users", [])
                if not isinstance(users, list):
                    users = []
            elif isinstance(response, list):
                users = response
            else:
                users = []
        elif isinstance(data, list):
            users = data
        else:
            break

        all_users.extend(users)

        # Stop when we've fetched all users or got a short page
        if total is not None:
            if len(all_users) >= total:
                break
        elif len(users) < _PAGE_SIZE:
            break  # last page (legacy format)
        start += _PAGE_SIZE
        await asyncio.sleep(0)  # yield to event loop between pages

    return all_users


async def check_expiry_notifications(bot: Bot) -> Dict[str, Any]:
    """
    Check all Remnawave users for upcoming expiry and send notifications.
    Returns stats dict: {checked, sent_3d, sent_0d, skipped_dedup, errors}.
    """
    stats = {"checked": 0, "sent_3d": 0, "sent_0d": 0, "skipped_dedup": 0, "errors": 0}

    from app.services.cache import get_redis_client
    redis_client = get_redis_client()
    if not redis_client:
        logger.debug("expiry_notifier: Redis unavailable — skipping (dedup requires Redis)")
        return stats

    from app.remnawave.client import RemnaClient
    client = RemnaClient()

    try:
        users = await _fetch_all_remna_users(client)
    except Exception as e:
        logger.error(f"expiry_notifier: failed to fetch users: {e}")
        return stats
    finally:
        await client.close()

    now_utc = datetime.now(timezone.utc)
    today_utc = now_utc.date()

    for user in users:
        if not isinstance(user, dict):
            continue

        telegram_id = user.get("telegramId") or user.get("telegram_id")
        if not telegram_id:
            continue
        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            continue

        expire_at_raw = user.get("expireAt") or user.get("expires_at") or user.get("valid_until")
        if not expire_at_raw:
            continue

        expire_dt = _parse_expire_dt(expire_at_raw)
        if not expire_dt:
            continue

        # Skip lifetime subscriptions (year >= 2099) and sentinel dates (year < 2020)
        if expire_dt.year >= 2099 or expire_dt.year < 2020:
            continue

        # Skip revoked subscriptions
        if user.get("subRevokedAt"):
            continue

        stats["checked"] += 1
        expire_date = expire_dt.date()
        days_until = (expire_date - today_utc).days

        if days_until == WINDOW_TODAY:
            notice_type = "0d"
            ttl = TTL_0D
            text = (
                "❌ <b>Ваша подписка VPN истекает сегодня.</b>\n\n"
                "Продлите её, чтобы сохранить доступ к VPN."
            )
        elif WINDOW_SOON_MIN <= days_until <= WINDOW_SOON_MAX:
            notice_type = "3d"
            ttl = TTL_3D
            text = (
                f"⚠️ <b>Ваша подписка VPN истекает через {days_until} дн.</b>\n\n"
                f"Дата окончания: {expire_date.strftime('%d.%m.%Y')}\n\n"
                "Не забудьте продлить подписку, чтобы не потерять доступ."
            )
        else:
            continue

        dedup_key = f"expiry_notice:{notice_type}:{telegram_id}:{expire_date.isoformat()}"
        sent = await _set_dedup(redis_client, dedup_key, ttl)
        if not sent:
            stats["skipped_dedup"] += 1
            continue

        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=_build_notify_keyboard(),
                parse_mode="HTML",
            )
            await asyncio.sleep(_SEND_DELAY)
            if notice_type == "3d":
                stats["sent_3d"] += 1
            else:
                stats["sent_0d"] += 1
            logger.info(
                f"expiry_notifier: sent {notice_type} notice to {telegram_id} "
                f"(expires {expire_date})"
            )
        except Exception as e:
            stats["errors"] += 1
            # Delete the dedup key so we can retry next run
            try:
                await redis_client.delete(dedup_key)
            except Exception:
                pass
            logger.warning(f"expiry_notifier: send failed to {telegram_id}: {e}")

    if stats["checked"] > 0 or stats["sent_3d"] > 0 or stats["sent_0d"] > 0:
        logger.info(f"expiry_notifier: {stats}")
    else:
        logger.debug(f"expiry_notifier: nothing to notify ({stats})")

    return stats
