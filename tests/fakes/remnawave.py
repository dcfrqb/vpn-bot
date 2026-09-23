"""In-memory фейк RemnaClient (Remnawave 3.4.3) для тестов хотфикса 2.1.

Поддерживает то, что реально вызывает бот при выдаче: get_user_by_id,
list_internal_squads / get_squad_by_name, update_user (PATCH), create_user,
_find_user_by_username / get_user_by_username, get_user_by_telegram_id,
get_user_subscription_url, disable_user. Все PATCH пишутся в self.patches.
"""
from typing import Any, Dict, List, Optional

import httpx

from app.remnawave.client import RemnaClient, RemnaUser, build_user_payload_from_kwargs


DEFAULT_SQUADS = {
    "basic": "sq-basic", "premium": "sq-premium", "lite": "sq-lite",
    "standard": "sq-standard", "pro": "sq-pro", "obhod": "sq-obhod",
    "pro-friend": "sq-pro-friend", "premium-friend": "sq-premium-friend",
    "arcadia": "sq-arcadia", "us-2": "sq-us-2",
}


def _exists_error(username: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://panel.example/api/users")
    resp = httpx.Response(400, request=req, text='{"message":"User username already exists","errorCode":"A019"}')
    return httpx.HTTPStatusError(f"exists {username}", request=req, response=resp)


class FakeRemna:
    # Логика выбора username при коллизии — настоящая, из RemnaClient.
    create_user_unique = RemnaClient.create_user_unique

    def __init__(self, squads: Optional[Dict[str, str]] = None):
        self.squads = dict(DEFAULT_SQUADS if squads is None else squads)
        self.users: Dict[int, Dict[str, Any]] = {}
        self.patches: List[Dict[str, Any]] = []
        self.created: List[Dict[str, Any]] = []
        self.disabled: List[int] = []
        self.enabled: List[int] = []
        self.fail_get_user = False
        self.fail_squads = False
        self.fail_lookup_tg = False
        self._next_id = 1000

    # ---- helpers for tests ----
    def add_user(self, uid: int, username: str, telegram_id: Optional[int] = None,
                 squads: Optional[List[str]] = None, limit: Optional[int] = None,
                 expire: str = "2026-10-01T00:00:00Z", status: str = "ACTIVE") -> Dict[str, Any]:
        user = {
            "id": uid, "username": username, "telegramId": telegram_id,
            "activeInternalSquads": [{"uuid": self.squads[n], "name": n} for n in (squads or [])],
            "hwidDeviceLimit": limit, "expireAt": expire, "status": status,
            "subscriptionUrl": f"https://sub.example/{uid}",
        }
        self.users[uid] = user
        return user

    def squad_names(self, uid: int) -> List[str]:
        by_uuid = {v: k for k, v in self.squads.items()}
        return [by_uuid.get(s["uuid"], s["uuid"]) for s in self.users[uid]["activeInternalSquads"]]

    # ---- RemnaClient surface ----
    async def close(self):
        return None

    async def get_user_by_id(self, user_id):
        if self.fail_get_user:
            raise httpx.ConnectError("panel down")
        uid = int(user_id)
        if uid not in self.users:
            req = httpx.Request("GET", f"https://panel.example/api/users/{uid}")
            raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))
        return {"response": dict(self.users[uid])}

    async def list_internal_squads(self):
        if self.fail_squads:
            raise httpx.ConnectError("panel down")
        return [{"uuid": u, "name": n} for n, u in self.squads.items()]

    async def get_squad_by_name(self, name):
        if self.fail_squads:
            return None
        u = self.squads.get(name)
        return {"uuid": u, "name": name} if u else None

    async def update_user(self, user_id, **kwargs):
        payload = build_user_payload_from_kwargs(kwargs)
        payload["id"] = int(user_id)
        self.patches.append(payload)
        user = self.users[int(user_id)]
        by_uuid = {v: k for k, v in self.squads.items()}
        for k, v in payload.items():
            if k == "activeInternalSquads":
                user[k] = [{"uuid": u, "name": by_uuid.get(u)} for u in v]
            elif k != "id":
                user[k] = v
        # Как панель: EXPIRED с expireAt в будущем снова ACTIVE; DISABLED так
        # не снимается (только /actions/enable).
        if "expireAt" in payload and user.get("status") == "EXPIRED":
            from datetime import datetime, timezone
            try:
                exp = datetime.fromisoformat(str(payload["expireAt"]).replace("Z", "+00:00"))
                if exp > datetime.now(timezone.utc):
                    user["status"] = "ACTIVE"
            except ValueError:
                pass
        return {"response": dict(user)}

    async def create_user(self, username, password=None, expire_at=None, telegram_id=None,
                          active_internal_squads=None, display_name=None, hwid_device_limit=None,
                          traffic_limit_bytes=None, traffic_limit_strategy=None):
        if any(u["username"] == username for u in self.users.values()):
            raise _exists_error(username)
        self._next_id += 1
        uid = self._next_id
        by_uuid = {v: k for k, v in self.squads.items()}
        user = self.add_user(uid, username, telegram_id=telegram_id, limit=hwid_device_limit,
                             expire=str(expire_at) if expire_at else "2000-01-01T00:00:00Z")
        user["activeInternalSquads"] = [{"uuid": u, "name": by_uuid.get(u)} for u in (active_internal_squads or [])]
        self.created.append({"username": username, "telegram_id": telegram_id, "id": uid})
        return {"response": dict(user)}

    async def _find_user_by_username(self, username):
        for u in self.users.values():
            if u["username"] == username:
                return dict(u)
        return None

    async def get_user_by_username(self, username):
        return await self._find_user_by_username(username)

    async def get_user_by_telegram_id(self, telegram_id, strict: bool = False):
        if self.fail_lookup_tg:
            if strict:
                raise httpx.ConnectError("panel down")
            return None
        matches = [u for u in self.users.values() if u.get("telegramId") == telegram_id]
        if not matches:
            return None
        u = matches[0]
        return RemnaUser(uuid=str(u["id"]), telegram_id=telegram_id, username=u["username"],
                         name=u["username"], raw_data=dict(u))

    async def get_user_subscription_url(self, user_id):
        u = self.users.get(int(user_id))
        return u["subscriptionUrl"] if u else None

    async def disable_user(self, user_id):
        self.disabled.append(int(user_id))
        self.users[int(user_id)]["status"] = "DISABLED"
        return {}

    async def enable_user(self, user_id):
        self.enabled.append(int(user_id))
        self.users[int(user_id)]["status"] = "ACTIVE"
        return {}
