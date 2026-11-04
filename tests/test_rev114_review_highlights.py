"""Review highlights are schematic, answer-specific and respect motion settings."""
import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


_KEEP = []


@pytest.mark.parametrize("grip,expected", [
    ("palm", {"thumb", "index", "middle", "ring", "little", "palm"}),
    ("claw", {"thumb_tip", "index_upper", "middle_upper", "ring_upper", "little_upper", "palm_heel"}),
    ("fingertip", {"thumb_tip", "index_tip", "middle_tip", "ring_tip", "little_tip"}),
    ("unsure", set()), (None, set()),
])
def test_grip_areas_match_self_report(qapp, grip, expected):
    from fps_coach.ui.setup_preview import SetupScene
    scene = SetupScene()
    _KEEP.append(scene)
    scene.set_answers({"grip": grip, "motion": None})
    gold, teal = scene.highlighted_regions()
    assert set(gold) == expected and not teal
    assert "Reported grip:" in scene.accessibleDescription()
    for fallback in (False, True):
        paths = scene._region_paths(fallback)
        assert all(not paths[name].isEmpty() for name in gold)


@pytest.mark.parametrize("motion,expected", [
    ("fingers", {"thumb", "index", "middle", "ring", "little"}),
    ("wrist", {"wrist"}), ("arm", {"forearm"}), ("shoulder_hybrid", {"forearm"}),
    (None, set()), ("unsure", set()),
])
def test_motion_areas_and_accessibility(qapp, motion, expected):
    from fps_coach.ui.setup_preview import SetupScene
    scene = SetupScene()
    _KEEP.append(scene)
    scene.set_answers({"grip": None, "motion": motion})
    gold, teal = scene.highlighted_regions()
    assert not gold and set(teal) == expected
    if motion == "shoulder_hybrid":
        assert "Shoulder and elbow are not shown" in scene.accessibleDescription()
    for fallback in (False, True):
        assert all(not scene._region_paths(fallback)[name].isEmpty() for name in teal)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_rendered_highlights_change_and_overlap_in_both_art_modes(qapp, fallback, theme):
    from fps_coach.ui.setup_preview import SetupScene
    from fps_coach.ui.styles import set_active_theme
    set_active_theme(theme)
    scene = SetupScene()
    _KEEP.append(scene)
    if fallback:
        scene.hand_art = QPixmap()
    scene.resize(420, 430)
    scene.show()
    scene.set_answers({"grip": None, "motion": None})
    plain = scene.grab().toImage()
    scene.set_answers({"grip": "fingertip", "motion": None})
    grip = scene.grab().toImage()
    scene.set_answers({"grip": "fingertip", "motion": "fingers"})
    both = scene.grab().toImage()
    assert plain != grip and grip != both
    # Teal occupies narrower interiors so the gold fingertip edges remain visible.
    paths = scene._region_paths(fallback)
    assert scene._narrow(paths["index"]).boundingRect().width() < paths["index"].boundingRect().width()
    scene.close()
    set_active_theme("dark")


def test_glow_animates_only_when_visible_enabled_and_selected(qapp):
    from fps_coach.ui.setup_preview import SetupPreview
    preview = SetupPreview()
    _KEEP.append(preview)
    preview.set_answers({"grip": "palm", "motion": "wrist"})
    preview.start_flow(True)
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Stopped
    preview.show()
    qapp.processEvents()
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Running
    initial = preview.scene._glow_level
    QTest.qWait(300)
    assert preview.scene._glow_level != initial
    preview.start_flow(False)
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Stopped
    assert preview.scene._glow_level == 0.65
    # Disabled animation still renders the selected static highlights.
    static = preview.scene.grab().toImage()
    preview.set_answers({"grip": None, "motion": None})
    assert static != preview.scene.grab().toImage()
    preview.start_flow(True)
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Stopped
    preview.set_answers({"grip": "claw", "motion": "arm"})
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Running
    preview.hide()
    qapp.processEvents()
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Stopped
    preview.show()
    qapp.processEvents()
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Running
    preview.close()
    assert preview.scene._glow_animation.state() == QAbstractAnimation.State.Stopped
