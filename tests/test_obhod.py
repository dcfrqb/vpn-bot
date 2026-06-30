"""Тесты архитектуры «две подписки» (основная + обход с лимитом трафика).

Покрывает:
- config обхода (константы/каталог пакетов/гейты цен);
- Remnawave-клиент: trafficLimit* в payload, create_obhod_user БЕЗ telegramId;
- obhod_service: провижн obhod-юзера для Pro (создание + продление), пакет
  поднимает кап, deactivate гасит обход; обход НЕ выдаётся не-Pro;
- connect VM/renderer: у Pro две ссылки, у не-Pro заглушка.

Remnawave и БД — только моки.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import plans
from app.db.models import Subscription, TelegramUser

# ---------------------------------------------------------------------------
# 1. Config обхода
# ---------------------------------------------------------------------------


def test_obhod_eligibility_only_pro():
    assert plans.is_obhod_eligible_plan("pro") is True
    for code in ("lite", "standard", "basic", "premium", "trial", None, ""):
        assert plans.is_obhod_eligible_plan(code) is False


def test_obhod_base_limit_bytes():
    assert plans.OBHOD_BASE_LIMIT_GB == 100
    assert plans.obhod_base_limit_bytes() == 100 * 1024 * 1024 * 1024


def test_obhod_packages_have_placeholder_prices_not_purchasable():
    # Пока заказчик не проставил цены — пакеты НЕ продаются.
    for code in plans.OBHOD_PACKAGE_CODES:
        assert plans.is_obhod_package_code(code) is True
        assert plans.is_obhod_package_purchasable(code) is False  # price=0


def test_obhod_package_limit_bytes():
    assert plans.get_obhod_package_limit_bytes("obhod_250") == 250 * 1024**3
    assert plans.get_obhod_package_limit_bytes("obhod_500") == 500 * 1024**3
    assert plans.get_obhod_package_limit_bytes("nope") is None


def test_obhod_package_purchasable_when_price_set():
    with patch.dict(
        plans.OBHOD_PACKAGE_CATALOG,
        {"obhod_250": {**plans.OBHOD_PACKAGE_CATALOG["obhod_250"], "price": 199}},
    ):
        assert plans.is_obhod_package_purchasable("obhod_250") is True


# ---------------------------------------------------------------------------
# 2. Remnawave-клиент
# ---------------------------------------------------------------------------


def test_payload_builder_traffic_limit_fields():
    from app.remnawave.client import build_user_payload_from_kwargs

    payload = build_user_payload_from_kwargs(
        {"traffic_limit_bytes": 100 * 1024**3, "traffic_limit_strategy": "MONTH"}
    )
    assert payload["trafficLimitBytes"] == 100 * 1024**3
    assert payload["trafficLimitStrategy"] == "MONTH"


@pytest.mark.asyncio
async def test_create_obhod_user_has_no_telegram_id():
    from app.remnawave.client import RemnaClient

    client = RemnaClient()
    captured = {}

    async def fake_request(method, endpoint, **kwargs):
        captured["json"] = kwargs.get("json")
        return {"response": {"uuid": "obhod-uuid-1"}}

    with patch.object(client, "request", side_effect=fake_request):
        uuid = await client.create_obhod_user(
            username="tg_123_obhod",
            password="x" * 24,
            expire_at="2026-12-31T00:00:00Z",
            active_internal_squads=["squad-uuid"],
            traffic_limit_bytes=100 * 1024**3,
            traffic_limit_strategy="MONTH",
        )
    assert uuid == "obhod-uuid-1"
    body = captured["json"]
    # КРИТИЧНО: obhod-юзер БЕЗ telegramId.
    assert "telegramId" not in body
    assert body["trafficLimitBytes"] == 100 * 1024**3
    assert body["trafficLimitStrategy"] == "MONTH"
    assert body["activeInternalSquads"] == ["squad-uuid"]


def test_build_obhod_username_suffix():
    from app.services.obhod_service import build_obhod_username

    assert build_obhod_username(123, username="kozlova") == "tg_kozlova_obhod"
    assert build_obhod_username(123) == "tg_123_obhod"


# ---------------------------------------------------------------------------
# 3. obhod_service
# ---------------------------------------------------------------------------


def _fake_session(existing_obhod=None, tg=None):
    """mock_session: telegram_users → tg, subscriptions(obhod) → existing_obhod."""
    state = {"added": []}

    async def mock_execute(query):
        s = str(query).lower()
        r = MagicMock()
        if "from telegram_users" in s:
            r.scalar_one_or_none.return_value = tg
        elif "from remna_users" in s:
            r.scalar_one_or_none.return_value = None
        else:  # subscriptions
            r.scalar_one_or_none.return_value = existing_obhod
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=mock_execute)
    session.add = MagicMock(side_effect=lambda o: state["added"].append(o))
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session, state


def _patch_remna_for_obhod(create_uuid="obhod-uuid-new", sub_url="https://sub/obhod"):
    """Контекст-менеджер, патчащий RemnaClient внутри obhod_service."""
    mock_client = AsyncMock()
    mock_client.get_squad_by_name = AsyncMock(return_value={"uuid": "obhod-squad-uuid"})
    mock_client.create_obhod_user = AsyncMock(return_value=create_uuid)
    mock_client.update_user = AsyncMock(return_value={})
    mock_client.get_user_subscription_url = AsyncMock(return_value=sub_url)
    mock_client.close = AsyncMock()
    return mock_client


@pytest.mark.asyncio
async def test_ensure_obhod_creates_user_for_pro():
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555, username="vasya")
    session, state = _fake_session(existing_obhod=None, tg=tg)
    mock_client = _patch_remna_for_obhod()
    valid_until = datetime.utcnow() + timedelta(days=30)

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        url = await obhod_service.ensure_obhod_for_pro(
            session=session,
            telegram_user_id=555,
            plan_code="pro",
            valid_until=valid_until,
        )

    assert url == "https://sub/obhod"
    # Создан obhod-юзер без telegramId через create_obhod_user.
    mock_client.create_obhod_user.assert_awaited_once()
    kwargs = mock_client.create_obhod_user.await_args.kwargs
    assert kwargs["username"] == "tg_vasya_obhod"
    assert kwargs["traffic_limit_bytes"] == plans.obhod_base_limit_bytes()
    assert kwargs["traffic_limit_strategy"] == "MONTH"
    # Создана obhod-подписка.
    subs = [o for o in state["added"] if isinstance(o, Subscription)]
    assert len(subs) == 1
    assert subs[0].sub_kind == "obhod"
    assert subs[0].active is True
    assert subs[0].remna_user_id == "obhod-uuid-new"


@pytest.mark.asyncio
async def test_ensure_obhod_skips_non_pro():
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555)
    session, state = _fake_session(existing_obhod=None, tg=tg)

    with patch.object(obhod_service, "RemnaClient") as mc:
        url = await obhod_service.ensure_obhod_for_pro(
            session=session,
            telegram_user_id=555,
            plan_code="standard",  # не Pro
            valid_until=datetime.utcnow() + timedelta(days=30),
        )
    assert url is None
    mc.assert_not_called()  # Remnawave вообще не дёргается


@pytest.mark.asyncio
async def test_ensure_obhod_extends_existing_user():
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555)
    existing = Subscription(
        id=7,
        telegram_user_id=555,
        plan_code="obhod",
        sub_kind="obhod",
        active=True,
        remna_user_id="obhod-existing-uuid",
        config_data={},
    )
    session, state = _fake_session(existing_obhod=existing, tg=tg)
    mock_client = _patch_remna_for_obhod()
    valid_until = datetime.utcnow() + timedelta(days=60)

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        await obhod_service.ensure_obhod_for_pro(
            session=session,
            telegram_user_id=555,
            plan_code="pro",
            valid_until=valid_until,
        )

    # Не создаём нового — обновляем существующего по uuid.
    mock_client.create_obhod_user.assert_not_called()
    mock_client.update_user.assert_awaited()
    upd_kwargs = mock_client.update_user.await_args.kwargs
    assert upd_kwargs["traffic_limit_bytes"] == plans.obhod_base_limit_bytes()
    assert existing.valid_until == valid_until


@pytest.mark.asyncio
async def test_apply_obhod_package_raises_cap():
    from app.services import obhod_service

    existing = Subscription(
        id=7,
        telegram_user_id=555,
        plan_code="obhod",
        sub_kind="obhod",
        active=True,
        remna_user_id="obhod-existing-uuid",
        config_data={},
    )
    session, _ = _fake_session(existing_obhod=existing)
    mock_client = AsyncMock()
    mock_client.update_user = AsyncMock(return_value={})
    mock_client.close = AsyncMock()

    # Временно делаем пакет с реальной ценой, чтобы лимит резолвился.
    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        ok = await obhod_service.apply_obhod_package(
            session=session,
            telegram_user_id=555,
            package_code="obhod_250",
        )

    assert ok is True
    upd_kwargs = mock_client.update_user.await_args.kwargs
    assert upd_kwargs["traffic_limit_bytes"] == 250 * 1024**3
    assert existing.config_data["package"] == "obhod_250"
    assert "package_until" in existing.config_data


@pytest.mark.asyncio
async def test_apply_obhod_package_no_active_obhod():
    from app.services import obhod_service

    session, _ = _fake_session(existing_obhod=None)
    with patch.object(obhod_service, "RemnaClient") as mc:
        ok = await obhod_service.apply_obhod_package(
            session=session, telegram_user_id=555, package_code="obhod_250"
        )
    assert ok is False
    mc.assert_not_called()


@pytest.mark.asyncio
async def test_apply_obhod_package_idempotent_same_payment():
    """C1: повтор того же payment_id НЕ поднимает кап/период второй раз."""
    from app.services import obhod_service

    existing = Subscription(
        id=7,
        telegram_user_id=555,
        plan_code="obhod",
        sub_kind="obhod",
        active=True,
        remna_user_id="obhod-existing-uuid",
        config_data={},
    )
    session, _ = _fake_session(existing_obhod=existing)
    mock_client = AsyncMock()
    mock_client.update_user = AsyncMock(return_value={})
    mock_client.close = AsyncMock()

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        # Первый вызов — кап поднят, payment_id зафиксирован.
        ok1 = await obhod_service.apply_obhod_package(
            session=session,
            telegram_user_id=555,
            package_code="obhod_250",
            payment_id=42,
        )
        assert ok1 is True
        assert existing.config_data["applied_payment_id"] == 42
        first_until = existing.config_data["package_until"]
        assert mock_client.update_user.await_count == 1

        # Повторная доставка ТОГО ЖЕ платежа — no-op, без второго update_user.
        ok2 = await obhod_service.apply_obhod_package(
            session=session,
            telegram_user_id=555,
            package_code="obhod_250",
            payment_id=42,
        )
        assert ok2 is True
        # Remnawave НЕ дёрнут второй раз (кап не поднят повторно).
        assert mock_client.update_user.await_count == 1
        # package_until НЕ продлён повторно.
        assert existing.config_data["package_until"] == first_until


@pytest.mark.asyncio
async def test_apply_obhod_package_different_payment_applies_again():
    """C1: другой payment_id (легитимная докупка) применяется заново."""
    from app.services import obhod_service

    existing = Subscription(
        id=7,
        telegram_user_id=555,
        plan_code="obhod",
        sub_kind="obhod",
        active=True,
        remna_user_id="obhod-existing-uuid",
        config_data={"applied_payment_id": 42},
    )
    session, _ = _fake_session(existing_obhod=existing)
    mock_client = AsyncMock()
    mock_client.update_user = AsyncMock(return_value={})
    mock_client.close = AsyncMock()

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        ok = await obhod_service.apply_obhod_package(
            session=session,
            telegram_user_id=555,
            package_code="obhod_250",
            payment_id=99,
        )
    assert ok is True
    assert mock_client.update_user.await_count == 1
    assert existing.config_data["applied_payment_id"] == 99


# ---------------------------------------------------------------------------
# 3b. H1 — гейт активного Pro при СОЗДАНИИ платежа за пакет
# ---------------------------------------------------------------------------


def _mock_buy_obhod_callback(user_id=555):
    cb = MagicMock()
    # isinstance(cb, types.CallbackQuery) должен быть True
    from aiogram import types

    cb.__class__ = types.CallbackQuery
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_buy_obhod_without_active_pro_no_payment():
    """H1: без активного обхода/Pro платёж за пакет НЕ создаётся."""
    from app.ui.screens.subscription import SubscriptionPlansScreen
    from app.services import obhod_service

    cb = _mock_buy_obhod_callback()

    # Делаем пакет покупаемым (реальная цена) и гарантируем «нет активного обхода».
    with patch.dict(
        plans.OBHOD_PACKAGE_CATALOG,
        {"obhod_250": {**plans.OBHOD_PACKAGE_CATALOG["obhod_250"], "price": 199}},
    ), patch.object(
        obhod_service, "has_active_obhod", AsyncMock(return_value=False)
    ), patch(
        "app.services.payments.yookassa.create_payment", new=AsyncMock()
    ) as mock_create:
        result = await SubscriptionPlansScreen().handle_action(
            action="buy_obhod",
            payload="obhod_250",
            message_or_callback=cb,
            user_id=555,
        )

    assert result is False
    mock_create.assert_not_called()  # платёж НЕ создан
    cb.answer.assert_awaited()  # юзеру показано сообщение


@pytest.mark.asyncio
async def test_buy_obhod_with_active_pro_creates_payment():
    """H1: при активном обходе платёж за пакет создаётся (гейт пропускает)."""
    from app.ui.screens.subscription import SubscriptionPlansScreen
    from app.services import obhod_service

    cb = _mock_buy_obhod_callback()

    with patch.dict(
        plans.OBHOD_PACKAGE_CATALOG,
        {"obhod_250": {**plans.OBHOD_PACKAGE_CATALOG["obhod_250"], "price": 199}},
    ), patch.object(
        obhod_service, "has_active_obhod", AsyncMock(return_value=True)
    ), patch(
        "app.services.payments.yookassa.create_payment",
        new=AsyncMock(return_value=("https://pay/url", "ext-id-1")),
    ) as mock_create:
        result = await SubscriptionPlansScreen().handle_action(
            action="buy_obhod",
            payload="obhod_250",
            message_or_callback=cb,
            user_id=555,
        )

    assert result is True
    mock_create.assert_awaited_once()
    # Пакет передан как plan_code в платёж.
    assert mock_create.await_args.kwargs["plan_code"] == "obhod_250"


@pytest.mark.asyncio
async def test_deactivate_obhod():
    from app.services import obhod_service

    existing = Subscription(
        id=7,
        telegram_user_id=555,
        sub_kind="obhod",
        plan_code="obhod",
        active=True,
        remna_user_id="obhod-existing-uuid",
    )
    session, _ = _fake_session(existing_obhod=existing)
    mock_client = AsyncMock()
    mock_client.update_user = AsyncMock(return_value={})
    mock_client.close = AsyncMock()

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        changed = await obhod_service.deactivate_obhod(session, 555)

    assert changed is True
    assert existing.active is False
    # Истечён в Remnawave (expireAt в прошлом).
    mock_client.update_user.assert_awaited()


# ---------------------------------------------------------------------------
# 4. Connect VM/renderer — две ссылки у Pro, одна у не-Pro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_renderer_pro_two_links():
    from app.ui.renderers.connect import render_connect_success_with_obhod
    from app.ui.viewmodels.connect import ConnectViewModel

    vm = ConnectViewModel(
        has_subscription=True,
        subscription_url="https://sub/main",
        status="success",
        is_pro=True,
        obhod_url="https://sub/obhod",
        obhod_used_bytes=10 * 1024**3,
        obhod_limit_bytes=100 * 1024**3,
        obhod_active=True,
    )
    text = await render_connect_success_with_obhod(vm)
    assert "https://sub/main" in text
    assert "https://sub/obhod" in text
    assert "Обход блокировок" in text
    assert "Осталось" in text  # остаток показан


@pytest.mark.asyncio
async def test_connect_renderer_non_pro_stub():
    from app.ui.renderers.connect import render_connect_success_with_obhod
    from app.ui.viewmodels.connect import ConnectViewModel

    vm = ConnectViewModel(
        has_subscription=True,
        subscription_url="https://sub/main",
        status="success",
        is_pro=False,
    )
    text = await render_connect_success_with_obhod(vm)
    assert "https://sub/main" in text
    assert "https://sub/obhod" not in text
    assert "Доступен в тарифе Pro" in text


@pytest.mark.asyncio
async def test_connect_keyboard_pro_has_obhod_button():
    from app.ui.keyboards.connect import build_connect_success_keyboard_with_obhod

    kb = build_connect_success_keyboard_with_obhod(
        subscription_url="https://sub/main",
        is_pro=True,
        obhod_url="https://sub/obhod",
        show_more_obhod=True,
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("основную ссылку" in t for t in texts)
    assert any("обхода" in t for t in texts)
    assert any("больше обхода" in t for t in texts)


@pytest.mark.asyncio
async def test_connect_keyboard_non_pro_no_obhod_button():
    from app.ui.keyboards.connect import build_connect_success_keyboard_with_obhod

    kb = build_connect_success_keyboard_with_obhod(
        subscription_url="https://sub/main",
        is_pro=False,
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("обхода" in t.lower() for t in texts)
