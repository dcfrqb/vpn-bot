"""User-side promo commands and buttons (stream E).

/trial, /solokhin, /sun718, /promo [code], PromoAct(enter|trial|apply),
Gift(claim), /friend (access request) and /admin from a non-admin (promo
request, PROMO_ADMIN_ENABLED). Included into the promo_deeplink router
(see its module docstring), so these commands win over 2.x routers.
Everything goes through the PromoEngine (services.promo) and the Notifier.
"""
from __future__ import annotations

import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Gift, Nav, PromoAct
from app.bot.middlewares.admin_guard import is_admin_id
from app.bot.views import kb, render
from app.bot.views import admin as admin_views
from app.domain.models import AdminTopic, PromoReward
from app.domain.plans import get_plan_name
from app.domain.texts import admin as TA
from app.domain.texts import h
from app.domain.texts import promo as T
from app.logger import logger
from app.services.promo import get_promo

router = Router(name="r3_trial_promo")


class PromoInput(StatesGroup):
    code = State()


def _support(container: Any) -> Any:
    s = container.settings
    return getattr(s, "SUPPORT_HANDLE", None) or getattr(s, "ADMIN_SUPPORT_USERNAME", None)


def result_view(code: str, reward: PromoReward, container: Any):
    if reward.applied:
        text = T.applied_text(code, reward, plan_title=get_plan_name(reward.plan_code), support=_support(container))
        return text, kb([[(T.BTN_CONNECT, Nav(s="connect"))], [(T.BTN_MENU, Nav(s="main"))]])
    return T.outcome_text(code, reward, support=_support(container)), kb([[(T.BTN_MENU, Nav(s="main"))]])


async def redeem_and_reply(event: Any, code: str, container: Any, *, source: str) -> PromoReward:
    reward = await get_promo(container).redeem(event.from_user.id, code, source=source)
    text, markup = result_view(code, reward, container)
    await render(event, text, markup)
    return reward


# ----------------------------------------------------------------- built-in commands


@router.message(Command("trial"))
async def cmd_trial(message: Message, container: Any) -> None:
    if not getattr(container.settings, "PROMO_TRIAL_ENABLED", True):
        return
    await redeem_and_reply(message, "trial", container, source="trial")


@router.message(Command("solokhin"))
async def cmd_solokhin(message: Message, container: Any) -> None:
    if not getattr(container.settings, "PROMO_SOLOKHIN_ENABLED", True):
        return
    await redeem_and_reply(message, "solokhin", container, source="command")


@router.message(Command("sun718"))
async def cmd_sun718(message: Message, container: Any) -> None:
    if not getattr(container.settings, "PROMO_SUN718_ENABLED", True):
        return
    await redeem_and_reply(message, "sun718", container, source="command")


# ----------------------------------------------------------------- promo codes


def _codes_enabled(container: Any) -> bool:
    return bool(getattr(container.settings, "PROMO_CODES_ENABLED", False))


@router.message(Command("promo"))
async def cmd_promo(message: Message, command: CommandObject, state: FSMContext, container: Any) -> None:
    if command.args:
        await state.clear()
        await redeem_and_reply(message, command.args.split()[0], container, source="command")
        return
    if not _codes_enabled(container):
        await render(message, T.CODES_DISABLED)
        return
    await state.set_state(PromoInput.code)
    await render(message, T.ENTER_CODE)


@router.callback_query(PromoAct.filter(F.a == "enter"))
async def cb_enter(callback: CallbackQuery, state: FSMContext, container: Any) -> None:
    if not _codes_enabled(container):
        await callback.answer(T.CODES_DISABLED, show_alert=True)
        return
    await state.set_state(PromoInput.code)
    await render(callback, T.ENTER_CODE, kb([[(T.BTN_MENU, Nav(s="main"))]]))


@router.message(StateFilter(PromoInput.code), Command("cancel"))
async def cancel_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    await render(message, T.ENTER_CANCELLED, kb([[(T.BTN_MENU, Nav(s="main"))]]))


@router.message(StateFilter(PromoInput.code), F.text)
async def got_code(message: Message, state: FSMContext, container: Any) -> None:
    await state.clear()
    await redeem_and_reply(message, (message.text or "").split()[0] if message.text.strip() else "",
                           container, source="input")


@router.callback_query(PromoAct.filter(F.a == "trial"))
async def cb_trial(callback: CallbackQuery, container: Any) -> None:
    await redeem_and_reply(callback, "trial", container, source="button")


@router.callback_query(PromoAct.filter(F.a == "apply"))
async def cb_apply(callback: CallbackQuery, callback_data: PromoAct, container: Any) -> None:
    await redeem_and_reply(callback, callback_data.arg, container, source="button")


@router.callback_query(Gift.filter(F.a == "claim"))
async def cb_gift_claim(callback: CallbackQuery, callback_data: Gift, container: Any) -> None:
    code = callback_data.id if callback_data.id.startswith("g_") else f"g_{callback_data.id}"
    await redeem_and_reply(callback, code, container, source="gift_button")


# ----------------------------------------------------------------- access requests (/friend, /admin)


def _who(message: Message) -> str:
    u = message.from_user
    name = " ".join(x for x in (u.first_name, u.last_name) if x) or u.username or f"User_{u.id}"
    return (f"Имя: {h(name)}\nUsername: @{h(u.username or 'не указан')}\n"
            f"Telegram ID: <code>{u.id}</code>")


async def _access_request(message: Message, container: Any, *, section: str, title: str) -> None:
    uid = message.from_user.id
    try:
        st = await container.status.get_state(uid, force=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"access request {section}: status failed tg={uid} ({type(e).__name__})")
        await render(message, T.REQUEST_CHECK_FAILED)
        return
    if st.stale:
        await render(message, T.REQUEST_CHECK_FAILED)
        return
    if st.active:
        await render(message, T.REQUEST_ALREADY_ACTIVE)
        return
    arg = f"{uid}.{int(time.time())}"
    sent = await container.notifier.notify_admins(
        AdminTopic.GENERAL, f"{title}\n\n{_who(message)}\n\n{TA.REQUEST_HINT}", html=True,
        reply_markup=admin_views.request_keyboard(section, arg, uid),
        dedup_key=f"access_req:{section}:{uid}", dedup_ttl=600,
    )
    await render(message, T.REQUEST_SENT if sent else T.REQUEST_DUPLICATE)


@router.message(Command("friend"))
async def cmd_friend(message: Message, container: Any) -> None:
    await _access_request(message, container, section="friend", title=TA.REQUEST_TITLE_FRIEND)


@router.message(Command("admin"), lambda m: not is_admin_id(m.from_user.id))
async def cmd_admin_as_promo(message: Message, container: Any) -> None:
    if not getattr(container.settings, "PROMO_ADMIN_ENABLED", True):
        await render(message, "❌ У тебя нет прав администратора")
        return
    await _access_request(message, container, section="promo_req", title=TA.REQUEST_TITLE_ADMIN)
