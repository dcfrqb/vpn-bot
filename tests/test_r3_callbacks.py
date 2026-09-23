"""3.0 Foundation: CallbackData classes fit 64 bytes and do not collide with 2.x strings."""
import pytest

from app.bot import callbacks as cb
from app.bot.legacy_aliases import NATIVE_LEGACY_STRINGS

LONG_PLAN = "obhod_500x"  # longer than any plan/package code in domain.plans
BIG_INT = 2_147_483_647  # max int4 id
ARG24 = "x" * 24
UUID = "2f8a1c3e-000f-5000-9000-1b2c3d4e5f60"

# Worst case each class must survive (fields at their documented maximum).
WORST = [
    cb.Nav(s="subscription_plan_detail", p="select.standard&12"),
    cb.Plan(c=LONG_PLAN),
    cb.Period(c=LONG_PLAN, m=12),
    cb.PayCheck(pid=BIG_INT, ext=UUID),
    cb.PayStars(c=LONG_PLAN, m=12),
    cb.AutoPay(a="info"),
    cb.Dev(a="unlink", id="a1b2c3d4"),
    cb.RefundReq(pid=BIG_INT),
    cb.AdmRefund(a="no", rid=BIG_INT),
    cb.AdmReview(a="ok", pid=BIG_INT),
    cb.PromoAct(a="apply", arg="SUMMER-2026-FRIENDS-XYZ"),
    cb.PromoAdm(a="toggle", id=BIG_INT, arg=ARG24),
    cb.Adm(s="promo_req", a="grant_forever", arg="9999999999.premium.12"),
    cb.Bc(a="unsub"),
    cb.BcAdm(a="credit", id=BIG_INT, arg=ARG24),
    cb.Gift(a="claim", id="g_" + "A" * 22),
]


def test_every_class_has_a_worst_case_sample():
    assert {type(x) for x in WORST} == set(cb.ALL_CALLBACKS)


@pytest.mark.parametrize("obj", WORST, ids=lambda o: type(o).__name__)
def test_worst_case_fits_64_bytes_and_roundtrips(obj):
    packed = obj.pack()
    assert len(packed.encode("utf-8")) <= 64, (packed, len(packed.encode()))
    assert type(obj).unpack(packed) == obj


def test_prefixes_are_unique_and_short():
    prefixes = [c.__prefix__ for c in cb.ALL_CALLBACKS]
    assert len(prefixes) == len(set(prefixes))
    assert all(1 <= len(p) <= 2 for p in prefixes)


def test_no_prefix_collides_with_legacy_strings():
    for c in cb.ALL_CALLBACKS:
        head = c.__prefix__ + ":"
        for legacy in cb.LEGACY_STRING_PREFIXES:
            assert not legacy.startswith(head) and not head.startswith(legacy), (c.__name__, legacy)


def test_bc_is_byte_identical_to_2x_broadcast_buttons():
    from app.services.broadcast import CLOSE_CALLBACK_DATA, UNSUB_CALLBACK_DATA

    assert set(NATIVE_LEGACY_STRINGS) == {UNSUB_CALLBACK_DATA, CLOSE_CALLBACK_DATA}
    assert cb.Bc.unpack(UNSUB_CALLBACK_DATA).a == "unsub"


def test_money_never_in_callback_fields():
    for c in cb.ALL_CALLBACKS:
        for name in c.model_fields:
            assert name not in {"amount", "price", "sum", "rub"}, (c.__name__, name)
