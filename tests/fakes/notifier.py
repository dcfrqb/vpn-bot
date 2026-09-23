"""RecordingNotifier: implements app.services.ports.Notifier in memory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.domain.models import AdminTopic


@dataclass
class Sent:
    kind: str  # "admins" | "user"
    target: Any  # AdminTopic or telegram_id
    text: str
    html: bool
    dedup_key: Optional[str]
    reply_markup: Any = None


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self._dedup: set[str] = set()

    def _dedup_ok(self, key: Optional[str]) -> bool:
        if not key:
            return True
        if key in self._dedup:
            return False
        self._dedup.add(key)
        return True

    async def notify_admins(self, topic: AdminTopic, text: str, *, html: bool = False, dedup_key: Optional[str] = None,
                            dedup_ttl: int = 3600, reply_markup: Any = None, disable_notification: bool = False) -> int:
        if not self._dedup_ok(dedup_key):
            return 0
        self.sent.append(Sent("admins", AdminTopic(topic), text, html, dedup_key, reply_markup))
        return 1

    async def notify_user(self, telegram_id: int, text: str, *, html: bool = False, reply_markup: Any = None,
                          dedup_key: Optional[str] = None, dedup_ttl: int = 3600) -> bool:
        if not self._dedup_ok(dedup_key):
            return False
        self.sent.append(Sent("user", int(telegram_id), text, html, dedup_key, reply_markup))
        return True

    def to_admins(self, topic: Optional[AdminTopic] = None) -> list[Sent]:
        return [s for s in self.sent if s.kind == "admins" and (topic is None or s.target == topic)]
