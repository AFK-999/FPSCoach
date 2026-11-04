"""The review POV is an open, self-report-driven schematic, not a measurement."""
from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_scene():
    from fps_coach.ui.setup_preview import SetupScene
    scene = SetupScene()
    _KEEP.append(scene)
    return scene


@pytest.mark.parametrize("motion", ["wrist", "fingers", "arm", "shoulder_hybrid"])
def test_two_open_rays_and_correct_motion_mapping(qapp, motion):
    scene = make_scene()
    scene.set_answers({"grip": "palm", "motion": motion})
    half, forward = scene.POV_SHAPES[motion]
    assert scene.pov_geometry() == (half, forward)
    p = scene._pov_path()
    assert p.elementCount() == 3  # no third, closing edge
    a, b, c = (p.elementAt(i) for i in range(3))
    assert (a.x, a.y) == (210 - half, forward)
    assert (b.x, b.y) == (210, 315)
    assert (c.x, c.y) == (210 + half, forward)
    assert "not measured game FOV" in scene.accessibleDescription()
    assert "narrower" in scene.accessibleDescription() if motion == "wrist" else "wider and farther forward" in scene.accessibleDescription()


def test_wrist_is_narrower_and_less_forward(qapp):
    scene = make_scene()
    wrist = scene.POV_SHAPES["wrist"]
    def angle(shape):
        return math.atan2(shape[0], scene.POV_ORIGIN.y() - shape[1])
    for motion in ("fingers", "arm", "shoulder_hybrid"):
        wider = scene.POV_SHAPES[motion]
        assert wider[0] > wrist[0] and wider[1] < wrist[1]
        assert angle(wider) > angle(wrist)
        assert 0 < 210 - wider[0] < 210 + wider[0] < 420
        assert 0 < wider[1] < scene.POV_ORIGIN.y()


@pytest.mark.parametrize("motion", ["", "unsure", "unrecognized"])
def test_unknown_clears_wedge_and_never_invents_range(qapp, motion):
    scene = make_scene()
    scene.set_answers({"grip": "palm", "motion": "arm"})
    scene.set_answers({"grip": "palm", "motion": motion})
    assert scene.pov_geometry() is None and scene._pov_path().isEmpty()
    assert "No POV wedge" in scene.accessibleDescription()
    assert scene.highlighted_regions()[0]  # grip remains independent


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("size", [(420, 430), (260, 310)])
@pytest.mark.parametrize("motion", ["wrist", "fingers", "arm", "shoulder_hybrid"])
def test_visible_wedge_in_theme_art_and_compact_sizes(qapp, theme, fallback, size, motion):
    from fps_coach.ui.styles import set_active_theme
    set_active_theme(theme)
    scene = make_scene()
    if fallback:
        scene.hand_art = QPixmap()
    scene.resize(*size)
    scene.set_answers({"grip": "palm", "motion": motion})
    scene.show()
    qapp.processEvents()
    image = scene.grab().toImage()
    # Purple edges remain visible outside the illustration and highlight colors.
    purple = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            purple += color.blue() - color.green() > 15 and color.red() - color.green() > 5
    assert purple > 20
    scene.close()
    set_active_theme("dark")


def test_animation_transitions_interrupt_and_respect_static_mode_and_visibility(qapp):
    scene = make_scene()
    scene.resize(420, 430)
    scene.set_answers({"grip": "palm", "motion": "wrist"})
    scene.set_animation_enabled(True)
    scene.show()
    qapp.processEvents()
    scene.set_answers({"grip": "palm", "motion": "arm"})
    assert scene._pov_animation.state() == QAbstractAnimation.State.Running
    QTest.qWait(230)
    half, forward = scene.pov_geometry()
    assert 80 < half < 162 and 18 < forward < 65
    scene.set_answers({"grip": "palm", "motion": "unsure"})
    assert scene.pov_geometry() is None
    assert scene._pov_animation.state() == QAbstractAnimation.State.Stopped
    scene.set_answers({"grip": "palm", "motion": "wrist"})
    scene.set_answers({"grip": "palm", "motion": "fingers"})
    assert scene._pov_animation.state() == QAbstractAnimation.State.Running
    scene.set_animation_enabled(False)
    assert scene._pov_animation.state() == QAbstractAnimation.State.Stopped
    assert scene.pov_geometry() == scene.POV_SHAPES["fingers"]
    before = scene.grab().toImage()
    QTest.qWait(60)
    assert before == scene.grab().toImage()
    scene.set_animation_enabled(True)
    scene.set_answers({"grip": "palm", "motion": "arm"})
    scene.hide()
    assert scene._pov_animation.state() == QAbstractAnimation.State.Stopped
    assert scene.pov_geometry() == scene.POV_SHAPES["arm"]
    scene.set_answers({"grip": "palm", "motion": "wrist"})
    assert scene.pov_geometry() == scene.POV_SHAPES["wrist"]
    scene.close()


def test_wizard_back_edit_refreshes_review_and_skip_removes_wedge(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard
    dialog = CalibrationWizard()
    _KEEP.append(dialog)
    dialog.ai_enabled.setChecked(False)
    dialog.cards[2]["wrist"].click()
    for _ in range(5):
        dialog.go_next()
    scene = dialog.setup_preview.scene
    assert scene.pov_geometry() == scene.POV_SHAPES["wrist"]
    for _ in range(3):
        dialog.go_back()
    dialog.cards[2]["fingers"].click()
    for _ in range(3):
        dialog.go_next()
    assert scene.pov_geometry() == scene.POV_SHAPES["fingers"]
    assert dialog.setup_preview.motion_value.text() == "Fingers"
    for _ in range(3):
        dialog.go_back()
    dialog.cards[2]["unsure"].click()
    for _ in range(3):
        dialog.go_next()
    assert scene.pov_geometry() is None
    assert dialog.setup_preview.motion_value.text() == "Not sure"
    dialog.reject()
