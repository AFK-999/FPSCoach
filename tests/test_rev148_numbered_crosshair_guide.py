"""Crosshair help uses the same numbered-card structure as the application guide."""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QScrollArea

_KEEP = []


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_crosshair_section_and_subsections_use_guide_card_badges(qapp, monkeypatch, tmp_path, theme):
    from fps_coach.ui import pages_setup
    from fps_coach.ui.styles import build_style, set_active_theme

    settings = QSettings(str(tmp_path / "guide.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme", theme)
    monkeypatch.setattr(pages_setup, "_settings", lambda: settings)
    set_active_theme(theme)
    qapp.setStyleSheet(build_style("luxurious", theme))
    page = pages_setup.GuidePage()
    _KEEP.append(page)
    page.resize(1120, 650)
    page.show()
    qapp.processEvents()

    badges = page.findChildren(QLabel, "stepBadge")
    assert [badge.text() for badge in badges] == [
        "01", "02", "03", "04", "05", "06", "06.1", "06.2", "06.3", "06.4", "06.5"
    ]
    assert all(badge.width() == 52 and badge.height() == 52 for badge in badges)
    names = ["Design and preview", "Overlay and position", "Import and export",
             "Dynamic Magnification", "Check the rules"]
    for i, name in enumerate(names, 1):
        card = badges[5 + i].parentWidget()
        assert card.accessibleName() == f"Crosshair guide section 06.{i}: {name}"
        assert any(label.text() == name for label in card.findChildren(QLabel))
    scroll = page.findChild(QScrollArea)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    qapp.processEvents()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert badges[-1].isVisible()
    assert page.open_crosshair_btn.accessibleName() == "Open Crosshair from the application guide"
    page.close()
    qapp.setStyleSheet("")
