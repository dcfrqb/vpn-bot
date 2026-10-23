"""Notifier (release 3.0 Foundation): admin forum topics + DM fallback.

Routing of ``notify_admins(topic, text)``:
  1. ADMIN_CHAT_ID set -> one message to that chat, into the forum topic
     ADMIN_TOPIC_<TOPIC> (message_thread_id) when configured, else the chat
     root. If that send fails, fall through to step 2.
  2. Otherwise (or on failure) -> a DM to every id in ADMINS (2.x behaviour).

Safety:
  - ``html=False`` (default) escapes the whole text; ``html=True`` only for
    text built from constants + ``h()``-escaped values.
  - ``dedup_key`` + ``dedup_ttl``: Redis SET NX ``notify:<key>``; a repeat
    within the TTL is dropped. Redis down -> send anyway (fail-open). The
    claim is taken before sending (so concurrent callers do not double-send)
    but released if the send does not actually deliver, so a failed send can
    be retried instead of silently eating the dedup slot until the TTL ends.
  - Exception text of a failed send is logged by type only; message text is
    never logged.

No aiogram imports: ``bot`` provides async ``send_message`` and, for card
replacement, ``edit_message_text``.
Old notify helpers (refunds._notify_admins, the 2.x reconciler loop) keep
working unchanged; stream E moves callers here.
"""
from __future__ import annotations

from typing import Any, Optional

from app.domain.models import AdminTopic
from app.domain.texts import h
from app.logger import logger

_TOPIC_SETTING = {
    AdminTopic.PAYMENTS: "ADMIN_TOPIC_PAYMENTS",
    AdminTopic.REFUNDS: "ADMIN_TOPIC_REFUNDS",
    AdminTopic.PANEL: "ADMIN_TOPIC_PANEL",
    AdminTopic.ERRORS: "ADMIN_TOPIC_ERRORS",
    AdminTopic.PROMO: "ADMIN_TOPIC_PROMO",
    AdminTopic.BROADCAST: "ADMIN_TOPIC_BROADCAST",
}

DEDUP_PREFIX = "notify:"


class TelegramNotifier:
    def __init__(self, bot: Any, settings: Any = None):
        self._bot = bot
        self._settings = settings

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    def topic_thread_id(self, topic: AdminTopic) -> Optional[int]:
        name = _TOPIC_SETTING.get(AdminTopic(topic))
        if not name:
            return None
        value = getattr(self.settings, name, None)
        return int(value) if value else None

    async def _dedup_ok(self, dedup_key: Optional[str], ttl: int) -> bool:
        if not dedup_key:
            return True
        from app.infra.redis.flags import set_once

        got = await set_once(f"{DEDUP_PREFIX}{dedup_key}", "1", ttl=int(ttl))
        if got is False:
            logger.info(f"notifier: dedup hit {dedup_key}")
            return False
        return True

    async def _dedup_release(self, dedup_key: Optional[str]) -> None:
        if not dedup_key:
            return
        from app.infra.redis.flags import delete_key

        await delete_key(f"{DEDUP_PREFIX}{dedup_key}")

    async def _send(self, chat_id: int, text: str, **kwargs: Any) -> bool:
        try:
            await self._bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", **kwargs)
            return True
        except Exception as e:  # noqa: BLE001 - delivery must never break the caller
            logger.warning(f"notifier: send to {chat_id} failed ({type(e).__name__})")
            return False

    async def notify_admins(
        self,
        topic: AdminTopic,
        text: str,
        *,
        html: bool = False,
        dedup_key: Optional[str] = None,
        dedup_ttl: int = 3600,
        reply_markup: Any = None,
        disable_notification: bool = False,
    ) -> int:
        if not await self._dedup_ok(dedup_key, dedup_ttl):
            return 0
        body = text if html else h(text)
        extra: dict[str, Any] = {}
        if reply_markup is not None:
            extra["reply_markup"] = reply_markup
        if disable_notification:
            extra["disable_notification"] = True

        chat_id = getattr(self.settings, "ADMIN_CHAT_ID", None)
        if chat_id:
            thread_id = self.topic_thread_id(topic)
            kwargs = dict(extra)
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            if await self._send(int(chat_id), body, **kwargs):
                return 1
            logger.warning(f"notifier: admin chat unavailable, DM fallback (topic={AdminTopic(topic).value})")

        delivered = 0
        for admin_id in list(getattr(self.settings, "ADMINS", None) or []):
            if await self._send(int(admin_id), body, **extra):
                delivered += 1
        if delivered == 0:
            await self._dedup_release(dedup_key)
        return delivered

    async def notify_user(
        self,
        telegram_id: int,
        text: str,
        *,
        html: bool = False,
        reply_markup: Any = None,
        dedup_key: Optional[str] = None,
        dedup_ttl: int = 3600,
        edit_message_id: Optional[int] = None,
    ) -> bool:
        if not await self._dedup_ok(dedup_key, dedup_ttl):
            return False
        extra: dict[str, Any] = {}
        if reply_markup is not None:
            extra["reply_markup"] = reply_markup
        body = text if html else h(text)
        if edit_message_id is None:
            ok = await self._send(int(telegram_id), body, **extra)
        else:
            try:
                await self._bot.edit_message_text(
                    chat_id=int(telegram_id), message_id=edit_message_id, text=body,
                    parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True,
                )
                ok = True
            except Exception as e:  # noqa: BLE001 - retry delivery without repeating payment
                error = str(getattr(e, "message", "")).lower()
                bad_request = type(e).__name__ == "TelegramBadRequest"
                if bad_request and "message is not modified" in error:
                    ok = True
                elif bad_request and any(reason in error for reason in (
                    "message to edit not found", "message can't be edited", "message_id_invalid",
                )):
                    ok = await self._send(int(telegram_id), body, **extra)
                else:
                    # Timeout may mean the edit succeeded: never send a duplicate on uncertainty.
                    logger.warning(f"notifier: edit for {telegram_id} failed ({type(e).__name__})")
                    ok = False
        if not ok:
            await self._dedup_release(dedup_key)
        return ok
