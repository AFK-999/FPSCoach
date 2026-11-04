"""Task settings, safe optional animations and review explanatory flow."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fps_coach.backend import _FallbackAnalyzer
from fps_coach.ui.pages_setup import CalibrationPage, DashboardPage
from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


_KEEP = []


class Settings:
    def __init__(self, animation=True, mode="luxurious"):
        self.values = {"ui/animations": animation, "ui/mode": mode}

    def value(self, key, default=None, **kwargs):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_task_gear_hosts_original_two_input_modes_and_rates(qapp):
    page = CalibrationPage(SimpleNamespace(analyzer=_FallbackAnalyzer()))
    _KEEP.append(page)
    page.resize(1200, 850)
    page.show()
    qapp.processEvents()
    assert page.task_settings_btn.accessibleName() == "Calibration input settings"
    assert not page.task_settings_btn.icon().isNull()
    assert page.input_mode_combo.window() is page.window()
    assert page.sample_rate_combo.window() is page.window()
    assert page.input_settings_panel.isHidden()
    assert page.task_settings_btn.property("glyph") == "settings"
    assert [page.input_mode_combo.itemData(i) for i in range(page.input_mode_combo.count())] == ["raw", "desktop"]
    assert [page.sample_rate_combo.itemData(i) for i in range(page.sample_rate_combo.count())] == [125, 250, 500, 1000]
    assert "not detected or compensated" in page.telemetry_note.text()
    page.task_settings_btn.click()
    qapp.processEvents()
    assert page.input_settings_panel.isVisible() and page.task_settings_btn.isChecked()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("raw"))
    assert page.canvas.input_mode == "raw"
    assert "virtual logical pixel" in page.input_note.text()
    assert not page.sample_rate_combo.isEnabled()
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("desktop"))
    assert page.canvas.input_mode == "desktop" and page.sample_rate_combo.isEnabled()
    page._set_curve_controls_enabled(False)
    assert not page.input_mode_combo.isEnabled() and not page.sample_rate_combo.isEnabled()
    assert not page.task_settings_btn.isEnabled()
    assert "locked" in page.input_settings_session_note.text()
    page._set_curve_controls_enabled(True)
    assert page.input_mode_combo.isEnabled() and page.sample_rate_combo.isEnabled()
    page.task_settings_btn.click()
    assert page.input_settings_panel.isHidden() and not page.task_settings_btn.isChecked()
    page.close()


def test_book_click_animates_once_then_opens_and_hide_cancels(qapp):
    page = DashboardPage()
    _KEEP.append(page)
    page.s = Settings(True, "luxurious")
    page.resize(1200, 900)
    page.show()
    qapp.processEvents()
    book = page.wizard_btn
    assert book.text() == "Start" and book.accessibleName()
    assert book.parentWidget() is page.dashboard_title.parentWidget()
    assert not hasattr(page, "guide_card")
    assert page._wizard_dialog is None
    QTest.mouseMove(book, book.rect().center())
    QTest.qWait(40)
    assert book._animation.state() == QAbstractAnimation.State.Stopped
    book.click()
    assert page._wizard_dialog is None and book._waiting
    QTest.qWait(90)
    assert 0 < book._pulse <= 1
    book.click()  # repeated activation during the pulse must not queue a second dialog
    QTest.qWait(250)
    assert page._wizard_dialog is not None and not book._waiting
    page._wizard_dialog.reject()
    qapp.processEvents()
    book.click()
    assert book._waiting
    page.hide()
    QTest.qWait(300)
    assert not book._waiting and page._wizard_dialog is None
    page.close()


@pytest.mark.parametrize("enabled,mode", [(False, "luxurious"), (True, "performance")])
def test_book_opens_immediately_when_animations_disabled(qapp, enabled, mode):
    page = DashboardPage()
    _KEEP.append(page)
    page.s = Settings(enabled, mode)
    page.show()
    qapp.processEvents()
    page.wizard_btn.setFocus()
    QTest.keyClick(page.wizard_btn, Qt.Key.Key_Space)
    assert page._wizard_dialog is not None
    assert page.wizard_btn._animation.state() == QAbstractAnimation.State.Stopped
    page._wizard_dialog.reject()
    page.close()


@pytest.mark.parametrize("enabled,mode,running", [(True, "luxurious", True), (False, "luxurious", False), (True, "performance", False)])
def test_review_flow_obeys_settings_and_never_applies_automatically(qapp, monkeypatch, enabled, mode, running):
    from fps_coach.ui import calibration_wizard

    monkeypatch.setattr(calibration_wizard, "_settings", lambda: Settings(enabled, mode))
    dialog = calibration_wizard.CalibrationWizard({"grip": "claw", "motion": "arm"})
    _KEEP.append(dialog)
    dialog.ai_enabled.setChecked(False)
    dialog.show()
    for _ in range(5):
        dialog.go_next()
    flow = dialog.setup_preview
    assert (flow._flow_animation.state() == QAbstractAnimation.State.Running) is running
    assert (flow.scene._glow_animation.state() == QAbstractAnimation.State.Running) is running
    assert "Optional local AI preview" in flow.flow_steps.text()
    assert "only if confirmed" in flow.flow_note.text()
    assert "game and device settings are not changed" in flow.flow_note.text()
    assert "five-finger" in flow.scene.accessibleName()
    assert not flow.scene.hand_art.isNull()
    if running:
        QTest.qWait(350)
        assert 0 < flow.flow_bar.value() < 100
    if not running:
        assert flow.flow_bar.value() == 100
    assert not dialog.preview_requested and not dialog.start_calibration_requested
    dialog.go_back()
    qapp.processEvents()
    assert flow._flow_animation.state() == QAbstractAnimation.State.Stopped
    assert flow.scene._glow_animation.state() == QAbstractAnimation.State.Stopped
    dialog.reject()


def test_missing_review_art_has_vector_fallback(qapp):
    from fps_coach.ui.setup_preview import SetupPreview
    from PySide6.QtGui import QPixmap

    preview = SetupPreview()
    _KEEP.append(preview)
    preview.scene.hand_art = QPixmap()
    preview.set_answers({"grip": "fingertip", "motion": "wrist"})
    preview.show()
    qapp.processEvents()
    assert not preview.scene.grab().isNull()
    assert preview.grip_value.text() == "Fingertip grip"
    assert preview.motion_value.text() == "Wrist"
    preview.close()
