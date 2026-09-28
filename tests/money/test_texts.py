"""Money texts: no em dashes / yo letter in user-facing strings, plural months, escaping."""
import ast
import pathlib

from app.domain.texts import checkout as T

ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "app"
FILES = [ROOT / "domain" / "texts" / "checkout.py", ROOT / "bot" / "views" / "money.py",
         ROOT / "bot" / "routers" / "checkout.py", ROOT / "bot" / "routers" / "refund.py",
         ROOT / "bot" / "routers" / "admin" / "payments.py"]


def test_no_em_dash_or_yo_in_string_constants():
    bad = []
    for f in FILES:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "—" in node.value or "ё" in node.value.lower():
                    bad.append(f"{f.name}:{node.lineno}")
    assert not bad, bad


def test_months_are_pluralized():
    assert "Lite, 1\u00a0месяц<" in T.paid_user("Lite", 1, None)
    assert "3 месяца" in T.btn_period(3, 329)
    assert "12 месяцев" in T.checkout_screen("Pro", 12, 3999, autorenew=None)


def test_user_values_are_escaped():
    text = T.admin_paid(full_name="<b>x</b>", username="a&b", telegram_id=1, plan_label="Lite, 1 месяц",
                        amount=129, currency="RUB", payment_number=1, total_rub=129, expires_at=None,
                        external_id="e<1>", method="ЮKassa")
    assert "<b>x</b>" not in text and "&lt;b&gt;x&lt;/b&gt;" in text and "e&lt;1&gt;" in text


def test_autorenew_line_only_when_asked():
    assert "Автопродление" not in T.checkout_screen("Lite", 1, 129, autorenew=None)
    assert "Автопродление: включено" in T.checkout_screen("Lite", 1, 129, autorenew=True)
    assert "Автопродление: выключено" in T.checkout_screen("Lite", 1, 129, autorenew=False)


# --- golden: plan list (compact cards) and plan detail ------------------------------------------


def _catalog_plan_options(codes):
    from app.bot.views import money as V
    from app.domain import plans as P

    return [V.PlanOption(c, P.get_plan_name(c), tuple(P.get_plan_features(c)), P.PLAN_CATALOG[c]["prices"][1])
            for c in codes]


def _catalog_periods(code):
    from app.bot.views import money as V
    from app.domain import plans as P

    pr = P.PLAN_CATALOG[code]["prices"]
    return [V.PeriodOption(m, pr[m], P.saving_percent(pr[m], pr[1], m)) for m in sorted(pr)]


def _labels(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_golden_plan_list():
    from app.bot.views import money as V
    from app.domain.plans import MENU_PLAN_CODES

    text, kb = V.plans_view(_catalog_plan_options(MENU_PLAN_CODES), gifts=True)
    assert text == (
        "💳 <b>Тарифы CRS VPN</b>\n\n"
        "🟢 <b>Lite</b> · от 129\xa0₽/мес\n<blockquote>🇫🇮 🇳🇱 · 3\xa0устройства</blockquote>\n\n"
        "🔵 <b>Standard</b> · от 249\xa0₽/мес · ⭐ выбирают чаще\n"
        "<blockquote>🇳🇱 🇫🇮 🇩🇪 · 5\xa0устройств</blockquote>\n\n"
        "💎 <b>Pro</b> · от 449\xa0₽/мес\n<blockquote>🇳🇱 🇫🇮 🇩🇪 🇺🇸 · 10\xa0устройств\n"
        "🛡 Обход блокировок 150 ГБ/мес</blockquote>\n\n"
        "<i>Во всех тарифах: безлимитный трафик и скорость. За год дешевле до 29%.</i>"
    )
    assert _labels(kb) == [
        ["🟢 Lite · 129\xa0₽", "🔵 Standard · 249\xa0₽"], ["💎 Pro · 449\xa0₽"], ["🎁 Подарить", "🏠 В меню"],
    ]


def test_golden_plan_list_with_legacy_plan_and_without_gifts():
    from app.bot.views import money as V
    from app.domain.plans import MENU_PLAN_CODES

    text, kb = V.plans_view(_catalog_plan_options([*MENU_PLAN_CODES, "basic"]), gifts=False)
    assert "🔹 <b>Базовый тариф</b> · от 99\xa0₽/мес\n<blockquote>🇳🇱 · 5\xa0устройств</blockquote>" in text
    assert _labels(kb) == [
        ["🟢 Lite · 129\xa0₽", "🔵 Standard · 249\xa0₽"], ["💎 Pro · 449\xa0₽", "🔹 Базовый тариф · 99\xa0₽"],
        ["🏠 В меню"],
    ]


def test_golden_plan_detail_pro():
    from app.bot.views import money as V
    from app.domain.plans import get_plan_features

    text, kb = V.periods_view("pro", "Pro", get_plan_features("pro"), _catalog_periods("pro"))
    assert text == (
        "💎 <b>Pro</b>\n<blockquote>🇳🇱 Нидерланды\n🇫🇮 Финляндия\n🇩🇪 Германия\n🇺🇸 США\n"
        "📱 До 10 устройств\n♾ Безлимитный трафик и скорость</blockquote>\n\n"
        "🛡 <b>Обход блокировок</b>\n<blockquote>Отдельная ссылка через российский вход для мобильного "
        "интернета с белыми списками, 150 ГБ в месяц.</blockquote>\n\n"
        "<i>Выбери срок кнопкой ниже.</i>"
    )
    assert _labels(kb) == [
        ["1\xa0месяц · 449\xa0₽"], ["3\xa0месяца · 1\xa0199\xa0₽ (−11%)"],
        ["6\xa0месяцев · 2\xa0199\xa0₽ (−18%)"], ["12\xa0месяцев · 3\xa0999\xa0₽ (−26%)"],
        ["⬅️ Назад", "🏠 В меню"],
    ]


def test_plan_detail_without_obhod_has_no_obhod_block():
    from app.bot.views import money as V
    from app.domain.plans import get_plan_features

    text, _ = V.periods_view("lite", "Lite", get_plan_features("lite"), _catalog_periods("lite"))
    assert text.startswith("🟢 <b>Lite</b>\n<blockquote>🇫🇮 Финляндия\n🇳🇱 Нидерланды\n📱 До 3 устройств")
    assert "Обход" not in text
