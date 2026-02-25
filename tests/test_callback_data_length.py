"""
Тесты для проверки длины callback_data (64 байта лимит Telegram)
"""
import pytest
import json
from app.navigation.callback_schema import CallbackSchema, CallbackAction, build_cb
from app.core.pagination import Pagination
from app.ui.screens import ScreenID


class TestCallbackDataLength:
    """Тесты для проверки длины callback_data"""
    
    def test_basic_callback_under_limit(self):
        """Базовый callback должен быть под лимитом"""
        callback = CallbackSchema.build(ScreenID.MAIN_MENU, CallbackAction.OPEN)
        assert len(callback.encode('utf-8')) <= 64
        assert callback == "ui:main_menu:open"
    
    def test_callback_with_payload_under_limit(self):
        """Callback с payload должен быть под лимитом"""
        payload = json.dumps({"p": 2, "s": 10})
        callback = CallbackSchema.build(ScreenID.ADMIN_USERS, CallbackAction.PAGE, payload)
        assert len(callback.encode('utf-8')) <= 64
    
    def test_pagination_payload_no_total(self):
        """Pagination payload НЕ должен содержать total"""
        pagination = Pagination(page=2, page_size=10, total=100)
        payload = pagination.to_payload()
        payload_dict = json.loads(payload)
        
        # Проверяем что total НЕ в payload
        assert "total" not in payload_dict
        assert "p" in payload_dict  # page → p
        assert "s" in payload_dict  # page_size → s
        assert payload_dict["p"] == 2
        assert payload_dict["s"] == 10
    
    def test_pagination_with_filter_under_limit(self):
        """Pagination с фильтром должен быть под лимитом"""
        pagination = Pagination(page=2, page_size=10, total=0)
        payload_dict = pagination.to_dict()
        payload_dict["f"] = "suc"  # filter → f, succeeded → suc
        payload = json.dumps(payload_dict)
        
        callback = CallbackSchema.build(ScreenID.ADMIN_PAYMENTS, CallbackAction.PAGE, payload)
        callback_bytes = callback.encode('utf-8')
        
        assert len(callback_bytes) <= 64, f"Callback слишком длинный: {len(callback_bytes)} байт, callback: {callback}"
    
    def test_callback_exceeds_limit_raises_error(self):
        """Callback превышающий 64 байта должен вызывать ошибку"""
        # Создаем очень длинный payload
        long_payload = "x" * 100
        with pytest.raises(ValueError, match="Callback data слишком длинный"):
            CallbackSchema.build(ScreenID.MAIN_MENU, CallbackAction.OPEN, long_payload)
    
    def test_build_cb_respects_limit(self):
        """build_cb должен проверять лимит"""
        long_payload = "x" * 100
        with pytest.raises(ValueError, match="Callback data слишком длинный"):
            build_cb(ScreenID.MAIN_MENU, "open", long_payload)
    
    def test_pagination_compressed_keys(self):
        """Проверка что Pagination использует сжатые ключи"""
        pagination = Pagination(page=5, page_size=20, total=0)
        payload_dict = pagination.to_dict()
        
        # Должны быть сжатые ключи
        assert "p" in payload_dict
        assert "s" in payload_dict
        assert "page" not in payload_dict
        assert "page_size" not in payload_dict
        assert payload_dict["p"] == 5
        assert payload_dict["s"] == 20
    
    def test_pagination_from_dict_supports_both_formats(self):
        """Pagination.from_dict поддерживает старый и новый формат"""
        # Новый формат (сжатые ключи)
        new_format = {"p": 3, "s": 15}
        pagination_new = Pagination.from_dict(new_format)
        assert pagination_new.page == 3
        assert pagination_new.page_size == 15
        
        # Старый формат (для обратной совместимости)
        old_format = {"page": 4, "page_size": 20}
        pagination_old = Pagination.from_dict(old_format)
        assert pagination_old.page == 4
        assert pagination_old.page_size == 20
    
    def test_check_payment_callback_with_external_id(self):
        """check_payment:<external_id> должен быть под лимитом 64 байта"""
        from app.keyboards import get_payment_keyboard
        external_id = "2d7f3e67-0000-5000-9000-1abc12345678"
        kb = get_payment_keyboard("https://yookassa.ru/checkout/xxx", external_id)
        check_btn = None
        for row in kb.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data and "check_payment" in btn.callback_data:
                    check_btn = btn
                    break
            if check_btn:
                break
        assert check_btn is not None
        cb = check_btn.callback_data
        assert cb.startswith("check_payment:")
        assert external_id in cb
        assert len(cb.encode("utf-8")) <= 64

    def test_real_world_pagination_callback_length(self):
        """Реальный пример пагинации должен быть под лимитом"""
        # Симулируем реальный callback для ADMIN_PAYMENTS с пагинацией и фильтром
        pagination = Pagination(page=10, page_size=10, total=0)
        payload_dict = pagination.to_dict()
        payload_dict["f"] = "suc"  # filter
        payload = json.dumps(payload_dict)
        
        callback = build_cb(ScreenID.ADMIN_PAYMENTS, "page", payload)
        callback_bytes = callback.encode('utf-8')
        
        assert len(callback_bytes) <= 64, (
            f"Реальный callback превышает лимит: {len(callback_bytes)} байт\n"
            f"Callback: {callback}\n"
            f"Payload: {payload}"
        )
