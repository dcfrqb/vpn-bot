"""
Тесты для единого формата callback_data
"""
import pytest
from app.ui.callbacks import (
    build_cb,
    parse_cb,
    is_ui_callback,
    validate_callback_length,
    CallbackParseError,
    MAX_CALLBACK_LENGTH,
    MAX_PAYLOAD_LENGTH
)
from app.ui.screens import ScreenID


class TestBuildCb:
    """Тесты для build_cb"""
    
    def test_build_basic_callback(self):
        """Тест создания базового callback"""
        result = build_cb(ScreenID.MAIN_MENU, "open")
        assert result == "ui:main_menu:open:-"
        assert is_ui_callback(result)
    
    def test_build_with_payload(self):
        """Тест создания callback с payload"""
        result = build_cb(ScreenID.ADMIN_USERS, "page", "2")
        assert result == "ui:admin_users:page:2"
        assert is_ui_callback(result)
    
    def test_build_with_complex_payload(self):
        """Тест создания callback со сложным payload"""
        result = build_cb(ScreenID.ADMIN_PAYMENTS, "page", "2&all")
        assert result == "ui:admin_payments:page:2&all"
        assert is_ui_callback(result)
    
    def test_payload_too_long(self):
        """Тест: payload слишком длинный"""
        long_payload = "x" * (MAX_PAYLOAD_LENGTH + 1)
        with pytest.raises(ValueError, match="Payload слишком длинный"):
            build_cb(ScreenID.MAIN_MENU, "open", long_payload)
    
    def test_payload_contains_colon(self):
        """Тест: payload содержит недопустимый символ"""
        with pytest.raises(ValueError, match="Payload не может содержать"):
            build_cb(ScreenID.MAIN_MENU, "open", "test:value")
    
    def test_callback_length_limit(self):
        """Тест: общая длина callback не превышает лимит"""
        # Создаем максимально длинный payload
        max_payload = "x" * MAX_PAYLOAD_LENGTH
        result = build_cb(ScreenID.MAIN_MENU, "open", max_payload)
        assert len(result) <= MAX_CALLBACK_LENGTH
        assert validate_callback_length(result)


class TestParseCb:
    """Тесты для parse_cb"""
    
    def test_parse_basic_callback(self):
        """Тест парсинга базового callback"""
        screen_id, action, payload = parse_cb("ui:main_menu:open:-")
        assert screen_id == ScreenID.MAIN_MENU
        assert action == "open"
        assert payload == "-"
    
    def test_parse_with_payload(self):
        """Тест парсинга callback с payload"""
        screen_id, action, payload = parse_cb("ui:admin_users:page:2")
        assert screen_id == ScreenID.ADMIN_USERS
        assert action == "page"
        assert payload == "2"
    
    def test_parse_with_complex_payload(self):
        """Тест парсинга callback со сложным payload"""
        screen_id, action, payload = parse_cb("ui:admin_payments:page:2&all")
        assert screen_id == ScreenID.ADMIN_PAYMENTS
        assert action == "page"
        assert payload == "2&all"
    
    def test_parse_invalid_prefix(self):
        """Тест: неверный префикс"""
        result = parse_cb("invalid:main_menu:open:-")
        assert result is None
    
    def test_parse_invalid_screen_id(self):
        """Тест: неверный ScreenID"""
        with pytest.raises(CallbackParseError, match="Неизвестный ScreenID"):
            parse_cb("ui:invalid_screen:open:-")
    
    def test_parse_empty_string(self):
        """Тест: пустая строка"""
        result = parse_cb("")
        assert result is None
    
    def test_parse_roundtrip(self):
        """Тест: roundtrip build -> parse"""
        original_screen = ScreenID.CONNECT
        original_action = "open"
        original_payload = "test123"
        
        built = build_cb(original_screen, original_action, original_payload)
        parsed = parse_cb(built)
        
        assert parsed is not None
        screen_id, action, payload = parsed
        assert screen_id == original_screen
        assert action == original_action
        assert payload == original_payload


class TestIsUiCallback:
    """Тесты для is_ui_callback"""
    
    def test_is_ui_callback_true(self):
        """Тест: это UI callback"""
        assert is_ui_callback("ui:main_menu:open:-") is True
        assert is_ui_callback("ui:connect:open:-") is True
    
    def test_is_ui_callback_false(self):
        """Тест: это не UI callback"""
        assert is_ui_callback("back_to_main") is False
        assert is_ui_callback("admin_panel") is False
        assert is_ui_callback("") is False
        assert is_ui_callback(None) is False


class TestValidateCallbackLength:
    """Тесты для validate_callback_length"""
    
    def test_valid_length(self):
        """Тест: валидная длина"""
        callback = "ui:main_menu:open:-"
        assert validate_callback_length(callback) is True
    
    def test_too_long(self):
        """Тест: слишком длинный callback"""
        long_callback = "x" * (MAX_CALLBACK_LENGTH + 1)
        assert validate_callback_length(long_callback) is False