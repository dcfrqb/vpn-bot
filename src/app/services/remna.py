# src/app/services/remna.py
import aiohttp
from app.config import settings

class RemnaClient:
    def __init__(self):
        self.base = str(settings.REMNA_API_BASE)
        self.headers = {"Authorization": f"Bearer {settings.REMNA_API_KEY}"}

    async def create_account(self, tg_id: int) -> dict:
        async with aiohttp.ClientSession(headers=self.headers) as s:
            async with s.post(f"{self.base}/accounts", json={"telegram_id": tg_id}) as r:
                r.raise_for_status()
                return await r.json()

    async def issue_config(self, tg_id: int, plan_code: str) -> dict:
        async with aiohttp.ClientSession(headers=self.headers) as s:
            async with s.post(f"{self.base}/configs", json={"telegram_id": tg_id, "plan": plan_code}) as r:
                r.raise_for_status()
                return await r.json()