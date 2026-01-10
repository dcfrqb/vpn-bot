"""
Архитектурный тест: запрет на UI в handlers

Проверяет, что handlers не формируют UI напрямую:
- Нет прямых вызовов .answer() или .edit_text() вообще
- Нет импортов ui/renderers, ui/keyboards, ui/screens напрямую
- Все UI должно идти через ScreenManager
- Исключения только через whitelist с комментариями
"""
import ast
import os
from pathlib import Path
import pytest
from tests.ui_guards_whitelist import (
    WHITELIST_FILES,
    ALLOWED_PATTERNS,
    REQUIRED_COMMENT_PATTERN
)


def get_router_files():
    """Получает список всех файлов в routers"""
    routers_dir = Path(__file__).parent.parent / "src" / "app" / "routers"
    if not routers_dir.exists():
        return []
    
    router_files = []
    for file_path in routers_dir.rglob("*.py"):
        # Пропускаем __pycache__ и __init__.py
        if "__pycache__" in str(file_path) or file_path.name == "__init__.py":
            continue
        router_files.append(file_path)
    
    return router_files


def parse_file(file_path: Path):
    """Парсит Python файл в AST"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return ast.parse(content, filename=str(file_path))
    except Exception as e:
        pytest.skip(f"Не удалось распарсить {file_path}: {e}")


def is_whitelisted(file_path: Path, line_number: int, source_lines: list) -> bool:
    """Проверяет, является ли строка whitelisted"""
    file_str = str(file_path)
    
    # Проверяем, есть ли файл в whitelist
    for whitelist_file, patterns in WHITELIST_FILES.items():
        if whitelist_file in file_str:
            # Проверяем, есть ли комментарий с UI EXCEPTION
            if line_number <= len(source_lines):
                line = source_lines[line_number - 1]
                if REQUIRED_COMMENT_PATTERN in line:
                    # Проверяем, соответствует ли паттерну
                    for pattern in patterns:
                        if pattern in line.lower():
                            return True
    
    return False


def find_ui_violations(file_path: Path, tree: ast.AST):
    """Находит нарушения архитектуры UI в коде"""
    violations = []
    
    # Читаем исходный код для проверки комментариев
    source_lines = None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source_lines = f.readlines()
    except:
        pass
    
    # Проверяем импорты
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                # Запрещаем прямой импорт renderers/keyboards/screens
                if any(x in module for x in ["ui.renderers", "ui.keyboards", "ui.screens"]):
                    if "legacy" not in module:  # legacy разрешен
                        violations.append({
                            "type": "forbidden_import",
                            "file": str(file_path),
                            "line": node.lineno,
                            "message": f"Запрещен прямой импорт UI модуля: {module}. Используйте ScreenManager."
                        })
        
        if isinstance(node, ast.ImportFrom):
            if node.module:
                # Запрещаем импорт из ui/renderers, ui/keyboards, ui/screens
                if any(x in node.module for x in ["ui.renderers", "ui.keyboards", "ui.screens"]):
                    if "legacy" not in node.module:  # legacy разрешен
                        violations.append({
                            "type": "forbidden_import",
                            "file": str(file_path),
                            "line": node.lineno,
                            "message": f"Запрещен прямой импорт из UI модуля: {node.module}. Используйте ScreenManager."
                        })
    
    # Проверяем вызовы методов .answer() и .edit_text()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
                
                # Запрещаем ВСЕ вызовы .answer() и .edit_text(), кроме разрешенных
                if method_name in ("answer", "edit_text"):
                    # Проверяем, не является ли это разрешенным паттерном
                    is_allowed = False
                    
                    # Проверяем, не является ли это callback.answer() без parse_mode
                    if method_name == "answer":
                        # Если это callback.answer() без parse_mode - разрешено
                        if isinstance(node.func.value, ast.Attribute):
                            if node.func.value.attr == "callback" or "callback" in str(node.func.value):
                                # Проверяем, нет ли parse_mode
                                has_parse_mode = any(
                                    kw.arg == "parse_mode" for kw in node.keywords
                                )
                                if not has_parse_mode:
                                    is_allowed = True
                    
                    # Проверяем, не является ли это ScreenManager
                    if not is_allowed:
                        # Проверяем контекст вызова
                        if isinstance(node.func.value, ast.Name):
                            var_name = node.func.value.id
                            if any(x in var_name.lower() for x in ["screen_manager", "manager"]):
                                is_allowed = True
                        
                        # Проверяем, не является ли это частью ScreenManager метода
                        parent = getattr(node, 'parent', None)
                        # Простая проверка по имени функции
                        if source_lines and node.lineno <= len(source_lines):
                            line = source_lines[node.lineno - 1]
                            if any(x in line for x in ["show_screen", "handle_action", "navigate", "ScreenManager"]):
                                is_allowed = True
                    
                    # Проверяем whitelist
                    if not is_allowed and source_lines:
                        # Проверяем предыдущие строки на наличие комментария
                        for check_line_num in range(max(1, node.lineno - 3), node.lineno + 1):
                            if check_line_num <= len(source_lines):
                                check_line = source_lines[check_line_num - 1]
                                if is_whitelisted(file_path, check_line_num, source_lines):
                                    is_allowed = True
                                    break
                    
                    if not is_allowed:
                        violations.append({
                            "type": "direct_ui_call",
                            "file": str(file_path),
                            "line": node.lineno,
                            "message": f"Прямой вызов UI метода {method_name}(). Используйте ScreenManager.show_screen() или добавьте комментарий '# UI EXCEPTION: ...'"
                        })
    
    return violations


class TestNoUIInHandlers:
    """Тесты на запрет UI в handlers"""
    
    def test_no_direct_ui_imports(self):
        """Тест: handlers не импортируют ui/renderers или ui/keyboards напрямую"""
        violations = []
        
        for file_path in get_router_files():
            # Пропускаем ui.py и legacy_callbacks.py (они специальные)
            if file_path.name in ("ui.py", "legacy_callbacks.py"):
                continue
            
            tree = parse_file(file_path)
            file_violations = find_ui_violations(file_path, tree)
            violations.extend(file_violations)
        
        if violations:
            violation_messages = "\n".join([
                f"  {v['file']}:{v['line']} - {v['message']}"
                for v in violations
            ])
            pytest.fail(
                f"Найдены нарушения архитектуры UI:\n{violation_messages}\n\n"
                f"Все UI должно идти через ScreenManager.show_screen()"
            )
    
    def test_no_direct_ui_calls(self):
        """Тест: handlers не вызывают .answer()/.edit_text() напрямую"""
        violations = []
        
        for file_path in get_router_files():
            # Пропускаем ui.py и legacy_callbacks.py
            if file_path.name in ("ui.py", "legacy_callbacks.py"):
                continue
            
            tree = parse_file(file_path)
            file_violations = find_ui_violations(file_path, tree)
            # Фильтруем только direct_ui_call
            ui_call_violations = [v for v in file_violations if v["type"] == "direct_ui_call"]
            violations.extend(ui_call_violations)
        
        if violations:
            violation_messages = "\n".join([
                f"  {v['file']}:{v['line']} - {v['message']}"
                for v in violations
            ])
            pytest.fail(
                f"Найдены прямые вызовы UI методов:\n{violation_messages}\n\n"
                f"Используйте ScreenManager.show_screen() или добавьте комментарий '# UI EXCEPTION: ...' для исключений"
            )