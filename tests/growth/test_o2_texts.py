"""O2 (review 3.0 run 3): button vocabulary and gift duration wording are consistent."""
from app.domain.models import PromoOutcome, PromoReward
from app.domain.texts import checkout as C
from app.domain.texts import common as _c
from app.domain.texts import promo as T


def test_applied_text_uses_the_shared_connect_button_name():
    r = PromoReward(code="spring", outcome=PromoOutcome.APPLIED, plan_code="standard", days=7)
    text = T.applied_text("spring", r, plan_title="Standard")
    assert "«Подключиться»" in text
    assert "Подключить VPN" not in text
    # the button under this message is the shared BTN_CONNECT constant
    assert _c.BTN_CONNECT.endswith("Подключиться")


def test_gift_buyer_and_recipient_see_the_same_duration_unit():
    buyer_text = C.gift_paid_buyer("Standard", 1, "https://t.me/bot?start=g_x")
    assert "на 1\xa0месяц" in buyer_text

    r = PromoReward(code="g_x", outcome=PromoOutcome.APPLIED, plan_code="standard", days=30, months=1)
    recipient_text = T.applied_text("g_x", r, plan_title="Standard")
    assert "на 1\xa0месяц" in recipient_text
    assert "дней" not in recipient_text


def test_gift_recipient_falls_back_to_days_without_a_months_reward():
    # legacy/gift rows that never carried months keep the old, still-correct wording
    r = PromoReward(code="g_y", outcome=PromoOutcome.APPLIED, plan_code="standard", days=14, months=None)
    text = T.applied_text("g_y", r, plan_title="Standard")
    assert "на 14\xa0дней" in text
