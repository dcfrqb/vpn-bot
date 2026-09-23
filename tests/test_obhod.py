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


def test_obhod_packages_purchasable_only_with_real_price():
    # Цены проставлены заказчиком 2026-07-01 (599/1199): пакеты продаются.
    for code in plans.OBHOD_PACKAGE_CODES:
        assert plans.is_obhod_package_code(code) is True
        assert plans.is_obhod_package_purchasable(code) is True
    # Плейсхолдер-цена 0 по-прежнему блокирует продажу.
    with patch.dict(plans.OBHOD_PACKAGE_CATALOG,
                    {"obhod_250": {**plans.OBHOD_PACKAGE_CATALOG["obhod_250"], "price": 0}}):
        assert plans.is_obhod_package_purchasable("obhod_250") is False


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

    # Хотфикс 2.1: только от telegram_id (@ник переиспользуемый -> захват чужого obhod)
    assert build_obhod_username(123, username="test_user") == "tg_123_obhod"
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
    # По умолчанию обходного юзера в Remnawave ещё нет (предрезолв пуст).
    mock_client.get_user_by_username = AsyncMock(return_value=None)
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
    assert kwargs["username"] == "tg_555_obhod"
    assert kwargs["traffic_limit_bytes"] == plans.obhod_base_limit_bytes()
    assert kwargs["traffic_limit_strategy"] == "MONTH"
    # Создана obhod-подписка.
    subs = [o for o in state["added"] if isinstance(o, Subscription)]
    assert len(subs) == 1
    assert subs[0].sub_kind == "obhod"
    assert subs[0].active is True
    assert subs[0].remna_user_id == "obhod-uuid-new"


@pytest.mark.asyncio
async def test_ensure_obhod_drops_site_profile_cache():
    """Нит из ревью: obhod ensure тоже должен сбрасывать кэш профиля сайта,
    иначе сайт до 60 с не увидит новый obhod-аккаунт."""
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555, username="vasya")
    session, _ = _fake_session(existing_obhod=None, tg=tg)
    mock_client = _patch_remna_for_obhod()
    valid_until = datetime.utcnow() + timedelta(days=30)

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client), \
         patch("app.services.cache.invalidate_site_profile_cache", AsyncMock()) as inv:
        await obhod_service.ensure_obhod_for_pro(
            session=session,
            telegram_user_id=555,
            plan_code="pro",
            valid_until=valid_until,
        )

    inv.assert_awaited_once_with(555)


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
async def test_ensure_obhod_recovers_orphan_by_username():
    """M1: юзер уже есть в Remnawave (орфан из прошлой попытки) → ensure находит
    его по username, переиспользует uuid и пишет DB-строку, НЕ создаёт нового."""
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555, username="vasya")
    session, state = _fake_session(existing_obhod=None, tg=tg)
    mock_client = _patch_remna_for_obhod()
    # Предрезолв находит существующего obhod-юзера.
    mock_client.get_user_by_username = AsyncMock(
        return_value={"uuid": "orphan-uuid-1", "username": "tg_555_obhod", "telegramId": None}
    )
    valid_until = datetime.utcnow() + timedelta(days=30)

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        url = await obhod_service.ensure_obhod_for_pro(
            session=session,
            telegram_user_id=555,
            plan_code="pro",
            valid_until=valid_until,
        )

    assert url == "https://sub/obhod"
    # НЕ создаём нового — переиспользуем орфана.
    mock_client.create_obhod_user.assert_not_called()
    mock_client.update_user.assert_awaited()
    # DB-строка записана с uuid орфана.
    subs = [o for o in state["added"] if isinstance(o, Subscription)]
    assert len(subs) == 1
    assert subs[0].remna_user_id == "orphan-uuid-1"
    assert subs[0].sub_kind == "obhod"


@pytest.mark.asyncio
async def test_ensure_obhod_recovers_on_duplicate_create():
    """M1: гонка — предрезолв пуст, create падает duplicate, затем резолв по
    username восстанавливает uuid (а не валит обход)."""
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555, username="vasya")
    session, state = _fake_session(existing_obhod=None, tg=tg)
    mock_client = _patch_remna_for_obhod()
    # Предрезолв пуст в первый раз, после duplicate-create — находит юзера.
    mock_client.get_user_by_username = AsyncMock(
        side_effect=[None, {"uuid": "dup-uuid-2", "username": "tg_555_obhod"}]
    )
    mock_client.create_obhod_user = AsyncMock(
        side_effect=Exception("user already exists")
    )
    valid_until = datetime.utcnow() + timedelta(days=30)

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        url = await obhod_service.ensure_obhod_for_pro(
            session=session,
            telegram_user_id=555,
            plan_code="pro",
            valid_until=valid_until,
        )

    assert url == "https://sub/obhod"
    mock_client.create_obhod_user.assert_awaited_once()  # попытка была
    # Восстановились по username и обновили юзера.
    mock_client.update_user.assert_awaited()
    subs = [o for o in state["added"] if isinstance(o, Subscription)]
    assert len(subs) == 1
    assert subs[0].remna_user_id == "dup-uuid-2"


@pytest.mark.asyncio
async def test_ensure_obhod_never_adopts_foreign_user_by_username():
    """Хотфикс 2.1: юзер с тем же username, но с telegramId (чужой основной
    аккаунт) — не забираем, obhod не выдаем этим путем."""
    from app.services import obhod_service

    tg = TelegramUser(telegram_id=555, username="vasya")
    session, state = _fake_session(existing_obhod=None, tg=tg)
    mock_client = _patch_remna_for_obhod()
    mock_client.get_user_by_username = AsyncMock(
        return_value={"uuid": "foreign", "username": "tg_555_obhod", "telegramId": 999}
    )
    mock_client.create_obhod_user = AsyncMock(side_effect=Exception("user already exists"))
    valid_until = datetime.utcnow() + timedelta(days=30)

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        url = await obhod_service.ensure_obhod_for_pro(
            session=session, telegram_user_id=555, plan_code="pro", valid_until=valid_until,
        )

    assert url is None
    mock_client.update_user.assert_not_called()
    assert [o for o in state["added"] if isinstance(o, Subscription)] == []


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
async def test_apply_obhod_package_commit_fail_rolls_back_cap():
    """M2: кап поднят в Remnawave, commit упал → кап откатывается к базовому,
    возвращается False (split-state не остаётся)."""
    from app.services import obhod_service

    existing = Subscription(
        id=7,
        telegram_user_id=555,
        plan_code="obhod",
        sub_kind="obhod",
        active=True,
        remna_user_id="obhod-existing-uuid",
        config_data={},  # пакета раньше не было → откат к базовому 100 ГБ
    )
    session, _ = _fake_session(existing_obhod=existing)
    # commit падает.
    session.commit = AsyncMock(side_effect=Exception("db down"))

    mock_client = AsyncMock()
    mock_client.update_user = AsyncMock(return_value={})
    mock_client.close = AsyncMock()

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        ok = await obhod_service.apply_obhod_package(
            session=session,
            telegram_user_id=555,
            package_code="obhod_250",
            payment_id=7,
        )

    assert ok is False
    # update_user вызван дважды: подъём капа + откат.
    assert mock_client.update_user.await_count == 2
    raise_call = mock_client.update_user.await_args_list[0]
    restore_call = mock_client.update_user.await_args_list[1]
    assert raise_call.kwargs["traffic_limit_bytes"] == 250 * 1024**3
    # Откат — к базовому лимиту (пакета раньше не было).
    assert restore_call.kwargs["traffic_limit_bytes"] == plans.obhod_base_limit_bytes()
    # Rollback БД вызван.
    session.rollback.assert_awaited()


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
    mock_client.disable_user = AsyncMock(return_value={})
    mock_client.close = AsyncMock()

    with patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        changed = await obhod_service.deactivate_obhod(session, 555)

    assert changed is True
    assert existing.active is False
    # Отключен в Remnawave через disable (прошлый expireAt панель отклоняет 400).
    mock_client.disable_user.assert_awaited_once_with("obhod-existing-uuid")


# ---------------------------------------------------------------------------
# 3c. M3 — is_pro/ссылка обхода не должны зависеть от живого Remnawave
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_obhod_link_info_soft_degrades_to_saved_url():
    """M3: live-трафик недоступен → url берётся из config_data (не теряется),
    остаток None; active/url не зависят от живого Remnawave."""
    from app.services import obhod_service

    existing = Subscription(
        id=7,
        telegram_user_id=555,
        sub_kind="obhod",
        plan_code="obhod",
        active=True,
        remna_user_id="obhod-uuid-1",
        valid_until=datetime.utcnow() + timedelta(days=30),
        config_data={"subscription_url": "https://saved/obhod"},
    )

    # Сессия, отдающая нашу obhod-строку.
    async def mock_execute(query):
        r = MagicMock()
        r.scalar_one_or_none.return_value = existing
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=mock_execute)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    # Remnawave недоступен — get_user_traffic_info падает.
    mock_client = AsyncMock()
    mock_client.get_user_traffic_info = AsyncMock(side_effect=Exception("remna down"))
    mock_client.get_user_subscription_url = AsyncMock(
        side_effect=Exception("remna down")
    )
    mock_client.close = AsyncMock()

    with patch(
        "app.db.session.SessionLocal", MagicMock(return_value=session_cm)
    ), patch.object(obhod_service, "RemnaClient", return_value=mock_client):
        info = await obhod_service.get_obhod_link_info(555)

    assert info is not None
    # Ссылка сохранена несмотря на падение live.
    assert info["url"] == "https://saved/obhod"
    # Цифры остатка скрыты (None), но не сама ссылка.
    assert info["used_bytes"] is None
    assert info["limit_bytes"] is None
    assert info["active"] is True




# ---------------------------------------------------------------------------
# 4. Connect VM/renderer — две ссылки у Pro, одна у не-Pro
# ---------------------------------------------------------------------------


    # Остаток трафика обхода рендерер пока не показывает (04 M7, план 3.0).






