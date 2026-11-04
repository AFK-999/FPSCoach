"""Review removes the POV rays but keeps the prior selected-area highlights."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("size", [(420, 430), (260, 310)])
def test_motion_choices_only_change_hand_highlights_no_pov_lines(qapp, theme, fallback, size, monkeypatch):
    from fps_coach.ui.setup_preview import SetupScene
    from fps_coach.ui.styles import set_active_theme
    set_active_theme(theme)
    scene = SetupScene()
    _KEEP.append(scene)
    if fallback:
        scene.hand_art = QPixmap()
    scene.resize(*size)
    scene.set_animation_enabled(False)
    scene.set_answers({"grip": "palm", "motion": ""})
    scene.show()
    qapp.processEvents()
    # Suppressing just the pre-existing highlights should make the scene
    # pixel-identical for every motion; no motion-dependent rays remain.
    with monkeypatch.context() as patch:
        patch.setattr(scene, "_paint_highlights", lambda *_args, **_kwargs: None)
        baseline = scene.grab().toImage()
        for motion in ("wrist", "fingers", "arm", "shoulder_hybrid", "unsure", ""):
            scene.set_answers({"grip": "palm", "motion": motion})
            assert scene.grab().toImage() == baseline
            assert "pov" not in scene.accessibleDescription().lower()
    scene.set_answers({"grip": "palm", "motion": "wrist"})
    assert scene.grab().toImage() != baseline  # the gold/teal highlights are preserved
    assert scene.highlighted_regions()[1] == ("wrist",)
    assert not hasattr(scene, "_pov_animation") and not hasattr(scene, "_paint_pov")
    scene.close()
    set_active_theme("dark")


def test_review_legend_and_accessibility_revert_to_grip_and_motion(qapp):
    from fps_coach.ui.setup_preview import SetupPreview
    preview = SetupPreview()
    _KEEP.append(preview)
    preview.set_answers({"grip": "fingertip", "motion": "shoulder_hybrid"})
    texts = [label.text() for label in preview.findChildren(QLabel)]
    assert "Gold: reported grip areas  |  Teal: reported flick-motion area" in texts
    assert all("pov" not in text.lower() and "wedge" not in text.lower() for text in texts)
    scene = preview.scene
    assert "pov" not in scene.accessibleName().lower()
    assert "pov" not in scene.accessibleDescription().lower()
    assert "pov" not in scene.toolTip().lower()
    assert "forearm is a schematic proxy" in scene.accessibleDescription().lower()
    assert scene.highlighted_regions()[1] == ("forearm",)
    preview.close()


def test_back_edit_still_updates_selected_highlights(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard
    dialog = CalibrationWizard()
    _KEEP.append(dialog)
    dialog.ai_enabled.setChecked(False)
    dialog.cards[1]["claw"].click()
    dialog.cards[2]["wrist"].click()
    for _ in range(5):
        dialog.go_next()
    scene = dialog.setup_preview.scene
    assert scene.highlighted_regions()[1] == ("wrist",)
    for _ in range(4):
        dialog.go_back()
    dialog.cards[2]["arm"].click()
    for _ in range(4):
        dialog.go_next()
    assert dialog.setup_preview.motion_value.text() == "Arm"
    assert scene.highlighted_regions()[1] == ("forearm",)
    assert scene.highlighted_regions()[0] == scene.GRIP_REGIONS["claw"]
    dialog.reject()
