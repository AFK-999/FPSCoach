"""The magnifier disclosure matches Coach tiles without starting capture."""
from __future__ import annotations

import pytest
from fps_coach.ui import pages_practice
from fps_coach.ui.guide_book import COACH_BUTTON_LABEL_SIZE, COACH_BUTTON_SIZE
from fps_coach.ui.pages_coach import PresetTile
from fps_coach.ui.styles import build_style
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_practice, "_settings", lambda: settings)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_tile_matches_coach_and_full_label_fits(app, monkeypatch, theme):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: False)
    app.setStyleSheet(build_style("luxurious", theme))
    page = pages_practice.CrosshairPage()
    coach = PresetTile("precision", "Precision for\nMicro Aim")
    page.resize(1100, 900)
    page.show()
    coach.show()
    app.processEvents()
    try:
        tile = page.mag_disclosure
        assert type(tile) is type(coach) is PresetTile
        assert tile.key == "scope" and tile.text().replace("\n", " ") == "Dynamic Magnification"
        assert (tile.width(), tile.height()) == (coach.width(), coach.height()) == COACH_BUTTON_SIZE
        font = tile.font()
        font.setBold(True)
        font.setPointSizeF(COACH_BUTTON_LABEL_SIZE)
        metrics = QFontMetricsF(font)
        assert max(metrics.horizontalAdvance(line) for line in tile.text().splitlines()) <= tile.width() - 16
        assert 2 * metrics.lineSpacing() <= 30
        assert tile.accessibleName() == "Dynamic Magnification settings"
        assert page.mag_panel.isHidden() and not page.magnification_toggle.isChecked()
    finally:
        page.close()
        coach.close()
        app.setStyleSheet("")


def test_keyboard_disclosure_does_not_change_effect_or_key_monitor(app, monkeypatch):
    monkeypatch.setattr(pages_practice, "windows_available", lambda: True)
    page = pages_practice.CrosshairPage()
    page.resize(1100, 900)
    page.show()
    page.activation.setChecked(True)
    page.mag_binding.setCurrentIndex(page.mag_binding.findData(0x05))
    page.mag_hotkey_arm.setChecked(True)
    emissions = []
    page.magnification_toggled.connect(emissions.append)
    page.mag_disclosure.setFocus()
    app.processEvents()
    try:
        QTest.keyClick(page.mag_disclosure, Qt.Key.Key_Space)
        assert page.mag_disclosure.isChecked() and not page.mag_panel.isHidden()
        assert "Expanded" in page.mag_disclosure.accessibleDescription()
        assert page.mag_hotkey_arm.isChecked() and page.mag_binding.currentData() == 0x05
        assert not page.magnification_toggle.isChecked() and not emissions
        QTest.keyClick(page.mag_disclosure, Qt.Key.Key_Space)
        assert not page.mag_disclosure.isChecked() and page.mag_panel.isHidden()
        assert "Collapsed" in page.mag_disclosure.accessibleDescription()
        assert page.mag_hotkey_arm.isChecked() and not emissions
    finally:
        page.mag_hotkey_arm.setChecked(False)
        page.close()
