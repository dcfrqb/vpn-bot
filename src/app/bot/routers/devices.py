"""«Мои устройства»: list and unlink (Dev callbacks).

Owner stream: D (User UI) over B's DevicesService.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import Dev
from app.bot.views import devices as devices_view
from app.bot.views import render
from app.config import settings
from app.domain.texts import devices as t
from app.domain.texts.common import support_handle

router = Router(name="r3_devices")


async def show_devices_screen(event, *, answer_callback: bool = True, **data) -> None:
    devices_service = data["devices"]
    status_service = data.get("status_service")
    user = event.from_user

    unlink_enabled = bool(settings.DEVICES_UNLINK_ENABLED)
    devices = await devices_service.list_devices(user.id)
    device_limit, active = None, True
    if status_service is not None:
        state = await status_service.get_state(user.id)
        device_limit = state.device_limit
        active = bool(state.active or state.stale)
    text, markup = devices_view.list_screen(devices, device_limit=device_limit, unlink_enabled=unlink_enabled,
                                            support_handle=support_handle(settings), active=active)
    await render(event, text, markup, answer_callback=answer_callback)


@router.callback_query(Dev.filter(F.a == "list"))
async def list_devices(callback: CallbackQuery, callback_data: Dev, **data) -> None:
    await show_devices_screen(callback, **data)


@router.callback_query(Dev.filter(F.a == "back"))
async def back_to_main(callback: CallbackQuery, callback_data: Dev, **data) -> None:
    from app.bot.routers.menu import show_main_screen

    await show_main_screen(callback, **data)


@router.callback_query(Dev.filter(F.a == "ask"))
async def ask_unlink(callback: CallbackQuery, callback_data: Dev, **data) -> None:
    if not settings.DEVICES_UNLINK_ENABLED:
        await show_devices_screen(callback, **data)
        return
    devices_service = data["devices"]
    devices = await devices_service.list_devices(callback.from_user.id)
    dev = next((d for d in devices if d.short_id == callback_data.id), None)
    if dev is None:
        text, markup = devices_view.not_found()
        await render(callback, text, markup)
        return
    text, markup = devices_view.ask_unlink(dev)
    await render(callback, text, markup)


@router.callback_query(Dev.filter(F.a == "unlink"))
async def unlink_device(callback: CallbackQuery, callback_data: Dev, **data) -> None:
    devices_service = data["devices"]
    if not settings.DEVICES_UNLINK_ENABLED:
        await show_devices_screen(callback, **data)
        return
    ok = await devices_service.unlink(callback.from_user.id, callback_data.id)
    await callback.answer(t.UNLINKED if ok else t.UNLINK_LIMIT, show_alert=not ok)
    await show_devices_screen(callback, answer_callback=False, **data)
