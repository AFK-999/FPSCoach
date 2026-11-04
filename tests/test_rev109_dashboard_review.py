"""Guide launch lives in the Dashboard header and review shows the forearm."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QAbstractAnimation, QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from fps_coach.ui import pages_setup

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setValue("ui/animations", True)
    monkeypatch.setattr(pages_setup, "_settings", lambda: store)
    return store


_KEEP = []


def test_header_button_animates_opens_guide_once_and_does_not_start_capture(qapp, settings):
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    _KEEP.append(page)
    page.resize(1200, 800)
    page.show()
    QTest.qWait(50)
    button = page.wizard_btn
    assert button.text() == "Start" and not button.icon().isNull()
    assert (button.width(), button.height()) == (140, 42)
    assert button.accessibleName() == "Start calibration guide"
    assert button.parentWidget() is page.dashboard_title.parentWidget()
    assert button.geometry().left() > page.dashboard_title.geometry().right()
    assert abs(button.geometry().center().y() - page.dashboard_title.geometry().center().y()) <= 1
    assert not hasattr(page, "guide_card")
    emitted = []
    page.navigate.connect(emitted.append)
    before = button.geometry()
    button.click()
    assert button._animation.state() == QAbstractAnimation.State.Running
    assert page._wizard_dialog is None
    QTest.qWait(90)
    assert button._pulse > 0 and button.geometry() == before
    button.click()
    QTest.qWait(250)
    assert page._wizard_dialog is not None
    assert not button._waiting and emitted == []
    page._wizard_dialog.reject()
    page.close()



@pytest.mark.parametrize("theme", ["dark", "light"])
def test_header_start_retains_height_with_application_stylesheet(qapp, settings, theme):
    from fps_coach.ui.pages_setup import DashboardPage
    from fps_coach.ui.styles import build_style

    old_style = qapp.styleSheet()
    page = DashboardPage()
    _KEEP.append(page)
    try:
        # Applying the stylesheet after creation mirrors theme changes at runtime.
        qapp.setStyleSheet(build_style("balanced", theme))
        page.resize(1200, 800)
        page.show()
        QTest.qWait(40)
        button = page.wizard_btn
        assert (button.width(), button.height()) == (140, 42)
        assert abs(button.geometry().center().y() - page.dashboard_title.geometry().center().y()) <= 1
        assert button.geometry().left() > page.dashboard_title.geometry().right()
    finally:
        page.close()
        qapp.setStyleSheet(old_style)

def test_header_start_is_keyboard_usable_and_missing_logo_has_text_fallback(qapp, settings):
    from fps_coach.ui.pages_setup import DashboardPage

    settings.setValue("ui/animations", False)
    page = DashboardPage()
    _KEEP.append(page)
    page.show()
    QTest.qWait(40)
    button = page.wizard_btn
    button.setIcon(QIcon())
    assert button.text() == "Start" and not button.grab().isNull()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert page._wizard_dialog is not None
    assert button._animation.state() == QAbstractAnimation.State.Stopped
    page._wizard_dialog.reject()
    page.close()


def test_review_forearm_framing_and_arm_wrist_highlights(qapp):
    from fps_coach.ui.setup_preview import SetupScene

    scene = SetupScene()
    _KEEP.append(scene)
    source, dest = scene.ART_SOURCE, scene.ART_DEST
    assert not scene.hand_art.isNull()
    assert source.bottom() == scene.hand_art.height()
    assert dest.bottom() < scene.SCENE_HEIGHT
    assert dest.height() >= 300
    assert dest.width() / dest.height() == pytest.approx(source.width() / source.height(), rel=0.002)
    assert "forearm" in scene.accessibleName().lower()
    paths = scene._region_paths(False)
    assert source.top() < paths["wrist"].boundingRect().center().y()
    assert paths["wrist"].boundingRect().center().y() < paths["forearm"].boundingRect().center().y() < source.bottom()
    scene.set_answers({"grip": "claw", "motion": "wrist"})
    assert scene.highlighted_regions()[1] == ("wrist",)
    scene.set_answers({"grip": "claw", "motion": "arm"})
    assert scene.highlighted_regions()[1] == ("forearm",)
    scene.close()


def test_shoulder_highlight_disclaims_shoulder_not_shown_and_exact_labels(qapp):
    from fps_coach.ui.setup_preview import SetupPreview

    preview = SetupPreview()
    _KEEP.append(preview)
    preview.show()
    preview.set_answers({"grip": "fingertip", "motion": "shoulder_hybrid"})
    assert preview.grip_value.text() == "Fingertip grip"
    assert preview.motion_value.text() == "Shoulder / Hybrid"
    assert not preview.shoulder_note.isHidden()
    assert "Shoulder not shown" in preview.shoulder_note.text()
    preview.set_answers({"grip": "palm", "motion": "wrist"})
    assert preview.shoulder_note.isHidden()
    assert preview.motion_value.text() == "Wrist"
    preview.close()
