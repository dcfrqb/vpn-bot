"""
Тест: примеры в документации соответствуют правилам

Проверяет, что в docs/ARCHITECTURE.md все примеры используют build_cb()
"""
import re
from pathlib import Path
import pytest


def check_docs_examples():
    """Проверяет примеры в документации"""
    docs_file = Path(__file__).parent.parent / "docs" / "ARCHITECTURE.md"
    
    if not docs_file.exists():
        pytest.skip("ARCHITECTURE.md не найден")
    
    with open(docs_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    violations = []
    
    # Ищем callback_data="..." в примерах кода
    # Игнорируем комментарии и строки с build_cb
    lines = content.split('\n')
    in_code_block = False
    code_block_language = None
    
    for i, line in enumerate(lines, 1):
        # Отслеживаем блоки кода
        if line.strip().startswith('```'):
            if in_code_block:
                in_code_block = False
                code_block_language = None
            else:
                in_code_block = True
                # Определяем язык
                lang_match = re.match(r'```(\w+)', line)
                code_block_language = lang_match.group(1) if lang_match else None
            continue
        
        # Проверяем только Python блоки
        if in_code_block and code_block_language == 'python':
            # Ищем callback_data="..." но не build_cb(...)
            if 'callback_data=' in line and 'build_cb' not in line:
                # Проверяем, не является ли это комментарием или строкой в кавычках
                if not line.strip().startswith('#') and '"' in line:
                    # Проверяем, не является ли это частью строки документации
                    if 'callback_data="' in line or "callback_data='" in line:
                        violations.append({
                            "line": i,
                            "content": line.strip(),
                            "message": "Пример использует callback_data напрямую вместо build_cb()"
                        })
    
    return violations


class TestDocsExamples:
    """Тесты примеров в документации"""
    
    def test_all_examples_use_build_cb(self):
        """Тест: все примеры в документации используют build_cb()"""
        violations = check_docs_examples()
        
        if violations:
            violation_messages = "\n".join([
                f"  Строка {v['line']}: {v['content'][:60]}... - {v['message']}"
                for v in violations
            ])
            pytest.fail(
                f"Найдены примеры без build_cb() в ARCHITECTURE.md:\n{violation_messages}\n\n"
                f"Все примеры должны использовать build_cb() для callback_data"
            )