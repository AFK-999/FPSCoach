"""Functional voice routing without a microphone, network, or provider request."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from fps_coach.ui import calibration_wizard, main_window, pages_coach, pages_setup, widgets

    settings = QSettings(str(tmp_path / "voice.ini"), QSettings.Format.IniFormat)
    for module in (calibration_wizard, main_window, pages_coach, pages_setup, widgets):
        if hasattr(module, "_settings"):
            monkeypatch.setattr(module, "_settings", lambda: settings)
    # Keep the real dispatcher but do not start Vosk or touch a microphone.
    monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
    win = main_window.MainWindow()
    _KEEP.append(win)
    win.resize(1400, 900)
    win.show()
    QTest.qWait(75)
    settings.setValue("ui/voice_commands", True)
    assert win._voice_app_is_active()
    yield win
    settings.setValue("ui/voice_commands", False)
    if win.dashboard_page._wizard_dialog is not None:
        win.dashboard_page._wizard_dialog.reject()
    win.close()
    QTest.qWait(20)


def test_settings_sections_open_and_close_real_controls(window):
    window._on_voice_command("Hey coach, open calibration input settings")
    assert window.stack.currentWidget() is window.calibration_page
    assert window.calibration_page.task_settings_btn.isChecked()
    assert window.calibration_page.input_settings_panel.isVisible()
    window._on_voice_command("close calibration settings")
    assert window.calibration_page.input_settings_panel.isHidden()
    window._on_voice_command("open training settings")
    assert window.stack.currentWidget() is window.training_page
    assert window.training_page.settings_toggle.isChecked()
    window._on_voice_command("open coach settings")
    assert window.stack.currentWidget() is window.chat_page
    assert window.chat_page.voice_settings_toggle.isChecked()
    window._on_voice_command("close coach settings")
    assert not window.chat_page.voice_settings_toggle.isChecked()


def test_global_settings_deep_link_does_not_enable_ingame(window):
    window._on_voice_command("open voice settings")
    QTest.qWait(40)
    assert window.stack.currentWidget() is window.settings_page
    assert window.settings_page.voice_card.isVisible()
    window._on_voice_command("open advanced settings")
    QTest.qWait(40)
    assert window.settings_page.advanced_toggle.isChecked()
    assert window.settings_page.advanced_body.isVisible()
    assert not window.settings_page.ingame_model().enabled
    window._on_voice_command("show sensitivity curve")
    assert window.stack.currentWidget() is window.calibration_page
    assert window.calibration_page.curve_title.text() == "Sensitivity curve"


def test_guide_step_navigation_never_finishes_review(window):
    window._on_voice_command("open calibration guide")
    QTest.qWait(50)
    guide = window.dashboard_page._wizard_dialog
    assert guide is not None and guide.isVisible() and guide.step == 0
    assert window._voice_app_is_active()
    window._on_voice_command("next guide question")
    assert guide.step == 1
    window._on_voice_command("previous guide step")
    assert guide.step == 0
    window._on_voice_command("skip guide question")
    assert guide.step == 1
    guide.step = 4
    guide._refresh()
    window._on_voice_command("next guide question")
    assert guide.isVisible() and guide.step == 5
    window._on_voice_command("next guide question")
    assert guide.isVisible() and guide.step == 5
    assert "on-screen Finish" in guide.error.text()
    guide.reject()


def test_chat_question_sends_explicitly_and_preserves_unsent_draft(window, monkeypatch):
    sent = []
    chat = window.chat_page

    def send():
        sent.append(chat.input.text())
        chat.input.clear()

    monkeypatch.setattr(chat, "_send", send)
    window._on_voice_command("ask coach What is a good warmup?")
    assert sent == ["What is a good warmup?"]
    window._on_voice_command("coach explain my calibration")
    assert sent[-1] == "Explain my latest calibration results and their limitations."
    chat.input.setText("my unsent draft")
    window._on_voice_command("ask coach another question")
    assert len(sent) == 2 and chat.input.text() == "my unsent draft"
    assert "draft preserved" in window._voice_notice.text()
    chat.input.clear()
    class Busy:
        def isRunning(self):
            return True
    monkeypatch.setattr(chat, "_thread", Busy())
    window._on_voice_command("ask coach can you help")
    assert len(sent) == 2 and not chat.input.text()
    assert "busy" in window._voice_notice.text()
    monkeypatch.setattr(chat, "_thread", None)


def test_disabled_or_background_listener_does_not_execute(window, monkeypatch):
    window.s.setValue("ui/voice_commands", False)
    window._on_voice_command("open calibration settings")
    assert window.stack.currentWidget() is window.dashboard_page
    window.s.setValue("ui/voice_commands", True)
    monkeypatch.setattr(window, "_voice_app_is_active", lambda: False)
    window._on_voice_command("open calibration settings")
    assert window.stack.currentWidget() is window.dashboard_page


def test_locked_calibration_settings_are_not_bypassed(window):
    window._select_page("calibration")
    page = window.calibration_page
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("desktop"))
    page.recalibrate_btn.click()
    QTest.qWait(40)
    assert page.canvas._active and not page.task_settings_btn.isEnabled()
    before = page.canvas.geometry()
    window._on_voice_command("open calibration settings")
    QTest.qWait(50)
    assert not page.task_settings_btn.isChecked() and page.input_settings_panel.isHidden()
    assert page.canvas.geometry() == before and page.canvas._active
    assert "locked" in window._voice_notice.text()
    page.reset_btn.click()


def test_dashboard_label_and_gold_highlight(window):
    page = window.dashboard_page
    assert page.serious_check.text() == "Animation flex"
    image = page.wizard_btn.grab().toImage()
    # Sample the upper-left fill away from the book, label and decorative line.
    assert image.pixelColor(15, 8).red() > image.pixelColor(15, 32).red()
    assert page.wizard_btn.accessibleName() == "Start calibration guide"


def test_modal_dialog_blocks_other_voice_actions(window):
    from PySide6.QtWidgets import QDialog

    dialog = QDialog(window)
    dialog.setModal(True)
    dialog.open()
    QTest.qWait(40)
    assert QApplication.activeModalWidget() is dialog
    window._on_voice_command("open training settings")
    assert window.stack.currentWidget() is window.dashboard_page
    assert not window.training_page.settings_toggle.isChecked()
    dialog.reject()


def test_calibration_guide_blocked_during_active_run(window):
    window._select_page("calibration")
    page = window.calibration_page
    page.input_mode_combo.setCurrentIndex(page.input_mode_combo.findData("desktop"))
    page.recalibrate_btn.click()
    assert page.canvas._active
    window._on_voice_command("open calibration guide")
    assert window.stack.currentWidget() is page and page.canvas._active
    assert window.dashboard_page._wizard_dialog is None
    assert "finish or reset" in window._voice_notice.text().lower()
    page.reset_btn.click()


def test_listener_status_and_errors_are_visible_and_stale_events_ignored(window):
    window._on_voice_status("Listening for voice commands...")
    assert "Listening" in window.settings_page.command_status.text()
    window._on_voice_failed("test microphone unavailable")
    assert "test microphone unavailable" in window.settings_page.command_status.text()
    assert "Voice commands unavailable" in window._voice_notice.text()
    window.s.setValue("ui/voice_commands", False)
    window._on_voice_status("late status")
    window._on_voice_failed("late failure")
    assert "test microphone unavailable" in window.settings_page.command_status.text()


def test_continuous_commands_do_not_run_while_one_shot_mic_is_active(window, monkeypatch):
    monkeypatch.setattr(window.chat_page, "_voice_thread", object())
    window._on_voice_command("open calibration settings")
    assert window.stack.currentWidget() is window.dashboard_page
    monkeypatch.setattr(window.chat_page, "_voice_thread", None)


def test_coach_speech_is_not_reinterpreted_as_a_command_except_stop(window, monkeypatch):
    window._speech_watch = (0.0, "reading a sentence")
    window._on_voice_command("open calibration settings")
    assert window.stack.currentWidget() is window.dashboard_page
    called = []
    monkeypatch.setattr(window, "_on_stop_speaking", lambda: called.append(True))
    window._on_voice_command("stop speaking")
    assert called == [True]
    window._speech_watch = None


def test_stop_speaking_works_in_open_guide(window, monkeypatch):
    window._on_voice_command("open calibration guide")
    QTest.qWait(40)
    guide = window.dashboard_page._wizard_dialog
    assert guide is not None and guide.isVisible()
    called = []
    monkeypatch.setattr(window, "_on_stop_speaking", lambda: called.append(True))
    window._speech_watch = (0.0, "reading")
    window._on_voice_command("stop speaking")
    assert called == [True]
    window._speech_watch = None
    guide.reject()
