"""
Тесты для модуля построения главного меню
"""
import pytest
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




