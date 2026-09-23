"""Webhook bodies in the exact shape of Remnawave 3.4.3
(@remnawave/backend-contract RemnawaveWebhook*Events, GetFullUserResponseModel,
NodeResponseModel). Secrets are dummies."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Optional

SECRET = "a" * 16 + "B" * 16 + "0123456789"  # 42 alnum chars, like the panel requires


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(uid: int = 1500, tg: Optional[int] = 700001, username: str = "tg_700001", status: str = "ACTIVE",
         squads=(("pro", "sq-pro"),), limit: Optional[int] = 10, expire: str = "2026-09-23T08:00:00.000Z",
         traffic_limit: int = 0, strategy: str = "NO_RESET") -> dict:
    return {
        "id": uid, "shortUuid": "shortuuid1", "username": username, "status": status,
        "trafficLimitBytes": traffic_limit, "trafficLimitStrategy": strategy, "expireAt": expire,
        "telegramId": tg, "email": None, "description": None, "tag": None, "hwidDeviceLimit": limit,
        "externalSquadUuid": None, "trojanPassword": "SECRET-trojan", "vlessUuid": "00000000-0000-4000-8000-000000000001",
        "ssPassword": "SECRET-ss", "lastTriggeredThreshold": 0, "subRevokedAt": None, "lastTrafficResetAt": None,
        "createdAt": "2026-01-01T00:00:00.000Z", "updatedAt": "2026-09-23T08:00:00.000Z",
        "subscriptionUrl": "https://sub.example/SECRETTOKEN",
        "activeInternalSquads": [{"uuid": u, "name": n} for n, u in squads],
        "userTraffic": {"usedTrafficBytes": 0, "lifetimeUsedTrafficBytes": 0, "onlineAt": None,
                        "firstConnectedAt": None, "lastConnectedNodeUuid": None},
    }


def node(name: str = "nl-1", address: str = "1.2.3.4", message: Optional[str] = "timeout") -> dict:
    return {"uuid": "11111111-1111-4111-8111-111111111111", "id": 7, "name": name, "address": address,
            "port": 2222, "isConnected": False, "isDisabled": False, "isConnecting": False,
            "lastStatusChange": None, "lastStatusMessage": message, "countryCode": "NL", "tags": []}


def event(name: str, data: Any, *, ts: datetime, meta: Any = None) -> dict:
    scope = name.split(".", 1)[0]
    body = {"scope": scope, "event": name, "timestamp": iso(ts), "data": data}
    if scope == "user":
        body["meta"] = meta
    return body


def hwid_event(u: dict, *, ts: datetime, hwid: str = "HWID-SECRET-123456", model: str = "iPhone 15") -> dict:
    return event("user_hwid_devices.added", {
        "user": u,
        "hwidUserDevice": {"hwid": hwid, "userId": u["id"], "platform": "iOS", "osVersion": "18",
                           "deviceModel": model, "userAgent": "Happ/3", "requestIp": None,
                           "createdAt": iso(ts), "updatedAt": iso(ts)},
    }, ts=ts)


def encode(body: dict) -> bytes:
    """Like the panel: JSON.stringify(json) -> compact."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def signed_headers(raw: bytes, secret: str = SECRET, ts: Optional[str] = None) -> dict:
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    h = {"Content-Type": "application/json", "X-Remnawave-Signature": sig, "User-Agent": "Remnawave"}
    if ts:
        h["X-Remnawave-Timestamp"] = ts
    return h
