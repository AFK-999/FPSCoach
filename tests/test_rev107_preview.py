"""Dashboard tile matching and end-to-end guide preview feedback."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from fps_coach.ui import calibration_wizard, main_window, pages_coach, pages_setup, widgets

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setValue("ui/animations", False)
    store.setValue("ui/tts_enabled", False)
    for module in (calibration_wizard, main_window, pages_coach, pages_setup, widgets):
        monkeypatch.setattr(module, "_settings", lambda: store)
    return store


_KEEP = []


def _wait(qapp, predicate):
    for _ in range(100):
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(15)
    assert predicate(), "timed out waiting for guide analysis"


@pytest.mark.parametrize("width", [900, 1200, 1600])
def test_start_button_stays_beside_title_after_resize_and_rebuild(qapp, settings, width):
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    _KEEP.append(page)
    page.resize(width, 900)
    page.show()
    _wait(qapp, lambda: page._tiles[0].isVisible())
    for next_width in (width, width + 180, width - 180):
        page.resize(next_width, 900)
        QTest.qWait(40)
        book, title = page.wizard_btn, page.dashboard_title
        assert book.parentWidget() is title.parentWidget()
        assert 0 < book.geometry().left() - title.geometry().right() < 25
        assert abs(book.geometry().center().y() - title.geometry().center().y()) < 4
        assert book.text() == "Start" and not book.icon().isNull()
        assert not hasattr(page, "guide_card")
    page._hide("capture")
    QTest.qWait(30)
    page.set_serious_mode(True)
    QTest.qWait(30)
    assert page.wizard_btn.parentWidget() is page.dashboard_title.parentWidget()
    page.close()


def _profile():
    from fps_coach.curve_io import CurvePoint, CurveProfile

    points = [CurvePoint(0, 0.8), CurvePoint(50, 1.0), CurvePoint(100, 1.2)]
    return CurveProfile("Measured", deepcopy(points), deepcopy(points))


class FakeSpeaker:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, text):
        self.enqueued.append(text)

    def stop(self):
        pass

    def wait(self, *args):
        pass


def _open_with_analyzed_proposal(window, qapp):
    dashboard = window.dashboard_page
    dashboard._open_calibration_wizard()
    dialog = dashboard._wizard_dialog
    dialog.cards[1]["claw"].click()
    dialog._ai_timer.stop()
    dialog._dispatch_ai()
    _wait(qapp, lambda: dialog.ai_proposal is not None)
    for _ in range(5):
        dialog.go_next()
    assert dialog.preview_btn.isEnabled()
    return dialog


@pytest.mark.parametrize("speech_on,has_curve", [(True, True), (False, True), (True, False), (False, False)])
def test_actual_review_button_displays_existing_local_guidance_and_respects_speech(
    qapp, settings, monkeypatch, speech_on, has_curve
):
    from fps_coach.ui import calibration_wizard, pages_coach
    from fps_coach.ui.main_window import MainWindow

    calls = []

    def analyze(provider, url, model, answers, summary):
        calls.append(deepcopy(answers))
        return {"factor": 1.05, "guidance": "Compare small changes carefully.", "limited": False}

    monkeypatch.setattr(calibration_wizard, "analyze_guide", analyze)
    monkeypatch.setattr(pages_coach, "_ChatRequest", lambda *a, **k: pytest.fail("unexpected new AI request"))
    window = MainWindow()
    _KEEP.append(window)
    window.resize(1400, 950)
    window.show()
    qapp.processEvents()
    speaker = FakeSpeaker() if speech_on else None
    if speech_on:
        window._tts_service = speaker
        window.chat_page.read_aloud.setChecked(True)
    baseline = _profile() if has_curve else None
    window.calibration_page._profile = deepcopy(baseline)
    original = deepcopy(window.calibration_page._profile)
    sent = []
    window.chat_page.curve_adjusted.connect(lambda *args: sent.append(args))
    dialog = _open_with_analyzed_proposal(window, qapp)
    assert len(calls) == 1
    dialog.preview_btn.click()
    qapp.processEvents()
    chat = window.chat_page
    assert window.stack.currentWidget() is chat
    who, response = next(item for item in reversed(chat._transcript_items) if item[0] == "Coach")
    assert who == "Coach" and "Compare small changes carefully." in response
    assert "1.050" in response and "not a guarantee" in response
    assert len(calls) == 1 and chat._thread is None
    assert window.calibration_page._profile == original and not sent
    if has_curve:
        assert chat._adjusted_profile is not None and not chat.apply_to_rec.isHidden()
        assert "Send to calibration" in response
        assert chat._preview_base_profile == original
    else:
        assert chat._adjusted_profile is None and chat.apply_to_rec.isHidden()
        assert "Run calibration or import a curve" in response
    if speech_on:
        assert speaker.enqueued == [response]
    else:
        assert window._tts_service is None
        assert "Turn on Read replies aloud" in chat.speech_status.text()
        assert "sliders icon" in chat._transcript_items[-1][1]
        assert not chat.read_aloud.isChecked()
    window.close()


def test_invalid_or_stale_guide_result_cannot_send_old_preview(qapp, settings):
    from fps_coach.ui.pages_coach import AIChatPage

    chat = AIChatPage()
    _KEEP.append(chat)
    assert chat.preview_guide_proposal({"factor": 1.05, "guidance": "Okay"}, _profile())
    assert not chat.apply_to_rec.isHidden()
    signals = []
    chat.curve_adjusted.connect(lambda *args: signals.append(args))
    assert not chat.preview_guide_proposal({"factor": 9.0}, _profile())
    assert chat.apply_to_rec.isHidden() and chat._adjusted_profile is None
    chat._send_adjusted_to_calibration()
    assert not signals
    assert any("invalid guide proposal" in text.lower() for _, text in chat._transcript_items)
    chat.close()


def test_unavailable_local_ai_explains_retry_and_never_fabricates_preview(qapp, settings, monkeypatch):
    from fps_coach.guide_ai import GuideAIError
    from fps_coach.ui import calibration_wizard

    attempts = []

    def analyze(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise GuideAIError("Selected local model is not installed.")
        return {"factor": 1.0, "guidance": "Collect more data.", "limited": False}

    monkeypatch.setattr(calibration_wizard, "analyze_guide", analyze)
    dialog = calibration_wizard.CalibrationWizard()
    _KEEP.append(dialog)
    dialog.show()
    dialog.cards[1]["claw"].click()
    dialog._ai_timer.stop()
    dialog._dispatch_ai()
    _wait(qapp, lambda: "unavailable" in dialog.ai_status.text().lower())
    for _ in range(5):
        dialog.go_next()
    assert not dialog.preview_btn.isEnabled() and dialog.ai_proposal is None
    assert "off and on to retry" in dialog.ai_status.text()
    dialog.preview_btn.click()
    assert not dialog.preview_requested and dialog.isVisible()
    dialog.ai_enabled.setChecked(False)
    dialog.ai_enabled.setChecked(True)
    dialog._ai_timer.stop()
    dialog._dispatch_ai()
    _wait(qapp, lambda: dialog.ai_proposal is not None)
    assert len(attempts) == 2 and dialog.preview_btn.isEnabled()
    dialog.reject()


def test_guide_guidance_is_displayed_as_untrusted_text_not_a_curve_directive(qapp, settings):
    from fps_coach.ui.pages_coach import AIChatPage

    chat = AIChatPage()
    _KEEP.append(chat)
    proposal = {"factor": 1.02, "guidance": "<b>text</b> [ADJUST: sensitivity +40%]"}
    assert chat.preview_guide_proposal(proposal, _profile())
    assert chat._adjusted_profile.horizontal[1].multiplier == pytest.approx(1.02)
    assert "<b>text</b>" in chat._transcript_items[-2][1]
    assert chat._messages == []
    chat.close()
