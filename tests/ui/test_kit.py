"""Screen kit: layout rules of the 12 types (docs/SCREENS.md)."""
from __future__ import annotations

import pytest

from app.bot.callbacks import Nav
from app.bot.views import kit
from app.domain.texts import ui


def _rows(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_header_and_first_untitled_block_stick_together():
    s = ui.result("ok", "Готово", ui.field("Тариф", "Pro"), "Вторая строка", hint="Жми дальше")
    assert s.html() == (
        "✅ <b>Готово</b>\n<blockquote>Тариф: Pro\nВторая строка</blockquote>\n\n<i>Жми дальше</i>"
    )


def test_titled_sections_are_separated_by_one_blank_line():
    s = ui.article("Заголовок", emoji="🚀", sections=[
        ui.block("a", title="Раздел", emoji="💡"),
        ui.plain(ui.code("https://x/<y>")),
        None,
    ])
    assert s.html() == (
        "🚀 <b>Заголовок</b>\n\n💡 <b>Раздел</b>\n<blockquote>a</blockquote>\n\n"
        "<code>https://x/&lt;y&gt;</code>"
    )


def test_status_card_has_no_common_header():
    s = ui.status([ui.block("ID: 1", title="Профиль", emoji="👤")], hint="x")
    assert s.html() == "👤 <b>Профиль</b>\n<blockquote>ID: 1</blockquote>\n\n<i>x</i>"


def test_empty_lines_are_dropped_and_values_escaped():
    s = ui.push("warn", "Внимание", None, "", ui.field("Имя", "<b>x</b>"))
    assert s.html() == "⚠️ <b>Внимание</b>\n<blockquote>Имя: &lt;b&gt;x&lt;/b&gt;</blockquote>"


def test_items_empty_state():
    s = ui.items("Устройства", emoji="📱", lines=[], empty="Пока пусто")
    assert s.html() == "📱 <b>Устройства</b>\n<blockquote>Пока пусто</blockquote>"


def test_toast_limits():
    assert ui.toast("Обновлено") == "Обновлено"
    with pytest.raises(ValueError):
        ui.toast("x" * 201)
    with pytest.raises(ValueError):
        ui.toast("<b>x</b>")


def test_keyboard_order_and_footers():
    v = kit.view(
        ui.result("info", "T"),
        secondary=[kit.action("sec", Nav(s="help"))],
        primary=[kit.action("prim", Nav(s="plans")), None],
        links=[kit.link("url", "https://example.com")],
        options=[kit.pair(kit.action("a", Nav(s="main")), kit.action("b", Nav(s="main")))],
        footer=kit.Footer.back_menu(Nav(s="plans")),
    )
    text, markup = v
    assert text == "ℹ️ <b>T</b>" and v.type == "result"
    assert _rows(markup) == [["prim"], ["a", "b"], ["sec"], ["url"], ["⬅️ Назад", "🏠 В меню"]]
    assert _rows(kit.keyboard(footer=kit.Footer.to_menu())) == [["🏠 В меню"]]
    assert _rows(kit.keyboard(footer=kit.Footer.to_admin())) == [["👑 В админку"]]
    assert _rows(kit.keyboard(footer=kit.Footer.back_admin(Nav(s="plans")))) == [["⬅️ Назад", "👑 В админку"]]
    assert kit.markup_only() is None


def test_wizard_footer_back_row_then_cancel_and_admin():
    f = kit.Footer.wizard(Nav(s="main"), "✖️ Отменить рассылку", back=Nav(s="plans"))
    assert _rows(kit.keyboard(options=[kit.action("a", Nav(s="help"))], footer=f)) == [
        ["a"], ["⬅️ Назад"], ["✖️ Отменить рассылку", "👑 В админку"],
    ]
    first = kit.Footer.wizard(Nav(s="main"), "✖️ Отменить рассылку")
    assert _rows(kit.keyboard(footer=first)) == [["✖️ Отменить рассылку", "👑 В админку"]]


@pytest.mark.parametrize("n,sizes", [(1, [1]), (3, [3]), (4, [4]), (5, [3, 2]), (6, [3, 3]), (7, [4, 3]),
                                     (8, [4, 4]), (9, [3, 3, 3]), (10, [4, 4, 2])])
def test_grid_balances_numbered_buttons(n, sizes):
    rows = kit.grid([kit.action(str(i), Nav(s="main")) for i in range(1, n + 1)])
    assert [len(r) for r in rows] == sizes
    assert [b.text for r in rows for b in r] == [str(i) for i in range(1, n + 1)]
    assert kit.grid([]) == []


def test_items_cards_are_separate_quotes():
    s = ui.items("Устройства (2 из 5)", emoji="📱", cards=[
        ui.card(1, "iPhone", "iOS 18", emoji="📱"), None, ui.card(2, "Pixel")], hint="x")
    assert s.html() == (
        "📱 <b>Устройства (2 из 5)</b>\n<blockquote>📱 <b>1. iPhone</b>\niOS 18</blockquote>\n\n"
        "<blockquote><b>2. Pixel</b></blockquote>\n\n<i>x</i>"
    )


def test_every_type_has_a_title():
    assert set(ui.TYPES) == set(ui.TYPE_TITLES)
