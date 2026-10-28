"""Combo boxes show a chevron: the field must look like it opens a list.

The stylesheet restyles ``QComboBox::drop-down`` to sit flush on the right, and that also
removes Qt's own arrow on the styles this app runs on — so the Model field (editable, so it
looked exactly like a text box) gave no hint that a list was behind it. ``widgets.ComboField``
paints a chevron in the drop-down strip instead.

These tests check the geometry, that the arrow is actually painted and only in that strip,
that it follows the theme, and that no page quietly goes back to a bare QComboBox.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach.ui.styles import build_style, set_active_theme  # noqa: E402
from fps_coach.ui.widgets import ComboField  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QComboBox  # noqa: E402

UI_DIR = Path(__file__).resolve().parents[1] / "python" / "fps_coach" / "ui"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def combo(qapp):
    """A styled, shown combo box wide enough to have a right-hand strip."""
    set_active_theme("dark")
    field = ComboField()
    field.setStyleSheet(build_style("luxurious", "dark"))
    field.addItems(["gemma3:4b", "qwen3:1.7b"])
    field.resize(240, 32)
    field.show()
    qapp.processEvents()
    yield field
    field.close()
    field.deleteLater()
    qapp.processEvents()


def _diff_pixels(left, right) -> list[tuple[int, int]]:
    """Coordinates whose pixels differ between two grabs."""
    assert left.size() == right.size()
    return [
        (x, y)
        for y in range(left.height())
        for x in range(left.width())
        if left.pixel(x, y) != right.pixel(x, y)
    ]


# ---------------------------------------------------------------------------
# It is a combo box, and it is in the places that need one
# ---------------------------------------------------------------------------
def test_the_field_is_a_combo_box(qapp) -> None:
    assert issubclass(ComboField, QComboBox)


def test_the_model_field_uses_it(qapp, tmp_path, monkeypatch) -> None:
    from fps_coach.ui import pages_coach
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store.clear()
    monkeypatch.setattr(pages_coach, "_settings", lambda: store)
    page = pages_coach.SettingsPage()
    try:
        assert isinstance(page.model, ComboField)
        assert page.model.isEditable() is True  # typing over it is still allowed
        assert isinstance(page.provider, ComboField)
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("module", ["pages_coach.py", "pages_setup.py", "pages_practice.py"])
def test_no_page_builds_a_bare_combo_box(module) -> None:
    """A plain QComboBox() would silently lose the chevron again."""
    tree = ast.parse((UI_DIR / module).read_text())
    bare = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "QComboBox"
    ]
    assert bare == []


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def test_the_arrow_sits_in_the_right_hand_strip(combo) -> None:
    rect = combo.arrow_rect()
    assert isinstance(rect, QRectF)
    assert rect.right() <= combo.width()
    assert rect.left() >= combo.width() - ComboField.ARROW_WIDTH
    assert rect.width() == pytest.approx(ComboField.ARROW_SIZE)


def test_the_arrow_is_centred_vertically(combo) -> None:
    rect = combo.arrow_rect()
    assert rect.center().y() == pytest.approx(combo.height() / 2, abs=1.0)


def test_a_narrower_field_keeps_the_arrow_inside(combo) -> None:
    combo.resize(120, 32)
    rect = combo.arrow_rect()
    assert rect.left() >= combo.width() - ComboField.ARROW_WIDTH
    assert rect.right() <= combo.width()


# ---------------------------------------------------------------------------
# It is really painted, and only there
# ---------------------------------------------------------------------------
def test_the_chevron_is_painted_in_the_arrow_strip(combo, monkeypatch, qapp) -> None:
    with_arrow = combo.grab().toImage()

    monkeypatch.setattr(ComboField, "_paint_arrow", lambda self: None)
    combo.update()
    qapp.processEvents()
    without_arrow = combo.grab().toImage()

    changed = _diff_pixels(with_arrow, without_arrow)
    assert changed, "the chevron was not drawn"
    strip_start = combo.width() - ComboField.ARROW_WIDTH
    assert all(x >= strip_start for x, _y in changed), "the chevron spilled over the text"


def test_the_chevron_leaves_the_text_area_alone(combo, monkeypatch, qapp) -> None:
    before = combo.grab().toImage()
    monkeypatch.setattr(ComboField, "_paint_arrow", lambda self: None)
    combo.update()
    qapp.processEvents()
    after = combo.grab().toImage()
    strip_start = combo.width() - ComboField.ARROW_WIDTH
    untouched_left = [p for p in _diff_pixels(before, after) if p[0] < strip_start]
    assert untouched_left == []


# ---------------------------------------------------------------------------
# It follows the theme and the enabled state
# ---------------------------------------------------------------------------
def test_the_arrow_colour_comes_from_the_active_theme(combo, monkeypatch) -> None:
    from fps_coach.ui import widgets

    set_active_theme("dark")
    dark = combo.arrow_colour()
    set_active_theme("light")
    light = combo.arrow_colour()
    assert dark != light
    assert light == widgets.active_palette()["text"]


def test_a_disabled_field_uses_the_muted_colour(combo) -> None:
    from fps_coach.ui import widgets

    set_active_theme("dark")
    palette = widgets.active_palette()
    combo.setEnabled(False)
    assert combo.arrow_colour() == palette["text3"]
    combo.setEnabled(True)
    assert combo.arrow_colour() == palette["text"]


# ---------------------------------------------------------------------------
# The stylesheet must not draw a second arrow
# ---------------------------------------------------------------------------
def test_the_stylesheet_suppresses_the_native_arrow() -> None:
    style = build_style("luxurious", "dark")
    assert "QComboBox::down-arrow" in style
    assert "image: none" in style
    # The strip the chevron is painted into is still reserved by the stylesheet.
    assert "QComboBox::drop-down" in style
    assert "width: 22px" in style


def test_the_drawn_arrow_and_the_reserved_strip_agree() -> None:
    """ARROW_WIDTH is the width the stylesheet reserves, so the two cannot drift apart."""
    style = build_style("luxurious", "light")
    assert f"width: {ComboField.ARROW_WIDTH}px" in style
