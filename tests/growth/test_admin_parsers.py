"""Pure pieces of stream E admin: /promo_new parser, id/button parsers, sun718
squad targets, texts, port conformance."""
import pytest

from app.domain.models import PromoOutcome, PromoReward
from app.domain.texts import promo as T


def test_promo_new_parser():
    from app.bot.routers.admin.promo import parse_promo_new

    spec = parse_promo_new("Autumn days=7 plan=pro kind=plan audience=new max=100 per_user=2 valid=30 traffic=50 devices=3")
    assert (spec.code, spec.days, spec.plan_code, spec.kind, spec.audience) == ("Autumn", 7, "pro", "plan", "new")
    assert (spec.max_uses, spec.per_user_limit, spec.traffic_gb, spec.devices) == (100, 2, 50, 3)
    assert spec.valid_until is not None
    assert parse_promo_new("x days=1").audience == "any"
    for bad in ("", "x", "x days=0", "x days=a", "x days=1 color=red", "x days=1 audience=vip", "x days=1 kind=gift",
                "x days=1 junk"):
        with pytest.raises(ValueError):
            parse_promo_new(bad)


def test_broadcast_parsers():
    from app.bot.routers.admin.broadcast import parse_buttons, parse_ids

    assert parse_ids("123456, 234567\n345678 12 abc 123456") == [123456, 234567, 345678]
    assert parse_buttons('[{"text": "a", "url": "https://x"}]') == [{"text": "a", "url": "https://x"}]
    for bad in ('{"text": "a"}', '[{"text": "a"}]', '[{"url": "https://x"}]', "not json",
                '[{"text": "a", "callback_data": "' + "x" * 65 + '"}]'):
        with pytest.raises((ValueError, TypeError)):
            parse_buttons(bad)


def test_sun718_target_squads_keep_foreign_and_drop_tariff():
    from app.services.referral import target_squads

    assert target_squads(["pro", "us-2", "obhod"], "standard") == ["us-2", "obhod", "standard"]
    assert target_squads(["pro"], "pro") == ["pro"]
    assert "pro-m" not in target_squads(["pro", "pro-m"], "lite")  # manual squads are kept by the gateway itself
    assert target_squads(["premium"], "unknown_plan") == ["basic"]


def test_engine_implements_the_port():
    from app.services.ports import PromoService
    from app.services.promo import PromoEngine
    from tests.growth.fakes import MemoryPromoRepo

    assert isinstance(PromoEngine(provisioning=None, status=None, notifier=None, repo=MemoryPromoRepo()), PromoService)


@pytest.mark.parametrize("outcome", list(PromoOutcome))
@pytest.mark.parametrize("code", ["trial", "sun718", "solokhin", "g_abc", "spring"])
def test_every_outcome_has_a_text_without_yo_and_dashes(code, outcome):
    r = PromoReward(code=code, outcome=outcome, plan_code="pro", days=5)
    text = T.applied_text(code, r, plan_title="Pro", support="dcfrq") if r.applied else T.outcome_text(code, r)
    assert text and "\u0451" not in text and "—" not in text


def test_user_text_escapes_the_code():
    r = PromoReward(code="<x>", outcome=PromoOutcome.NOT_FOUND)
    assert "&lt;x&gt;" in T.outcome_text("<x>", r)
