"""Admin: promo codes (PromoAdm, /promo_new, /promo_list). Owner: E.

Also carries the other stream E admin routers (home, users, grants, ops) as
sub-routers until they are listed in NEW_ROUTER_MODULES themselves (request
in impl/requests/E.md); the include is skipped once they are.
Admins only (AdminGuard).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import PromoAdm
from app.bot.middlewares.admin_guard import guard_router
from app.bot.views import admin as V
from app.bot.views import render
from app.domain.texts import admin as T
from app.domain.texts import h
from app.services.promo import KIND_DAYS, KIND_PLAN, PromoCodeSpec, get_promo, normalize_audience

router = guard_router(Router(name="r3_admin_promo"))

SUBROUTER_MODULES = (
    "app.bot.routers.admin.home",
    "app.bot.routers.admin.users",
    "app.bot.routers.admin.grants",
    "app.bot.routers.admin.ops",
)


def parse_promo_new(args: str) -> PromoCodeSpec:
    """'CODE days=7 plan=standard audience=new max=100 per_user=1 valid=30' -> spec.
    Raises ValueError with a short reason."""
    parts = (args or "").split()
    if not parts:
        raise ValueError("нет кода")
    code, opts = parts[0], {}
    for p in parts[1:]:
        k, sep, v = p.partition("=")
        if not sep:
            raise ValueError(f"не понял «{p}»")
        opts[k.lower()] = v

    def num(key: str, lo: int = 0, hi: int = 100000) -> Optional[int]:
        if key not in opts:
            return None
        if not opts[key].isdigit() or not lo <= int(opts[key]) <= hi:
            raise ValueError(f"{key} должно быть числом {lo}..{hi}")
        return int(opts[key])

    unknown = set(opts) - {"days", "plan", "kind", "audience", "max", "per_user", "valid", "traffic", "devices"}
    if unknown:
        raise ValueError("неизвестные поля: " + ", ".join(sorted(unknown)))
    days = num("days", 1, 3650)
    if not days:
        raise ValueError("нужно days=N")
    kind = opts.get("kind", KIND_DAYS)
    if kind not in (KIND_DAYS, KIND_PLAN):
        raise ValueError("kind: days или plan")
    audience = normalize_audience(opts.get("audience", "any"))
    if audience is None:
        raise ValueError("audience: new, existing или any")
    valid = num("valid", 1, 3650)
    return PromoCodeSpec(
        code=code, kind=kind, plan_code=(opts.get("plan") or None), days=days,
        traffic_gb=num("traffic", 0, 10000) or None, devices=num("devices", 0, 50) or None,
        audience=audience, max_uses=num("max", 1, 1000000), per_user_limit=num("per_user", 1, 100) or 1,
        valid_until=(datetime.now(timezone.utc) + timedelta(days=valid)) if valid else None,
    )


async def _list(event: Any, container: Any) -> None:
    await render(event, *V.promo_list(await get_promo(container).list_codes(20)))


@router.message(Command("promo_list"))
async def cmd_promo_list(message: Message, container: Any) -> None:
    await _list(message, container)


@router.message(Command("promo_new"))
async def cmd_promo_new(message: Message, command: CommandObject, container: Any) -> None:
    if not command.args:
        await render(message, T.USAGE_PROMO_NEW)
        return
    try:
        spec = parse_promo_new(command.args)
        row = await get_promo(container).create_code(spec, created_by=message.from_user.id)
    except ValueError as e:
        await render(message, f"Не создал: {h(str(e))}\n\n{T.USAGE_PROMO_NEW}")
        return
    if row is None:
        await render(message, "Такой код уже есть.")
        return
    me = await message.bot.me()
    text, markup = V.promo_card(row, me.username)
    note = "" if getattr(container.settings, "PROMO_CODES_ENABLED", False) else \
        "\n\n⚠️ PROMO_CODES_ENABLED выключен: код не примется, пока флаг не включат."
    await render(message, f"✅ Создан\n\n{text}{note}", markup)


@router.callback_query(PromoAdm.filter(F.a == "list"))
async def cb_list(callback: CallbackQuery, container: Any) -> None:
    await _list(callback, container)


@router.callback_query(PromoAdm.filter(F.a.in_({"show", "on", "off"})))
async def cb_code(callback: CallbackQuery, callback_data: PromoAdm, container: Any) -> None:
    engine = get_promo(container)
    if callback_data.a in ("on", "off"):
        await engine.set_active(callback_data.id, callback_data.a == "on")
    row = await engine.get_code(callback_data.id)
    if row is None:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    me = await callback.bot.me()
    await render(callback, *V.promo_card(row, me.username))


def _include_subrouters() -> None:
    from app.bot.routers import NEW_ROUTER_MODULES

    for mod in SUBROUTER_MODULES:
        if mod in NEW_ROUTER_MODULES:
            continue
        sub = importlib.import_module(mod).router
        if sub.parent_router is None:
            router.include_router(sub)


_include_subrouters()
