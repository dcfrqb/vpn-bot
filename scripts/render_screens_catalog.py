#!/usr/bin/env python3
"""Editable catalog of every user- and admin-facing message of release 3.0.

Renders each screen through the REAL view/text functions with fake data
(ids like 900000123, names like «Иван», prices from app.domain.plans), then
turns the dynamic values back into {placeholders} by exact substring
substitution (each substitution must hit, otherwise the script fails, so a
drifted text never slips through silently). Strings that live inline in
routers/services (no function to call) are read from the source with ``ast``:
the f-string itself becomes the template, ``{expr}`` -> ``{name}``.

Screens built by the screen kit (app.domain.texts.ui, app.bot.views.kit) are
recognised by their html (ui.render is wrapped while the catalog is built) and
listed by TYPE with their words only; the rest keep their html and a «вручную» mark.

Output (Markdown, strict format, see the header it writes):
    ЭКРАНЫ_3.0.md              - the catalog the owner edits: part 1 types, part 2 dictionary,
                                 part 3 screens grouped by type
    screens_catalog_mapping.md - screen id -> type, code location, callbacks, notes

Usage (python 3.11):
    PYTHONPATH=src uv run --no-project --python 3.11 --with-requirements requirements.txt \
        python scripts/render_screens_catalog.py --out-dir <dir> [--raw raw.md]

``--raw`` also dumps the fake-data renders (exact HTML as sent) for checking.
Read-only for the bot: nothing is sent, no DB/Redis/panel is touched.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# --------------------------------------------------------------------------- imports of the bot

# Every Screen rendered while the catalog is built is remembered (html -> Screen),
# so a text that came out of the kit is shown by its type and blocks. Patched
# before the bot modules are imported: module-level screens register too.
from app.domain.texts import ui as UI  # noqa: E402

RENDERED: dict[str, "UI.Screen"] = {}
_render = UI.render


def _recording_render(screen):
    html = _render(screen)
    RENDERED.setdefault(html, screen)
    return html


UI.render = _recording_render

from aiogram.types import InlineKeyboardMarkup  # noqa: E402

from app.bot import views as V  # noqa: E402
from app.bot.views import admin as VA  # noqa: E402
from app.bot.views import broadcast as VB  # noqa: E402
from app.bot.views import connect as VC  # noqa: E402
from app.bot.views import devices as VD  # noqa: E402
from app.bot.views import menu as VM  # noqa: E402
from app.bot.views import money as VMo  # noqa: E402
from app.bot.views import notify as VN  # noqa: E402
from app.bot.views import support as VS  # noqa: E402
from app.bot.callbacks import Adm, Nav  # noqa: E402
from app.domain import plans as P  # noqa: E402
from app.domain.models import DeviceInfo, PromoOutcome, PromoReward, SubscriptionState  # noqa: E402
from app.domain.texts import admin as TA  # noqa: E402
from app.domain.texts import checkout as TCh  # noqa: E402
from app.domain.texts import common as TCo  # noqa: E402
from app.domain.texts import connect as TCn  # noqa: E402
from app.domain.texts import devices as TD  # noqa: E402
from app.domain.texts import notify as TN  # noqa: E402
from app.domain.texts import promo as TP  # noqa: E402
from app.domain.texts import days_ru, fmt_date_msk, fmt_gb, fmt_rub, h, months_ru  # noqa: E402

# --------------------------------------------------------------------------- fake data

TG = 900000123
TG2 = 900000456
FIRST, USERNAME = "Иван", "ivan_p"
NAME = "Иван"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
EXP = NOW + timedelta(days=11, hours=12)          # "12 дней"
EXP_TODAY = NOW + timedelta(hours=5)
EXPIRED = NOW - timedelta(days=4)
GRACE_UNTIL = NOW + timedelta(days=2, hours=3)
GIB = 1024 ** 3
SUB_URL = "https://sub.crs-projects.com/AbC123xyz"
OBHOD_URL = "https://sub.crs-projects.com/ObH456qwe"
ARTICLE_URL = "https://telegra.ph/Kak-podklyuchit-CRS-VPN-09-23"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti--CRS-VPN-04-08"
SUPPORT = "dcfrq"
SUPPORT_URL = "https://t.me/dcfrq"
PAY_URL = "https://yoomoney.ru/checkout/payments/v2/contract?orderId=2f8a1c3e-000f-5000-9000-1b2c3d4e5f60"
EXT_ID = "2f8a1c3e-000f-5000-9000-1b2c3d4e5f60"
PID = 4812
BOT_USERNAME = "crs_vpn_bot"

D_EXP = fmt_date_msk(EXP)
D_EXPIRED = fmt_date_msk(EXPIRED)
DT_GRACE = fmt_date_msk(GRACE_UNTIL, with_time=True)
DT_NOW = fmt_date_msk(NOW, with_time=True)
D_NOW = fmt_date_msk(NOW)
DAYS12 = days_ru(12)

# placeholder glossary (Russian, shown at the top of the catalog)
GLOSSARY = {
    "id": "Telegram ID пользователя",
    "name": "имя пользователя из Telegram",
    "username": "username без @",
    "plan": "название тарифа (Lite, Standard, Pro...)",
    "date": "дата, ДД.ММ.ГГГГ по Москве",
    "datetime": "дата и время, ДД.ММ.ГГГГ ЧЧ:ММ по Москве",
    "days_left": "сколько дней осталось, число со словом («12 дней»)",
    "days": "число дней (со словом, если так в тексте)",
    "months": "срок, число со словом («3 месяца»)",
    "price": "сумма в рублях со знаком («1 199 ₽»)",
    "stars": "цена в звездах Telegram (число)",
    "url": "ссылка подписки",
    "obhod_url": "ссылка обхода",
    "obhod_used": "сколько трафика обхода израсходовано («12,4 ГБ»)",
    "obhod_limit": "лимит обхода («100 ГБ») или «без лимита»",
    "devices_used": "сколько устройств подключено",
    "device_limit": "лимит устройств по тарифу",
    "device": "название устройства (модель или платформа)",
    "last_seen": "когда устройство было онлайн («сегодня», «вчера», «3 дня назад»)",
    "code": "промокод",
    "support": "контакт поддержки (@username)",
    "support_url": "ссылка на поддержку t.me/...",
    "article_url": "ссылка на статью-инструкцию (CONNECT_ARTICLE_URL)",
    "pay_url": "ссылка на оплату ЮKassa",
    "link": "ссылка-подарок t.me/<бот>?start=g_...",
    "features": "список особенностей тарифа, строки «· ...» из каталога тарифов",
    "payment_id": "номер платежа в БД бота",
    "external_id": "ID платежа в ЮKassa",
    "request_id": "номер запроса на возврат",
    "reason": "причина (текст)",
    "error": "текст ошибки",
    "seconds": "сколько секунд ждать",
    "grace_days": "длительность льготного периода в днях («3 дня»)",
    "daily_gb": "лимит трафика в сутки на льготном периоде, ГБ",
    "text_html": "текст рассылки, который ввел админ",
}


# --------------------------------------------------------------------------- entry model


@dataclass
class Entry:
    id: str
    section: str
    source: str
    when: str
    text: str                      # template with {placeholders}
    raw: str                       # exact render with fake data
    buttons: list                  # [[(label_tmpl, target_tmpl)]]
    raw_buttons: list
    fmt: str = "HTML"
    note: str = ""
    code_loc: str = ""             # English: where to write the edit back
    layout: str = ""               # English: buttons/layout built by code logic
    old: Optional[dict] = None
    type: str = "result"           # one of UI.TYPES
    screen: Any = None             # the kit Screen, when the text came out of the kit
    subs: tuple = ()               # [(fake value, placeholder)] as applied


ENTRIES: list[Entry] = []
SECTIONS = ["Пользователь", "Оплата", "Устройства", "Промо и подарки", "Возвраты", "Уведомления", "Админка",
            "Ошибки"]

FMT_HTML = "HTML"
FMT_PLAIN = "обычный текст (теги не работают: бот экранирует < > &)"
FMT_ALERT = "всплывающее уведомление над чатом (без тегов, до 200 символов)"
FMT_ALERT_SMALL = "короткая подсказка вверху экрана (без тегов, до 200 символов)"
FMT_INVOICE = "счет Telegram Stars (без тегов; заголовок до 32 символов)"


def kb_rows(markup: Optional[InlineKeyboardMarkup]) -> list:
    if markup is None:
        return []
    out = []
    for row in markup.inline_keyboard:
        line = []
        for b in row:
            if b.url:
                line.append((b.text, f"url:{b.url}"))
            else:
                line.append((b.text, f"cb:{b.callback_data}"))
        if line:
            out.append(line)
    return out


def _sub(s: str, subs) -> str:
    for find, repl in subs:
        s = s.replace(find, repl)
    return s


MISSING_SUBS: list[str] = []

# Substitutions for screens whose wording moved to the kit layout (label: value lines);
# stale pairs of the original call are dropped for these ids.
SUBS_FIX = {
    "admin.home": [("Пользователей: 1482 (сегодня +7)", "Пользователей: {total_users} (сегодня +{today_users})"),
                   ("Активных подписок: 213", "Активных подписок: {active}"),
                   ("Выручка сегодня: 898\xa0₽, за 30 дней: 48\xa0750\xa0₽",
                    "Выручка сегодня: {revenue_today}, за 30 дней: {revenue_30d}")],
    "admin.stats": [("Пользователи: 1482", "Пользователи: {total_users}"), ("сегодня +7", "сегодня +{today_users}"),
                    ("Активные подписки: 213", "Активные подписки: {active}"), ("Оплат: 905", "Оплат: {paid_total}"),
                    ("сегодня 2", "сегодня {paid_today}"), ("Выручка: 312\xa0450\xa0₽", "Выручка: {revenue_total}")],
    "admin.users": [("Всего 1482, стр. 2 из 149", "Всего {total}, стр. {page} из {pages}")],
    "admin.whois": [("ID в панели: 1734", "ID в панели: {panel_id}")],
    "admin.referral": [("Активаций: 41", "Активаций: {activations}"), ("С зачетом: 12", "С зачетом: {paying}")],
    "admin.obhod": [("Активных: 38 из 44", "Активных: {active} из {total}")],
    "admin.promo_card": [("<b>AUTUMN7 ", "<b>{code} ")],
    "admin.promo_created": [("<b>AUTUMN7 ", "<b>{code} ")],
    "admin.broadcast.draft": [("сейчас около 205", "сейчас около {audience}")],
    "admin.broadcast.confirm": [("около 205 получателей", "около {audience} получателей")],
    "admin.broadcast.progress": [("Подарок начислено: 120", "Подарок начислено: {credited}")],
    "admin.alert.paid": [("Иван Петров", "{name}")],
    "admin.alert.not_provisioned": [("Payment row id: 4812", "Payment row id: {payment_id}")],
    "admin.alert.gift_pending": [("Покупатель: 900000123", "Покупатель: {id}"),
                                 ("Payment row id: 4812", "Payment row id: {payment_id}")],
    "admin.alert.panel_down": [("Проверок подряд: 3", "Проверок подряд: {fails}")],
}


def _guess_type(id: str, fmt: str) -> str:
    if fmt in (FMT_ALERT, FMT_ALERT_SMALL) or fmt.startswith(FMT_ALERT):
        return "toast"
    if id.startswith(("admin.alert", "admin.report", "admin.review", "admin.refund", "admin.request")):
        return "admin_alert"
    if id.startswith("admin."):
        return "admin_screen"
    return "result"


def add(id: str, section: str, source: str, when: str, text: Any, markup: Any = None, *, subs=(), opt=(),
        fmt: str = FMT_HTML, note: str = "", code: str = "", layout: str = "", type: str = "") -> Entry:
    """Register a rendered screen. ``text``: a kit View, a Screen or the sent text.
    ``subs``: [(exact fake value, placeholder)], applied longest-first to the
    text and to every button label/target."""
    assert section in SECTIONS, section
    assert not any(e.id == id for e in ENTRIES), f"duplicate id {id}"
    screen = None
    if isinstance(text, tuple) and hasattr(text, "screen"):  # kit.View
        screen, markup, text = text.screen, text.markup, text.text
    elif isinstance(text, UI.Screen):
        screen, text = text, text.html()
    if screen is None:
        screen = RENDERED.get(text)
    if isinstance(markup, list) and any(not isinstance(c, str) for row in markup for _, c in
                                        ((b.text, b) if not isinstance(b, tuple) else b for b in row)):
        markup = V.kb(markup)
    rows = markup if isinstance(markup, list) else kb_rows(markup)
    blob = text + "\n" + "\n".join(f"{t}\n{c}" for row in rows for t, c in row)
    fix = [(a.replace("\\xa0", "\xa0"), b) for a, b in SUBS_FIX.get(id, [])]
    subs = sorted(list(subs) + fix + [p for p in opt if p[0] in blob], key=lambda p: -len(p[0]))
    for find, _ in subs:
        if find not in blob and id not in SUBS_FIX:
            MISSING_SUBS.append(f"[{id}] substitution {find!r} not found")
    subs = [p for p in subs if p[0] in blob]
    tmpl = _sub(text, subs)
    t_rows = [[(_sub(t, subs), _sub(c, subs)) for t, c in row] for row in rows]
    kind = screen.type if screen is not None else (type or _guess_type(id, fmt))
    if screen is not None:
        fmt = FMT_HTML
    e = Entry(id=id, section=section, source=source, when=when, text=tmpl, raw=text, buttons=t_rows,
              raw_buttons=rows, fmt=fmt, note=note, code_loc=code or source, layout=layout, type=kind,
              screen=screen, subs=tuple(subs))
    ENTRIES.append(e)
    return e


# --------------------------------------------------------------------------- static (ast) templates

_WRAPPERS = {"h", "int", "str", "float", "fmt_rub", "fmt_date_msk", "days_ru", "months_ru", "fmt_gb", "_he",
             "_rub", "escape_html", "abs", "len", "repr"}
_FILES: dict[str, ast.Module] = {}


def _tree(path: str) -> ast.Module:
    if path not in _FILES:
        _FILES[path] = ast.parse((SRC / "app" / path).read_text(encoding="utf-8"))
    return _FILES[path]


_ID_NAMES = {"tg": "id", "uid": "id", "telegram_id": "id", "tg_id": "id", "user_id": "id"}
_OUTER_NAMES = {"months_ru": "months", "days_ru": "days"}


def _ph(node: ast.AST, names: dict) -> str:
    src = ast.unparse(node)
    if src in names:
        return names[src]
    n = node
    outer = None
    while True:
        if isinstance(n, ast.IfExp):
            n = n.body
        elif isinstance(n, ast.BoolOp):
            n = n.values[0]
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _WRAPPERS and n.args:
            if outer is None:
                outer = n
            n = n.args[0]
        else:
            break
    if isinstance(outer, ast.Call) and outer.func.id == "fmt_date_msk":
        with_time = any(k.arg == "with_time" and getattr(k.value, "value", False) for k in outer.keywords)
        return "datetime" if with_time else "date"
    if isinstance(outer, ast.Call) and outer.func.id in _OUTER_NAMES:
        return _OUTER_NAMES[outer.func.id]
    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args
            and isinstance(n.args[0], ast.Constant)):
        return str(n.args[0].value)
    while isinstance(n, ast.Subscript):
        n = n.value
    if isinstance(n, ast.Attribute):
        return "error" if n.attr == "__name__" else _ID_NAMES.get(n.attr, n.attr)
    if isinstance(n, ast.Name):
        return _ID_NAMES.get(n.id, n.id)
    return re.sub(r"\W+", "_", ast.unparse(n)).strip("_")[:30] or "value"


def _stringish(node: ast.AST) -> bool:
    return isinstance(node, (ast.Constant, ast.JoinedStr)) and not (
        isinstance(node, ast.Constant) and not isinstance(node.value, str))


def _tmpl(node: ast.AST, names: dict) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                inner = v.value
                if isinstance(inner, ast.IfExp) and ast.unparse(inner) not in names:
                    a, b = _tmpl(inner.body, names), _tmpl(inner.orelse, names)
                    if a is not None and b is not None and (a == "" or b == ""):
                        out.append(a or b)
                        continue
                out.append("{" + _ph(inner, names) + "}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _tmpl(node.left, names), _tmpl(node.right, names)
        if a is not None and b is not None:
            return a + b
        bad = (ast.List, ast.Tuple, ast.Dict, ast.ListComp)
        if a is not None and b is None and not isinstance(node.right, bad):
            return a + "{" + _ph(node.right, names) + "}"
        if b is not None and a is None and not isinstance(node.left, bad) and _stringish(node.right):
            return "{" + _ph(node.left, names) + "}" + b
        return None
    if isinstance(node, ast.IfExp):
        a, b = _tmpl(node.body, names), _tmpl(node.orelse, names)
        if a is not None and b is not None and (a == "" or b == ""):
            return a or b
    return None


STATIC_MISSING: list[str] = []


def static(path: str, anchor: str, names: Optional[dict] = None) -> str:
    """Template of the outermost string expression in ``src/app/<path>``
    that contains ``anchor``. ``names`` maps unparsed exprs to placeholder names."""
    names = names or {}
    best, best_len = None, -1
    for node in ast.walk(_tree(path)):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.IfExp)):
            continue
        t = _tmpl(node, names)
        if t is None or anchor not in t:
            continue
        span = (node.end_lineno - node.lineno) * 10000 + (node.end_col_offset - node.col_offset)
        if span > best_len:
            best, best_len = t, span
    if best is None:
        STATIC_MISSING.append(f"{path} / {anchor!r}")
        return f"(текст собирается в коде: src/app/{path}, строка с «{anchor}»)"
    return best


def add_static(id: str, section: str, path: str, anchor: str, when: str, *, names=None, fmt: str = FMT_HTML,
               buttons=None, note: str = "", func: str = "", wrap=None, layout: str = "") -> Entry:
    t = static(path, anchor, names)
    if wrap:
        t = wrap(t)
    src = f"src/app/{path}" + (f":{func}" if func else "") + f" (строка с «{anchor[:40]}»)"
    rows = buttons or []
    return add(id, section, src, when, t, rows, fmt=fmt, note=note,
               code=f"src/app/{path}" + (f" {func}()" if func else "") + f", string literal containing {anchor[:40]!r}",
               layout=layout)


def btn_rows(*rows) -> list:
    """Static buttons: btn_rows([("⬅️ Назад", "cb:...")], ...)."""
    return [list(r) for r in rows]


# --------------------------------------------------------------------------- states


def st(**kw) -> SubscriptionState:
    base = dict(telegram_id=TG, has_panel_user=True)
    base.update(kw)
    return SubscriptionState(**base)


ST_NONE = st(has_panel_user=False)
ST_TRIAL = st(active=True, plan_code="standard", expires_at=EXP, device_limit=5, devices_used=1, is_trial=True,
              subscription_url=SUB_URL)
ST_ACTIVE = st(active=True, plan_code="standard", expires_at=EXP, device_limit=5, devices_used=3,
               subscription_url=SUB_URL)
ST_ACTIVE_NOCOUNT = st(active=True, plan_code="standard", expires_at=EXP, device_limit=5, devices_used=None,
                       subscription_url=SUB_URL)
ST_TODAY = st(active=True, plan_code="lite", expires_at=EXP_TODAY, device_limit=2, devices_used=1,
              subscription_url=SUB_URL)
ST_PRO = st(active=True, plan_code="pro", expires_at=EXP, device_limit=10, devices_used=4, obhod_active=True,
            obhod_used_bytes=int(12.4 * GIB), obhod_limit_bytes=100 * GIB, subscription_url=SUB_URL,
            obhod_subscription_url=OBHOD_URL)
ST_PRO_PREP = st(active=True, plan_code="pro", expires_at=EXP, device_limit=10, devices_used=4, obhod_active=False,
                 subscription_url=SUB_URL)
ST_LIFETIME = st(active=True, plan_code="premium", expires_at=datetime(2099, 12, 31, tzinfo=timezone.utc),
                 is_lifetime=True, device_limit=15, devices_used=6, subscription_url=SUB_URL)
ST_GRACE = st(active=False, plan_code="standard", expires_at=EXPIRED, grace_until=GRACE_UNTIL,
              subscription_url=SUB_URL)
ST_EXPIRED = st(active=False, plan_code="standard", expires_at=EXPIRED, subscription_url=SUB_URL)
ST_STALE = st(active=True, plan_code="standard", expires_at=EXP, device_limit=5, stale=True, has_panel_user=False)

S_ID = [(str(TG), "{id}")]
S_NAME = [(f"Имя: {NAME}", "Имя: {name}")]
S_EXP = [(D_EXP, "{date}"), (DAYS12, "{days_left}")]

MENU_SRC = ("src/app/domain/texts/menu.py:main_menu_text (profile_block + subscription_block + _obhod_block "
            "+ STALE_NOTE + TRIAL_OFFER); кнопки: src/app/bot/views/menu.py:render")
MENU_CODE = ("text: src/app/domain/texts/menu.py (profile_block, subscription_block, _obhod_block, TRIAL_OFFER, "
             "STALE_NOTE); buttons: src/app/domain/texts/common.py BTN_* ; order: src/app/bot/views/menu.py:render")
MENU_LAYOUT = ("rows built in views/menu.py:render: Connect; Trial (only when trial_available); Subscription; "
               "Devices; [Refresh | Help]; Admin panel (admins only)")


def section_user() -> None:
    sec = "Пользователь"

    def menu(id_, state, when, *, trial=False, admin=False, subs=(), note=""):
        text, mk = VM.render(TG, NAME, state, is_admin=admin, trial_available=trial)
        add(id_, sec, MENU_SRC, when, text, mk, subs=S_ID + S_NAME + list(subs), note=note, code=MENU_CODE,
            layout=MENU_LAYOUT)

    menu("user.menu.none", ST_NONE, "/start или «В главное меню», подписки нет, пробный период доступен",
         trial=True, note="кнопка «🎁 Попробовать 5 дней бесплатно» и строка про пробный период только пока триал "
                          "доступен")
    menu("user.menu.none_no_trial", ST_NONE, "главное меню, подписки нет, пробный уже использован")
    menu("user.menu.trial", ST_TRIAL, "главное меню во время пробного периода",
         subs=S_EXP + [("Тариф: Standard (пробный)", "Тариф: {plan} (пробный)"),
                       ("Устройства: 1 из 5", "Устройства: {devices_used} из {device_limit}")])
    menu("user.menu.active", ST_ACTIVE, "главное меню, платная подписка (не Pro)",
         subs=S_EXP + [("Тариф: Standard", "Тариф: {plan}"),
                       ("Устройства: 3 из 5", "Устройства: {devices_used} из {device_limit}")],
         note="если число подключенных устройств неизвестно, строка «Устройства: до {device_limit}»")
    menu("user.menu.active_pro", ST_PRO, "главное меню, тариф Pro, ссылка обхода готова",
         subs=S_EXP + [("Тариф: Pro", "Тариф: {plan}"),
                       ("Устройства: 4 из 10", "Устройства: {devices_used} из {device_limit}"),
                       (f"Использовано: {fmt_gb(ST_PRO.obhod_used_bytes)} из {fmt_gb(ST_PRO.obhod_limit_bytes)}",
                        "Использовано: {obhod_used} из {obhod_limit}")])
    menu("user.menu.active_pro_preparing", ST_PRO_PREP, "главное меню, Pro, ссылка обхода еще не готова",
         subs=S_EXP + [("Тариф: Pro", "Тариф: {plan}"),
                       ("Устройства: 4 из 10", "Устройства: {devices_used} из {device_limit}")])
    menu("user.menu.expires_today", ST_TODAY, "главное меню в последний день подписки",
         subs=[(fmt_date_msk(EXP_TODAY), "{date}"),
               ("Тариф: Lite", "Тариф: {plan}"),
               ("Устройства: 1 из 2", "Устройства: {devices_used} из {device_limit}")])
    menu("user.menu.lifetime", ST_LIFETIME, "главное меню, бессрочная подписка",
         subs=[("Тариф: Премиум тариф", "Тариф: {plan}"),
               ("Устройства: 6 из 15", "Устройства: {devices_used} из {device_limit}")])
    menu("user.menu.grace", ST_GRACE, "главное меню в льготный период (оплата кончилась, доступ урезан)",
         subs=[(D_EXPIRED, "{date}"), (DT_GRACE, "{datetime}")])
    menu("user.menu.expired", ST_EXPIRED, "главное меню, подписка истекла (пробный уже был)",
         subs=[(D_EXPIRED, "{date}")])
    menu("user.menu.stale", ST_STALE, "главное меню, когда панель не ответила (данные из кэша/БД)",
         subs=S_EXP + [("Тариф: Standard", "Тариф: {plan}"), ("Устройства: до 5", "Устройства: до {device_limit}")])
    menu("user.menu.admin", ST_ACTIVE, "главное меню у администратора (добавлена кнопка админки)", admin=True,
         subs=S_EXP + [("Тариф: Standard", "Тариф: {plan}"),
                       ("Устройства: 3 из 5", "Устройства: {devices_used} из {device_limit}")])
    add("user.menu.refreshed", sec, "src/app/domain/texts/common.py:REFRESHED",
        "после «🔄 Обновить» в главном меню", TCo.REFRESHED, fmt=FMT_ALERT_SMALL,
        code="src/app/domain/texts/common.py REFRESHED")

    # --- connect
    conn_code = "src/app/domain/texts/connect.py; buttons src/app/bot/views/connect.py"
    text, mk = VC.success(ST_ACTIVE, article_url=ARTICLE_URL)
    add("user.connect.success", sec,
        "src/app/domain/texts/connect.py:success + OBHOD_PRO_ONLY + obhod_ready; src/app/bot/views/connect.py:success",
        "«🚀 Подключиться», подписка активна, тариф не Pro", text, mk,
        subs=[(SUB_URL, "{url}"), (ARTICLE_URL, "{article_url}")], code=conn_code,
        note="ВНИМАНИЕ (баг 3.0): у не-Pro после блока «Есть в тарифе Pro» код дописывает еще и блок готовой "
             "ссылки обхода с пустой ссылкой (views/connect.py:success, ветка else у grace). Кнопка "
             "«📖 Инструкция» только если задан CONNECT_ARTICLE_URL",
        layout="views/connect.py:success: main link url button; obhod url button (Pro+ready); «Нужно больше "
               "обхода» (Pro, packages on sale); Refresh (Pro, obhod not ready); article url (if set); back")
    text, mk = VC.success(ST_PRO, article_url=ARTICLE_URL)
    add("user.connect.success_pro", sec,
        "src/app/domain/texts/connect.py:success + obhod_ready (OBHOD_ABOUT); src/app/bot/views/connect.py:success",
        "«🚀 Подключиться», тариф Pro, ссылка обхода готова", text, mk,
        subs=[(SUB_URL, "{url}"), (OBHOD_URL, "{obhod_url}"), (ARTICLE_URL, "{article_url}"),
              (f"Использовано: {fmt_gb(ST_PRO.obhod_used_bytes)} из {fmt_gb(ST_PRO.obhod_limit_bytes)}",
               "Использовано: {obhod_used} из {obhod_limit}")],
        code=conn_code, layout="same as user.connect.success")
    text, mk = VC.success(ST_PRO_PREP, article_url=ARTICLE_URL)
    add("user.connect.success_pro_preparing", sec,
        "src/app/domain/texts/connect.py:success + OBHOD_PREPARING + obhod_ready; src/app/bot/views/connect.py:success",
        "«🚀 Подключиться», Pro, ссылка обхода еще готовится", text, mk,
        subs=[(SUB_URL, "{url}"), (ARTICLE_URL, "{article_url}")], code=conn_code,
        note="ВНИМАНИЕ (баг 3.0): после «Готовим твою ссылку обхода» код дописывает блок готовой ссылки с пустой "
             "ссылкой, как у не-Pro", layout="same as user.connect.success")
    text, mk = VC.success(ST_GRACE, article_url=ARTICLE_URL)
    add("user.connect.success_grace", sec,
        "src/app/domain/texts/connect.py:success + OBHOD_PRO_ONLY + grace_note; src/app/bot/views/connect.py:success",
        "«🚀 Подключиться» в льготный период (ссылка еще работает)", text, mk,
        subs=[(SUB_URL, "{url}"), (ARTICLE_URL, "{article_url}"), (DT_GRACE, "{datetime}")], code=conn_code,
        layout="same as user.connect.success")
    text, mk = VC.no_subscription(trial_available=True, support_handle=SUPPORT)
    add("user.connect.no_sub_trial", sec, "src/app/domain/texts/connect.py:NO_SUBSCRIPTION; views/connect.py:no_subscription",
        "«🚀 Подключиться» без подписки, пробный доступен", text, mk, subs=[(SUPPORT_URL, "{support_url}")],
        code=conn_code)
    text, mk = VC.no_subscription(trial_available=False, support_handle=SUPPORT)
    add("user.connect.no_sub", sec, "src/app/domain/texts/connect.py:NO_SUBSCRIPTION_NO_TRIAL; views/connect.py:no_subscription",
        "«🚀 Подключиться» без подписки, пробный уже был", text, mk, subs=[(SUPPORT_URL, "{support_url}")],
        code=conn_code)
    text, mk = VC.error(SUPPORT)
    add("user.connect.error", sec, "src/app/domain/texts/connect.py:ERROR; views/connect.py:error",
        "«🚀 Подключиться», подписка есть, но ссылку сейчас не получить (панель недоступна)", text, mk,
        subs=[(SUPPORT_URL, "{support_url}")], code=conn_code)
    for cid, const, when in [
        ("user.connect.trial_started", "TRIAL_STARTED", "нажал «Попробовать 5 дней бесплатно», триал включен"),
        ("user.connect.trial_already_used", "TRIAL_ALREADY_USED", "нажал кнопку триала, а он уже был"),
        ("user.connect.trial_not_eligible", "TRIAL_NOT_ELIGIBLE", "кнопка триала, но аккаунту он не положен"),
        ("user.connect.trial_unavailable", "TRIAL_UNAVAILABLE",
         "кнопка триала при сбое; также ответ на /trial, когда триал выключен флагом"),
        ("user.connect.trial_busy", "TRIAL_BUSY", "повторное нажатие кнопки триала, пока он включается"),
    ]:
        add(cid, sec, f"src/app/domain/texts/connect.py:{const}", when, getattr(TCn, const),
            fmt=FMT_ALERT_SMALL if const == "TRIAL_STARTED" else FMT_ALERT,
            code=f"src/app/domain/texts/connect.py {const}",
            note="в ответ на /trial (флаг выключен) этот же текст приходит обычным сообщением"
            if const == "TRIAL_UNAVAILABLE" else "")

    # --- help, myid, commands
    for hid, unlink in (("user.help", False), ("user.help.unlink_on", True)):
        text, mk = VS.render(support_handle=SUPPORT, privacy_url=PRIVACY_URL, unlink_enabled=unlink)
        add(hid, sec, "src/app/domain/texts/common.py:help_text; views/support.py:render",
            "«ℹ️ Помощь» или /help" + (" (включено отвязывание устройств)" if unlink else ""), text, mk,
            subs=[(SUPPORT_URL, "{support_url}"), (PRIVACY_URL, "{privacy_url}")],
            code="src/app/domain/texts/common.py help_text (change_device variants), BTN_SUPPORT/BTN_OFFER/"
                 "BTN_PRIVACY; OFFER_URL",
            note="кнопка «🔒 Политика конфиденциальности» только если задан PRIVACY_URL",
            layout="views/support.py:render: support url, offer url, privacy url (if set), back to main")
    add("user.myid", sec, "src/app/domain/texts/common.py:myid_text", "команда /myid", TCo.myid_text(TG, False),
        subs=S_ID, code="src/app/domain/texts/common.py myid_text")
    add("user.myid_admin", sec, "src/app/domain/texts/common.py:myid_text", "команда /myid у администратора",
        TCo.myid_text(TG, True), subs=S_ID, code="src/app/domain/texts/common.py myid_text")
    from app.bot.routers.start import BOT_COMMANDS
    cmds = "\n".join(f"/{c.command}: {c.description}" for c in BOT_COMMANDS)
    add("user.commands", sec, "src/app/bot/routers/start.py:BOT_COMMANDS",
        "меню команд Telegram (кнопка «Меню» у поля ввода); /trial и /promo скрываются, если выключены",
        cmds, fmt="список команд: слева команда, справа описание (без тегов)",
        code="src/app/bot/routers/start.py BOT_COMMANDS (description of each BotCommand)")

    # --- broadcasts, user side
    add("user.stop", sec, "src/app/domain/texts/admin.py:STOP_DONE", "команда /stop (отписка от рассылок)",
        TA.STOP_DONE, code="src/app/domain/texts/admin.py STOP_DONE")
    add("user.unsub_alert", sec, "src/app/domain/texts/admin.py:UNSUB_ALERT",
        "кнопка «🔕 Отписаться от рассылок» под рассылкой", TA.UNSUB_ALERT, fmt=FMT_ALERT_SMALL,
        code="src/app/domain/texts/admin.py UNSUB_ALERT")

    # --- site login relay (2.x router kept in 3.0)
    from app.routers import site_login as SL
    for sid, const, when, fmt in [
        ("user.site_login.off", "FEATURE_OFF_TEXT", "/start login_... (вход на сайт), функция выключена", FMT_PLAIN),
        ("user.site_login.unavailable", "SITE_UNAVAILABLE_TEXT", "/start login_..., сайт не ответил", FMT_PLAIN),
        ("user.site_login.unavailable_alert", "SITE_UNAVAILABLE_CALLBACK_TEXT",
         "кнопка подтверждения входа, сайт не ответил", FMT_ALERT_SMALL),
    ]:
        add(sid, sec, f"src/app/routers/site_login.py:{const}", when, getattr(SL, const), fmt=fmt,
            code=f"src/app/routers/site_login.py {const}",
            note="остальные тексты и кнопки входа на сайт присылает сам сайт, в боте их нет")


# --------------------------------------------------------------------------- payments


def _plan_options(gift: bool = False, legacy: Optional[str] = None) -> list:
    """Same numbers as CheckoutServiceImpl.plan_options/period_options (catalog prices)."""
    codes = list(P.MENU_PLAN_CODES) + ([legacy] if legacy and not gift else [])
    return [VMo.PlanOption(code=c, name=P.get_plan_name(c), features=tuple(P.get_plan_features(c)),
                           from_rub=min(P.PLAN_CATALOG[c]["prices"].values())) for c in codes]


def _period_options(code: str) -> list:
    prices = P.PLAN_CATALOG[code]["prices"]
    base = prices.get(1)
    out = []
    for m in sorted(prices):
        a = prices[m]
        saving = max(0, int(round((1 - a / (base * m)) * 100))) if base and m > 1 else 0
        out.append(VMo.PeriodOption(months=m, amount_rub=a, saving_percent=saving))
    return out


def _features_sub(code: str) -> list:
    lines = "\n".join(f"· {h(f)}" for f in P.get_plan_features(code))
    return [(lines, "{features}")]


def section_payments() -> None:
    sec = "Оплата"
    money_code = "src/app/domain/texts/checkout.py; buttons src/app/bot/views/money.py"
    pro_1 = P.PLAN_CATALOG["pro"]["prices"][1]

    text, mk = VMo.plans_view(_plan_options(), gifts=True)
    add("pay.plans", sec, "src/app/domain/texts/checkout.py:plans_screen + btn_plan; views/money.py:plans_view",
        "«💳 Подписка»: список тарифов", text, mk, code=money_code,
        note="блоки тарифов и кнопки строятся кодом из каталога src/app/domain/plans.py (PLAN_CATALOG: display, "
             "features, prices); шаблон кнопки: «{plan} · от {price}/мес». Кнопка «Подарить подписку» только при "
             "GIFTS_ENABLED. У клиента со старым тарифом (Базовый/Премиум) в конце списка добавляется его тариф",
        layout="views/money.py:plans_view: one row per plan (btn_plan), gift row (GIFTS_ENABLED), back")
    text, mk = VMo.periods_view("pro", P.get_plan_name("pro"), P.get_plan_features("pro"), _period_options("pro"))
    add("pay.periods", sec, "src/app/domain/texts/checkout.py:periods_screen + btn_period; views/money.py:periods_view",
        "выбрал тариф: выбор срока (пример для Pro)", text, mk, subs=_features_sub("pro") + [("<b>Pro</b>", "<b>{plan}</b>")],
        code=money_code,
        note="кнопки сроков строятся из цен тарифа; шаблон кнопки: «{months} · {price} (выгода N%)», выгода "
             "считается от цены за 1 месяц",
        layout="views/money.py:periods_view: one row per period (btn_period), back to plans")
    sub_checkout = [(PAY_URL, "{pay_url}"), (fmt_rub(pro_1), "{price}"), ("Тариф: Pro", "Тариф: {plan}"),
                    (f"Срок: {months_ru(1)}", "Срок: {months}")]
    text, mk = VMo.checkout_view(plan_code="pro", name="Pro", months=1, amount_rub=pro_1, payment_id=PID, url=PAY_URL,
                                 autorenew=None, stars=None)
    add("pay.checkout", sec, "src/app/domain/texts/checkout.py:checkout_screen; views/money.py:checkout_view",
        "выбрал срок: экран оплаты (автопродление и звезды выключены флагами)", text, mk, subs=sub_checkout,
        code=money_code, layout="views/money.py:checkout_view: pay url; stars (STARS_ENABLED); autorenew toggle "
                                "(AUTOPAY_ENABLED); check payment; back")
    text, mk = VMo.checkout_view(plan_code="pro", name="Pro", months=1, amount_rub=pro_1, payment_id=PID, url=PAY_URL,
                                 autorenew=False, stars=None)
    add("pay.checkout.autopay_off", sec, "src/app/domain/texts/checkout.py:checkout_screen; views/money.py:checkout_view",
        "экран оплаты при AUTOPAY_ENABLED, автопродление выключено", text, mk, subs=sub_checkout, code=money_code)
    text, mk = VMo.checkout_view(plan_code="pro", name="Pro", months=1, amount_rub=pro_1, payment_id=PID, url=PAY_URL,
                                 autorenew=True, stars=None)
    add("pay.checkout.autopay_on", sec, "src/app/domain/texts/checkout.py:checkout_screen; views/money.py:checkout_view",
        "экран оплаты после «Включить автопродление»", text, mk, subs=sub_checkout, code=money_code)
    text, mk = VMo.checkout_view(plan_code="pro", name="Pro", months=1, amount_rub=pro_1, payment_id=PID, url=PAY_URL,
                                 autorenew=None, stars=300)
    add("pay.checkout.stars", sec, "src/app/domain/texts/checkout.py:checkout_screen + btn_pay_stars",
        "экран оплаты при STARS_ENABLED (можно звездами)", text, mk,
        subs=sub_checkout + [("300 ⭐", "{stars} ⭐"), ("звездами (300)", "звездами ({stars})")], code=money_code)

    pk = [(c, P.OBHOD_PACKAGE_CATALOG[c]["display"], P.OBHOD_PACKAGE_CATALOG[c]["price"]) for c in P.OBHOD_PACKAGE_CODES]
    text, mk = VMo.obhod_packages_view(P.OBHOD_BASE_LIMIT_GB, pk)
    add("pay.obhod_packages", sec, "src/app/domain/texts/checkout.py:obhod_packages_screen + btn_obhod_package",
        "«➕ Нужно больше обхода» на экране подключения (Pro)", text, mk, code=money_code,
        note="пакеты, цены и лимит 100 ГБ берутся из src/app/domain/plans.py (OBHOD_PACKAGE_CATALOG, "
             "OBHOD_BASE_LIMIT_GB); шаблон строки «· <b>{пакет}</b>: {price}», кнопки «{пакет}: {price}»",
        layout="views/money.py:obhod_packages_view: one row per package, back")
    text, mk = VMo.obhod_packages_view(P.OBHOD_BASE_LIMIT_GB, [])
    add("pay.obhod_packages_empty", sec, "src/app/domain/texts/checkout.py:obhod_packages_screen",
        "экран пакетов обхода, когда ни один пакет не продается", text, mk, code=money_code)
    p250 = P.OBHOD_PACKAGE_CATALOG["obhod_250"]
    text, mk = VMo.checkout_view(plan_code="obhod_250", name=p250["display"], months=1, amount_rub=p250["price"],
                                 payment_id=PID, url=PAY_URL, autorenew=None, stars=None,
                                 back=Nav(s="plans", p="obhod"))
    add("pay.checkout.obhod_package", sec, "src/app/domain/texts/checkout.py:checkout_screen",
        "выбрал пакет обхода: экран оплаты", text, mk,
        subs=[(PAY_URL, "{pay_url}"), (fmt_rub(p250["price"]), "{price}"),
              (f"Тариф: {p250['display']}", "Тариф: {plan}"), (f"Срок: {months_ru(1)}", "Срок: {months}")],
        code=money_code)

    for eid, const, when, support in [
        ("pay.error.plan_unavailable", "PLAN_UNAVAILABLE", "тариф/срок сейчас не продается", False),
        ("pay.error.blocked", "PAYMENT_BLOCKED", "оплата из стоп-листа (кому не продаем)", True),
        ("pay.error.create_failed", "PAYMENT_CREATE_FAILED", "ЮKassa не создала платеж", True),
        ("pay.error.obhod_needs_pro", "OBHOD_NEEDS_PRO", "пакет обхода без активного Pro", True),
        ("pay.error.stars_unavailable", "STARS_UNAVAILABLE", "звезды выключены (экран или всплывашка)", True),
    ]:
        text, mk = VMo.message_view(getattr(TCh, const + "_SCREEN"), support=SUPPORT_URL if support else None)
        add(eid, sec, f"src/app/domain/texts/checkout.py:{const}; views/money.py:message_view", when, text, mk,
            subs=[(SUPPORT_URL, "{support_url}")] if support else [], code=f"src/app/domain/texts/checkout.py {const}",
            layout="views/money.py:message_view: optional pay/check/connect/support rows, plans, main menu")
    for eid, const, when in [
        ("pay.alert.busy", "PAYMENT_BUSY", "двойное нажатие: платеж уже создается"),
        ("pay.alert.gifts_unavailable", "GIFTS_UNAVAILABLE", "кнопки подарка при выключенных подарках"),
        ("pay.alert.autopay_unavailable", "AUTOPAY_UNAVAILABLE", "кнопка автопродления при выключенном AUTOPAY_ENABLED"),
        ("pay.alert.stale", "PRECHECK_STALE", "старая кнопка автопродления / устаревший счет в звездах"),
        ("pay.alert.price_changed", "PRECHECK_PRICE_CHANGED", "счет в звездах, а цена уже другая"),
        ("pay.alert.stars_invoice_sent", "STARS_INVOICE_SENT", "нажал «Оплатить звездами»"),
    ]:
        add(eid, sec, f"src/app/domain/texts/checkout.py:{const}", when, getattr(TCh, const), fmt=FMT_ALERT,
            code=f"src/app/domain/texts/checkout.py {const}")
    add("pay.alert.rate_limited", sec, "src/app/domain/texts/checkout.py:check_rate_limited",
        "слишком частое «Проверить оплату»", TCh.check_rate_limited(20), subs=[("20", "{seconds}")], fmt=FMT_ALERT,
        code="src/app/domain/texts/checkout.py check_rate_limited")
    add("pay.stars.invoice", sec, "src/app/domain/texts/checkout.py:stars_invoice_title + stars_invoice_description",
        "счет в звездах (заголовок, потом описание)",
        TCh.stars_invoice_title("Pro") + "\n" + TCh.stars_invoice_description("Pro", 3),
        subs=[("CRS VPN Pro", "CRS VPN {plan}"), (f"Pro, {months_ru(3)}.", "{plan}, {months}.")], fmt=FMT_INVOICE,
        code="src/app/domain/texts/checkout.py stars_invoice_title (line 1), stars_invoice_description (line 2)",
        note="в подарок описание начинается с «Подарок: »")

    # --- check payment
    for cid, const, when, kw in [
        ("pay.check.pending", "CHECK_PENDING", "«Проверить оплату», денег еще нет",
         dict(pay_url=PAY_URL, amount_rub=pro_1, check_pid=PID)),
        ("pay.check.paid", "CHECK_PAID", "«Проверить оплату», оплата прошла", dict(back_to_plans=False, connect=True)),
        ("pay.check.gift_paid", "CHECK_GIFT_PAID", "«Проверить оплату» для подарка, оплата прошла",
         dict(back_to_plans=False)),
        ("pay.check.provisioning", "CHECK_PROVISIONING", "оплата пришла, доступ еще выдается",
         dict(back_to_plans=False, support=SUPPORT_URL)),
        ("pay.check.held", "CHECK_HELD", "платеж на ручной проверке у админа", dict(back_to_plans=False, support=SUPPORT_URL)),
        ("pay.check.rejected", "CHECK_REJECTED", "админ отклонил платеж", dict(back_to_plans=False, support=SUPPORT_URL)),
        ("pay.check.canceled", "CHECK_CANCELED", "платеж отменен в ЮKassa", {}),
        ("pay.check.refunded", "CHECK_REFUNDED", "по платежу уже вернули деньги", dict(support=SUPPORT_URL)),
        ("pay.check.not_found", "CHECK_NOT_FOUND", "платеж не найден", {}),
        ("pay.check.error", "CHECK_ERROR", "не удалось проверить (ЮKassa недоступна)", dict(check_pid=PID)),
    ]:
        text, mk = VMo.message_view(getattr(TCh, const + "_SCREEN"), **kw)
        add(cid, sec, f"src/app/domain/texts/checkout.py:{const}; views/money.py:message_view", when, text, mk,
            opt=[(PAY_URL, "{pay_url}"), (fmt_rub(pro_1), "{price}"), (SUPPORT_URL, "{support_url}")],
            code=f"src/app/domain/texts/checkout.py {const}; buttons chosen in src/app/bot/routers/checkout.py:on_pay_check",
            layout="routers/checkout.py:on_pay_check picks the message_view flags per outcome")

    # --- after payment
    text = TCh.paid_user("Pro", 3, EXP)
    add("pay.paid_user", sec, "src/app/domain/texts/checkout.py:paid_user; кнопки views/money.py:paid_kb",
        "сразу после успешной оплаты подписки (приходит само)", text, VMo.paid_kb(PID, refund_button=True),
        subs=[(D_EXP, "{date}"), (f"Тариф: Pro, {months_ru(3)}", "Тариф: {plan}, {months}")],
        code="src/app/domain/texts/checkout.py paid_user; BTN_REFUND",
        note="кнопка «Не смог подключиться» (возврат за 24 часа) только при REFUND_24H_ENABLED и в первые 24 часа",
        layout="views/money.py:paid_kb: Connect; refund request (conditional)")
    add("pay.autorenew_paid", sec, "src/app/domain/texts/checkout.py:autorenew_paid_user",
        "автопродление списало деньги", TCh.autorenew_paid_user("Pro", pro_1, EXP), VMo.paid_kb(PID, refund_button=False),
        subs=[(D_EXP, "{date}"), (fmt_rub(pro_1), "{price}"), ("Тариф: Pro", "Тариф: {plan}")],
        code="src/app/domain/texts/checkout.py autorenew_paid_user")
    add("pay.obhod_package_paid", sec, "src/app/domain/texts/checkout.py:obhod_package_paid",
        "оплачен пакет обхода, лимит поднят", TCh.obhod_package_paid(), code="src/app/domain/texts/checkout.py obhod_package_paid")
    add("pay.obhod_package_manual", sec, "src/app/domain/texts/checkout.py:OBHOD_PACKAGE_MANUAL",
        "оплачен пакет обхода, но применить сам не смог", TCh.OBHOD_PACKAGE_MANUAL,
        btn_rows([(TCh.BTN_SUPPORT, "url:{support_url}")]), code="src/app/domain/texts/checkout.py OBHOD_PACKAGE_MANUAL")
    add("pay.held_user", sec, "src/app/domain/texts/checkout.py:HELD_USER",
        "оплата пришла, но платеж ушел на ручную проверку (сумма не сошлась, стоп-лист)", TCh.HELD_USER,
        btn_rows([(TCh.BTN_SUPPORT, "url:{support_url}")]), code="src/app/domain/texts/checkout.py HELD_USER")

    # --- autopay
    renew = kb_rows(VMo.TelegramMoneyUi(NS(SUPPORT_HANDLE=SUPPORT)).renew())
    add("pay.autopay.notice", sec, "src/app/domain/texts/checkout.py:autopay_notice",
        "за 3 дня до конца срока, если включено автопродление",
        TCh.autopay_notice("Pro", 1, pro_1, EXP - timedelta(days=1)),
        VMo.TelegramMoneyUi().autopay_notice(),
        subs=[(fmt_date_msk(EXP - timedelta(days=1)), "{date}"), (fmt_rub(pro_1), "{price}"),
              (f"Тариф: Pro, {months_ru(1)}", "Тариф: {plan}, {months}")],
        code="src/app/domain/texts/checkout.py autopay_notice; BTN_AUTOPAY_STOP")
    add("pay.autopay.failed", sec, "src/app/domain/texts/checkout.py:AUTOPAY_FAILED",
        "автопродление не смогло списать (первая неудача)", TCh.AUTOPAY_FAILED, renew,
        code="src/app/domain/texts/checkout.py AUTOPAY_FAILED")
    add("pay.autopay.turned_off", sec, "src/app/domain/texts/checkout.py:AUTOPAY_TURNED_OFF_FAILS",
        "автопродление выключено само (две неудачи, нет карты, тариф снят с продажи)", TCh.AUTOPAY_TURNED_OFF_FAILS,
        renew, code="src/app/domain/texts/checkout.py AUTOPAY_TURNED_OFF_FAILS")
    add("pay.autopay.stopped", sec, "src/app/domain/texts/checkout.py:autopay_stopped",
        "нажал «Отключить автопродление»", TCh.autopay_stopped(EXP), subs=[(D_EXP, "{date}")],
        code="src/app/domain/texts/checkout.py autopay_stopped", note="отправляется отдельным сообщением")
    add("pay.autopay.nothing_to_stop", sec, "src/app/domain/texts/checkout.py:AUTOPAY_NOTHING_TO_STOP",
        "нажал «Отключить автопродление», а оно и так выключено", TCh.AUTOPAY_NOTHING_TO_STOP,
        code="src/app/domain/texts/checkout.py AUTOPAY_NOTHING_TO_STOP")
    text, mk = VMo.message_view(TCh.AUTOPAY_INFO_SCREEN)
    add("pay.autopay.info", sec, "src/app/domain/texts/checkout.py:AUTOPAY_INFO",
        "не показывается: обработчик AutoPay(a=info) есть, но ни одна кнопка на него не ведет", text, mk,
        code="src/app/domain/texts/checkout.py AUTOPAY_INFO")
    add("pay.maintenance_notice", sec, "src/app/domain/texts/notify.py:MAINTENANCE_CHECKOUT_NOTICE",
        "во время техработ, при первом нажатии на тариф/оплату (раз в 10 минут)", TN.MAINTENANCE_CHECKOUT_NOTICE,
        code="src/app/domain/texts/notify.py MAINTENANCE_CHECKOUT_NOTICE")


# --------------------------------------------------------------------------- devices


DEVS = [
    DeviceInfo(hwid="hw-aaaa-11112222", platform="iOS", os_version="18.1", device_model="iPhone 15",
               user_agent="Happ/3.1.0/ios CFNetwork/1568", created_at=NOW - timedelta(days=10), updated_at=NOW),
    DeviceInfo(hwid="hw-bbbb-33334444", platform="Windows", os_version="11", device_model=None,
               user_agent="Hiddify/2.5.7", created_at=NOW - timedelta(days=20), updated_at=NOW - timedelta(days=1)),
    DeviceInfo(hwid="hw-cccc-55556666", platform="Android", os_version="14", device_model="Pixel 8",
               user_agent="v2RayTun/5.13.2", created_at=NOW - timedelta(days=90), updated_at=NOW - timedelta(days=40)),
]
DEV_SUBS = [("📱 <b>1. iPhone 15</b>", "{icon} <b>{n}. {device}</b>"), ("iOS 18.1 · Happ 3.1", "{os} · {app}"),
            (f"Добавлено: {fmt_date_msk(NOW - timedelta(days=10))}", "Добавлено: {added}"),
            ("Онлайн: сегодня", "Онлайн: {last_seen}")]
DEV_NOTE = ("карточка повторяется для каждого устройства, свежие сверху; {icon}: 📱 телефон, 💻 компьютер, "
            "📶 неизвестно; {device}: модель, иначе ОС, иначе приложение, иначе «Устройство {n}» (одно имя в "
            "карточке, кнопке и подтверждении); {os}: платформа и версия ОС, {app}: приложение из User-Agent "
            "(части, которых нет, не выводятся); {last_seen}: сегодня / вчера / N дней назад (МСК); строка "
            "«⚠️ Давно не выходило на связь», если устройство молчит больше 30 дней")


def section_devices() -> None:
    sec = "Устройства"
    code = "src/app/domain/texts/devices.py; buttons src/app/bot/views/devices.py"
    layout = ("views/devices.py:list_screen: numbered unlink buttons ❌ n in kit.grid rows of <= 4 "
              "(DEVICES_UNLINK_ENABLED) or support; plans (no active subscription); back to main. Cards: "
              "texts/devices.py:device_card, name display_name, order ordered(), app app_title")
    note = DEV_NOTE
    text, mk = VD.list_screen(DEVS, device_limit=5, unlink_enabled=False, support_handle=SUPPORT)
    add("dev.list", sec, "src/app/domain/texts/devices.py:list_text; views/devices.py:list_screen",
        "«📱 Мои устройства» или /devices (отвязывание выключено, как сейчас на проде)", text, mk,
        subs=DEV_SUBS + [("(3 из 5)", "({devices_used} из {device_limit})"), (SUPPORT_URL, "{support_url}")],
        code=code, layout=layout, note=note)
    text, mk = VD.list_screen(DEVS, device_limit=5, unlink_enabled=True)
    add("dev.list.unlink_on", sec, "src/app/domain/texts/devices.py:list_text; views/devices.py:list_screen",
        "«📱 Мои устройства» при DEVICES_UNLINK_ENABLED (номерные кнопки «❌ n»)", text, mk,
        subs=DEV_SUBS + [("(3 из 5)", "({devices_used} из {device_limit})")],
        code=code, layout=layout, note=note + "; кнопка «❌ n» на каждую карточку, номер = номер карточки, "
                                              "до 4 в ряд (5 = 3+2, 6 = 3+3); имя обрезается до 32 символов")
    text, mk = VD.list_screen(DEVS[:1], device_limit=None, unlink_enabled=False, support_handle=SUPPORT, active=False)
    add("dev.list.no_sub", sec, "src/app/domain/texts/devices.py:list_text; views/devices.py:list_screen",
        "«📱 Мои устройства» без активной подписки (устройства остались)", text, mk,
        subs=DEV_SUBS + [("Мои устройства (1)", "Мои устройства ({devices_used})"), (SUPPORT_URL, "{support_url}")],
        code=code, layout=layout, note="когда лимит неизвестен, в заголовке только число устройств")
    text, mk = VD.list_screen([], device_limit=5, unlink_enabled=False, support_handle=SUPPORT)
    add("dev.list.empty", sec, "src/app/domain/texts/devices.py:EMPTY", "«📱 Мои устройства», ни одного подключения",
        text, mk, code="src/app/domain/texts/devices.py EMPTY", layout=layout)
    text, mk = VD.ask_unlink(DEVS[0], 1)
    add("dev.ask_unlink", sec, "src/app/domain/texts/devices.py:ask_unlink; views/devices.py:ask_unlink",
        "нажал «❌ n» под карточкой", text, mk, subs=[("Отвязать iPhone 15?", "Отвязать {device}?"),
                                                     ("dv:unlink:11112222", "dv:unlink:{device_id}")],
        code="src/app/domain/texts/devices.py ask_unlink; buttons in src/app/bot/views/devices.py:ask_unlink")
    text, mk = VD.not_found()
    add("dev.not_found", sec, "src/app/domain/texts/devices.py:UNLINK_NOT_FOUND; views/devices.py:not_found",
        "отвязка устройства, которого уже нет", text, mk,
        code="src/app/domain/texts/devices.py UNLINK_NOT_FOUND; button in views/devices.py:not_found")
    add("dev.unlinked", sec, "src/app/domain/texts/devices.py:UNLINKED", "устройство отвязано", TD.UNLINKED,
        fmt=FMT_ALERT_SMALL, code="src/app/domain/texts/devices.py UNLINKED")
    add("dev.unlink_limit", sec, "src/app/domain/texts/devices.py:UNLINK_LIMIT", "больше 3 отвязок в сутки",
        TD.UNLINK_LIMIT, fmt=FMT_ALERT, code="src/app/domain/texts/devices.py UNLINK_LIMIT")


# --------------------------------------------------------------------------- promo and gifts


def section_promo() -> None:
    sec = "Промо и подарки"
    from app.bot.routers.trial_promo import result_view

    cont = NS(settings=NS(SUPPORT_HANDLE=SUPPORT, ADMIN_SUPPORT_USERNAME=None))
    pcode = "src/app/domain/texts/promo.py applied_text; buttons src/app/bot/routers/trial_promo.py:result_view"
    ocode = "src/app/domain/texts/promo.py outcome_text; button src/app/bot/routers/trial_promo.py:result_view"

    def applied(id_, code, reward, when, subs):
        text, mk = result_view(code, reward, cont)
        add(id_, sec, "src/app/domain/texts/promo.py:applied_text; routers/trial_promo.py:result_view", when, text,
            mk, subs=subs, code=pcode)

    applied("promo.applied.trial", "trial",
            PromoReward("trial", PromoOutcome.APPLIED, plan_code="standard", days=5, expires_at=EXP),
            "/trial, «🎁 Попробовать», ссылка ?start=trial: пробный период включен",
            [(D_EXP, "{date}"), (f"Standard на {days_ru(5)}", "{plan} на {days}")])
    applied("promo.applied.gift", "g_Ab12Cd34",
            PromoReward("g_Ab12Cd34", PromoOutcome.APPLIED, plan_code="pro", months=3, expires_at=EXP),
            "друг открыл ссылку-подарок: подписка подключена",
            [(D_EXP, "{date}"), (f"Pro на {months_ru(3)}", "{plan} на {months}")])
    applied("promo.applied.sun718", "sun718",
            PromoReward("sun718", PromoOutcome.APPLIED, plan_code="pro", days=5, expires_at=EXP),
            "/sun718 или ?start=sun718: Pro на 5 дней",
            [(D_EXP, "{date}"), (f"Тебе {days_ru(5)}", "Тебе {days}"), ("@dcfrq", "{support}")])
    applied("promo.applied.code", "AUTUMN7",
            PromoReward("AUTUMN7", PromoOutcome.APPLIED, plan_code="standard", days=7, expires_at=EXP),
            "/promo КОД, ввод кода или ссылка ?start=КОД: код сработал",
            [(D_EXP, "{date}"), (f"Standard: +{days_ru(7)}", "{plan}: +{days}")])
    applied("promo.applied.solokhin", "solokhin",
            PromoReward("solokhin", PromoOutcome.APPLIED, plan_code="standard", days=30, expires_at=EXP),
            "/solokhin: код сработал (текст как у обычного промокода)",
            [(D_EXP, "{date}"), (f"Standard: +{days_ru(30)}", "{plan}: +{days}")])

    def outcome(id_, code, o, when, subs=(), plan=None):
        text, mk = result_view(code, PromoReward(code, o, plan_code=plan), cont)
        add(id_, sec, "src/app/domain/texts/promo.py:outcome_text", when, text, mk, subs=list(subs), code=ocode)

    S_CODE = [("<b>AUTUMN7</b>", "<b>{code}</b>")]
    outcome("promo.outcome.rate_limited", "XXX", PromoOutcome.RATE_LIMITED, "много неверных кодов подряд")
    outcome("promo.outcome.busy", "AUTUMN7", PromoOutcome.BUSY, "код уже обрабатывается (двойная отправка)")
    outcome("promo.outcome.trial_used", "trial", PromoOutcome.ALREADY_USED, "/trial, а пробный уже был")
    outcome("promo.outcome.gift_used", "g_Ab12Cd34", PromoOutcome.ALREADY_USED, "подарок уже активирован")
    outcome("promo.outcome.code_used", "AUTUMN7", PromoOutcome.ALREADY_USED, "этот код человек уже вводил", S_CODE)
    outcome("promo.outcome.sun718_used", "sun718", PromoOutcome.ALREADY_USED, "/sun718 повторно",
            [("<b>sun718</b>", "<b>{code}</b>"), ("@dcfrq", "{support}")])
    outcome("promo.outcome.sun718_lifetime", "sun718", PromoOutcome.NOT_ELIGIBLE, "/sun718 у бессрочной подписки",
            [("@dcfrq", "{support}")], plan="lifetime")
    outcome("promo.outcome.gift_lifetime", "g_Ab12Cd34", PromoOutcome.NOT_ELIGIBLE, "подарок открыл владелец бессрочной")
    outcome("promo.outcome.trial_has_sub", "trial", PromoOutcome.NOT_ELIGIBLE,
            "/trial или /solokhin при активной подписке")
    outcome("promo.outcome.not_eligible", "AUTUMN7", PromoOutcome.NOT_ELIGIBLE,
            "код не подходит по аудитории (новым/старым)", S_CODE)
    outcome("promo.outcome.not_found", "AUTUMN7", PromoOutcome.NOT_FOUND, "такого кода нет", S_CODE)
    outcome("promo.outcome.gift_revoked", "g_Ab12Cd34", PromoOutcome.EXPIRED, "подарок отменен (покупателю вернули деньги)")
    outcome("promo.outcome.expired", "AUTUMN7", PromoOutcome.EXPIRED, "срок кода кончился", S_CODE)
    outcome("promo.outcome.exhausted", "AUTUMN7", PromoOutcome.EXHAUSTED, "у кода кончились активации", S_CODE)
    outcome("promo.outcome.gift_disabled", "g_Ab12Cd34", PromoOutcome.DISABLED, "подарки выключены флагом")
    outcome("promo.outcome.disabled", "AUTUMN7", PromoOutcome.DISABLED, "код выключен админом")
    outcome("promo.outcome.error", "AUTUMN7", PromoOutcome.ERROR, "сбой при выдаче")
    outcome("promo.outcome.sun718_error", "sun718", PromoOutcome.ERROR, "сбой при выдаче /sun718",
            [("@dcfrq", "{support}")])

    mb = btn_rows([(TP.BTN_MENU, "cb:" + Nav(s="main").pack())])
    add("promo.enter", sec, "src/app/domain/texts/promo.py:ENTER_CODE", "/promo без кода (кнопка меню только если "
        "пришел по кнопке)", TP.ENTER_CODE, mb, code="src/app/domain/texts/promo.py ENTER_CODE")
    add("promo.enter_cancelled", sec, "src/app/domain/texts/promo.py:ENTER_CANCELLED", "/cancel во время ввода кода",
        TP.ENTER_CANCELLED, mb, code="src/app/domain/texts/promo.py ENTER_CANCELLED")
    add("promo.codes_disabled", sec, "src/app/domain/texts/promo.py:CODES_DISABLED_SCREEN",
        "/promo без кода при выключенных промокодах (кнопкой: всплывашка с тем же текстом)", TP.CODES_DISABLED_SCREEN, code="src/app/domain/texts/promo.py CODES_DISABLED")

    # /friend, /admin requests
    for rid, const, when in [
        ("promo.request.sent", "REQUEST_SENT", "/friend (или /admin не-админом): запрос ушел админу"),
        ("promo.request.duplicate", "REQUEST_DUPLICATE", "повторный /friend в течение 10 минут"),
        ("promo.request.already_active", "REQUEST_ALREADY_ACTIVE", "/friend при активной подписке"),
        ("promo.request.check_failed", "REQUEST_CHECK_FAILED", "/friend, когда панель не ответила"),
    ]:
        add(rid, sec, f"src/app/domain/texts/promo.py:{const}", when, getattr(TP, const),
            code=f"src/app/domain/texts/promo.py {const}")
    add("promo.request.admin_no_rights", sec, "src/app/domain/texts/promo.py:NO_ADMIN_RIGHTS_SCREEN",
        "/admin от не-админа, когда PROMO_ADMIN_ENABLED выключен", TP.NO_ADMIN_RIGHTS_SCREEN,
        code="src/app/domain/texts/promo.py NO_ADMIN_RIGHTS_SCREEN")
    conn = btn_rows([(TP.BTN_CONNECT, "cb:" + Nav(s="connect").pack())])
    add("promo.access_granted", sec, "src/app/domain/texts/promo.py:ACCESS_GRANTED + services/grants.py:GRANT_KEYS",
        "админ выдал доступ по запросу /friend", TP.access_granted_screen(h("Pro на 1 месяц")), conn,
        subs=[("Pro на 1 месяц", "{what}")], code="src/app/domain/texts/promo.py access_granted",
        note="{what}: «Pro на 1 месяц», «Pro на 3 месяца», «Pro навсегда» (services/grants.py GRANT_KEYS)")
    add("promo.access_granted_days", sec, "src/app/domain/texts/promo.py:ACCESS_GRANTED; routers/admin/grants.py:cmd_grant",
        "админ продлил командой /grant", TP.access_granted_screen(f"Подписка продлена на {days_ru(7)}"), conn,
        subs=[(days_ru(7), "{days}")],
        code="src/app/domain/texts/promo.py access_granted; the {what} phrase is in src/app/bot/routers/admin/grants.py:cmd_grant")
    add("promo.access_rejected", sec, "src/app/domain/texts/promo.py:ACCESS_REJECTED; кнопка routers/admin/grants.py",
        "админ отклонил запрос /friend", TP.ACCESS_REJECTED, btn_rows([(TP.BTN_WRITE_ADMIN, "url:{support_url}")]), code="src/app/domain/texts/promo.py ACCESS_REJECTED; button label in routers/admin/grants.py:cb_request")
    add("promo.friend_request_cancelled", sec, "src/app/domain/texts/promo.py:FRIEND_REQUEST_CANCELLED",
        "старая кнопка «Нет» из 2.x-подтверждения /friend", TP.FRIEND_REQUEST_CANCELLED_SCREEN,
        code="src/app/domain/texts/promo.py FRIEND_REQUEST_CANCELLED")
    add("promo.friend_use_command", sec, "src/app/domain/texts/promo.py:FRIEND_USE_COMMAND",
        "старая кнопка «Да» из 2.x-подтверждения /friend", TP.FRIEND_USE_COMMAND, fmt=FMT_ALERT,
        code="src/app/domain/texts/promo.py FRIEND_USE_COMMAND")

    # gifts (buyer side)
    text, mk = VMo.plans_view(_plan_options(gift=True), gifts=True, gift=True)
    add("gift.plans", sec, "src/app/domain/texts/checkout.py:plans_screen(gift=True); views/money.py:plans_view",
        "«Подарить подписку» в списке тарифов (GIFTS_ENABLED)", text, mk, code="src/app/domain/texts/checkout.py plans_screen",
        note="блоки тарифов строятся из каталога, как в pay.plans", layout="views/money.py:plans_view(gift=True)")
    text, mk = VMo.periods_view("standard", "Standard", P.get_plan_features("standard"), _period_options("standard"),
                                gift=True)
    add("gift.periods", sec, "src/app/domain/texts/checkout.py:periods_screen(gift=True)", "подарок: выбор срока",
        text, mk, subs=_features_sub("standard") + [("Подарок: Standard", "Подарок: {plan}")],
        code="src/app/domain/texts/checkout.py periods_screen", layout="views/money.py:periods_view(gift=True)")
    st_1 = P.PLAN_CATALOG["standard"]["prices"][3]
    text, mk = VMo.checkout_view(plan_code="standard", name="Standard", months=3, amount_rub=st_1, payment_id=PID,
                                 url=PAY_URL, autorenew=None, stars=None, gift=True)
    add("gift.checkout", sec, "src/app/domain/texts/checkout.py:checkout_screen(gift=True)", "подарок: экран оплаты",
        text, mk, subs=[(PAY_URL, "{pay_url}"), (fmt_rub(st_1), "{price}"),
                        ("Подарок: Standard", "Подарок: {plan}"), (f"Срок: {months_ru(3)}", "Срок: {months}")],
        code="src/app/domain/texts/checkout.py checkout_screen")
    link = f"https://t.me/{BOT_USERNAME}?start=g_Ab12Cd34"
    add("gift.paid_buyer", sec, "src/app/domain/texts/checkout.py:gift_paid_buyer",
        "покупатель оплатил подарок: ссылка для друга", TCh.gift_paid_buyer("Standard", 3, link),
        VMo.TelegramMoneyUi().gift_paid(),
        subs=[(link, "{link}"), (f"Standard на {months_ru(3)}", "{plan} на {months}")],
        code="src/app/domain/texts/checkout.py gift_paid_buyer")
    add("gift.pending", sec, "src/app/domain/texts/checkout.py:GIFT_PENDING",
        "подарок оплачен, но код еще не создан", TCh.GIFT_PENDING, code="src/app/domain/texts/checkout.py GIFT_PENDING")
    add("gift.activated_buyer", sec, "src/app/domain/texts/promo.py:GIFT_USED_BUYER_SCREEN",
        "друг активировал подарок: сообщение покупателю", TP.GIFT_USED_BUYER_SCREEN,
        code="src/app/domain/texts/promo.py GIFT_USED_BUYER_SCREEN")

    # sun718 referral owner (services/referral.py + services/referral_tracker.py)
    add("promo.referral.owner_payout", sec, "src/app/domain/texts/promo.py:referral_payout_screen",
        "владельцу /sun718: админ записал выплату бонуса (/referral_payout)",
        TP.referral_payout_screen(2, "за сентябрь", 1),
        subs=[("бонусных месяцев: 2", "бонусных месяцев: {months}"), ("за сентябрь", "{note}"),
              ("доступно: 1 мес.", "доступно: {available} мес.")],
        code="src/app/domain/texts/promo.py referral_payout_screen", note="строка «Комментарий» только если он есть")
    add("promo.referral.owner_new_payment", sec, "src/app/domain/texts/promo.py:referral_new_payment_screen",
        "владельцу /sun718: приглашенный оплатил Pro", TP.referral_new_payment_screen(3, 12, 2.4, 1),
        subs=[(months_ru(3), "{months}"), ("Pro-месяцев: 12", "Pro-месяцев: {earned}"),
              ("месяцев: 2.40", "месяцев: {bonus}"), ("выдаче: 1 мес.", "выдаче: {available} мес.")],
        code="src/app/domain/texts/promo.py referral_new_payment_screen")
    add("promo.referral.owner_bonus", sec, "src/app/domain/texts/promo.py:referral_bonus_screen",
        "владельцу /sun718: набран новый бонусный месяц", TP.referral_bonus_screen(1, 3, 2),
        subs=[("еще 1 бонусный месяц", "еще {delta} бонусный месяц"), ("бонусов: 3 мес.", "бонусов: {full} мес."),
              ("выдаче: 2 мес.", "выдаче: {available} мес.")],
        code="src/app/domain/texts/promo.py referral_bonus_screen")


# --------------------------------------------------------------------------- refunds


def section_refunds() -> None:
    sec = "Возвраты"
    for rid, const, when in [
        ("refund.requested", "REFUND_REQUESTED", "нажал «Не смог подключиться» (возврат за 24 часа)"),
        ("refund.already", "REFUND_ALREADY", "повторно нажал «Не смог подключиться»"),
        ("refund.not_eligible", "REFUND_NOT_ELIGIBLE", "«Не смог подключиться» позже 24 часов или функция выключена"),
        ("refund.approved_card", "REFUND_APPROVED_CARD", "админ одобрил возврат (оплата картой)"),
        ("refund.approved_stars", "REFUND_APPROVED_STARS", "админ одобрил возврат (оплата звездами)"),
    ]:
        add(rid, sec, f"src/app/domain/texts/checkout.py:{const}", when, getattr(TCh, const),
            code=f"src/app/domain/texts/checkout.py {const}",
            note="запрос уходит отдельным сообщением, кнопка «Не смог подключиться» при этом убирается"
            if rid == "refund.requested" else "")
    add("refund.rejected", sec, "src/app/domain/texts/checkout.py:refund_rejected", "админ отклонил возврат",
        TCh.refund_rejected("@dcfrq"), subs=[("@dcfrq", "{support}")], code="src/app/domain/texts/checkout.py refund_rejected")
    add("refund.webhook.expired", sec, "src/app/domain/texts/checkout.py:refund_done_screen(expired=True)",
        "деньги вернули через кабинет ЮKassa, оплаченный срок уже прошел (доступ снят)",
        TCh.refund_done_screen(expired=True), code="src/app/domain/texts/checkout.py refund_done_screen")
    add("refund.webhook.shortened", sec, "src/app/domain/texts/checkout.py:refund_done_screen(expired=False)",
        "деньги вернули через кабинет ЮKassa, срок подписки укорочен", TCh.refund_done_screen(EXP, expired=False),
        subs=[(D_EXP, "{date}")], code="src/app/domain/texts/checkout.py refund_done_screen")


# --------------------------------------------------------------------------- notifications


def section_notify() -> None:
    sec = "Уведомления"
    renew_period = VN.renew_kb("standard", 3)
    renew_plans = VN.renew_kb(None, None)
    rcode = "src/app/domain/texts/notify.py {c}; button src/app/bot/views/notify.py:renew_kb"
    rlayout = ("views/notify.py:renew_kb: «Продлить подписку» opens checkout of the last plan+period when still "
               "sold, else the plan list")
    add("notify.remind_3d", sec, "src/app/domain/texts/notify.py:REMIND_3D (reminder_text)",
        "за 3 дня до конца подписки, 10:00-21:00 МСК (нет автопродления)", TN.reminder_text("3d", EXP), renew_period,
        subs=[(D_EXP, "{date}")], code=rcode.format(c="REMIND_3D"), layout=rlayout)
    add("notify.remind_1d", sec, "src/app/domain/texts/notify.py:REMIND_1D", "за 1 день до конца",
        TN.reminder_text("1d"), renew_period, code=rcode.format(c="REMIND_1D"), layout=rlayout)
    add("notify.remind_0d", sec, "src/app/domain/texts/notify.py:REMIND_0D", "в последний день подписки",
        TN.reminder_text("0d"), renew_period, code=rcode.format(c="REMIND_0D"), layout=rlayout)
    add("notify.remind_after_1d", sec, "src/app/domain/texts/notify.py:REMIND_AFTER_1D", "на следующий день после конца",
        TN.reminder_text("a1d"), renew_plans, code=rcode.format(c="REMIND_AFTER_1D"), layout=rlayout,
        note="кнопка ведет в оплату прошлого тарифа или в список тарифов, если он больше не продается")
    add("notify.grace_started", sec, "src/app/domain/texts/notify.py:grace_started",
        "подписка кончилась, включен льготный период (GRACE_ENABLED)", TN.grace_started(3, GRACE_UNTIL, 5), renew_period,
        subs=[(days_ru(3), "{grace_days}"), (DT_GRACE, "{datetime}"), ("до 5 ГБ", "до {daily_gb} ГБ")],
        code=rcode.format(c="grace_started"), layout=rlayout)
    add("notify.grace_ended", sec, "src/app/domain/texts/notify.py:GRACE_ENDED", "льготный период кончился",
        TN.GRACE_ENDED, renew_period, code=rcode.format(c="GRACE_ENDED"), layout=rlayout)
    dcode = "src/app/domain/texts/notify.py device_added; button src/app/bot/views/notify.py:devices_kb"
    add("notify.device_added", sec, "src/app/domain/texts/notify.py:device_added",
        "вебхук панели: к подписке подключилось новое устройство", TN.device_added("iPhone 15", 3, 5, "@dcfrq"),
        VN.devices_kb(), subs=[("Устройство: iPhone 15", "Устройство: {device}"), ("Занято 3 из 5", "Занято {devices_used} из {device_limit}"),
                               ("@dcfrq", "{support}")], code=dcode,
        note="если модель неизвестна, без «: {device}»; если число мест неизвестно, строка «Лимит на твоем тарифе: "
             "{device_limit}» (со словом «устройств»); без контакта поддержки: «напиши в поддержку»")
    add("notify.not_connected", sec, "src/app/domain/texts/notify.py:NOT_CONNECTED",
        "вебхук панели: VPN ни разу не подключался спустя N часов", TN.NOT_CONNECTED, VN.connect_kb(ARTICLE_URL),
        subs=[(ARTICLE_URL, "{article_url}")],
        code="src/app/domain/texts/notify.py NOT_CONNECTED; buttons src/app/bot/views/notify.py:connect_kb")
    add("notify.obhod_limited", sec, "src/app/domain/texts/notify.py:obhod_limited",
        "вебхук панели: кончился месячный трафик обхода (можно докупить)", TN.obhod_limited(100 * GIB, True),
        VN.obhod_packages_kb(), subs=[(f"Лимит на месяц: {fmt_gb(100 * GIB)}", "Лимит на месяц: {obhod_limit}")],
        code="src/app/domain/texts/notify.py obhod_limited; button views/notify.py:obhod_packages_kb")
    add("notify.obhod_limited_no_buy", sec, "src/app/domain/texts/notify.py:obhod_limited",
        "кончился трафик обхода, пакеты не продаются", TN.obhod_limited(100 * GIB, False),
        subs=[(f"Лимит на месяц: {fmt_gb(100 * GIB)}", "Лимит на месяц: {obhod_limit}")],
        code="src/app/domain/texts/notify.py obhod_limited")
    legacy_kb = btn_rows([(UI.B.RENEW, "cb:buy_subscription")])
    add("notify.legacy_remind_3d", sec, "src/app/tasks/expiry_notifier.py (texts/notify.py:reminder_screen)",
        "старое напоминание 2.x (работает, только если выключен новый job reminders)", TN.reminder_text("3d", EXP),
        legacy_kb, subs=[(D_EXP, "{date}")], code="src/app/domain/texts/notify.py reminder_screen",
        note="текст общий с notify.remind_3d")
    add("notify.legacy_remind_0d", sec, "src/app/tasks/expiry_notifier.py (texts/notify.py:reminder_screen)",
        "старое напоминание 2.x в день окончания (только если выключен reminders)", TN.reminder_text("0d"),
        legacy_kb, code="src/app/domain/texts/notify.py reminder_screen", note="текст общий с notify.remind_0d")


# --------------------------------------------------------------------------- admin


def section_admin() -> None:
    sec = "Админка"
    from app.services.admin_stats import BotStats, PaymentLine, UserCard
    from app.services.blocklist import StopEntry
    from app.services.devices import CleanupReport
    from app.services.grants import ObhodInfo
    from app.services.obhod import ObhodReport
    from app.services.panel_sync import SyncReport
    from app.services.promo_types import PromoCodeRow
    from app.services.referral import InvitedRow, ReferralStats

    acode = "src/app/bot/views/admin.py {f} (text is inline in the view); labels src/app/domain/texts/admin.py BTN_*"
    ADM = VA.plain("")[1]  # [👑 В админку] under admin command replies (views/admin.py note/plain)
    stats = BotStats(total_users=1482, today_users=7, active_subscriptions=213, trials_total=356, trials_today=3,
                     paid_total=905, paid_today=2, revenue_total=312450, revenue_today=898, revenue_30d=48750,
                     refunded_total=2196)
    text, mk = VA.home(stats)
    add("admin.home", sec, "src/app/bot/views/admin.py:home (+ texts/admin.py PANEL_TITLE, BTN_*)",
        "/admin или «👑 Админ-панель»", text, mk,
        subs=[("<b>1482</b> (сегодня +7)", "<b>{total_users}</b> (сегодня +{today_users})"),
              ("<b>213</b>", "<b>{active}</b>"), ("Триалов: 356 (сегодня 3)", "Триалов: {trials_total} (сегодня {trials_today})"),
              (f"сегодня: {fmt_rub(898)}, за 30 дней: {fmt_rub(48750)}", "сегодня: {revenue_today}, за 30 дней: {revenue_30d}")],
        code=acode.format(f="home"), layout="views/admin.py:home: fixed 2-column grid")
    text, mk = VA.stats(stats)
    add("admin.stats", sec, "src/app/bot/views/admin.py:stats", "/stats или «📊 Статистика»", text, mk,
        subs=[("<b>1482</b>, сегодня +7", "<b>{total_users}</b>, сегодня +{today_users}"), ("<b>213</b>", "<b>{active}</b>"),
              ("Триалы: 356, сегодня 3", "Триалы: {trials_total}, сегодня {trials_today}"),
              ("<b>905</b>, сегодня 2", "<b>{paid_total}</b>, сегодня {paid_today}"),
              (f"<b>{fmt_rub(312450)}</b>", "<b>{revenue_total}</b>"), (f"сегодня: {fmt_rub(898)}", "сегодня: {revenue_today}"),
              (f"за 30 дней: {fmt_rub(48750)}", "за 30 дней: {revenue_30d}"), (fmt_rub(2196), "{refunded_total}")],
        code=acode.format(f="stats"))
    text, mk = VA.users({"total": 1482, "page": 2, "total_pages": 149,
                         "users": [{"telegram_id": TG, "username": USERNAME, "subscription_plan": "pro"}]})
    add("admin.users", sec, "src/app/bot/views/admin.py:users", "«👥 Пользователи» (по 10 на странице)", text, mk,
        subs=[("(всего 1482, стр. 2 из 149)", "(всего {total}, стр. {page} из {pages})"),
              (f"• <code>{TG}</code> {USERNAME} · pro", "• <code>{id}</code> {username} · {plan}"),
              ("ad:users:page:1", "ad:users:page:{page-1}"),
              ("ad:users:page:3", "ad:users:page:{page+1}")],
        code=acode.format(f="users"), note="строка пользователя повторяется; стрелки листания появляются по ситуации",
        layout="views/admin.py:users + _pager")
    text, mk = VA.payments({"total": 905, "page": 1, "total_pages": 91,
                            "payments": [{"status": "succeeded", "username": USERNAME, "amount": 449, "provider": "yookassa"}]},
                           "all")
    add("admin.payments", sec, "src/app/bot/views/admin.py:payments", "«💳 Платежи» (фильтр: все)", text, mk,
        subs=[("Всего: 905, стр. 1 из 91", "Всего: {total}, стр. {page} из {pages}"),
              (f"1. ✅ {fmt_rub(449)} · @{USERNAME} · yookassa", "1. {status_icon} {price} · @{username} · {provider}"),
              ("ad:payments:page:2.all", "ad:payments:page:{page+1}.{filter}")],
        code=acode.format(f="payments"),
        note="строка платежа повторяется; значок статуса: ✅ ⏳ ❌ ⚠️; вместо @username может быть ID; "
             "названия фильтров в PAYMENT_FILTERS (views/admin.py)", layout="views/admin.py:payments + _pager")
    card = UserCard(telegram_id=TG, known=True, username=USERNAME, name="Иван Петров",
                    created_at=datetime(2026, 3, 14, 9, 30, tzinfo=timezone.utc), panel_id="1734",
                    trial_at=datetime(2026, 3, 14, 9, 31, tzinfo=timezone.utc), promos=["trial", "sun718"],
                    payments=[PaymentLine(id=PID, provider="yookassa", status="succeeded", amount=1199, plan_code="pro",
                                          months=3, paid_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))],
                    paid_sum=1646)
    text = VA.whois(card, ST_PRO, bot_blocked=False, is_admin=False)
    add("admin.whois", sec, "src/app/bot/views/admin.py:whois", "/whois <id>", text,
        subs=[(f"Whois {TG}", "Whois {id}"), (f"@{USERNAME} Иван Петров", "@{username} {name}"),
              ("В боте с: 14.03.2026", "В боте с: {date}"), ("<code>1734</code>", "<code>{panel_id}</code>"),
              (f"✅ pro до {fmt_date_msk(EXP, with_time=True)}", "✅ {plan} до {datetime}"),
              ("Триал: 14.03.2026", "Триал: {trial_date}"), ("Промо: trial, sun718", "Промо: {promos}"),
              (f"Оплачено всего: {fmt_rub(1646)}", "Оплачено всего: {paid_sum}"),
              (f"• #{PID} yookassa succeeded {fmt_rub(1199)} pro/3м 01.09.2026",
               "• #{payment_id} {provider} {status} {price} {plan}/{months}м {paid_date}")],
        code=acode.format(f="whois"),
        note="варианты строки «Подписка»: ✅ бессрочно (тариф) / ✅ тариф до даты / ❌ нет (было до даты) / панель "
             "недоступна; если юзера нет в БД: «В базе бота нет.»; строка платежа повторяется (до 5)")
    add("admin.sync", sec, "src/app/bot/routers/admin/users.py:_sync", "/sync <id> или /syncme",
        TA.result_screen("ok", f"Sync {TG}", "Подписка: ✅ активна", "Тариф: pro", f"До: {DT_GRACE}"),
        subs=[(str(TG), "{id}"), ("✅ активна", "{status}"), ("Тариф: pro", "Тариф: {plan}"), (DT_GRACE, "{datetime}")],
        markup=ADM, code="src/app/bot/routers/admin/users.py _sync (T.result_screen)",
        note="{status}: «✅ активна», «❌ нет» или «панель недоступна»")
    add("admin.sync_failed", sec, "src/app/bot/routers/admin/users.py:_sync", "/sync, панель ответила ошибкой",
        TA.result_screen("error", "Синхронизация", f"Не удалась для {TG} (RemnaUnavailable)"),
        ADM, subs=[(str(TG), "{id}"), ("RemnaUnavailable", "{error}")], code="src/app/bot/routers/admin/users.py _sync")

    # access requests
    req_kb = VA.request_keyboard("friend", f"{TG}.1760000000", TG)
    wsubs = [("Иван Петров", "{name}"), (f"@{USERNAME}", "@{username}"), (f"<code>{TG}</code>", "<code>{id}</code>"),
             (f"{TG}.1760000000", "{id}.{ts}"), (f"tg://user?id={TG}", "tg://user?id={id}")]
    add("admin.request.friend", sec, "src/app/domain/texts/admin.py:access_request_alert; кнопки views/admin.py:request_keyboard",
        "пользователь прислал /friend: запрос в админ-чат",
        TA.access_request_alert(TA.REQUEST_TITLE_FRIEND, name="Иван Петров", username=USERNAME, telegram_id=TG),
        req_kb, subs=wsubs, code="src/app/domain/texts/admin.py access_request_alert, REQUEST_TITLE_FRIEND, BTN_GRANT_*")
    add("admin.request.admin_promo", sec, "src/app/domain/texts/admin.py:access_request_alert",
        "не-админ прислал /admin (PROMO_ADMIN_ENABLED): запрос в админ-чат",
        TA.access_request_alert(TA.REQUEST_TITLE_ADMIN, name="Иван Петров", username=USERNAME, telegram_id=TG),
        VA.request_keyboard("promo_req", f"{TG}.1760000000", TG), subs=wsubs,
        code="src/app/domain/texts/admin.py access_request_alert, REQUEST_TITLE_ADMIN")
    processed = static("bot/routers/admin/grants.py", "Решение: ")
    add("admin.request.processed_granted", sec,
        "src/app/bot/routers/admin/grants.py:_mark_processed + texts/admin.py:PROCESSED",
        "админ нажал «Выдать ...»: запрос переписывается (кнопки убираются)",
        processed.replace("{PROCESSED}", TA.PROCESSED).replace("{line}", static("bot/routers/admin/grants.py",
                                                                                 "выдано администратором")),
        code="src/app/bot/routers/admin/grants.py _mark_processed and cb_request; PROCESSED in texts/admin.py",
        note="{base}: первый абзац исходного запроса (заголовок); {who}: имя нажавшего админа; {label}: «Pro на 1 месяц» и т.п.")
    add("admin.request.processed_rejected", sec, "src/app/bot/routers/admin/grants.py:_mark_processed",
        "админ нажал «Отклонить»: запрос переписывается",
        processed.replace("{PROCESSED}", TA.PROCESSED).replace("{line}", static("bot/routers/admin/grants.py",
                                                                                 "Запрос отклонен.")),
        code="src/app/bot/routers/admin/grants.py _mark_processed and cb_request")
    for aid, anchor, when in [
        ("admin.alert_cb.bad_data", "Неверные данные", "кнопка запроса с битыми данными"),
        ("admin.alert_cb.rejected", "Запрос отклонен", "после «Отклонить»"),
    ]:
        add_static(aid, sec, "bot/routers/admin/grants.py", anchor, when, fmt=FMT_ALERT, func="cb_request")
    add("admin.alert_cb.granted", sec, "src/app/bot/routers/admin/grants.py:cb_request", "после «Выдать ...»",
        "✅ Pro на 1 месяц: выдано", subs=[("Pro на 1 месяц", "{label}")], fmt=FMT_ALERT_SMALL,
        code="src/app/bot/routers/admin/grants.py cb_request (f\"✅ {res.label}: выдано\")")
    for aid, const, when, fmt in [
        ("admin.msg.already_done", "ALREADY_DONE", "запрос уже решен другим админом / повторный /grant", FMT_ALERT),
        ("admin.msg.in_progress", "IN_PROGRESS", "выдача этому юзеру уже идет", FMT_ALERT),
        ("admin.msg.grant_failed", "GRANT_FAILED", "выдача упала", FMT_ALERT),
        ("admin.msg.use_panel", "USE_PANEL", "старые кнопки 2.x admin_grant_*", FMT_ALERT),
        ("admin.msg.nothing_to_extend", "NOTHING_TO_EXTEND", "/grant юзеру без подписки и без тарифа", FMT_HTML),
        ("admin.msg.no_db", "NO_DB", "админ-экран без БД", FMT_HTML),
        ("admin.msg.done", "DONE", "действие в карточке обхода выполнено", FMT_ALERT_SMALL),
    ]:
        add(aid, sec, f"src/app/domain/texts/admin.py:{const}", when, getattr(TA, const),
            ADM if fmt == FMT_HTML else None, fmt=fmt,
            code=f"src/app/domain/texts/admin.py {const}",
            note="в /grant этот же текст приходит обычным сообщением" if fmt != FMT_HTML and const != "USE_PANEL" and const != "DONE" else "")
    for uid_, const, when, kw in [
        ("admin.usage.grant", "USAGE_GRANT", "/grant без аргументов", {}),
        ("admin.usage.id", "USAGE_ID", "/whois, /sync, /block, /unblock без ID", {"cmd": "whois"}),
        ("admin.usage.payout", "USAGE_PAYOUT", "/referral_payout с ошибкой", {}),
        ("admin.usage.promo_new", "USAGE_PROMO_NEW", "/promo_new без аргументов (и под списком промокодов)", {}),
        ("admin.usage.stoplist", "USAGE_STOPLIST", "/stoplist_add без аргументов (и под стоп-листом)", {}),
    ]:
        t = getattr(TA, const)
        add(uid_, sec, f"src/app/domain/texts/admin.py:{const}", when, t.format(**kw) if kw else t, ADM,
            subs=[("/whois", "/{cmd}")] if kw else [], code=f"src/app/domain/texts/admin.py {const}")
    add_static("admin.grant.done", sec, "bot/routers/admin/grants.py", ", до ", "/grant <id> <дней> выполнен",
               func="cmd_grant", names={"fmt_date_msk(until)": "date"},
               note="{label}: «+7 дн.» или «+7 дн. (pro)»; пользователю уходит promo.access_granted_days", buttons=ADM)
    add_static("admin.grant.bad_plan", sec, "bot/routers/admin/grants.py", "Неизвестный тариф", "/grant с неизвестным тарифом",
               func="cmd_grant", buttons=ADM)

    # referral
    rst = ReferralStats(code="sun718", activations=41, paying=12, earned_months=23, full_bonus=4, bonus=4.6,
                        paid_out=3, available=1, owner_id=1328087031,
                        top=[InvitedRow(telegram_id=TG, months=6, payments_after=2, pre_credit=0)])
    add("admin.referral", sec, "src/app/bot/views/admin.py:referral", "/referral_stats или «🤝 Рефералка»",
        VA.referral(rst), [VA.BACK_TO_PANEL],
        subs=[("/sun718", "/{code}"), ("<b>41</b>, с зачетом: <b>12</b>", "<b>{activations}</b>, с зачетом: <b>{paying}</b>"),
              ("Pro-месяцев: 23", "Pro-месяцев: {earned_months}"), ("(целых): 4 (4.60)", "(целых): {full_bonus} ({bonus})"),
              ("выплачено: 3", "выплачено: {paid_out}"), ("к выдаче: 1", "к выдаче: {available}"),
              ("<code>1328087031</code>", "<code>{owner_id}</code>"),
              (f"• <code>{TG}</code>: 6 мес (2 плт)", "• <code>{id}</code>: {months} мес ({payments} плт)")],
        code=acode.format(f="referral"), note="строка приглашенного повторяется (топ); может быть «+1 пред-кредит»")
    add("admin.referral_empty", sec, "src/app/bot/views/admin.py:referral", "рефералка, активаций нет",
        VA.referral(None), [VA.BACK_TO_PANEL], code=acode.format(f="referral"))
    add_static("admin.referral.payout_done", sec, "bot/routers/admin/ops.py", "Записано: ", "/referral_payout sun718 N выполнен",
               func="cmd_referral_payout",
               note="если выплачено больше доступного, сверху строка из admin.referral.payout_warn", buttons=ADM)
    add_static("admin.referral.payout_warn", sec, "bot/routers/admin/ops.py", "а доступно было только",
               "/referral_payout больше доступного (предупреждение над ответом)", func="cmd_referral_payout", buttons=ADM)

    # obhod
    text, mk = VA.obhod_overview({"active": 38, "total": 44})
    add("admin.obhod", sec, "src/app/bot/views/admin.py:obhod_overview", "«🛡 Обход» или /obhod без ID", text, mk,
        subs=[("<b>38</b> из 44", "<b>{active}</b> из {total}")], code=acode.format(f="obhod_overview"))
    from app.bot.routers.admin.obhod import PACKAGES
    info = ObhodInfo(telegram_id=TG, exists=True, active=True, used_bytes=int(12.4 * GIB), limit_bytes=250 * GIB,
                     expire_at=EXP, package="Обход 250 ГБ / мес", package_until="2026-10-27")
    text, mk = VA.obhod_card(info, PACKAGES)
    add("admin.obhod_card", sec, "src/app/bot/views/admin.py:obhod_card", "/obhod <id>: карточка обхода", text, mk,
        subs=[(f"Обход {TG}", "Обход {id}"), (f"{fmt_gb(int(12.4 * GIB))} из {fmt_gb(250 * GIB)}", "{obhod_used} из {obhod_limit}"),
              (f"Срок: {D_EXP}", "Срок: {date}"), ("Пакет: Обход 250 ГБ / мес до 2026-10-27", "Пакет: {package} до {package_until}"),
              (str(TG), "{id}")],
        code=acode.format(f="obhod_card"), note="кнопки пакетов строятся из OBHOD_PACKAGE_CATALOG (➕ + название)",
        layout="views/admin.py:obhod_card: one row per package, [base | off], refresh, back")
    text, mk = VA.obhod_card(ObhodInfo(telegram_id=TG, exists=False), PACKAGES)
    add("admin.obhod_card_none", sec, "src/app/bot/views/admin.py:obhod_card", "/obhod <id>, у юзера нет обхода", text, mk,
        subs=[(str(TG), "{id}")], code=acode.format(f="obhod_card"))
    text, mk = VA.confirm(f"Выключить обход у <code>{TG}</code>?", Adm(s="obhod", a="offok", arg=str(TG)),
                          Adm(s="obhod", a="show", arg=str(TG)))
    add("admin.obhod_confirm_off", sec, "src/app/bot/routers/admin/obhod.py:cb_obhod + views/admin.py:confirm",
        "«⛔ Выключить» в карточке обхода", text, mk, subs=[(str(TG), "{id}")],
        code="text: src/app/bot/routers/admin/obhod.py cb_obhod; buttons: src/app/bot/views/admin.py confirm")
    add_static("admin.obhod_read_failed", sec, "bot/routers/admin/obhod.py", "Не получилось прочитать обход",
               "/obhod <id>, панель или БД недоступны", func="_card", buttons=ADM)
    for aid, anchor, when in [
        ("admin.alert_cb.obhod_pkg_unknown", "Неизвестный пакет", "кнопка пакета с неизвестным кодом"),
        ("admin.alert_cb.obhod_pkg_failed", "Не применился", "пакет не применился"),
        ("admin.alert_cb.obhod_base_failed", "Не получилось", "«Базовый лимит» не сработал"),
        ("admin.alert_cb.obhod_already_off", "Обход и так не активен", "выключение уже выключенного обхода"),
    ]:
        add_static(aid, sec, "bot/routers/admin/obhod.py", anchor, when, fmt=FMT_ALERT, func="cb_obhod")

    # stop-list and bot blocklist
    text, mk = VA.blocklist([StopEntry(key=str(TG2), reason="чарджбэк", blocked_at=None)],
                            [StopEntry(key="220220-7882-09/2028", reason="чужая карта", blocked_at=None)])
    add("admin.blocklist", sec, "src/app/bot/views/admin.py:blocklist + texts/admin.py:USAGE_STOPLIST",
        "«⛔ Стоп-лист» или /stoplist", text, mk,
        subs=[("Пользователи (1)", "Пользователи ({users_count})"), ("Карты (1)", "Карты ({cards_count})"),
              (f"• <code>{TG2}</code> чарджбэк", "• <code>{id}</code> {reason}"),
              ("• <code>220220-7882-09/2028</code> чужая карта", "• <code>{card}</code> {reason}")],
        code=acode.format(f="blocklist") + "; USAGE_STOPLIST", note="строки повторяются (до 30 в каждом списке)")
    for aid, anchor, when, func in [
        ("admin.block.done", "заблокирован в боте", "/block <id>", "cmd_block"),
        ("admin.block.admin", "Нельзя заблокировать администратора", "/block админа", "cmd_block"),
        ("admin.unblock.done", "разблокирован", "/unblock <id>", "cmd_unblock"),
        ("admin.stoplist.added", "в стоп-листе", "/stoplist_add", "cmd_stoplist_add"),
        ("admin.stoplist.admin", "Нельзя добавить администратора", "/stoplist_add админа", "cmd_stoplist_add"),
        ("admin.stoplist.removed", "убран из стоп-листа", "/stoplist_del", "cmd_stoplist_del"),
        ("admin.stoplist.not_found", "Такой записи нет", "/stoplist_del несуществующего", "cmd_stoplist_del"),
    ]:
        add_static(aid, sec, "bot/routers/admin/ops.py", anchor, when, func=func,
                   names={"h(key)": "key"}, buttons=ADM)

    # promo codes
    row = PromoCodeRow(id=17, code="AUTUMN7", kind="days", plan_code="standard", days=7, audience="new", max_uses=100,
                       uses=23, per_user_limit=1, valid_until=EXP, is_active=True)
    text, mk = VA.promo_list([row])
    add("admin.promo_list", sec, "src/app/bot/views/admin.py:promo_list + texts/admin.py:USAGE_PROMO_NEW",
        "«🎟 Промокоды» или /promo_list", text, mk,
        subs=[("🟢 <code>AUTUMN7</code> +7 дн. standard · new · 23/100", "{mark} <code>{code}</code> +{days} дн. {plan} · {audience} · {uses}/{max_uses}"),
              ("🟢 AUTUMN7", "{mark} {code}"), ("pa:show:17", "pa:show:{promo_id}")],
        code=acode.format(f="promo_list"), note="строка и кнопка повторяются для каждого кода; 🟢 включен, ⚪️ выключен")
    card_text, card_mk = VA.promo_card(row, BOT_USERNAME)
    csubs = [("<b>AUTUMN7</b>", "<b>{code}</b>"), ("Тип: days, дней: 7, тариф: standard", "Тип: {kind}, дней: {days}, тариф: {plan}"),
             ("Аудитория: new", "Аудитория: {audience}"), ("Активаций: 23 из 100, на человека: 1",
                                                          "Активаций: {uses} из {max_uses}, на человека: {per_user}"),
             (f"Действует до: {D_EXP}", "Действует до: {date}"),
             (f"https://t.me/{BOT_USERNAME}?start=AUTUMN7", "https://t.me/{bot}?start={code}"), ("pa:off:17", "pa:off:{promo_id}")]
    add("admin.promo_card", sec, "src/app/bot/views/admin.py:promo_card", "карточка промокода", card_text, card_mk,
        subs=csubs, code=acode.format(f="promo_card"),
        note="строка «Трафик: N ГБ, устройств: M» только если заданы; кнопка «🟢 Включить», если код выключен")
    created = static("bot/routers/admin/promo.py", "✅ Создан")
    add("admin.promo_created", sec, "src/app/bot/routers/admin/promo.py:cmd_promo_new + views/admin.py:promo_card",
        "/promo_new ... выполнен", created.replace("{text}", card_text), card_mk, subs=csubs,
        code="src/app/bot/routers/admin/promo.py cmd_promo_new (wrapper and the PROMO_CODES_ENABLED note)",
        note="{note}: «⚠️ PROMO_CODES_ENABLED выключен: код не примется, пока флаг не включат.» (только когда флаг выключен)")
    add_static("admin.promo_not_created", sec, "bot/routers/admin/promo.py", "Не создал:", "/promo_new с ошибкой",
               func="cmd_promo_new", names={"h(str(e))": "error", "T.USAGE_PROMO_NEW": "usage_promo_new"},
               note="{usage_promo_new}: текст admin.usage.promo_new; {error}: причина из parse_promo_new", buttons=ADM)
    add_static("admin.promo_exists", sec, "bot/routers/admin/promo.py", "Такой код уже есть", "/promo_new с занятым кодом",
               func="cmd_promo_new", buttons=ADM)
    add_static("admin.alert_cb.promo_not_found", sec, "bot/routers/admin/promo.py", "Промокод не найден",
               "карточка удаленного кода", fmt=FMT_ALERT, func="cb_code")

    # maintenance
    for mid, active in (("admin.maintenance_off", False), ("admin.maintenance_on", True)):
        add(mid, sec, "src/app/domain/texts/notify.py:admin_maintenance_state; views/notify.py:maintenance_admin_kb",
            "/maintenance или «🛠 Техработы»" + (" (режим включен)" if active else ""),
            TN.admin_maintenance_state(active, "вручную" if active else None, False), VN.maintenance_admin_kb(active),
            fmt=FMT_PLAIN + "; отправляется без parse_mode",
            code="src/app/domain/texts/notify.py admin_maintenance_state, BTN_MAINT_ON/OFF",
            note="{reason} при включении: «вручную» или «auto: ...»; строка автовключения: «да» / «нет "
                 "(MAINTENANCE_AUTO_ENABLED)»" if active else "")

    # broadcasts
    seg = NS(kind="active", sub_kind="main", days=7, ids=())
    info = NS(id=12, state="draft", segment=seg, photo_file_id=None, buttons=[{"text": "Открыть", "url": "https://x"}],
              disable_notification=False, credit_days=3, total=210, delivered=0, failed=0, blocked=0,
              started_at=None, finished_at=None, text_html="...")
    bsubs = [("#12", "#{bc_id}"), ("ba:prev:12", "ba:prev:{bc_id}"), ("ba:go:12", "ba:go:{bc_id}"),
             ("ba:del:12", "ba:del:{bc_id}"), ("ba:go2:12", "ba:go2:{bc_id}"), ("ba:show:12", "ba:show:{bc_id}"),
             ("ba:stats:12", "ba:stats:{bc_id}"), ("ba:cancel:12", "ba:cancel:{bc_id}"),
             (f"с активной подпиской, в пределах {days_ru(7)}", "{segment}")]
    bcode = "src/app/bot/views/broadcast.py {f}; texts src/app/domain/texts/admin.py (BC_*, SEGMENT_TITLES)"
    listing_items = [NS(id=12, state="done", segment=seg, delivered=198, total=210),
                     NS(id=13, state="draft", segment=NS(kind="all", sub_kind="main", days=None, ids=()), delivered=0, total=0)]
    text, mk = VB.listing(listing_items)
    add("admin.broadcast.list", sec, "src/app/bot/views/broadcast.py:listing", "«📢 Рассылки» или /bc_list", text, mk,
        subs=[("#12 ✅ завершена · " + f"с активной подпиской, в пределах {days_ru(7)} · 198/210",
               "#{bc_id} {state} · {segment} · {delivered}/{total}")] + [(s, p) for s, p in bsubs if s in ("ba:show:12",)],
        code=bcode.format(f="listing"),
        note="строка и кнопка повторяются для каждой рассылки; состояния: 📝 черновик, 🟡 идет, ✅ завершена")
    text, mk = VB.draft(info, 205)
    add("admin.broadcast.draft", sec, "src/app/bot/views/broadcast.py:draft", "карточка черновика рассылки", text, mk,
        opt=bsubs, subs=[("<b>205</b>", "<b>{audience}</b>"), ("кнопок: 1", "кнопок: {buttons_count}"),
                      (f"+{days_ru(3)}", "+{credit_days}")],
        code=bcode.format(f="draft"), note="у запущенной рассылки вместо «Запустить/Удалить» кнопка «📈 Прогресс»",
        layout="views/broadcast.py:draft: preview; [start | delete] for drafts or progress; back")
    text, mk = VB.confirm_start(info, 205)
    add("admin.broadcast.confirm", sec, "src/app/bot/views/broadcast.py:confirm_start", "«🚀 Запустить» или /bc_send <id>",
        text, mk, opt=bsubs, subs=[("<b>205</b>", "<b>{audience}</b>"), (f"+{days_ru(3)}", "+{credit_days}")],
        code=bcode.format(f="confirm_start"))
    run = NS(**{**info.__dict__, "state": "running", "total": 210, "delivered": 150, "failed": 3, "blocked": 7,
                "started_at": NOW - timedelta(minutes=12), "finished_at": None})
    text, mk = VB.progress(run, 120)
    add("admin.broadcast.progress", sec, "src/app/bot/views/broadcast.py:progress", "«📈 Прогресс» или /bc_stats <id>",
        text, mk, opt=bsubs, subs=[("Всего: 210, доставлено: 150, ошибок: 3, заблокировали: 7",
                                 "Всего: {total}, доставлено: {delivered}, ошибок: {failed}, заблокировали: {blocked}"),
                                ("Прогресс: 160/210 (76%)", "Прогресс: {processed}/{total} ({pct}%)"),
                                (f"+{days_ru(3)}: начислено 120", "+{credit_days}: начислено {credited}"),
                                (fmt_date_msk(run.started_at, with_time=True), "{started}")],
        code=bcode.format(f="progress"), note="строка «Финиш: ...» после завершения; кнопка «🛑 Остановить» пока идет")
    BC_NAV_NOTE = ("на каждом шаге внизу [✖️ Отменить рассылку] [👑 В админку] (черновик сбрасывается), выше "
                   "[⬅️ Назад] на прошлый шаг (кроме шага 1); /cancel работает как «Отменить»")
    for bid, const, kbf, when in [
        ("admin.broadcast.step_text", "BC_STEP_TEXT", VB.text_kb, "«➕ Новая рассылка» или /bc_new"),
        ("admin.broadcast.step_photo", "BC_STEP_PHOTO", lambda: VB.skip_kb("photo"), "мастер, шаг 2"),
        ("admin.broadcast.step_buttons", "BC_STEP_BUTTONS", lambda: VB.skip_kb("buttons"), "мастер, шаг 3"),
        ("admin.broadcast.step_segment", "BC_STEP_SEGMENT", VB.segment_kb, "мастер, шаг 4"),
        ("admin.broadcast.step_days", "BC_STEP_DAYS", VB.days_kb, "мастер: выбран сегмент с ограничением по дням"),
        ("admin.broadcast.step_ids", "BC_STEP_IDS", VB.ids_kb, "мастер: сегмент «список ID»"),
        ("admin.broadcast.step_credit", "BC_STEP_CREDIT", VB.credit_kb, "мастер, шаг 5"),
        ("admin.broadcast.step_sound", "BC_STEP_SOUND", VB.sound_kb, "мастер, шаг 6"),
    ]:
        add(bid, sec, f"src/app/domain/texts/admin.py:{const}" + ("; кнопки views/broadcast.py" if kbf else ""), when,
            getattr(TA, const + "_SCREEN", getattr(TA, const)), kbf() if kbf else None,
            code=f"src/app/domain/texts/admin.py {const}" + ("; buttons src/app/bot/views/broadcast.py" if kbf else ""),
            note=("названия сегментов: SEGMENT_TITLES в texts/admin.py; " if const == "BC_STEP_SEGMENT" else "")
            + BC_NAV_NOTE)
    add("admin.broadcast.step_subkind", sec, "src/app/domain/texts/admin.py:BC_STEP_SUBKIND_SCREEN",
        "мастер: сегмент «активные» или «истекшие»", TA.BC_STEP_SUBKIND_SCREEN, VB.subkind_kb(), note=BC_NAV_NOTE,
        code="src/app/domain/texts/admin.py BC_STEP_SUBKIND_SCREEN; buttons src/app/bot/views/broadcast.py subkind_kb")
    for bid, const, when, kw, fmt in [
        ("admin.broadcast.cancelled", "BC_CANCELLED", "«✖️ Отменить рассылку» или /cancel в мастере", {}, FMT_HTML),
        ("admin.broadcast.empty_text", "BC_EMPTY_TEXT", "мастер: пустой текст", {}, FMT_HTML),
        ("admin.broadcast.too_long", "BC_TOO_LONG", "мастер: текст длиннее 4000", {"n": "{n}"}, FMT_HTML),
        ("admin.broadcast.bad_buttons", "BC_BAD_BUTTONS", "мастер: кривой JSON кнопок", {"err": "{error}"}, FMT_HTML),
        ("admin.broadcast.bad_number", "BC_BAD_NUMBER", "мастер: не число дней", {"max": "3650"}, FMT_HTML),
        ("admin.broadcast.bad_ids", "BC_BAD_IDS", "мастер: не нашел ID", {}, FMT_HTML),
        ("admin.broadcast.not_found", "BC_NOT_FOUND", "рассылка удалена / не существует", {}, FMT_HTML),
        ("admin.broadcast.already_running", "BC_ALREADY_RUNNING", "повторный запуск", {}, FMT_HTML),
        ("admin.broadcast.already_done", "BC_ALREADY_DONE", "запуск завершенной", {}, FMT_HTML),
        ("admin.broadcast.started", "BC_STARTED", "после «✅ Да, запустить»", {}, FMT_HTML),
        ("admin.broadcast.cancel_sent", "BC_CANCEL_SENT", "«🛑 Остановить» (всплывашка) или /bc_cancel", {}, FMT_ALERT),
        ("admin.broadcast.not_running", "BC_NOT_RUNNING", "остановка незапущенной", {}, FMT_ALERT),
        ("admin.broadcast.deleted", "BC_DELETED", "«🗑 Удалить» черновик", {}, FMT_ALERT_SMALL),
        ("admin.broadcast.preview_failed", "BC_PREVIEW_FAILED", "превью не отправилось", {"err": "{error}"}, FMT_HTML),
    ]:
        t = getattr(TA, const)
        t = t.format(**kw) if kw else t
        if fmt == FMT_HTML:  # routers wrap these notes into a result screen (T.bc_note)
            warn = const in ("BC_EMPTY_TEXT", "BC_TOO_LONG", "BC_BAD_BUTTONS", "BC_BAD_NUMBER", "BC_BAD_IDS",
                             "BC_PREVIEW_FAILED")
            t = TA.bc_note(t, kind="warn" if warn else "info")
        bkb = None
        if fmt == FMT_HTML:
            bkb = {"BC_EMPTY_TEXT": VB.text_kb, "BC_TOO_LONG": VB.text_kb, "BC_BAD_NUMBER": VB.days_kb,
                   "BC_BAD_IDS": VB.ids_kb, "BC_BAD_BUTTONS": lambda: VB.skip_kb("buttons"),
                   "BC_STARTED": lambda: VB.started_kb(12)}.get(const, VB.exit_kb)()
        add(bid, sec, f"src/app/domain/texts/admin.py:{const}", when, t, bkb, fmt=fmt, opt=bsubs,
            code=f"src/app/domain/texts/admin.py {const}" + (" (str.format placeholders)" if kw else ""),
            note="ошибка ввода в мастере: кнопки того же шага" if bkb is not None and const.startswith("BC_BAD")
            or const in ("BC_EMPTY_TEXT", "BC_TOO_LONG") else "")
    add_static("admin.broadcast.preview_sent", sec, "bot/routers/admin/broadcast.py", "Превью отправлено",
               "«👁 Превью себе»", fmt=FMT_ALERT_SMALL, func="_preview")
    add_static("admin.broadcast.unknown_segment", sec, "bot/routers/admin/broadcast.py", "Неизвестный сегмент",
               "мастер: битая кнопка сегмента", fmt=FMT_ALERT, func="cb_segment")
    from app.bot.broadcast_sender import build_markup
    add("admin.broadcast.message", sec, "src/app/bot/broadcast_sender.py:build_markup; services/broadcast.py:"
        "UNSUB_BUTTON_TEXT, CLOSE_BUTTON_TEXT",
        "как пользователь видит рассылку (и превью админу): текст админа + его кнопки + системный ряд",
        "{text_html}", build_markup([{"text": "Открыть сайт", "url": "https://example.com"}]),
        code="system row labels: src/app/services/broadcast.py UNSUB_BUTTON_TEXT, CLOSE_BUTTON_TEXT",
        note="первый ряд кнопок задает админ в мастере (пример); ряд «Отписаться | Закрыть» добавляется всегда")
    for bid, anchor, when in [
        ("admin.broadcast.usage_preview", "/bc_preview &lt;id&gt;", "/bc_preview без ID"),
        ("admin.broadcast.usage_send_to", "/bc_send_to &lt;broadcast_id&gt;", "/bc_send_to без аргументов"),
        ("admin.broadcast.usage_send", "/bc_send &lt;id&gt;", "/bc_send без ID"),
        ("admin.broadcast.usage_stats", "/bc_stats &lt;id&gt;", "/bc_stats без ID"),
        ("admin.broadcast.usage_cancel", "/bc_cancel &lt;id&gt;", "/bc_cancel без ID"),
        ("admin.broadcast.sent_to", "Отправлено в чат", "/bc_send_to выполнен"),
    ]:
        add_static(bid, sec, "bot/routers/admin/broadcast.py", anchor, when, names={"ids[1]": "id"}, buttons=VB.exit_kb())

    # 2.x payment-request log and legacy hits
    add("admin.payments_new", sec, "src/app/bot/routers/admin/ops.py:cmd_payments_new", "/payments_new",
        static("bot/routers/admin/ops.py", "Новые заявки на оплату") + "\n\n"
        + static("bot/routers/admin/ops.py", "req_id={", {"i": "n"}),
        markup=ADM, code="src/app/bot/routers/admin/ops.py cmd_payments_new (header and line f-strings)",
        note="строка заявки повторяется (до 10)")
    add_static("admin.payments_new_empty", sec, "bot/routers/admin/ops.py", "Новых заявок нет", "/payments_new, заявок нет", buttons=ADM)
    add_static("admin.payment_find_usage", sec, "bot/routers/admin/ops.py", "/payment_find PRQ-XXXXX", "/payment_find без ID", buttons=ADM)
    add_static("admin.payment_find_none", sec, "bot/routers/admin/ops.py", "не найдена", "/payment_find, заявка не найдена",
               names={"h(req_id)": "req_id"}, buttons=ADM)
    add("admin.payment_find", sec, "src/app/bot/routers/admin/ops.py:cmd_payment_find", "/payment_find <req_id>",
        static("bot/routers/admin/ops.py", "📋 <b>Заявка ", {"h(req_id)": "req_id"}) + "\n\n"
        + static("bot/routers/admin/ops.py", "event={"),
        markup=ADM, code="src/app/bot/routers/admin/ops.py cmd_payment_find", note="блок event/tg_id повторяется")
    add("admin.legacy_hits", sec, "src/app/bot/routers/admin/ops.py:cmd_legacy_hits", "/legacy_hits (нажатия старых кнопок)",
        static("bot/routers/admin/ops.py", "Старые кнопки (2.x)", {"body": "lines"}),
        markup=ADM, code="src/app/bot/routers/admin/ops.py cmd_legacy_hits",
        note="{lines}: строки «• {кнопка}: {число}» или «Нажатий старых кнопок нет.»")

    # guard texts
    from app.bot.middlewares.admin_guard import NO_RIGHTS
    add("admin.no_rights", sec, "src/app/bot/middlewares/admin_guard.py:NO_RIGHTS",
        "не-админ нажал админскую кнопку (на команды не-админу бот молчит)", NO_RIGHTS, fmt=FMT_ALERT,
        code="src/app/bot/middlewares/admin_guard.py NO_RIGHTS")
    add("admin.not_admin", sec, "src/app/domain/texts/checkout.py:NOT_ADMIN",
        "не-админ нажал кнопку решения по платежу/возврату", TCh.NOT_ADMIN, fmt=FMT_ALERT,
        code="src/app/domain/texts/checkout.py NOT_ADMIN")
    add("admin.alert_cb.wait", sec, "src/app/bot/routers/admin/payments.py:on_review / on_refund_decision",
        "сразу после нажатия «Одобрить»/«Вернуть»/«Отклонить»", "⏳", fmt=FMT_ALERT_SMALL,
        code="src/app/bot/routers/admin/payments.py cb.answer(\"⏳\")")

    # ---------------- admin alerts (pushes into the admin chat / DMs)
    ap = TCh.admin_paid(full_name="Иван Петров", username=USERNAME, telegram_id=TG, plan_label="Pro, 3 месяца",
                        amount=1199, currency="RUB", payment_number=3, total_rub=2546, expires_at=EXP,
                        external_id=EXT_ID, method="ЮKassa")
    add("admin.alert.paid", sec, "src/app/domain/texts/checkout.py:admin_paid; services/payments/notices.py:_admin_message",
        "каждая успешная оплата (топик «Платежи»)", ap,
        subs=[("<b>Иван Петров</b>", "<b>{name}</b>"), (f"@{USERNAME}", "@{username}"), (f"<code>{TG}</code>", "<code>{id}</code>"),
              ("Тариф: Pro, 3 месяца", "Тариф: {plan_label}"), (f"Сумма: {fmt_rub(1199)}", "Сумма: {price}"),
              ("Способ: ЮKassa", "Способ: {method}"), ("🔁 Постоянный клиент · 3-я оплата", "{client_line}"),
              (f"Всего с клиента: {fmt_rub(2546)}", "Всего с клиента: {total_paid}"), (D_EXP, "{date}"), (EXT_ID, "{external_id}")],
        code="src/app/domain/texts/checkout.py admin_paid; method names and «Подарок: »/«Автопродление: » prefixes in "
             "src/app/services/payments/notices.py _admin_message",
        note="{client_line}: «🟢 Новый клиент · 1-я оплата» или «🔁 Постоянный клиент · N-я оплата»; {method}: ЮKassa, "
             "звезды, сохраненная карта; {plan_label} может начинаться с «Подарок: » или «Автопродление: »; строка "
             "«Действует до» не пишется для подарка")
    rv = kb_rows(VMo.TelegramMoneyUi().review_admin(PID))
    held = TCh.admin_held(payment_id=PID, external_id=EXT_ID, telegram_id=TG, amount=1199, currency="RUB",
                          reason="сумма 1199 не совпадает с прайсом [449]")
    add("admin.alert.held", sec, "src/app/domain/texts/checkout.py:admin_held; кнопки views/money.py:review_admin",
        "платеж ушел на ручную проверку (сумма не сошлась, стоп-лист, звезды от заблокированного)", held, rv,
        subs=[(f"#{PID}", "#{payment_id}"), (EXT_ID, "{external_id}"), (f"<code>{TG}</code>", "<code>{id}</code>"),
              ("Сумма: 1199 RUB", "Сумма: {amount} {currency}"),
              ("сумма 1199 не совпадает с прайсом [449]", "{reason}"), (f":{PID}", ":{payment_id}")],
        code="src/app/domain/texts/checkout.py admin_held, BTN_REVIEW_OK/NO", note="{reason} пишет код (pricing.py, fulfillment.py)")
    for code_, t in TCh.REVIEW_TEXT.items():
        add(f"admin.review.{code_}", sec, f"src/app/domain/texts/checkout.py:REVIEW_TEXT['{code_}']; "
            "routers/admin/payments.py:_close", "решение по платежу на проверке: дописывается жирным под алертом",
            "{alert}\n\n<b>" + t + "</b>", code=f"src/app/domain/texts/checkout.py REVIEW_TEXT[{code_!r}]; wrapper in "
            "src/app/bot/routers/admin/payments.py _close",
            note="{alert}: исходный текст алерта admin.alert.held")
    add("admin.alert.not_provisioned", sec, "src/app/domain/texts/checkout.py:admin_not_provisioned",
        "оплата есть, а выдать доступ не вышло", TCh.admin_not_provisioned(payment_id=PID, telegram_id=TG,
                                                                            error="RemnaUnavailable: timeout"),
        subs=[(f"<code>{TG}</code>", "<code>{id}</code>"), (f"<code>{PID}</code>", "<code>{payment_id}</code>"),
              ("RemnaUnavailable: timeout", "{error}")], code="src/app/domain/texts/checkout.py admin_not_provisioned")
    add("admin.alert.obhod_manual", sec, "src/app/domain/texts/checkout.py:admin_obhod_manual",
        "оплачен пакет обхода, но не применен", TCh.admin_obhod_manual(telegram_id=TG, package="obhod_250", external_id=EXT_ID),
        subs=[(f"<code>{TG}</code>", "<code>{id}</code>"), ("Пакет: obhod_250", "Пакет: {package}"), (EXT_ID, "{external_id}")],
        code="src/app/domain/texts/checkout.py admin_obhod_manual")
    add("admin.alert.gift_pending", sec, "src/app/domain/texts/checkout.py:admin_gift_pending",
        "подарок оплачен, код не создан", TCh.admin_gift_pending(telegram_id=TG, payment_id=PID),
        subs=[(f"<code>{TG}</code>", "<code>{id}</code>"), (f"<code>{PID}</code>", "<code>{payment_id}</code>")],
        code="src/app/domain/texts/checkout.py admin_gift_pending")
    add("admin.alert.blocked_card", sec, "src/app/domain/texts/checkout.py:admin_blocked_card",
        "оплата картой из стоп-листа", TCh.admin_blocked_card(external_id=EXT_ID, fingerprint="220220-7882-09/2028",
                                                               reason="чужая карта"),
        subs=[(EXT_ID, "{external_id}"), ("220220-7882-09/2028", "{card}"), ("чужая карта", "{reason}")],
        code="src/app/domain/texts/checkout.py admin_blocked_card")
    add("admin.alert.blocked_user_pay", sec, "src/app/domain/texts/checkout.py:admin_blocked_user_pay",
        "человек из стоп-листа нажал оплату", TCh.admin_blocked_user_pay(telegram_id=TG, plan_code="pro", reason="чарджбэк"),
        subs=[(str(TG), "{id}"), ("Тариф: pro", "Тариф: {plan}"), ("чарджбэк", "{reason}")],
        code="src/app/domain/texts/checkout.py admin_blocked_user_pay")
    add("admin.alert.stars_orphan", sec, "src/app/domain/texts/checkout.py:admin_stars_orphan",
        "пришла оплата звездами, а платежа нет в БД",
        TCh.admin_stars_orphan(telegram_id=TG, payment_id=PID, amount=300, currency="XTR", charge_id="ch_77"),
        subs=[(str(TG), "{id}"), (f"Payment: {PID}", "Payment: {payment_id}"), ("300 XTR", "{amount} {currency}"),
              ("ch_77", "{charge_id}")], code="src/app/domain/texts/checkout.py admin_stars_orphan")
    add("admin.alert.stars_dup", sec, "src/app/domain/texts/checkout.py:admin_stars_dup",
        "второй платеж звездами по одному счету", TCh.admin_stars_dup(telegram_id=TG, payment_id=PID, charge_id="ch_78"),
        subs=[(str(TG), "{id}"), (f"#{PID}", "#{payment_id}"), ("ch_78", "{charge_id}")],
        code="src/app/domain/texts/checkout.py admin_stars_dup")
    rr = TCh.admin_refund_request(request_id=31, full_name="Иван Петров", username=USERNAME, telegram_id=TG, payment_id=PID,
                                  external_id=EXT_ID, plan_label="Pro, 1 месяц", amount=449, currency="RUB",
                                  paid_at=NOW - timedelta(hours=3))
    add("admin.alert.refund_request", sec, "src/app/domain/texts/checkout.py:admin_refund_request; кнопки views/money.py:refund_admin",
        "клиент нажал «Не смог подключиться» (топик «Возвраты»)", rr, VMo.TelegramMoneyUi().refund_admin(31),
        subs=[("#31", "#{request_id}"), ("Иван Петров @" + USERNAME, "{name} @{username}"), (f"<code>{TG}</code>", "<code>{id}</code>"),
              (f"#{PID}", "#{payment_id}"), (EXT_ID, "{external_id}"), ("Тариф: Pro, 1 месяц", "Тариф: {plan_label}"),
              (f"Сумма: {fmt_rub(449)}", "Сумма: {price}"), (fmt_date_msk(NOW - timedelta(hours=3), with_time=True), "{datetime}"),
              ("ar:ok:31", "ar:ok:{request_id}"), ("ar:no:31", "ar:no:{request_id}")],
        code="src/app/domain/texts/checkout.py admin_refund_request, BTN_REFUND_OK/NO")
    for rid_, const, when in [
        ("admin.refund.done", "ADMIN_REFUND_DONE", "«Вернуть»: деньги вернули, доступ снят"),
        ("admin.refund.done_no_revoke", "ADMIN_REFUND_DONE_NO_REVOKE", "«Вернуть»: деньги вернули, доступ снять не вышло"),
        ("admin.refund.rejected", "ADMIN_REFUND_REJECTED", "«Отклонить» запрос на возврат"),
        ("admin.refund.busy", "ADMIN_REFUND_BUSY", "не используется: текст есть, но код его не выбирает"),
        ("admin.refund.not_found", "ADMIN_REFUND_NOT_FOUND", "запрос не найден"),
    ]:
        add(rid_, sec, f"src/app/domain/texts/checkout.py:{const}; routers/admin/payments.py:_close", when,
            "{alert}\n\n<b>" + getattr(TCh, const) + "</b>",
            code=f"src/app/domain/texts/checkout.py {const}; wrapper src/app/bot/routers/admin/payments.py _close",
            note="{alert}: исходный текст admin.alert.refund_request, кнопки убираются")
    add("admin.refund.failed", sec, "src/app/domain/texts/checkout.py:admin_refund_failed",
        "«Вернуть», но ЮKassa/Telegram не вернули деньги (кнопки остаются)", TCh.admin_refund_failed("ЮKassa не приняла возврат"),
        subs=[("ЮKassa не приняла возврат", "{error}")], code="src/app/domain/texts/checkout.py admin_refund_failed")
    add("admin.refund.already", sec, "src/app/domain/texts/checkout.py:admin_refund_already",
        "повторное решение по уже решенному запросу", "{alert}\n\n<b>" + TCh.admin_refund_already("refunded") + "</b>",
        subs=[("деньги возвращены", "{status}")], code="src/app/domain/texts/checkout.py admin_refund_already",
        note="{status}: в работе, отклонен, деньги возвращены, ошибка возврата")
    add("admin.alert.refund_webhook", sec, "src/app/domain/texts/checkout.py:admin_refund_webhook",
        "ЮKassa прислала возврат (в т.ч. сделанный в кабинете)",
        TCh.admin_refund_webhook(full=True, telegram_id=TG, external_id=EXT_ID, amount=449, refunded_total=449,
                                 paid_amount=449, plan_line="Pro, 1 мес.", note="Срок откатан на 1 мес."),
        subs=[(str(TG), "{id}"), (EXT_ID, "{external_id}"), ("Pro, 1 мес.", "{plan}"),
              ("Срок откатан на 1 мес.", "{action_note}"), (fmt_rub(449), "{price}")],
        code="src/app/domain/texts/checkout.py admin_refund_webhook; note built in services/payments/refunds.py",
        note="заголовок «Полный возврат» или «Частичный возврат»; {action_note}: что сделано с доступом")
    add("admin.alert.refund_unknown", sec, "src/app/domain/texts/checkout.py:admin_refund_unknown",
        "возврат по платежу, которого нет в БД",
        TCh.admin_refund_unknown(refund_id="rf_12", external_id=EXT_ID, amount="449", currency="RUB"),
        subs=[("rf_12", "{refund_id}"), (EXT_ID, "{external_id}"), ("449 RUB", "{amount} {currency}")],
        code="src/app/domain/texts/checkout.py admin_refund_unknown")
    add("admin.alert.node_lost", sec, "src/app/domain/texts/notify.py:admin_node_lost", "вебхук панели: нода недоступна",
        TN.admin_node_lost("nl-1", "95.182.97.10", "connect ECONNREFUSED"),
        subs=[("nl-1", "{node}"), ("95.182.97.10", "{address}"), ("connect ECONNREFUSED", "{reason}")], fmt=FMT_PLAIN,
        code="src/app/domain/texts/notify.py admin_node_lost")
    add("admin.alert.node_restored", sec, "src/app/domain/texts/notify.py:admin_node_restored", "нода снова на связи",
        TN.admin_node_restored("nl-1", "95.182.97.10"), subs=[("nl-1", "{node}"), ("95.182.97.10", "{address}")],
        fmt=FMT_PLAIN, code="src/app/domain/texts/notify.py admin_node_restored")
    add("admin.alert.panel_down", sec, "src/app/domain/texts/notify.py:admin_panel_down",
        "панель Remnawave не отвечает N проверок подряд", TN.admin_panel_down(3, True), subs=[("(3 проверки", "({fails} проверки")],
        fmt=FMT_PLAIN, code="src/app/domain/texts/notify.py admin_panel_down",
        note="если автовключение выключено: «Режим техработ не включался (MAINTENANCE_AUTO_ENABLED выключен).»")
    add("admin.alert.panel_up", sec, "src/app/domain/texts/notify.py:ADMIN_PANEL_UP", "панель снова отвечает",
        TN.ADMIN_PANEL_UP, fmt=FMT_PLAIN, code="src/app/domain/texts/notify.py ADMIN_PANEL_UP")
    add("admin.alert.grace_started", sec, "src/app/domain/texts/notify.py:admin_grace_started",
        "юзеру включен льготный период (без звука)", TN.admin_grace_started(TG, GRACE_UNTIL),
        subs=[(str(TG), "{id}"), (DT_GRACE, "{datetime}")], fmt=FMT_PLAIN,
        code="src/app/domain/texts/notify.py admin_grace_started")
    add("admin.alert.webhook_error", sec, "src/app/domain/texts/notify.py:admin_webhook_error",
        "вебхук панели упал при обработке", TN.admin_webhook_error("user.expired", "KeyError"),
        subs=[("user.expired", "{event}"), ("KeyError", "{error}")], fmt=FMT_PLAIN,
        code="src/app/domain/texts/notify.py admin_webhook_error")
    add("admin.alert.handler_error", sec, "src/app/bot/middlewares/errors.py:ErrorsMiddleware",
        "необработанная ошибка в обработчике 3.0 (топик «Ошибки», раз в 10 минут на тип)",
        UI.admin_alert("Ошибка в on_plans", emoji="❌", lines=[UI.field("Тип", "KeyError"), UI.field("Текст", "'pro'"),
                                                                UI.field("user", TG)]),
        subs=[("on_plans", "{handler}"), ("KeyError", "{error}"), ("&#x27;pro&#x27;", "{error_text}"), (str(TG), "{id}")],
        code="src/app/bot/middlewares/errors.py ErrorsMiddleware.__call__ (ui.admin_alert)")

    def alert(id_, where, when, title, *lines, emoji, subs=(), note=""):
        add(id_, sec, where, when, UI.admin_alert(title, emoji=emoji, who=UI.who_block(name=None, username=None, telegram_id=TG), lines=list(lines)),
            subs=[(str(TG), "{id}")] + list(subs), code=where, note=note)

    alert("admin.alert.promo_applied", "src/app/services/promo.py:_reserve_and_grant + _alert",
          "кто-то активировал промокод из таблицы или подарок (топик «Промо»)", "Промокод AUTUMN7 активирован",
          UI.field("Тариф", "standard +7 дн."), UI.field("До", D_EXP), UI.field("Использований", "24/100"), emoji="🎟",
          subs=[("AUTUMN7", "{code}"), ("standard +7 дн.", "{plan} +{days} дн."), (D_EXP, "{date}"),
                ("24/100", "{uses}/{max_uses}")],
          note="для подарка заголовок «Подарок активирован» и эмодзи 🎁")
    alert("admin.alert.promo_builtin", "src/app/services/promo.py:_builtin + _alert",
          "активирован встроенный код: trial, solokhin (топик «Промо»)", "Промокод TRIAL активирован",
          UI.field("Тариф", "standard на 5 дн."), UI.field("До", D_EXP), emoji="🎁",
          subs=[("TRIAL", "{code}"), ("standard на 5 дн.", "{plan} на {days} дн."), (D_EXP, "{date}")])
    alert("admin.alert.promo_builtin_failed", "src/app/services/promo.py:_builtin + _alert",
          "встроенный код (trial, solokhin): выдача упала", "TRIAL: выдача не удалась",
          "Запись использования откатили, пользователь может повторить.", emoji="❌", subs=[("TRIAL", "{code}")])
    alert("admin.alert.grant", "src/app/services/grants.py:GrantsService._grant", "админ выдал доступ (/friend, /grant)",
          "Выдача администратором", UI.field("Тариф", "Pro на 1 месяц"), UI.field("До", D_EXP),
          "Админ: <code>1328087031</code>", emoji="⭐",
          subs=[("Pro на 1 месяц", "{label}"), (D_EXP, "{date}"), ("1328087031", "{admin_id}")],
          note="{date}: «бессрочно» для выдачи навсегда")
    ref = "src/app/services/referral.py (engine._alert / Sun718Reverter._alert)"
    alert("admin.alert.sun718_applied", ref, "/sun718 активирован", "SUN718 активирован",
          "Тариф: Pro 5 дн. поверх standard", f"Возврат тарифа: {DT_GRACE} на standard", f"До: {D_EXP}",
          "Записано для рефералки", emoji="🎁",
          subs=[("Pro 5 дн. поверх standard", "Pro {days} дн. поверх {old_plan}"), (DT_GRACE, "{datetime}"),
                ("на standard", "на {old_plan}"), (D_EXP, "{date}")],
          note="без возврата тарифа строка «Тариф: Pro N дн.» (или «(продление)»), строки «Возврат тарифа» нет")
    for sid_, title, line, emoji, when in [
        ("admin.alert.sun718_repeat", "SUN718: повторная активация", "Повторно не выдавали, в БД ничего не писали.", "⚠️",
         "/sun718 повторно"),
        ("admin.alert.sun718_panel_down", "SUN718: панель недоступна", "Статус подписки не проверен, ничего не выдали.",
         "❌", "/sun718, панель не ответила"),
        ("admin.alert.sun718_lifetime", "SUN718: бессрочная подписка",
         "Отказ (рефералить бессрочных нельзя), в БД не писали.", "🌟", "/sun718 у бессрочной"),
        ("admin.alert.sun718_not_recorded", "SUN718: не записали активацию",
         "Подписка не выдана (без записи код стал бы многоразовым).", "❌", "/sun718: запись в БД не удалась"),
        ("admin.alert.sun718_grant_failed", "SUN718: выдача не удалась", "Запись откатили, пользователь может повторить.",
         "❌", "/sun718: выдача упала"),
        ("admin.alert.sun718_revert_skipped", "SUN718 REVERT: пропущен", "Нет аккаунта в панели.", "⚠️",
         "возврат тарифа пропущен"),
    ]:
        alert(sid_, ref, when, title, line, emoji=emoji)
    alert("admin.alert.sun718_revert", ref, "через 5 дней вернули прежний тариф", "SUN718 REVERT выполнен",
          "Тариф: standard → <b>standard</b>", emoji="🔄",
          subs=[("standard → <b>standard</b>", "{old_plan} → <b>{plan}</b>")],
          note="если пользователь докупил Pro: строка «Пользователь докупил Pro, Pro остался.»")
    alert("admin.alert.sun718_revert_failed", ref, "возврат тарифа не удался", "SUN718 REVERT: сквад не вернули",
          "Цель: standard. Ошибка: RemnaUnavailable. Повторим через час.", emoji="❌",
          subs=[("Цель: standard", "Цель: {plan}"), ("RemnaUnavailable", "{error}")])
    add_static("admin.alert.referral_payout", sec, "services/referral.py", "SUN718: выплата записана",
               "админ записал выплату /referral_payout", func="ReferralService.record_payout",
               names={"int(months)": "months", "h(note) or '—'": "note",
                      "after.full_bonus if after else 0": "full_bonus",
                      "after.earned_months if after else 0": "earned_months",
                      "after.paid_out if after else months": "paid_out", "avail_after": "available"})
    add_static("admin.alert.referral_payment", sec, "services/referral_tracker.py", "к рефералке</b>",
               "приглашенный по /sun718 оплатил Pro", func="notify_referral_payment_if_applicable",
               names={"earned_after // 5": "full_bonus"})
    add_static("admin.alert.referral_bonus", sec, "services/referral_tracker.py", "бонусн. {",
               "пул /sun718 набрал новый бонусный месяц", func="notify_referral_payment_if_applicable")
    add_static("admin.alert.provisioning_disabled", sec, "services/provisioning.py", "Выдача не выполнена",
               "выдача юзеру, отключенному в панели вручную", fmt=FMT_PLAIN, func="ProvisioningService._grant",
               names={"ent.source.value": "source", "ent.plan_code": "plan"})
    add_static("admin.alert.obhod_kept_manual", sec, "services/obhod.py", "Обход оставлен",
               "ежедневная проверка обхода: основной подписки в БД нет, но в панели жива", fmt=FMT_PLAIN,
               func="ObhodLifecycle")
    orep = ObhodReport(scanned=44, deactivated=2, packages_expired=1, orphans=1, orphan_reasons={"main_inactive": 1}, errors=0)
    add("admin.report.obhod_daily", sec, "src/app/services/obhod.py:ObhodReport.text", "ежедневный отчет по обходу (без звука)",
        orep.text(), subs=[("Активных строк: 44", "Активных строк: {scanned}"), ("Выключено: 2", "Выключено: {deactivated}"),
                           ("истекло: 1", "истекло: {packages_expired}"), (": 1 (main_inactive: 1)", ": {orphans} ({reasons})")],
        fmt=FMT_PLAIN, code="src/app/services/obhod.py ObhodReport.text",
        note="строка про обход без Pro и строка ошибок только если есть что сказать")
    crep = CleanupReport(dry_run=False, days=60, scanned=812, stale=97, deleted=95, failed=2, users={1, 2, 3})
    add("admin.report.devices_cleanup", sec, "src/app/services/devices.py:CleanupReport.text",
        "отчет чистки старых устройств (без звука)", crep.text(),
        subs=[("(удаление)", "({mode})"), ("не заходили 60 дн.", "не заходили {days} дн."), ("в панели: 812", "в панели: {scanned}"),
              ("Устаревших: 97 (у 3 польз.)", "Устаревших: {stale} (у {users} польз.)"),
              ("Удалено: 95, ошибок: 2", "Удалено: {deleted}, ошибок: {failed}")],
        fmt=FMT_PLAIN, code="src/app/services/devices.py CleanupReport.text",
        note="{mode}: «удаление» или «пробный прогон, ничего не удалено»")
    srep = SyncReport(panel_users=640, rows=598, pulled_forward=4, deactivated=6, shortfall=1, missing=2, disabled=3)
    srep.shortfall_ids = [TG]
    add("admin.report.panel_sync", sec, "src/app/services/panel_sync.py:SyncReport.text", "ночная сверка БД с панелью (без звука)",
        srep.text(), subs=[("в панели: 640, активных строк: 598", "в панели: {panel_users}, активных строк: {rows}"),
                           ("из панели: 4", "из панели: {pulled_forward}"), ("закрыты: 6", "закрыты: {deactivated}"),
                           (f": 1 [{TG}]", ": {shortfall} [{ids}]"), ("в панели: 2", "в панели: {missing}"),
                           ("вручную: 3", "вручную: {disabled}")],
        fmt=FMT_PLAIN, code="src/app/services/panel_sync.py SyncReport.text",
        note="при обрыве: «Сверка с панелью прервана: панель не отдала полный список пользователей. БД не менялась.»")
    add("admin.alert.broadcast_credit_failed", sec, "src/app/services/broadcast.py:_alert_credit_failures",
        "после рассылки с подарком часть дней не начислилась",
        UI.admin_alert("Рассылка: сбой начисления подарка", emoji="⚠️",
                       lines=[UI.field("Рассылка", "#12"), UI.field("Не начислено", "+3 дн. у 2 получателей")],
                       hint="Причина в логах бота (broadcast credit ... failed). Начислить вручную: /grant."),
        subs=[("#12", "#{bc_id}"), ("+3 дн. у 2 получателей", "+{days} дн. у {failed} получателей")],
        code="src/app/services/broadcast.py _alert_credit_failures")
    add_static("admin.alert.reconciler_stuck", sec, "tasks/remnawave_reconciler.py", "Reconciler: подписка застряла",
               "старая сверка 2.x: подписка не синкается много раз подряд", func="RemnawaveReconciler",
               names={"MAX_RESYNC_ATTEMPTS": "max_attempts"})


# --------------------------------------------------------------------------- errors


def section_errors() -> None:
    sec = "Ошибки"
    add("err.generic", sec, "src/app/domain/texts/common.py:GENERIC_ERROR", "неожиданная ошибка в ответ на команду/сообщение",
        TCo.GENERIC_ERROR, code="src/app/domain/texts/common.py GENERIC_ERROR")
    add("err.generic_alert", sec, "src/app/domain/texts/common.py:GENERIC_ERROR_ALERT", "неожиданная ошибка при нажатии кнопки",
        TCo.GENERIC_ERROR_ALERT, fmt=FMT_ALERT, code="src/app/domain/texts/common.py GENERIC_ERROR_ALERT")
    add("err.stale_button", sec, "src/app/domain/texts/common.py:STALE_BUTTON",
        "нажата старая/неизвестная кнопка (потом открывается главное меню)", TCo.STALE_BUTTON, fmt=FMT_ALERT_SMALL,
        code="src/app/domain/texts/common.py STALE_BUTTON")
    add("err.maintenance", sec, "src/app/domain/texts/notify.py:MAINTENANCE_SCREEN",
        "техработы: нажал «Подключиться», «Устройства», триал (всплывашка) или /trial, /devices (сообщение)",
        TN.MAINTENANCE_SCREEN, fmt=FMT_ALERT + "; на команды приходит обычным сообщением",
        code="src/app/domain/texts/notify.py MAINTENANCE_SCREEN")
    add("err.blocked", sec, "src/app/middlewares/blocklist.py:BlocklistMiddleware",
        "пользователь заблокирован в боте (/block): любое сообщение (на кнопку: всплывашка «⛔ Доступ ограничен.»)",
        UI.result("error", "Доступ ограничен"), code="src/app/middlewares/blocklist.py BlocklistMiddleware")
    add("err.unused_buttons", sec, "src/app/domain/texts/promo.py:BTN_ENTER_CODE, BTN_TRIAL, BTN_WRITE_USER; "
        "texts/connect.py:LOADING",
        "не используются: тексты есть, но нигде не показываются",
        "\n".join([TP.BTN_ENTER_CODE, TP.BTN_TRIAL, TP.BTN_WRITE_USER, "", TCn.LOADING]), fmt=FMT_HTML,
        code="src/app/domain/texts/promo.py BTN_ENTER_CODE, BTN_TRIAL, BTN_WRITE_USER; src/app/domain/texts/connect.py LOADING",
        note="строки 1-3: подписи кнопок; после пустой строки: экран загрузки ссылки (views/connect.py:loading не вызывается)")


# --------------------------------------------------------------------------- 2.1.1 comparison

# 2.1.1 screen id (in OLD_2_1_1 below) -> 3.0 screen ids it is shown under.
OLD_ALIASES = {
    "user.menu.none": ["user.menu.none", "user.menu.none_no_trial"],
    "user.menu.active": ["user.menu.active", "user.menu.active_pro_preparing", "user.menu.expires_today",
                         "user.menu.admin"],
    "user.connect.no_sub": ["user.connect.no_sub", "user.connect.no_sub_trial"],
    "user.blocked": ["err.blocked"],
    "promo.outcome.already_used": ["promo.outcome.code_used", "promo.outcome.trial_used"],
    "promo.outcome.not_eligible": ["promo.outcome.trial_has_sub"],
    "admin.blocklist": ["admin.block.done"],
    "admin.alert.review_result": ["admin.review.approved"],
    "admin.alert.grant": ["admin.request.processed_granted"],
    "notify.remind_3d": ["notify.remind_3d", "notify.legacy_remind_3d"],
    "notify.remind_0d": ["notify.remind_0d", "notify.legacy_remind_0d"],
}
# 2.1.1 screens with no 3.0 counterpart (listed in the mapping file only)
OLD_REMOVED_NOTE = {
    "user.profile": "2.x profile screen; in 3.0 /profile opens the main menu (status card)",
    "user.connect.loading": "2.x loading screen; 3.0 has connect.LOADING but never shows it",
    "err.access_denied": "2.x error screen; 3.0 retired error screens (fallback -> main menu)",
    "err.remna_unavailable": "2.x error screen; 3.0 shows user.connect.error / user.menu.stale instead",
}


def attach_old() -> list[str]:
    by_id = {e.id: e for e in ENTRIES}
    unknown = []
    for old_id, data in OLD_2_1_1.items():
        targets = OLD_ALIASES.get(old_id, [old_id])
        hit = False
        for t in targets:
            if t in by_id:
                by_id[t].old = data
                hit = True
        if not hit:
            unknown.append(old_id)
    return unknown


# --------------------------------------------------------------------------- writers

HEADER = """# Экраны бота 3.0

Все сообщения и экраны релиза 3.0: что видит пользователь и что приходит админам. Файл собран скриптом
`scripts/render_screens_catalog.py` из настоящего кода ветки release/3.0 ({head}): тексты отрисованы теми же
функциями, что работают в боте.

Каждый экран относится к одному из {ntypes} типов. Тип задает вид: заголовок, цитаты-плашки, подсказку,
порядок кнопок. Экран задает только слова. Поэтому файл в трех частях:

1. **Типы экранов.** Шаблон каждого типа. Правка шаблона меняет все экраны этого типа сразу.
2. **Словарь.** Подписи кнопок и эмодзи. Правка здесь меняет кнопку или эмодзи везде.
3. **Экраны по типам.** Только слова каждого экрана: заголовок, строки в цитатах, подсказка, кнопки.

Итого: {count} экранов, из них {kit} собраны из типов; {manual} пока собраны в коде вручную
(в основном короткие ответы админке и служебные тексты 2.x), у них пометка «вручную».

## Как править

- В части 1 правь шаблон типа (```html блок) и правила кнопок. Слова в фигурных скобках там это места,
  куда встанет содержимое экрана.
- В части 2 правь подпись кнопки или эмодзи в таблице.
- В части 3 правь слова экрана в строках `заголовок:`, `раздел:`, `> ...` (строка внутри цитаты),
  `текст:`, `подсказка:` и подписи в `кнопки:`. Одна строка `- [...] [...]` = один ряд кнопок.
  Не меняй оформление в части 3 (жирный, цитаты, пустые строки): его задает тип.
- Слова в фигурных скобках, например `{{date}}`, это подстановки: бот вставит туда значение. Их можно
  переставлять и удалять, но не переименовывать (новую подстановку опиши словами рядом).
- Не трогай строки `## ...` (ID экрана) и `тип:`: по ним правка попадает обратно в код.
- Хочешь оставить комментарий: строка, начинающаяся с `>>`, прямо под экраном.
- «Всплывашка» Telegram показывает без форматирования, длина до 200 символов.

## Подстановки

{glossary}
"""

# One template, keyboard rule and one example per type (docs/SCREENS.md).
TYPE_DOCS = {
    "status": ("""{эмодзи раздела} <b>{раздел}</b>
<blockquote>{Метка: значение}
{Метка: значение}</blockquote>

{эмодзи раздела} <b>{раздел}</b>
<blockquote>{строки}</blockquote>

<i>{подсказка}</i>""", "Общего заголовка нет, карточка состоит из разделов: профиль, статус подписки, обход (только Pro). "
     "Кнопки: главное действие (Подключиться), пробный период (если доступен), затем по одной в ряд, "
     "пара [Обновить] [Помощь], админам внизу «Админ-панель». Футера нет: это корневой экран.",
     "user.menu.active_pro"),
    "choice": ("""{эмодзи} <b>{заголовок}</b>
<blockquote>{вводная}</blockquote>

{эмодзи варианта} <b>{вариант}</b>
<blockquote>· {особенность}</blockquote>

<i>{Выбери ... кнопкой ниже}</i>""", "Варианты кнопками, по одному в ряд («Название · цена»), затем доп. действие (подарить), "
     "футер: из меню [🏠 В меню], глубже [⬅️ Назад] [🏠 В меню].", "pay.plans"),
    "checkout": ("""💳 <b>{заголовок}</b>
<blockquote>Тариф: {plan}
Срок: {months}
К оплате: <b>{price}</b>
Автопродление: {включено | выключено}</blockquote>

<i>{как оплатить и что будет после}</i>

<i>Нажимая «Оплатить», ты принимаешь условия <a href="{offer}">оферты</a> и <a href="{privacy}">политики конфиденциальности</a>.</i>""",
     "Первая кнопка «💳 Оплатить {price}» (ссылка ЮKassa), затем звезды, автопродление, «🔄 Проверить оплату», "
     "футер [⬅️ Назад] [🏠 В меню].", "pay.checkout.autopay_on"),
    "result": ("""{✅ | ⏳ | ℹ️ | ⚠️ | ❌} <b>{заголовок}</b>
<blockquote>{строка или Метка: значение}</blockquote>

<i>{что делать дальше}</i>""", "Эмодзи заголовка задает вид: ✅ готово, ⏳ ждем, ℹ️ справка, ⚠️ внимание, ❌ ошибка. "
     "Кнопки: 0-2 действия (главное первым), ссылки (поддержка), футер [🏠 В меню].", "pay.check.pending"),
    "article": ("""{эмодзи} <b>{заголовок}</b>

{эмодзи} <b>{раздел}</b>
<blockquote>{текст раздела}</blockquote>

{эмодзи} <b>{раздел со ссылкой}</b>
<code>{ссылка}</code>

<i>{подсказка}</i>""", "Кнопки-ссылки (открыть ссылку, инструкция, поддержка), действия, футер [🏠 В меню].",
     "user.connect.success_pro"),
    "items": ("""{эмодзи} <b>{заголовок} ({n} из {limit})</b>
<blockquote>{иконка} <b>1. {элемент}</b>
{детали, по строке}</blockquote>

<blockquote>{иконка} <b>2. {элемент}</b>
{детали, по строке}</blockquote>

<i>{что можно сделать со списком}</i>""", "Каждый элемент в своей цитате-карточке с номером (или строками в одной "
     "цитате). Номерные кнопки [❌ 1] [❌ 2] ... до 4 в ряд, номер = номер карточки, затем прочее, футер "
     "[🏠 В меню]. Пустой список: одна строка в цитате.", "dev.list.unlink_on"),
    "confirm": ("""❓ <b>{вопрос?}</b>
<blockquote>{последствия}</blockquote>""", "Один ряд: [✅ Да, {действие}] [✖️ Отмена]. Футера нет.", "dev.ask_unlink"),
    "prompt": ("""✍️ <b>{что прислать}</b>
<blockquote>{как прислать}</blockquote>

<i>Отмена: /cancel</i>""", "Футер [🏠 В меню].", "promo.enter"),
    "push": ("""{эмодзи} <b>{заголовок}</b>
<blockquote>{строка или Метка: значение}</blockquote>

<i>{что делать дальше}</i>""", "Бот пишет сам. 0-2 кнопки действия, футера нет.", "pay.paid_user"),
    "toast": ("""{одно-два предложения без тегов, до 200 символов}""",
              "Всплывает над чатом после нажатия кнопки. Кнопок нет.", "user.menu.refreshed"),
    "admin_screen": ("""{эмодзи} <b>{заголовок}</b>
<blockquote>{Метка: значение}</blockquote>

{эмодзи} <b>{раздел}</b>
<blockquote>{строки или элементы списка}</blockquote>

<i>{команды или подсказка}</i>""", "Действия, листалка [⬅️] [➡️], футер [👑 В админку] или [⬅️ Назад] [👑 В админку]; шаг мастера: [⬅️ Назад], затем [✖️ Отменить рассылку] [👑 В админку].", "admin.stats"),
    "admin_alert": ("""{эмодзи} <b>{событие}</b>
<blockquote>👤 {имя} @{username}
🆔 {id}</blockquote>

<blockquote>{Метка: значение}</blockquote>

<i>{что сделать админу}</i>""", "Решение одним рядом [✅ ...] [❌ ...], иначе без кнопок.", "admin.alert.paid"),
}

# Russian meaning of the dictionary entries (part 2).
B_MEANING = {
    "CONNECT": "подключение", "SUBSCRIPTION": "тарифы и подписка", "RENEW": "продлить (пуши)",
    "PAY_PREFIX": "оплата (к подписи добавляется сумма)", "CHECK_PAYMENT": "проверить оплату",
    "PAY_STARS": "оплата звездами", "AUTOPAY_ON": "включить автопродление", "AUTOPAY_OFF": "не продлевать автоматически",
    "AUTOPAY_STOP": "отключить автопродление (пуш)", "DEVICES": "мои устройства", "OBHOD_MORE": "докупить трафик обхода",
    "OPEN_LINK": "открыть ссылку подписки", "OPEN_OBHOD": "открыть ссылку обхода", "ARTICLE": "статья-инструкция",
    "SUPPORT": "поддержка", "WRITE_ADMIN": "написать администратору", "OFFER": "оферта",
    "PRIVACY": "политика конфиденциальности", "TRIAL": "пробный период", "GIFT": "подарить подписку",
    "REFUND": "возврат за 24 часа", "REFRESH": "обновить", "HELP": "помощь", "ADMIN_PANEL": "админ-панель",
    "UNLINK": "отвязать устройство", "YES_PREFIX": "подтверждение", "CANCEL": "отмена", "TO_LIST": "к списку",
    "BACK": "на экран выше", "MENU": "в главное меню", "BACK_ADMIN": "в админ-панель",
    "APPROVE": "решение админа: да", "REJECT": "решение админа: нет", "PREV": "листалка назад", "NEXT": "листалка вперед",
}
E_MEANING = {
    "OK": "готово", "WAIT": "ждем", "INFO": "справка", "WARN": "внимание", "ERROR": "ошибка",
    "ACTIVE": "подписка активна", "GRACE": "льготный период", "EXPIRED": "подписка истекла", "NONE": "подписки нет",
    "PROFILE": "профиль", "CONNECT": "подключение", "SUBSCRIPTION": "подписка", "PAYMENT": "оплата", "OBHOD": "обход",
    "DEVICES": "устройство, телефон", "DEVICE_DESKTOP": "компьютер", "DEVICE_OTHER": "неизвестное устройство",
    "LINK": "ссылка", "HOWTO": "как подключить", "GIFT": "подарок, пробный период", "PROMO": "промокод",
    "REFUND": "возврат", "AUTOPAY": "автопродление", "LOCK": "доступ закрыт", "ASK": "вопрос", "INPUT": "ввод",
    "ID": "Telegram ID", "DATE": "дата", "LEFT": "сколько осталось", "HELP": "помощь", "FAQ": "частые вопросы",
    "VPN": "что такое VPN", "MONEY": "деньги", "BELL": "со звуком", "MUTE": "без звука", "ADMIN": "админ",
}

SPECIAL = "special"  # command menu, Stars invoice, broadcast body: not screens


def _buttons_md(rows: list) -> list[str]:
    return ["- " + " ".join(f"[{t}]" for t, _ in row) for row in rows]


def _vars(e: Entry) -> list[str]:
    seen = []
    blob = e.text + "\n" + "\n".join(t for row in e.buttons for t, _ in row)
    for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_+\-]*)\}", blob):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def lost_blockquote(e: Entry) -> bool:
    return bool(e.old and "<blockquote>" in e.old.get("html", "") and "<blockquote>" not in e.text)


def _content_md(e: Entry) -> list[str]:
    """Part 3 body of a kit screen: its words only, line by line, placeholders applied."""
    sc = e.screen

    def t(x: str) -> str:
        return _sub(x, e.subs)

    out = []
    if sc.title:
        out.append(f"заголовок: {sc.emoji} {t(sc.title)}".replace(":  ", ": "))
    prev = None
    for bl in sc.blocks:
        if prev is not None and prev.quote and bl.quote and not bl.title:
            out.append("")  # two quotes in a row (item cards): keep them apart
        prev = bl
        if bl.title:
            out.append(f"раздел: {bl.emoji} {t(bl.title)}".replace(":  ", ": "))
        for line in bl.lines:
            for part in t(line).split("\n"):
                out.append(("> " if bl.quote else "текст: ") + part if part else ">")
    if sc.hint:
        for part in t(sc.hint).split("\n"):
            out.append(f"подсказка: {part}")
    return out


def _entry_type(e: Entry) -> str:
    if e.id in ("user.commands", "pay.stars.invoice", "admin.broadcast.message", "err.unused_buttons"):
        return SPECIAL
    return e.type


def _example(type_: str) -> Optional[Entry]:
    want = TYPE_DOCS.get(type_, ("", "", ""))[2]
    for e in ENTRIES:
        if e.id == want:
            return e
    return next((e for e in ENTRIES if _entry_type(e) == type_ and e.screen is not None), None)


def write_catalog(path: Path, head: str) -> None:
    used = sorted({v for e in ENTRIES for v in _vars(e)})
    gl = "\n".join(f"- `{{{k}}}`: {GLOSSARY[k]}" for k in GLOSSARY if k in used)
    others = [v for v in used if v not in GLOSSARY]
    if others:
        gl += "\n- остальные (" + ", ".join(f"`{{{v}}}`" for v in others) + "): смысл понятен из названия и " \
              "строки «примечание» у экрана"
    kit_n = sum(1 for e in ENTRIES if e.screen is not None)
    manual = sum(1 for e in ENTRIES if e.screen is None and _entry_type(e) not in ("toast", SPECIAL))
    out = [HEADER.format(head=head, ntypes=len(UI.TYPES), count=len(ENTRIES), kit=kit_n, manual=manual, glossary=gl)]

    # Part 1: types
    out.append("\n# Часть 1. Типы экранов\n")
    for n, ty in enumerate(UI.TYPES, 1):
        tmpl, rules, _ = TYPE_DOCS[ty]
        items = [e for e in ENTRIES if _entry_type(e) == ty]
        out.append("---")
        out.append(f"## тип {n}. {ty} · {UI.TYPE_TITLES[ty]}")
        out.append(f"экранов: {len(items)}")
        out.append("шаблон:")
        out.append("```html")
        out.append(tmpl)
        out.append("```")
        out.append(f"кнопки: {rules}")
        ex = _example(ty)
        if ex is not None:
            out.append(f"пример ({ex.id}):")
            out.append("```html")
            out.append(ex.text)
            out.append("```")
            if ex.buttons:
                out.extend(_buttons_md(ex.buttons))
    out.append("---")

    # Part 2: dictionary
    out.append("\n# Часть 2. Словарь\n")
    out.append("Подписи кнопок (`src/app/domain/texts/ui.py`, класс `B`). Одна вещь называется одинаково везде.\n")
    out.append("| Ключ | Подпись | Что это |")
    out.append("|---|---|---|")
    for k, v in vars(UI.B).items():
        if k.isupper():
            out.append(f"| `{k}` | {v} | {B_MEANING.get(k, '')} |")
    out.append("\nЭмодзи (`src/app/domain/texts/ui.py`, класс `E`).\n")
    out.append("| Ключ | Эмодзи | Значение |")
    out.append("|---|---|---|")
    for k, v in vars(UI.E).items():
        if k.isupper():
            out.append(f"| `{k}` | {v} | {E_MEANING.get(k, '')} |")
    out.append("\nТарифы в списках: " + ", ".join(f"{v} {k}" for k, v in TCh.PLAN_EMOJI.items()) + ".")

    # Part 3: screens by type
    out.append("\n# Часть 3. Экраны по типам\n")
    for n, ty in enumerate(list(UI.TYPES) + [SPECIAL], 1):
        items = [e for e in ENTRIES if _entry_type(e) == ty]
        if not items:
            continue
        title = UI.TYPE_TITLES.get(ty, "Особые тексты (не экраны)")
        out.append(f"\n## 3.{n}. {ty} · {title} ({len(items)})\n")
        for e in items:
            out.append("---")
            out.append(f"## {e.id}")
            mark = "" if e.screen is not None or ty in ("toast", SPECIAL) else " · вручную (переносится на тип)"
            out.append(f"тип: {ty}{mark}")
            out.append(f"когда: {e.when}")
            if e.note:
                out.append(f"примечание: {e.note}")
            if e.screen is not None:
                out.extend(_content_md(e))
            else:
                if e.fmt != FMT_HTML:
                    out.append(f"формат: {e.fmt}")
                out.append("```html")
                out.append(e.text)
                out.append("```")
            if e.buttons:
                out.append("кнопки:")
                out.extend(_buttons_md(e.buttons))
    out.append("---")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


MAPPING_HEADER = """# Screens catalog 3.0: mapping back to code

Generated by `scripts/render_screens_catalog.py` (release/3.0 {head}) together with
`../ЭКРАНЫ_3.0.md`. English on purpose: this is the implementer's side of the owner's catalog.
Screen kit: `docs/SCREENS.md`, `src/app/domain/texts/ui.py` (dictionary + 12 type builders),
`src/app/bot/views/kit.py` (keyboard order and footers).

## How to apply an edited catalog

1. Parse the owner's file. Part 1 (`## тип N. <type> · ...`): the ```html template and the `кнопки:` rule of
   each type. Part 2: two tables (`B` labels, `E` emoji), key in the first column. Part 3: screens separated
   by `---`; each starts with `## <SCREEN_ID>` and `тип: <type>`; the words are the lines starting with
   `заголовок:`, `раздел:`, `> ` (a line inside the quote), `текст:` (a plain line), `подсказка:`; buttons are
   the `- [..] [..]` lines under `кнопки:` (one line = one row). Screens marked `вручную` keep a ```html block
   (text still built by hand). Lines starting with `>>` are owner comments: read them, do not paste them.
2. Diff against a fresh run of the generator on the same commit, compare by id.
3. Where each kind of edit goes:
   - Part 1 template or keyboard rule changed: change the type, not the screens: `ui.render` / the type
     builder in `src/app/domain/texts/ui.py` (text) or `kit.keyboard` / `kit.Footer` (buttons). Then update
     `docs/SCREENS.md` and the layout tests in `tests/ui/test_kit.py`.
   - Part 2 label or emoji changed: `B.<KEY>` / `E.<KEY>` in `src/app/domain/texts/ui.py` (every screen
     follows).
   - Part 3 words changed: the text function named in the table below (a `*_screen` function or a
     `*_SCREEN` constant in `src/app/domain/texts/<area>.py`). `{{name}}` placeholders map to the argument
     the function formats; keep the helper (`h()`, `ui.field`, `fmt_rub`, `fmt_date_msk`, `days_ru`,
     `months_ru`, `fmt_gb`). A new placeholder needs new data: ask.
   - Button order/rows: the view function (`kit.view(... primary=, options=, secondary=, links=, footer=)`)
     named in the "layout" list; the order of groups itself is the type rule.
4. Text rules: no letter U+0451 (test enforces it), no em dashes in user copy, "ты", values through `h()` /
   `ui.field`. `pytest tests/ui` checks closed tags, toasts <= 200 chars, footers.
5. Re-run the generator: every screen must match the owner's file. Golden tests in `tests/ui` pin the main
   screens and must be updated together with the texts.

## Screen -> code location

| Screen id | Type | Where to edit | Callbacks / URLs of the buttons |
|---|---|---|---|
"""


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def write_mapping(path: Path, head: str, unknown_old: list[str]) -> None:
    out = [MAPPING_HEADER.format(head=head).rstrip("\n")]
    for e in ENTRIES:
        cbs = "; ".join(f"{t} -> {c}" for row in e.buttons for t, c in row) or "-"
        kind = _entry_type(e) + ("" if e.screen is not None or _entry_type(e) in ("toast", SPECIAL) else " (manual)")
        out.append(f"| `{e.id}` | {kind} | {_md_cell(e.code_loc)} | {_md_cell(cbs)} |")
    out.append("\n## Screens whose layout or buttons come from code logic, not a template\n")
    out.append("Edits to these need code changes in the named function, not just a text swap.\n")
    seen = set()
    for e in ENTRIES:
        if e.layout and e.id not in seen:
            out.append(f"- `{e.id}`: {e.layout}")
    out.append("\nAlso generated by code (lists and conditional lines, the catalog shows one sample line):")
    for e in ENTRIES:
        if e.note and ("повторя" in e.note or "строятся" in e.note) and not e.layout:
            out.append(f"- `{e.id}`: repeated/conditional lines, see its `примечание` in the catalog")
    out.append("""
Placeholders produced by the generator itself (not code variables):
- `{features}`: `· <feature>` lines joined by newlines from `PLAN_CATALOG[plan]["features"]`.
- `{icon}`: device icon from `texts/devices.py:_device_icon`.
- `{client_line}` (admin.alert.paid): the `count` expression in `admin_paid`.
- `{alert}` (review/refund results): the original alert text, `msg.html_text` in `routers/admin/payments.py:_close`.
- `{page-1}` / `{page+1}`: pager callbacks built by `views/admin.py:_pager`.

Screens rendered from AST (inline f-strings in routers/services): their `source` line names the file and a
quoted anchor; the placeholder names are the interpolated expressions with formatting wrappers stripped.

Fixed in the screen-kit migration (were listed here as known issues):
- `user.connect.success*`: no second, empty «Обход блокировок (использовано 0 ГБ)» block for non-Pro users and
  for Pro while the obhod link is not ready (`texts/connect.py:obhod_sections`).
- `user.menu.expires_today`: the last day by the Moscow calendar says «⏳ Истекает сегодня»
  (`texts/menu.py:_expires_today`).
- `pay.checkout*`, `gift.checkout`: the offer acceptance line is back, with links from `OFFER_URL` /
  `PRIVACY_URL` (defaults: the 2.1.1 documents, `texts/common.py:legal_line`).
- Dead texts removed: `common.MAINTENANCE`, `promo.gift_link_text`.

Still dead (defined, never shown): `connect.LOADING`, `promo.BTN_ENTER_CODE`, `promo.BTN_TRIAL`,
`promo.BTN_WRITE_USER`, `checkout.ADMIN_REFUND_BUSY`, `checkout.AUTOPAY_INFO` (handler exists, no button
leads to it).

Manual (not on the kit yet): entries of type `(manual)` in the table: short admin replies in routers
(usage strings, stop-list and block confirmations, payments_new/payment_find, legacy hits), the decision
lines appended to review/refund alerts, nightly reports (`ObhodReport`, `CleanupReport`, `SyncReport`),
2.x services (`referral_tracker` admin alerts, `remnawave_reconciler`, `provisioning` disabled-user alert,
`obhod` kept-manual alert) and the 2.x site-login relay texts.
""")
    out.append("## 2.1.1 screens with no 3.0 counterpart\n")
    for k, v in OLD_REMOVED_NOTE.items():
        out.append(f"- `{k}`: {v}")
    for k in unknown_old:
        if k not in OLD_REMOVED_NOTE:
            out.append(f"- `{k}`: (no 3.0 id matched)")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_raw(path: Path) -> None:
    out = ["# Raw renders with fake data (exact HTML as sent)\n"]
    for e in ENTRIES:
        out.append(f"## {e.id}\n```html\n{e.raw}\n```")
        for row in e.raw_buttons:
            out.append("- " + " ".join(f"[{t} -> {c}]" for t, c in row))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def git_head() -> str:
    import subprocess

    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", required=True, help="where to write ЭКРАНЫ_3.0.md (mapping goes to impl/)")
    ap.add_argument("--raw", help="also write the fake-data renders to this file")
    args = ap.parse_args()

    section_user()
    section_payments()
    section_devices()
    section_promo()
    section_refunds()
    section_notify()
    section_admin()
    section_errors()
    ENTRIES.sort(key=lambda e: SECTIONS.index(e.section))  # stable: keeps order inside a section
    unknown = attach_old()

    for e in ENTRIES:
        if "\u0451" in e.text or any("\u0451" in t for row in e.buttons for t, _ in row):
            print(f"warning: letter yo in {e.id}", file=sys.stderr)

    out = Path(args.out_dir)
    (out / "impl").mkdir(parents=True, exist_ok=True)
    head = git_head()
    write_catalog(out / "ЭКРАНЫ_3.0.md", head)
    write_mapping(out / "impl" / "screens_catalog_mapping.md", head, unknown)
    if args.raw:
        write_raw(Path(args.raw))
    for m in MISSING_SUBS + [f"static text not found: {x}" for x in STATIC_MISSING]:
        print(f"warning: {m}", file=sys.stderr)
    lost = sum(1 for e in ENTRIES if lost_blockquote(e))
    per = {s: sum(1 for e in ENTRIES if e.section == s) for s in SECTIONS}
    print(f"screens: {len(ENTRIES)}; sections: {per}; lost blockquote: {lost}; with 2.1.1: "
          f"{sum(1 for e in ENTRIES if e.old)}; unmatched 2.1.1 ids: {unknown}")


# --------------------------------------------------------------------------- 2.1.1 data
# Old templates, copied by hand from `git show v2.1.1:<path>` (verbatim, may contain the letter yo).
# Keys are screen ids; OLD_ALIASES maps them onto 3.0 ids.

OLD_2_1_1 = {
    # ===================== USER =====================
    "user.menu.none": {
        "source": "src/app/routers/menu_builder.py:build_main_menu_text + routers/subscription_view.py:render_subscription_block (no sub); keyboard ui/keyboards/main_menu.py (admin row omitted)",
        "html": "👤 <b>Профиль:</b>\n<blockquote>ID: {id}\nИмя: {name}</blockquote>\n\n<b>💡 Подписка не оформлена</b>\n<blockquote>Нажмите «Подписка» для активации VPN.</blockquote>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить", "ℹ️ Помощь"]],
    },
    "user.menu.trial": {
        "source": "src/app/routers/menu_builder.py:build_main_menu_text + render_subscription_block (active; 2.x had no separate trial variant)",
        "html": "👤 <b>Профиль:</b>\n<blockquote>ID: {id}\nИмя: {name}</blockquote>\n\n<b>🟢 Подписка активна</b>\n<blockquote>📅 До: {date}\n⏳ Осталось: {days_left} {days_word}</blockquote>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить", "ℹ️ Помощь"]],
    },
    "user.menu.active": {
        "source": "src/app/routers/menu_builder.py:build_main_menu_text + render_subscription_block (active)",
        "html": "👤 <b>Профиль:</b>\n<blockquote>ID: {id}\nИмя: {name}</blockquote>\n\n<b>🟢 Подписка активна</b>\n<blockquote>📅 До: {date}\n⏳ Осталось: {days_left} {days_word}</blockquote>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить", "ℹ️ Помощь"]],
    },
    "user.menu.active_pro": {
        "source": "src/app/routers/menu_builder.py:build_main_menu_text + render_subscription_block (active; no Pro/obhod info in 2.x menu)",
        "html": "👤 <b>Профиль:</b>\n<blockquote>ID: {id}\nИмя: {name}</blockquote>\n\n<b>🟢 Подписка активна</b>\n<blockquote>📅 До: {date}\n⏳ Осталось: {days_left} {days_word}</blockquote>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить", "ℹ️ Помощь"]],
    },
    "user.menu.lifetime": {
        "source": "src/app/routers/menu_builder.py:build_main_menu_text + render_subscription_block (active; lifetime shown as a 2099 date, no dedicated variant)",
        "html": "👤 <b>Профиль:</b>\n<blockquote>ID: {id}\nИмя: {name}</blockquote>\n\n<b>🟢 Подписка активна</b>\n<blockquote>📅 До: {date}\n⏳ Осталось: {days_left} {days_word}</blockquote>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить", "ℹ️ Помощь"]],
    },
    "user.menu.expired": {
        "source": "src/app/routers/menu_builder.py:build_main_menu_text + render_subscription_block (expired)",
        "html": "👤 <b>Профиль:</b>\n<blockquote>ID: {id}\nИмя: {name}</blockquote>\n\n<b>🔴 Подписка истекла</b>\n<blockquote>📅 Истекла: {date}\n💡 Нажмите «Подписка», чтобы продлить доступ.</blockquote>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить", "ℹ️ Помощь"]],
    },
    "user.menu.stale": {
        "source": "src/app/routers/start.py:refresh_info (RemnaUnavailableError branch, replaces menu text; keyboards.get_main_menu_keyboard)",
        "html": "❌ <b>Не удалось обновить данные</b>\n\nСервис Remna временно недоступен. Пожалуйста, попробуйте позже.\n\nЕсли проблема сохраняется, обратитесь в поддержку: @dcfrq",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить"], ["ℹ️ Помощь"]],
    },
    "user.connect.success": {
        "source": "src/app/ui/renderers/connect.py:render_connect_success_with_obhod (is_pro=False) + ui/keyboards/connect.py:build_connect_success_keyboard_with_obhod",
        "html": "🚀 <b>Ссылка для подключения VPN</b>\n\nИспользуйте эту ссылку для настройки VPN на вашем устройстве:\n\n<code>{url}</code>\n\n💡 <b>Как использовать:</b>\n\n<b>Вариант 1:</b>\n<blockquote>\n1. Откройте ссылку\n2. Скачайте подходящий VPN клиент\n3. Импортируйте подписку\n</blockquote>\n\n<b>Вариант 2:</b>\n<blockquote>\n1. Скопируйте ссылку подписки\n2. Вставьте ее в VPN клиент\n</blockquote>\n\n———\n🛡 <b>Обход блокировок</b>\nДоступен в тарифе Pro. Отдельная ссылка для сайтов, которые заблокированы.",
        "buttons": [["🔗 Открыть основную ссылку"], ["⬅️ В главное меню"]],
    },
    "user.connect.success_pro": {
        "source": "src/app/ui/renderers/connect.py:render_connect_success_with_obhod (is_pro=True, obhod active) + build_connect_success_keyboard_with_obhod (show_more_obhod=True)",
        "html": "🚀 <b>Ссылка для подключения VPN</b>\n\nИспользуйте эту ссылку для настройки VPN на вашем устройстве:\n\n<code>{url}</code>\n\n💡 <b>Как использовать:</b>\n\n<b>Вариант 1:</b>\n<blockquote>\n1. Откройте ссылку\n2. Скачайте подходящий VPN клиент\n3. Импортируйте подписку\n</blockquote>\n\n<b>Вариант 2:</b>\n<blockquote>\n1. Скопируйте ссылку подписки\n2. Вставьте ее в VPN клиент\n</blockquote>\n\n———\n🛡 <b>Обход блокировок</b>\n<blockquote>Отдельная ссылка. Добавляется так же, как и первая. Включайте обход, когда мобильный интернет отключен, и выключайте, когда все работает штатно.</blockquote>\n\n<code>{obhod_url}</code>",
        "buttons": [["🔗 Открыть основную ссылку"], ["🛡 Открыть ссылку обхода"], ["➕ Нужно больше обхода"], ["⬅️ В главное меню"]],
    },
    "user.connect.success_pro_preparing": {
        "source": "src/app/ui/renderers/connect.py:render_connect_success_with_obhod (is_pro=True, obhod not active) + build_connect_success_keyboard_with_obhod",
        "html": "🚀 <b>Ссылка для подключения VPN</b>\n\nИспользуйте эту ссылку для настройки VPN на вашем устройстве:\n\n<code>{url}</code>\n\n💡 <b>Как использовать:</b>\n\n<b>Вариант 1:</b>\n<blockquote>\n1. Откройте ссылку\n2. Скачайте подходящий VPN клиент\n3. Импортируйте подписку\n</blockquote>\n\n<b>Вариант 2:</b>\n<blockquote>\n1. Скопируйте ссылку подписки\n2. Вставьте ее в VPN клиент\n</blockquote>\n\n———\n🛡 <b>Обход блокировок</b>\nГотовим вашу ссылку обхода. Загляните чуть позже или нажмите «Обновить».",
        "buttons": [["🔗 Открыть основную ссылку"], ["➕ Нужно больше обхода"], ["⬅️ В главное меню"]],
    },
    "user.connect.no_sub": {
        "source": "src/app/ui/renderers/connect.py:render_connect_no_subscription + ui/keyboards/connect.py:build_connect_no_subscription_keyboard",
        "html": "🔒 <b>Подписка не активна</b>\n\nДля подключения к VPN нужна активная подписка.\n\nНажмите кнопку ниже, чтобы выбрать тариф и оформить доступ.",
        "buttons": [["📋 Подписка"], ["✍️ Написать администратору"], ["⬅️ Назад"]],
    },
    "user.connect.error": {
        "source": "src/app/ui/renderers/connect.py:render_connect_error + ui/keyboards/connect.py:build_connect_error_keyboard",
        "html": "❌ <b>Не удалось получить ссылку подключения</b>\n\nСервис временно недоступен. Попробуйте нажать «Обновить» или зайдите позже.\n\nЕсли проблема не исчезает — обратитесь в поддержку: @dcfrq",
        "buttons": [["📋 Подписка"], ["✍️ Написать администратору"], ["⬅️ Назад"]],
    },
    "user.connect.loading": {
        "source": "src/app/ui/renderers/connect.py:render_connect_loading (keyboard falls to build_connect_error_keyboard in ui/screens/connect.py)",
        "html": "⏳ <b>Получение ссылки подписки</b>\n\nОбрабатываем запрос...",
        "buttons": [["📋 Подписка"], ["✍️ Написать администратору"], ["⬅️ Назад"]],
    },
    "user.help": {
        "source": "src/app/ui/renderers/help.py:render_help + ui/keyboards/help.py:build_help_keyboard",
        "html": "ℹ️ <b>Справка по CRS-VPN</b>\n\n🔐 <b>Что такое VPN?</b>\n<blockquote>\nVPN (Virtual Private Network) - это технология, которая создает безопасное соединение между вашим устройством и интернетом.\n</blockquote>\n\n✅ <b>Преимущества VPN:</b>\n<blockquote>\n• Защита ваших данных от хакеров\n• Приватность в интернете\n• Доступ к зарубежным сервисам\n• Безопасный Wi-Fi в общественных местах\n</blockquote>\n\n📱 <b>Как использовать:</b>\n<blockquote>\n1. Выберите и оплатите подписку\n2. Получите ссылку конфигурации\n3. Установите VPN клиент\n4. Импортируйте конфигурацию\n5. Включите VPN и наслаждайтесь!\n</blockquote>\n\n🆘 <b>Нужна помощь?</b>\nОбратитесь к администратору через кнопку ниже.",
        "buttons": [["✍️ Написать администратору"], ["📄 Оферта"], ["🔒 Политика конфиденциальности"], ["⬅️ Назад"]],
    },
    "user.myid": {
        "source": "src/app/routers/start.py:cmd_myid (non-admin variant; keyboards.get_main_menu_keyboard)",
        "html": "🆔 <b>Ваш Telegram ID:</b> <code>{id}</code>\n\n❌ <b>Статус:</b> Обычный пользователь\n💡 Чтобы стать администратором, добавьте ваш ID в .env файл:\n<code>ADMINS={id}</code>",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить"], ["ℹ️ Помощь"]],
    },
    "user.stop": {
        "source": "src/app/routers/admin_broadcast.py:cmd_stop",
        "html": "🔕 Вы отписаны от рассылок.\n\nТранзакционные уведомления (об оплате, активации подписки) продолжат приходить.\nЧтобы снова подписаться — команда /start.",
        "buttons": [],
    },
    "user.unsub_alert": {
        "source": "src/app/routers/admin_broadcast.py:cb_unsub (callback.answer toast)",
        "html": "🔕 Вы отписаны от рассылок",
        "buttons": [],
    },
    "user.blocked": {
        "source": "src/app/middlewares/blocklist.py (message answer / callback alert)",
        "html": "⛔ Доступ ограничен.",
        "buttons": [],
    },
    "user.profile": {
        "source": "src/app/ui/renderers/profile.py:render_profile (has_subscription, username and created_at present) + ui/keyboards/profile.py",
        "html": "👤 <b>Ваш профиль</b>\n\n🆔 <b>ID:</b> {id}\n👤 <b>Username:</b> @{username}\n📅 <b>Регистрация:</b> {reg_date}\n\n✅ <b>Подписка:</b> Активна\n💳 <b>Тариф:</b> {plan}\n📅 <b>Действует до:</b> {datetime}\n⏰ <b>Осталось дней:</b> {days_left}\n\n💳 <b>Платежи:</b>\n• Всего успешных: {count}\n• Потрачено: {total}₽\n",
        "buttons": [["⬅️ В главное меню"]],
    },

    # ===================== PAYMENT =====================
    "pay.plans": {
        "source": "src/app/ui/renderers/subscription.py:render_subscription_plans (no last_plan) + ui/keyboards/subscription.py:build_subscription_plans_keyboard; features from core/plans.py PLAN_CATALOG",
        "html": "💳 <b>Подписка на VPN</b>\n\n🟢 <b>Lite</b>\n<blockquote>• Неограниченный трафик и скорость\n• YouTube без рекламы\n• Серверы: NL\n• Подключение до 2 устройств</blockquote>\n\n🔵 <b>Standard</b>\n<blockquote>• Неограниченный трафик и скорость\n• YouTube без рекламы\n• Серверы: NL + FR\n• Подключение до 5 устройств</blockquote>\n\n💎 <b>Pro</b>\n<blockquote>• Неограниченный трафик и скорость (не считая обход)\n• YouTube без рекламы\n• Все серверы: NL, FR, USA, ESP\n• Обход блокировок (100 ГБ/мес)\n• Подключение до 10 устройств</blockquote>\n\nВыберите тариф:",
        "buttons": [["Lite - от 129₽/мес"], ["Standard - от 249₽/мес"], ["Pro - от 449₽/мес"], ["🛡 Обход +трафик"], ["⬅️ Назад"]],
    },
    "pay.periods": {
        "source": "src/app/ui/renderers/subscription.py:render_subscription_plan_detail (period_months=0) + build_subscription_plan_detail_keyboard; {features} = lines '• <feature>\\n'",
        "html": "💳 <b>{plan}</b>\n\n📅 <b>Выберите период подписки:</b>\n\n📋 <b>Включено:</b>\n<blockquote>{features}</blockquote>\n👇 Выберите период подписки ниже:",
        "buttons": [["1 месяц - {price_1}₽"], ["3 месяцев - {price_3}₽"], ["6 месяцев - {price_6}₽"], ["12 месяцев - {price_12}₽"], ["⬅️ Назад"]],
    },
    "pay.checkout": {
        "source": "src/app/legacy/routers/payments.py:handle_yookassa_payment (after create_payment) + keyboards.get_payment_keyboard",
        "html": "💳 <b>{plan} - {period}</b>\n💰 <b>Сумма:</b> {price}₽\n\n🔗 <b>Для оплаты перейдите по ссылке:</b>\n<a href='{pay_url}'>Оплатить подписку</a>\n\n💡 После оплаты вы получите конфигурацию VPN",
        "buttons": [["💳 Оплатить"], ["🔄 Проверить оплату"], ["⬅️ Назад к тарифам"]],
    },
    "pay.obhod_packages": {
        "source": "src/app/ui/renderers/subscription.py:render_obhod_packages (both packages purchasable) + build_obhod_packages_keyboard",
        "html": "🛡 <b>Обход блокировок — больше трафика</b>\n\n<blockquote>В тарифе Pro обход включен с лимитом 100 ГБ в месяц. Если нужно больше — докупите пакет, и месячный лимит обхода поднимется на вашей ссылке обхода.</blockquote>\n\n• <b>Обход 250 ГБ / мес</b> — 599₽\n• <b>Обход 500 ГБ / мес</b> — 1199₽",
        "buttons": [["Обход 250 ГБ / мес - 599₽"], ["Обход 500 ГБ / мес - 1199₽"], ["⬅️ Назад"]],
    },
    "pay.check.pending": {
        "source": "src/app/legacy/routers/payments.py:handle_check_payment (status pending, payment_url found)",
        "html": "⏳ Платеж еще не получен.\n\nЕсли вы уже оплатили — подождите 1–2 минуты и нажмите «Проверить оплату» снова.",
        "buttons": [["💳 Оплатить"], ["🔄 Проверить оплату"], ["⬅️ Назад к тарифам"]],
    },
    "pay.check.paid": {
        "source": "src/app/legacy/routers/payments.py:handle_check_payment (provisioned) + get_subscription_info_keyboard(True)",
        "html": "✅ <b>Оплата подтверждена!</b>\n\nПодписка активирована. Нажмите «Получить ссылку» для настройки VPN.",
        "buttons": [["🔗 Получить ссылку"], ["⬅️ Назад"]],
    },
    "pay.check.canceled": {
        "source": "src/app/legacy/routers/payments.py:handle_check_payment (_final_texts['canceled']) + get_new_payment_keyboard",
        "html": "❌ <b>Платеж отменен</b>\n\nОплата не прошла или была отменена, деньги не списаны. Можно создать новый платеж.",
        "buttons": [["💳 Создать новый платеж"], ["⬅️ Назад"]],
    },
    "pay.check.not_found": {
        "source": "src/app/legacy/routers/payments.py:handle_check_payment (payment row not in DB; recheck not_found variant differs only by <b>)",
        "html": "ℹ️ Платеж не найден.\n\nВозможно, ссылка устарела — создайте новый платеж.",
        "buttons": [["💳 Создать новый платеж"], ["⬅️ Назад"]],
    },
    "pay.check.error": {
        "source": "src/app/legacy/routers/payments.py:handle_check_payment (except branch)",
        "html": "❌ Ошибка при проверке. Попробуйте позже.",
        "buttons": [["⬅️ Назад к тарифам"]],
    },
    "pay.paid_user": {
        "source": "src/app/services/payments/yookassa.py:handle_successful_payment (user message) + keyboards.get_subscription_link_keyboard",
        "html": "✅ <b>Оплата подтверждена, подписка активирована!</b>\n\n💳 <b>Тариф:</b> {plan}\n📅 <b>Действует до:</b> {datetime}\n💰 <b>Сумма:</b> {price}₽\n\n🎉 Теперь вы можете получить ссылку для настройки VPN.",
        "buttons": [["🔗 Получить ссылку"], ["⬅️ В главное меню"]],
    },
    "pay.error.create_failed": {
        "source": "src/app/legacy/routers/payments.py:handle_yookassa_payment (generic except)",
        "html": "❌ <b>Ошибка создания платежа</b>\n\nПроизошла ошибка при создании платежа. Попробуйте позже или обратитесь в поддержку.",
        "buttons": [["⬅️ Назад к тарифам"]],
    },
    "pay.error.blocked": {
        "source": "src/app/legacy/routers/payments.py:handle_yookassa_payment (ValueError else-branch; create_payment raises 'Оплата недоступна для этого аккаунта' for stop-listed user)",
        "html": "❌ <b>Ошибка создания платежа</b>\n\nПроизошла ошибка: Оплата недоступна для этого аккаунта\n\nПопробуйте позже или обратитесь в поддержку.",
        "buttons": [["⬅️ Назад к тарифам"]],
    },
    "pay.held_user": {
        "source": "src/app/services/payments/yookassa.py:_hold_payment_for_review (user notice) + keyboards.get_support_keyboard",
        "html": "⏳ <b>Оплата получена</b>\n\nПлатеж передан на ручную проверку администратору. Мы свяжемся с вами в ближайшее время. Если есть вопросы, напишите в поддержку.",
        "buttons": [["✍️ Написать в поддержку"], ["⬅️ В главное меню"]],
    },

    # ===================== PROMO =====================
    "promo.applied.trial": {
        "source": "src/app/routers/start.py:_handle_promo_command_locked via cmd_trial (plan_label=Standard, days=5) + get_subscription_link_keyboard",
        "html": "🎉 <b>Промокод активирован!</b>\n\nВам выдан Standard на 5 дней.\n📅 <b>Действует до:</b> {date}\n\nНажмите «Получить ссылку», чтобы настроить VPN.",
        "buttons": [["🔗 Получить ссылку"], ["⬅️ В главное меню"]],
    },
    "promo.applied.code": {
        "source": "src/app/routers/start.py:_handle_promo_command_locked (generic, e.g. /solokhin Premium 15)",
        "html": "🎉 <b>Промокод активирован!</b>\n\nВам выдан {plan} на {days} дней.\n📅 <b>Действует до:</b> {date}\n\nНажмите «Получить ссылку», чтобы настроить VPN.",
        "buttons": [["🔗 Получить ссылку"], ["⬅️ В главное меню"]],
    },
    "promo.applied.sun718": {
        "source": "src/app/routers/start.py:_cmd_sun718_locked (no revert: new user or active Pro; verb 'выдан'/'продлён') + _sun718_support_footer",
        "html": "🎉 <b>Промокод активирован!</b>\n\nВам выдан <b>Pro</b> на 5 дней.\n📅 <b>Действует до:</b> {date}\n\nНажмите «Получить ссылку», чтобы настроить VPN.\n\n💬 Если это ошибка — напишите @{support}",
        "buttons": [["🔗 Получить ссылку"], ["⬅️ В главное меню"]],
    },
    "promo.outcome.already_used": {
        "source": "src/app/routers/start.py:_handle_promo_command_locked (already used; {code} = '/trial' etc.) + get_main_menu_keyboard",
        "html": "❌ Промокод {code} уже был использован вами ранее.\n\nПовторная активация невозможна.",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить"], ["ℹ️ Помощь"]],
    },
    "promo.outcome.not_eligible": {
        "source": "src/app/routers/start.py:_handle_promo_command_locked (active subscription)",
        "html": "❌ У вас уже есть активная подписка.\n\nПромокод {code} доступен только пользователям без активной подписки.",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить"], ["ℹ️ Помощь"]],
    },
    "promo.request.sent": {
        "source": "src/app/routers/start.py:cmd_friend (user confirmation) + get_main_menu_keyboard",
        "html": "⏳ Запрос отправлен администратору. Ожидайте ответа.",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить"], ["ℹ️ Помощь"]],
    },
    "promo.request.already_active": {
        "source": "src/app/routers/start.py:cmd_friend (active subscription)",
        "html": "❌ У вас уже есть активная подписка.",
        "buttons": [["🚀 Подключиться"], ["💳 Подписка"], ["🔄 Обновить"], ["ℹ️ Помощь"]],
    },
    "promo.access_granted": {
        "source": "src/app/routers/admin.py:_handle_friend_grant / _handle_admin_promo_grant (user message; {period} = '1 месяц'/'3 месяца'/'всегда')",
        "html": "✅ <b>Вам выдан доступ!</b>\n\nPremium на {period}. Нажмите «Получить ссылку» для настройки VPN.",
        "buttons": [["🔗 Получить ссылку"], ["⬅️ В главное меню"]],
    },
    "promo.access_rejected": {
        "source": "src/app/routers/admin.py:_handle_friend_reject / _handle_admin_promo_reject (user message)",
        "html": "❌ Ваш запрос на доступ отклонен. Свяжитесь с администратором при необходимости.",
        "buttons": [["✍️ Написать"]],
    },
    "promo.referral.owner_new_payment": {
        "source": "src/app/services/referral_tracker.py:notify_referral_payment_if_applicable (owner_b)",
        "html": "💰 <b>Новая оплата приглашённого!</b>\n\nОдин из приглашённых вами юзеров оплатил Pro на <b>{months} мес</b>.\n\n📊 <b>Ваш прогресс:</b>\n  • Заработано Pro-месяцев: <b>{earned}</b>\n  • Бонусных месяцев: <b>{bonus}</b>\n  • <b>Доступно к выдаче: {available} мес</b>",
        "buttons": [],
    },
    "promo.referral.owner_bonus": {
        "source": "src/app/services/referral_tracker.py:notify_referral_payment_if_applicable (owner_c)",
        "html": "🎁 <b>Поздравляем! +{delta} бонусн. {word}!</b>\n\nВы заработали ещё один целый бонусный месяц подписки.\n\n📊 <b>Всего заработано бонусов:</b> {full_bonus} мес\n✅ <b>Доступно к выдаче:</b> {available} мес\n\nСвяжитесь с админом для выдачи.",
        "buttons": [],
    },
    "promo.referral.owner_payout": {
        "source": "src/app/services/referral_tracker.py:notify_payout (owner_text, with note)",
        "html": "✅ <b>Вам выдано {months} бонусн. {word}!</b>\n\nАдмин продлил вашу подписку.\n📝 Комментарий: {note}\n\n📊 <b>Осталось доступно:</b> {available} мес\nСпасибо за приглашённых!",
        "buttons": [],
    },

    # ===================== REFUNDS =====================
    "refund.webhook.expired": {
        "source": "src/app/services/payments/refunds.py:process_refund_webhook (full refund, target in past -> expired)",
        "html": "↩️ <b>Возврат оформлен</b>\n\nДеньги по платежу возвращены, доступ по этой оплате закончился. Если захотите вернуться, оформите подписку в меню.",
        "buttons": [],
    },
    "refund.webhook.shortened": {
        "source": "src/app/services/payments/refunds.py:process_refund_webhook (full refund, shortened)",
        "html": "↩️ <b>Возврат оформлен</b>\n\nДеньги по платежу возвращены, оплаченный период снят. Подписка действует до {date}.",
        "buttons": [],
    },

    # ===================== NOTIFY =====================
    "notify.remind_3d": {
        "source": "src/app/tasks/expiry_notifier.py (3d window) + _build_notify_keyboard",
        "html": "⚠️ <b>Ваша подписка VPN истекает через 3 дня.</b>\n\nДата окончания: {date}\n\nНе забудьте продлить подписку, чтобы не потерять доступ.",
        "buttons": [["💳 Продлить подписку"]],
    },
    "notify.remind_0d": {
        "source": "src/app/tasks/expiry_notifier.py (0d window) + _build_notify_keyboard",
        "html": "❌ <b>Ваша подписка VPN истекает сегодня.</b>\n\nПродлите ее, чтобы сохранить доступ к VPN.",
        "buttons": [["💳 Продлить подписку"]],
    },

    # ===================== ADMIN =====================
    "admin.home": {
        "source": "src/app/ui/renderers/admin.py:render_admin_panel + ui/keyboards/admin.py:build_admin_panel_keyboard",
        "html": "👑 <b>Панель администратора</b>\n\n📊 <b>Статистика:</b>\n<blockquote>\n• Пользователей: {total_users}\n• Активных подписок: {active_subs}\n• Платежей: {total_payments}\n• Доход: {revenue}₽\n</blockquote>\n\n📈 <b>За сегодня:</b>\n<blockquote>\n• Новых пользователей: {today_users}\n• Платежей: {today_payments}\n• Доход: {today_revenue}₽\n</blockquote>",
        "buttons": [["👥 Пользователи"], ["💳 Платежи"], ["🔄 Обновить"], ["🔗 Панель"], ["⬅️ Назад в меню"]],
    },
    "admin.stats": {
        "source": "src/app/ui/renderers/admin.py:render_admin_stats (same text as routers/admin.py:admin_stats /stats) + build_admin_stats_keyboard",
        "html": "📊 <b>Статистика бота</b>\n\n👥 <b>Пользователи:</b> {total_users}\n💳 <b>Платежи:</b> {total_payments}\n🔄 <b>Активные подписки:</b> {active_subs}\n💰 <b>Доход:</b> {revenue}₽\n\n📈 <b>За сегодня:</b>\n• Новых пользователей: {today_users}\n• Платежей: {today_payments}\n• Доход: {today_revenue}₽",
        "buttons": [["🔄 Обновить"], ["⬅️ Назад в админ-панель"]],
    },
    "admin.users": {
        "source": "src/app/ui/renderers/admin.py:render_admin_users (one row shown; repeated per user) + build_admin_users_keyboard",
        "html": "👥 <b>Список пользователей</b>\n\nВсего: {total}\nСтраница {page} из {pages}\n\n{n}. {admin_badge}@{username} (ID: {id})\n   Подписка: {status} ({plan})\n",
        "buttons": [["⬅️ Предыдущая", "Следующая ➡️"], ["⬅️ Назад в админ-панель"]],
    },
    "admin.payments": {
        "source": "src/app/ui/renderers/admin.py:render_admin_payments (one row shown; repeated per payment) + build_admin_payments_keyboard",
        "html": "💳 <b>История платежей</b>\n\nФильтр: {filter}\nВсего: {total}\nСтраница {page} из {pages}\n\n{n}. {status_emoji} {price}{currency} - @{username}\n   Статус: {status} | {provider}\n",
        "buttons": [["⬅️ Предыдущая", "Следующая ➡️"], ["📊 Все"], ["✅ Успешные"], ["⏳ Ожидают"], ["⬅️ Назад в админ-панель"]],
    },
    "admin.whois": {
        "source": "src/app/routers/admin.py:cmd_whois",
        "html": "<b>Whois {id}</b>\nRemna UUID: <code>{remna_id}</code>\nСтатус подписки: {status}\nИстекает: {datetime}\nЗаблокирован: {blocked}\nАдминистратор: {is_admin}",
        "buttons": [],
    },
    "admin.sync": {
        "source": "src/app/routers/admin.py:_do_sync",
        "html": "<b>Sync {id}</b>\nRemna UUID: <code>{remna_id}</code>\nСтатус: {status}\nИстекает: {datetime}\nИсточник: {source}",
        "buttons": [],
    },
    "admin.request.friend": {
        "source": "src/app/routers/start.py:cmd_friend (admin_msg + admin_keyboard)",
        "html": "👤 <b>Запрос на доступ (/friend)</b>\n\nИмя: {name}\nUsername: @{username}\nTelegram ID: <code>{id}</code>\n\nВыдайте Premium или отклоните запрос.",
        "buttons": [["Выдать Premium на 1 месяц"], ["Выдать Premium на 3 месяца"], ["Выдать Premium навсегда"], ["Отклонить"], ["📩 Написать пользователю"]],
    },
    "admin.request.admin_promo": {
        "source": "src/app/routers/admin.py:_handle_admin_promo_request",
        "html": "👤 <b>Запрос на доступ (промокод /admin)</b>\n\nИмя: {name}\nUsername: @{username}\nTelegram ID: <code>{id}</code>\n\nВыдайте Premium или отклоните запрос.",
        "buttons": [["Выдать Premium на 1 месяц"], ["Выдать Premium на 3 месяца"], ["Выдать Premium навсегда"], ["Отклонить"], ["📩 Написать пользователю"]],
    },
    "admin.referral": {
        "source": "src/app/routers/admin.py:cmd_referral_stats (with owner line and one top row)",
        "html": "📊 <b>Рефералка /{code}</b>\n\nАктиваций: <b>{count}</b>\nИз них с зачётом: <b>{paying}</b>\n\n💰 <b>Заработано Pro-месяцев:</b> {earned}\n🎁 <b>Бонусов (целых):</b> {full_bonus}  ({bonus})\n💸 <b>Уже выплачено:</b> {paid_out}\n✅ <b>Доступно к выдаче:</b> <b>{available}</b>\n\n<i>Владелец <code>{owner_id}</code> исключён из пула</i>\n\n<b>Топ приглашённых:</b>\n• <code>{id}</code> — {months} мес ({tag})\n\n<i>Выдал бонус? зафиксируй:</i>\n<code>/referral_payout sun718 N комментарий</code>",
        "buttons": [],
    },
    "admin.blocklist": {
        "source": "src/app/routers/admin.py:cmd_block (in-memory blocklist; blocked_users table was SQL-only, no UI)",
        "html": "✅ Пользователь <code>{id}</code> заблокирован.",
        "buttons": [],
    },
    "admin.broadcast.list": {
        "source": "src/app/routers/admin_broadcast.py:cmd_bc_list (one row shown)",
        "html": "<b>Рассылки (последние 20):</b>\n#{id} {state} seg=<code>{segment}</code> total={total} ok={delivered} fail={failed} blk={blocked}",
        "buttons": [],
    },
    "admin.broadcast.step_text": {
        "source": "src/app/routers/admin_broadcast.py:cmd_bc_new",
        "html": "📢 <b>Новая рассылка — шаг 1/5</b>\n\nОтправьте текст сообщения (HTML-разметка поддерживается: &lt;b&gt;, &lt;i&gt;, &lt;a&gt;, &lt;code&gt;, &lt;blockquote&gt;).\n\nОтмена: /cancel",
        "buttons": [],
    },
    "admin.broadcast.step_photo": {
        "source": "src/app/routers/admin_broadcast.py:bc_step_text",
        "html": "📷 <b>Шаг 2/5 — фото</b>\n\nОтправьте фото, или напишите <code>пропустить</code> чтобы без фото.",
        "buttons": [],
    },
    "admin.broadcast.step_buttons": {
        "source": "src/app/routers/admin_broadcast.py:_ask_buttons",
        "html": "🔘 <b>Шаг 3/5 — кнопки</b>\n\nОтправьте JSON-массив кнопок или <code>пропустить</code>.\n\nПример: <code>[{\"text\": \"Открыть сайт\", \"url\": \"https://example.com\"}]</code>\n\nПоддерживаются поля: <code>text</code> + <code>url</code> ИЛИ <code>text</code> + <code>callback_data</code>.\nКаждая кнопка — отдельной строкой в клавиатуре. Кнопка «Отписаться» добавляется автоматически.",
        "buttons": [],
    },
    "admin.broadcast.step_segment": {
        "source": "src/app/routers/admin_broadcast.py:_ask_segment",
        "html": "👥 <b>Шаг 4/5 — сегмент</b>\n\nКому шлем?\n• <code>all</code> — все активные юзеры не в opt-out\n• <code>active</code> — с активной подпиской\n• <code>expired</code> — были, но истекли\n• <code>never</code> — никогда не платили\n\nПришлите одно из четырех значений.",
        "buttons": [],
    },
    "admin.broadcast.draft": {
        "source": "src/app/routers/admin_broadcast.py:bc_step_notify (draft created)",
        "html": "✅ <b>Черновик создан: ID={id}</b>\n\nСегмент: <code>{segment}</code> (≈{count} получателей)\nФото: {has_photo}\nКнопок: {buttons_count}\nЗвук: {sound}\n\nКоманды:\n• <code>/bc_preview {id}</code> — отправить себе\n• <code>/bc_send {id}</code> — запустить реальную рассылку\n• <code>/bc_list</code> — список всех",
        "buttons": [],
    },
    "admin.broadcast.confirm": {
        "source": "src/app/routers/admin_broadcast.py:cmd_bc_send (without confirm)",
        "html": "⚠️ Подтверждение: рассылка <b>#{id}</b>, сегмент <code>{segment}</code>, ≈<b>{count}</b> получателей.\n\nОтправьте <code>/bc_send {id} confirm</code> чтобы запустить.",
        "buttons": [],
    },
    "admin.broadcast.progress": {
        "source": "src/app/routers/admin_broadcast.py:cmd_bc_stats (running, with Started line)",
        "html": "<b>Рассылка #{id} — {state}</b>\n\nСегмент: <code>{segment}</code>\nTotal: {total}\nDelivered: {delivered}\nFailed: {failed}\nBlocked: {blocked}\nProgress: {processed}/{total} ({pct}%)\nStarted: {datetime}\n",
        "buttons": [],
    },
    "admin.alert.paid": {
        "source": "src/app/services/payments/yookassa.py:handle_successful_payment (admin_text; repeat-client variant of count_line, username line present)",
        "html": "💰 <b>Новая оплата VPN</b>\n\n👤 <b>{name}</b>\n🔗 @{username}\n🆔 ID: <code>{id}</code>\n\n<blockquote>Тариф: {plan} {months} {months_word}\nСумма: {price} {currency}</blockquote>\n\n<blockquote>🔁 Постоянный клиент · {count}-я оплата\n📈 Всего с клиента: {total} ₽</blockquote>\n\n📅 Действует до: {date}\n\nPayment ID: <code>{external_id}</code>\nRemnawave ID: <code>{remna_id}</code>",
        "buttons": [],
    },
    "admin.alert.held": {
        "source": "src/app/services/payments/yookassa.py:_hold_payment_for_review (admin_text) + keyboards.get_payment_review_keyboard",
        "html": "🚨 <b>Платеж на ручной проверке</b>\n\nPayment ID: <code>{external_id}</code>\nTelegram ID: <code>{id}</code>\nСумма: {price} {currency}\nПричина: {reason}\n\nПодписка НЕ выдана. «Одобрить и выдать» проведет обычную выдачу, «Отклонить» оставит без доступа (возврат оформите в кабинете YooKassa).",
        "buttons": [["✅ Одобрить и выдать"], ["❌ Отклонить"]],
    },
    "admin.alert.review_result": {
        "source": "src/app/routers/admin.py:payment_review_decision (edits held alert; {result} from services/payments/review.py RESULT_TEXT, e.g. '✅ Одобрено, доступ выдан.')",
        "html": "{original}\n\n<b>{result}</b>\nРешение: {admin_name}",
        "buttons": [],
    },
    "admin.alert.blocked_user_pay": {
        "source": "src/app/services/payments/yookassa.py:create_payment (blocklist notify_admins)",
        "html": "⛔️ <b>Заблокированный пользователь пытался оплатить</b>\nID: <code>{id}</code>\nТариф: {plan}, сумма: {price}₽\nПричина блокировки: {reason}",
        "buttons": [],
    },
    "admin.alert.refund_webhook": {
        "source": "src/app/services/payments/refunds.py:process_refund_webhook (admin alert; {kind} = 'Полный'/'Частичный')",
        "html": "↩️ <b>{kind} возврат</b>\n\nTelegram ID: <code>{id}</code>\nPayment: <code>{external_id}</code>\nВозврат: {price} ₽ (всего возвращено {refunded_total} из {total} ₽)\nТариф: {plan}\n\n{reason}",
        "buttons": [],
    },
    "admin.alert.refund_unknown": {
        "source": "src/app/services/payments/refunds.py:process_refund_webhook (payment not in DB)",
        "html": "↩️ <b>Возврат по неизвестному платежу</b>\n\nRefund: <code>{refund_id}</code>\nPayment: <code>{external_id}</code>\nСумма: {price} {currency}\nПлатежа нет в БД бота, доступ не трогали.",
        "buttons": [],
    },
    "admin.alert.promo_applied": {
        "source": "src/app/routers/start.py:_handle_promo_command_locked (admin_msg; {code} upper-cased; {username} is '@name' or 'ID:<id>')",
        "html": "🎁 <b>ПРОМОКОД {code} АКТИВИРОВАН</b>\n\n👤 <b>Пользователь:</b> {username}\n🆔 <b>Telegram ID:</b> <code>{id}</code>\n📝 <b>Имя:</b> {name}\n\n📦 <b>Тариф:</b> {plan} {days} дней\n📅 <b>Действует до:</b> {date}\n🔗 <b>Remnawave ID:</b> <code>{remna_id}</code>\n✅ <b>Подписка выдана автоматически</b>",
        "buttons": [],
    },
    "admin.alert.grant": {
        "source": "src/app/routers/admin.py:_handle_friend_grant (edits request alert: first paragraph + marker)",
        "html": "{original_title}\n\n✅ Обработано\n⭐ Premium на {period} выдан администратором.",
        "buttons": [],
    },
    "admin.alert.sun718_applied": {
        "source": "src/app/routers/start.py:_cmd_sun718_locked -> _sun718_notify_admins (no-revert variant; {username} is '@name' or 'ID:<id>')",
        "html": "<b>🎁 SUN718 АКТИВИРОВАН</b>\n\n👤 <b>Пользователь:</b> {username}\n🆔 <b>Telegram ID:</b> <code>{id}</code>\n📝 <b>Имя:</b> {name}\n\n📦 <b>Тариф:</b> Pro 5 дней\n📅 <b>Pro до:</b> {date}\n🔗 <b>Remnawave ID:</b> <code>{remna_id}</code>\n✅ <b>Записано в БД для рефералки</b>",
        "buttons": [],
    },
    "admin.alert.sun718_revert": {
        "source": "src/app/tasks/sun718_revert.py:_revert_one -> _notify (success, no 'bought Pro' line)",
        "html": "<b>🔄 SUN718 REVERT выполнен</b>\n\n👤 <code>{id}</code>\n📦 Squad возвращён: <b>{plan_from}</b> → <b>{plan}</b>\n🔗 Remnawave ID: <code>{remna_id}</code>",
        "buttons": [],
    },
    "admin.alert.referral_payment": {
        "source": "src/app/services/referral_tracker.py:notify_referral_payment_if_applicable (admin_b; {username} is '@name' or 'ID:<code>id</code>')",
        "html": "💰 <b>SUN718: +{months} мес к рефералке</b>\n\n👤 {username} ({name})\n📦 Оплатил Pro {months} мес\n\n📊 <b>Текущий пул:</b>\n  • Pro-месяцев: <b>{earned}</b>  (было {earned_before})\n  • Бонус заработано: <b>{bonus}</b>  (целых: {full_bonus})\n  • Уже выплачено: <b>{paid_out}</b>\n  • <b>Доступно к выдаче: {available}</b>",
        "buttons": [],
    },
    "admin.alert.referral_bonus": {
        "source": "src/app/services/referral_tracker.py:notify_referral_payment_if_applicable (admin_c)",
        "html": "🎁 <b>SUN718: +{delta} бонусн. {word} заработано!</b>\n\nЦелых бонусов всего: <b>{full_bonus}</b>\nУже выплачено: <b>{paid_out}</b>\n<b>Доступно к выдаче: {available} мес</b>\n\nВыдай в Remna вручную и зафиксируй: <code>/referral_payout sun718 N</code>",
        "buttons": [],
    },
    "admin.alert.referral_payout": {
        "source": "src/app/services/referral_tracker.py:notify_payout (admin_text)",
        "html": "✅ <b>SUN718 PAYOUT записана</b>\n\n💸 Выплачено: <b>{months} мес</b>\n📝 Note: {note}\n\n📊 <b>Состояние:</b>\n  • Заработано: {full_bonus} целых бонусов ({earned} Pro-мес)\n  • Выплачено всего: <b>{paid_out}</b>\n  • <b>Доступно к выдаче: {available}</b>",
        "buttons": [],
    },
    "admin.alert.reconciler_stuck": {
        "source": "src/app/tasks/remnawave_reconciler.py (stuck alert, MAX_RESYNC_ATTEMPTS=5)",
        "html": "⚠️ <b>Reconciler: подписка застряла</b>\n\nsubscription_id: <code>{subscription_id}</code>\ntg_id: <code>{id}</code>\nremna_user_id: <code>{remna_id}</code>\nvalid_until: <code>{datetime}</code>\nlast_error: <code>{reason}</code>\n\nНе синкается 5+ попыток подряд.",
        "buttons": [],
    },

    # ===================== ERRORS =====================
    "err.generic": {
        "source": "src/app/ui/renderers/error.py:render_error (with request_id) + ui/keyboards/error.py:build_error_keyboard",
        "html": "<b>❌ Ошибка</b>\n\n{reason}\n\n<i>ID запроса: {request_id}</i>\nСообщите этот ID администратору для решения проблемы.",
        "buttons": [["⬅️ В главное меню"]],
    },
    "err.access_denied": {
        "source": "src/app/ui/renderers/error.py:render_access_denied + build_access_denied_keyboard",
        "html": "<b>🚫 Доступ запрещен</b>\n\n{reason}\n\nЕсли вы считаете, что это ошибка, обратитесь к администратору.",
        "buttons": [["⬅️ В главное меню"]],
    },
    "err.remna_unavailable": {
        "source": "src/app/ui/renderers/error.py:render_remna_unavailable + build_remna_unavailable_keyboard",
        "html": "<b>⚠️ Сервис временно недоступен</b>\n\n{reason}\n\nПопробуйте позже или обратитесь к администратору.",
        "buttons": [["🔄 Попробовать снова"], ["⬅️ В главное меню"]],
    },
    "err.stale_button": {
        "source": "src/app/routers/legacy_callbacks.py (callback alert for outdated callback format)",
        "html": "❌ Устаревший формат запроса",
        "buttons": [],
    },
}


if __name__ == "__main__":
    main()

