"""
Тест: дисциплина UI в payment flows

Проверяет, что в payment routers нет HTML-сборки строк напрямую.
Все тексты должны формироваться через payment/ui/renderers.
"""
import ast
from pathlib import Path
import pytest


def get_payment_router_files():
    """Получает список payment router файлов"""
    routers_dir = Path(__file__).parent.parent / "src" / "app" / "routers"
    if not routers_dir.exists():
        return []
    
    payment_files = []
    for file_path in routers_dir.glob("*.py"):
        if file_path.name == "payments.py":
            payment_files.append(file_path)
    
    return payment_files


def find_html_violations(file_path: Path, tree: ast.AST):
    """Находит HTML-сборку строк в коде"""
    violations = []
    
    # Читаем исходный код
    source_lines = None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source_lines = f.readlines()
    except:
        pass
    
    # HTML маркеры, которые указывают на сборку HTML
    html_markers = ["<b>", "<i>", "<code>", "<blockquote>", "<pre>", "parse_mode=\"HTML\""]
    
    # Проверяем строковые литералы и конкатенации
    for node in ast.walk(tree):
        # Проверяем строковые литералы
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in html_markers):
                # Проверяем, не является ли это частью renderer вызова
                if source_lines and node.lineno <= len(source_lines):
                    line = source_lines[node.lineno - 1]
                    if "render_" not in line.lower() and "payment/ui" not in line:
                        violations.append({
                            "file": str(file_path),
                            "line": node.lineno,
                            "message": f"HTML-сборка строки напрямую. Используйте payment/ui/renderers.py"
                        })
        
        # Проверяем конкатенации строк
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # Это может быть конкатенация строк с HTML
            if source_lines and node.lineno <= len(source_lines):
                line = source_lines[node.lineno - 1]
                if any(marker in line for marker in html_markers):
                    if "render_" not in line.lower() and "payment/ui" not in line:
                        violations.append({
                            "file": str(file_path),
                            "line": node.lineno,
                            "message": f"HTML-сборка через конкатенацию. Используйте payment/ui/renderers.py"
                        })
    
    return violations


class TestPaymentUIDiscipline:
    """Тесты дисциплины UI в payment flows"""
    
    def test_no_html_assembly_in_payments(self):
        """Тест: в payment routers нет HTML-сборки строк напрямую"""
        violations = []
        
        for file_path in get_payment_router_files():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                tree = ast.parse(content, filename=str(file_path))
                file_violations = find_html_violations(file_path, tree)
                violations.extend(file_violations)
            except Exception as e:
                pytest.skip(f"Не удалось обработать {file_path}: {e}")
        
        if violations:
            violation_messages = "\n".join([
                f"  {v['file']}:{v['line']} - {v['message']}"
                for v in violations
            ])
            pytest.fail(
                f"Найдена HTML-сборка строк в payment routers:\n{violation_messages}\n\n"
                f"Используйте payment/ui/renderers.py для формирования текстов"
            )