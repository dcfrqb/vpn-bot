"""FakeDevicesService: implements app.services.ports.DevicesService in memory."""
from __future__ import annotations

from typing import Dict, List

from app.domain.models import DeviceInfo


class FakeDevicesService:
    def __init__(self) -> None:
        self.devices: Dict[int, List[DeviceInfo]] = {}
        self.unlink_limit_hit: set[int] = set()
        self.unlinked: list[tuple[int, str]] = []

    async def list_devices(self, telegram_id: int) -> list[DeviceInfo]:
        return list(self.devices.get(int(telegram_id), []))

    async def unlink(self, telegram_id: int, device_short_id: str) -> bool:
        if int(telegram_id) in self.unlink_limit_hit:
            return False
        devs = self.devices.get(int(telegram_id), [])
        remaining = [d for d in devs if d.short_id != device_short_id]
        if len(remaining) == len(devs):
            return False
        self.devices[int(telegram_id)] = remaining
        self.unlinked.append((int(telegram_id), device_short_id))
        return True

    async def unlinks_left(self, telegram_id: int) -> int:
        return 0 if int(telegram_id) in self.unlink_limit_hit else 3
