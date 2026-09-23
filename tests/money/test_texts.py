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
    assert "1 месяц," in T.paid_user("Lite", 1, None)
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
