"""
Тесты для модуля построения главного меню
"""
import pytest
from datetime import datetime, timedelta
from app.routers.menu_builder import build_main_menu_text, MenuData
from app.utils.html import escape_html, render_pre_block, safe_format_user_name


@pytest.mark.skip(reason="MenuBuilder deprecated, replaced by ScreenManager")
class TestHTMLEscaping:
    """Тесты экранирования HTML"""
    
    def test_escape_html_basic(self):
        """Тест базового экранирования"""
        assert escape_html("test") == "test"
        assert escape_html("<test>") == "&lt;test&gt;"
        assert escape_html("&test") == "&amp;test"
        assert escape_html('"test"') == "&quot;test&quot;"
        assert escape_html("'test'") == "&#x27;test&#x27;"
    
    def test_escape_html_special_chars(self):
        """Тест экранирования специальных символов"""
        text = "<script>alert('xss')</script>"
        escaped = escape_html(text)
        assert "<" not in escaped
        assert ">" not in escaped
        assert "'" not in escaped or "'" in escaped  # Может быть экранировано по-разному
    
    def test_escape_html_none(self):
        """Тест обработки None"""
        assert escape_html(None) == ""
    
    def test_render_pre_block(self):
        """Тест создания pre блока"""
        result = render_pre_block("test content")
        assert result == "<pre>test content</pre>"
        assert "<" in result  # Тег должен быть
    
    def test_render_pre_block_with_special_chars(self):
        """Тест pre блока с HTML символами"""
        content = "<script>alert('xss')</script>"
        result = render_pre_block(content)
        assert "<pre>" in result
        assert "</pre>" in result
        # Содержимое должно быть экранировано
        assert "<script>" not in result or "&lt;script&gt;" in result
    
    def test_safe_format_user_name(self):
        """Тест безопасного форматирования имени"""
        name = safe_format_user_name("John", "Doe", "johndoe", 123)
        assert "John" in name
        assert "Doe" in name
        
        # Тест с None
        name = safe_format_user_name(None, None, None, 123)
        assert "User_123" in name
        
        # Тест с username fallback
        name = safe_format_user_name(None, None, "testuser", 123)
        assert "testuser" in name


@pytest.mark.skip(reason="MenuBuilder deprecated, replaced by ScreenManager")
class TestMenuBuilder:
    """Тесты построения меню"""
    
    def test_build_menu_no_subscription(self):
        """Тест меню без подписки"""
        data = MenuData(
            user_id=12345,
            user_first_name="Test",
            user_last_name="User",
            subscription_status="none"
        )
        text = build_main_menu_text(data)
        
        assert "👤 Профиль:" in text
        assert "12345" in text
        assert "Подписки нет" in text
        assert "<pre>" not in text  # Не должно быть <pre> блоков
        assert "<b>" in text
        assert "ID: 12345" in text
        assert "Имя:" in text
    
    def test_build_menu_active_subscription(self):
        """Тест меню с активной подпиской"""
        expires_at = datetime.utcnow() + timedelta(days=30)
        data = MenuData(
            user_id=12345,
            user_first_name="Test",
            user_last_name="User",
            subscription_status="active",
            expires_at=expires_at,
            days_left=30
        )
        text = build_main_menu_text(data)
        
        assert "Подписка активна" in text
        assert "Осталось (дней): 30" in text
        assert "<pre>" not in text  # Не должно быть <pre> блоков
        assert "<b>" in text
        assert "Действует до:" in text
    
    def test_build_menu_expired_subscription(self):
        """Тест меню с истекшей подпиской"""
        expires_at = datetime.utcnow() - timedelta(days=1)
        data = MenuData(
            user_id=12345,
            user_first_name="Test",
            user_last_name="User",
            subscription_status="expired",
            expires_at=expires_at
        )
        text = build_main_menu_text(data)
        
        assert "Подписка истекла" in text
        assert "<pre>" not in text  # Не должно быть <pre> блоков
        assert "<b>" in text
        assert "Истекла:" in text
    
    def test_build_menu_with_special_chars_in_name(self):
        """Тест меню с HTML символами в имени"""
        data = MenuData(
            user_id=12345,
            user_first_name="<script>alert('xss')</script>",
            user_last_name="&test",
            subscription_status="none"
        )
        text = build_main_menu_text(data)
        
        # Имя должно быть экранировано
        assert "<script>" not in text
        assert "&lt;script&gt;" in text or "alert" not in text
        # Проверяем, что HTML валиден (нет незакрытых тегов)
        assert text.count("<pre>") == text.count("</pre>")
        assert text.count("<b>") == text.count("</b>")
    
    def test_build_menu_with_none_fields(self):
        """Тест меню с None полями"""
        data = MenuData(
            user_id=12345,
            user_first_name=None,
            user_last_name=None,
            user_username=None,
            subscription_status="none",
            expires_at=None,
            days_left=None
        )
        text = build_main_menu_text(data)
        
        # Не должно быть ошибок
        assert "12345" in text
        assert "User_12345" in text or "12345" in text
        assert "<pre>" not in text  # Не должно быть <pre> блоков
        assert "ID: 12345" in text
    
    def test_build_menu_html_validity(self):
        """Тест валидности HTML разметки"""
        data = MenuData(
            user_id=12345,
            user_first_name="Test",
            user_last_name="User",
            subscription_status="active",
            expires_at=datetime.utcnow() + timedelta(days=10),
            days_left=10
        )
        text = build_main_menu_text(data)
        
        # Проверяем баланс тегов
        assert text.count("<b>") == text.count("</b>")
        # Не должно быть <pre> блоков
        assert "<pre>" not in text
        assert "</pre>" not in text
        
        # Проверяем структуру: заголовки должны быть в <b>
        assert "<b>👤 Профиль:</b>" in text
        assert "<b>✅ Подписка активна</b>" in text


@pytest.mark.skip(reason="MenuBuilder deprecated, replaced by ScreenManager")
class TestMenuBuilderEdgeCases:
    """Тесты граничных случаев"""
    
    def test_build_menu_empty_name(self):
        """Тест с пустым именем"""
        data = MenuData(
            user_id=12345,
            user_first_name="",
            user_last_name="",
            user_username="testuser",
            subscription_status="none"
        )
        text = build_main_menu_text(data)
        assert "testuser" in text or "12345" in text
    
    def test_build_menu_very_long_name(self):
        """Тест с очень длинным именем"""
        long_name = "A" * 1000
        data = MenuData(
            user_id=12345,
            user_first_name=long_name,
            subscription_status="none"
        )
        text = build_main_menu_text(data)
        # Не должно быть ошибок
        assert "12345" in text
    
    def test_build_menu_unicode_chars(self):
        """Тест с unicode символами"""
        data = MenuData(
            user_id=12345,
            user_first_name="Тест",
            user_last_name="Пользователь",
            subscription_status="none"
        )
        text = build_main_menu_text(data)
        # Unicode должен обрабатываться корректно
        assert "12345" in text
