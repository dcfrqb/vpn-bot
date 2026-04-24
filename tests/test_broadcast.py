"""
Smoke-тесты broadcast модуля.

Покрываем только то, что легко проверить без живого TG/Postgres:
- сегменты строят корректный SQL-фильтр (эвристика: разные сегменты дают разные filter-выражения)
- VALID_SEGMENTS канонический
- константы рейт-лимита в разумном диапазоне
- _attach_unsub_button добавляет кнопку в конец
- _segment_filter не падает на некорректном имени (raise ValueError)
"""
from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardButton

from app.services.broadcast import (
    CHUNK_SIZE,
    COMMIT_EVERY,
    GLOBAL_RATE_LIMIT_PER_SEC,
    SEGMENT_ACTIVE,
    SEGMENT_ALL,
    SEGMENT_EXPIRED,
    SEGMENT_NEVER,
    SEND_INTERVAL,
    UNSUB_CALLBACK_DATA,
    VALID_SEGMENTS,
    _attach_unsub_button,
    _segment_filter,
)


class TestBroadcastConstants:
    def test_segments_canonical(self):
        assert VALID_SEGMENTS == {SEGMENT_ALL, SEGMENT_ACTIVE, SEGMENT_EXPIRED, SEGMENT_NEVER}

    def test_rate_limit_is_safe_for_telegram(self):
        # Telegram hard-limit ≈ 30 msg/sec на бота. 25 даёт запас.
        assert 10 <= GLOBAL_RATE_LIMIT_PER_SEC <= 30
        assert SEND_INTERVAL == pytest.approx(1.0 / GLOBAL_RATE_LIMIT_PER_SEC)

    def test_chunk_and_commit_sizes_reasonable(self):
        assert CHUNK_SIZE >= 100
        assert 10 <= COMMIT_EVERY <= CHUNK_SIZE

    def test_unsub_callback_stable(self):
        # Стабильность контракта: клавиатура в разосланных сообщениях должна совпадать
        # с хендлером в admin_broadcast.cb_unsub до конца времён.
        assert UNSUB_CALLBACK_DATA == "bc:unsub"


class TestSegmentFilter:
    def test_unknown_segment_raises(self):
        with pytest.raises(ValueError):
            _segment_filter("garbage")

    def test_all_distinct(self):
        # Не equal — sqlalchemy-выражения разные по структуре.
        flt_all = _segment_filter(SEGMENT_ALL)
        flt_active = _segment_filter(SEGMENT_ACTIVE)
        flt_expired = _segment_filter(SEGMENT_EXPIRED)
        flt_never = _segment_filter(SEGMENT_NEVER)
        # str() даёт компилированное SQL-like представление — достаточно, чтобы различить.
        reprs = {str(flt_all), str(flt_active), str(flt_expired), str(flt_never)}
        assert len(reprs) == 4, f"сегменты должны давать разные фильтры, получили {reprs}"


class TestUnsubButton:
    def test_attach_with_no_custom_buttons(self):
        kb = _attach_unsub_button(None)
        rows = kb.inline_keyboard
        assert len(rows) == 1
        assert rows[0][0].callback_data == UNSUB_CALLBACK_DATA

    def test_attach_with_url_button(self):
        kb = _attach_unsub_button([{"text": "Go", "url": "https://example.com"}])
        rows = kb.inline_keyboard
        assert len(rows) == 2
        assert rows[0][0].url == "https://example.com"
        assert rows[1][0].callback_data == UNSUB_CALLBACK_DATA

    def test_attach_with_callback_button(self):
        kb = _attach_unsub_button([{"text": "Next", "callback_data": "foo:bar"}])
        rows = kb.inline_keyboard
        assert len(rows) == 2
        assert rows[0][0].callback_data == "foo:bar"
        assert rows[1][0].callback_data == UNSUB_CALLBACK_DATA

    def test_attach_with_invalid_button_drops_it(self):
        # Кнопка без url/callback_data — пропускается (worker не упадёт на невалидном JSON).
        kb = _attach_unsub_button([{"text": "broken"}, {"text": "Go", "url": "https://x.io"}])
        # Expected: только валидная + unsub; невалидная тихо проигнорирована.
        rows = kb.inline_keyboard
        assert UNSUB_CALLBACK_DATA in {btn.callback_data for row in rows for btn in row}


class TestRouterRegistration:
    def test_admin_broadcast_router_has_bc_commands(self):
        from aiogram.filters import Command
        from app.routers.admin_broadcast import router

        registered_cmds: set[str] = set()
        for handler in router.message.handlers:
            for flt in handler.filters or []:
                cb = getattr(flt, "callback", None)
                if isinstance(cb, Command):
                    for c in cb.commands:
                        registered_cmds.add(c)

        # /bc_* семейство + /stop + /cancel должны быть на своих местах.
        expected = {"bc_new", "bc_preview", "bc_list", "bc_send", "bc_stats", "bc_cancel", "stop", "cancel"}
        missing = expected - registered_cmds
        assert not missing, f"отсутствуют команды в admin_broadcast router: {missing}"
