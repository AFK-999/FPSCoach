"""Review highlights are vivid, anatomically-shaped illustrations, not marker blobs."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("fallback", [False, True])
def test_individual_finger_segments_follow_long_digit_contours(qapp, fallback):
    from fps_coach.ui.setup_preview import SetupScene
    paths = SetupScene._region_paths(fallback)
    for finger in ("index", "middle", "ring", "little"):
        full = paths[finger]
        upper = paths[finger + "_upper"]
        tip = paths[finger + "_tip"]
        rect = full.boundingRect()
        # Actual digit silhouettes, not circular point markers.
        assert rect.height() > 2.3 * rect.width()
        assert tip.boundingRect().height() > 0.25 * rect.height()
        assert tip.boundingRect().height() < upper.boundingRect().height() < rect.height()
        top_point = QPointF(rect.center().x(), rect.top() + rect.height() * .20)
        lower_point = QPointF(rect.center().x(), rect.top() + rect.height() * .80)
        assert full.contains(top_point) and tip.contains(top_point)
        assert full.contains(lower_point) and not tip.contains(lower_point)
    assert not paths["thumb"].isEmpty() and not paths["thumb_tip"].isEmpty()


@pytest.mark.parametrize("fallback", [False, True])
def test_wrist_and_forearm_have_separate_contoured_regions(qapp, fallback):
    from fps_coach.ui.setup_preview import SetupScene
    regions = SetupScene._region_paths(fallback)
    if fallback:
        wrist_mid, forearm_mid = QPointF(210, 242), QPointF(198, 306)
        outside_wrist, outside_arm = QPointF(170, 241), QPointF(246, 315)
    else:
        wrist_mid, forearm_mid = QPointF(200, 480), QPointF(200, 620)
        outside_wrist, outside_arm = QPointF(125, 480), QPointF(130, 620)
    assert regions["wrist"].contains(wrist_mid) and not regions["wrist"].contains(outside_wrist)
    assert regions["forearm"].contains(forearm_mid) and not regions["forearm"].contains(outside_arm)
    assert not regions["wrist"].contains(forearm_mid)
    assert not regions["forearm"].contains(wrist_mid)
    assert regions["forearm"].elementCount() > 8
    assert regions["palm"].elementCount() > 8


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fallback", [False, True])
def test_strong_gold_and_teal_contrast_on_actual_art_and_fallback(qapp, theme, fallback):
    from fps_coach.ui.setup_preview import SetupScene
    from fps_coach.ui.styles import set_active_theme
    set_active_theme(theme)
    scene = SetupScene()
    _KEEP.append(scene)
    if fallback:
        scene.hand_art = QPixmap()
    scene.resize(420, 430)
    scene.show()
    scene.set_answers({"grip": "palm", "motion": "wrist"})
    image = scene.grab().toImage()
    gold_point, teal_point = ((189, 149), (210, 255)) if fallback else ((191, 150), (216, 294))
    gold, teal = image.pixelColor(*gold_point), image.pixelColor(*teal_point)
    assert gold.red() - gold.blue() >= 60
    assert teal.green() - teal.red() >= 60 and teal.blue() - teal.red() >= 60
    scene.close()
    set_active_theme("dark")


@pytest.mark.parametrize("fallback", [False, True])
def test_overlapping_finger_selection_retains_both_color_cues_and_animation(qapp, fallback):
    from fps_coach.ui.setup_preview import SetupScene
    scene = SetupScene()
    _KEEP.append(scene)
    if fallback:
        scene.hand_art = QPixmap()
    scene.resize(420, 430)
    scene.show()
    scene.set_answers({"grip": "fingertip", "motion": "fingers"})
    scene._set_glow_level(.35)
    low = scene.grab().toImage()
    scene._set_glow_level(1.0)
    bright = scene.grab().toImage()
    assert low != bright
    # Both colors remain independently distinguishable near the fingers.
    gold = teal = 0
    for y in range(120, 230):
        for x in range(170, 270):
            c = bright.pixelColor(x, y)
            gold += c.red() - c.blue() > 45 and c.red() - c.green() > 10
            teal += c.green() - c.red() > 45 and c.blue() - c.red() > 45
    assert gold > 30 and teal > 100
    scene.close()


def test_palm_masks_are_merged_without_highlight_seams(qapp):
    from fps_coach.ui.setup_preview import SetupScene
    names = SetupScene.GRIP_REGIONS["palm"]
    for fallback in (False, True):
        combined = SetupScene._combined_region_path(fallback, names, False)
        regions = SetupScene._region_paths(fallback)
        for name in names:
            # All component interiors remain part of the merged silhouette.
            assert not combined.intersected(regions[name]).isEmpty()
        assert SetupScene._combined_region_path(fallback, names, False) is combined
