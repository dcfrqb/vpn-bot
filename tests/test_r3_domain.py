"""3.0 Foundation: domain layer (plans move, models, text helpers)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain import models as m
from app.domain.texts import NBSP, days_ru, fmt_date_msk, fmt_gb, fmt_rub, h, n_plural, plural_ru


def test_core_plans_is_the_same_module_as_domain_plans():
    import app.core.plans as core_plans
    from app.core import plans as core_plans2
    from app.domain import plans

    assert core_plans is plans and core_plans2 is plans
    from app.core.plans import PLAN_CATALOG, quote_purchase  # noqa: F401  old imports keep working


@pytest.mark.parametrize("n,word", [
    (0, "дней"), (1, "день"), (2, "дня"), (4, "дня"), (5, "дней"), (11, "дней"), (12, "дней"),
    (14, "дней"), (21, "день"), (22, "дня"), (25, "дней"), (101, "день"), (111, "дней"), (-1, "день"),
])
def test_plural_ru(n, word):
    assert plural_ru(n, "день", "дня", "дней") == word


def test_n_plural_and_days():
    assert n_plural(3, "день", "дня", "дней") == f"3{NBSP}дня"
    assert days_ru(21) == f"21{NBSP}день"


def test_fmt_date_msk_converts_naive_utc_and_aware():
    naive_utc = datetime(2026, 9, 30, 22, 30)  # 01:30 MSK next day
    assert fmt_date_msk(naive_utc) == "01.10.2026"
    assert fmt_date_msk(naive_utc, with_time=True) == "01.10.2026 01:30"
    aware = datetime(2026, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert fmt_date_msk(aware, with_time=True) == "31.12.2025 22:00"
    assert fmt_date_msk(date(2026, 2, 3)) == "03.02.2026"
    assert fmt_date_msk(None) == "—"


@pytest.mark.parametrize("v,out", [
    (449, f"449{NBSP}₽"), (1199, f"1{NBSP}199{NBSP}₽"), ("3999.00", f"3{NBSP}999{NBSP}₽"),
    (99.5, f"99,50{NBSP}₽"), (None, f"0{NBSP}₽"),
])
def test_fmt_rub(v, out):
    assert fmt_rub(v) == out


def test_fmt_gb_and_h():
    assert fmt_gb(100 * 1024 ** 3) == f"100{NBSP}ГБ"
    assert fmt_gb(int(1.5 * 1024 ** 3)) == f"1,5{NBSP}ГБ"
    assert fmt_gb(0) == f"0{NBSP}ГБ"
    assert h('<b>"x" & y</b>') == "&lt;b&gt;&quot;x&quot; &amp; y&lt;/b&gt;"
    assert h(None) == ""


def test_subscription_state_days_left_and_url_not_in_repr():
    now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    st = m.SubscriptionState(telegram_id=1, active=True, expires_at=now + timedelta(days=2, hours=1),
                             subscription_url="https://sub.example/SECRET")
    assert st.days_left(now) == 3
    assert m.SubscriptionState(telegram_id=1, expires_at=now - timedelta(days=1)).days_left(now) == 0
    assert m.SubscriptionState(telegram_id=1, is_lifetime=True).days_left(now) is None
    assert "SECRET" not in repr(st)
    naive = m.SubscriptionState(telegram_id=1, expires_at=datetime(2026, 9, 24, 12))
    assert naive.days_left(now) == 1


def test_dtos_are_frozen_and_hide_secrets():
    intent = m.PaymentIntent(plan_code="pro", months=1, amount_rub=449, confirmation_url="https://pay/SECRET")
    assert "SECRET" not in repr(intent)
    with pytest.raises(Exception):
        intent.amount_rub = 1  # type: ignore[misc]
    dev = m.DeviceInfo(hwid="abcdef0123456789")
    assert dev.short_id == "23456789" and "abcdef" not in repr(dev)
    pu = m.PanelUser(id=1, subscription_url="https://sub/SECRET", raw={"x": "SECRET"})
    assert "SECRET" not in repr(pu)
    assert m.Quote(plan_code="pro", months=1, amount_rub=0).sellable is False
    assert m.PromoReward(code="x", outcome=m.PromoOutcome.APPLIED).applied


def test_enums_values_match_db_strings():
    assert m.SubKind.MAIN == "main" and m.SubKind.OBHOD == "obhod"
    assert {t.value for t in m.AdminTopic} == {"payments", "refunds", "panel", "errors", "promo", "broadcast", "general"}


def test_ensure_utc():
    assert m.ensure_utc(None) is None
    assert m.ensure_utc(datetime(2026, 1, 1)).tzinfo == timezone.utc
