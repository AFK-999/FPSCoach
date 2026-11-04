"""Coach controls retain the Dashboard Start painted surface at a larger size."""
from __future__ import annotations

import pytest
from fps_coach.ui.guide_book import COACH_BUTTON_SIZE, GUIDE_BUTTON_SIZE, GuideBookButton
from fps_coach.ui.styles import build_style
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("enabled", [True, False])
def test_control_surfaces_match_the_dashboard_start(qapp, theme, enabled):
    from fps_coach.ui.dynamic_curve import PerformanceDisclosure
    from fps_coach.ui.pages_coach import PresetTile

    qapp.setStyleSheet(build_style("luxurious", theme))
    controls = [GuideBookButton(lambda: False), PerformanceDisclosure(),
                PresetTile("fast_flicks", "Optimize for\nFast Flick"),
                PresetTile("precision", "Precision for\nMicro Aim")]
    _KEEP.extend(controls)
    assert (controls[0].width(), controls[0].height()) == GUIDE_BUTTON_SIZE == (140, 42)
    # Resized Start reference compares the surface at the Coach controls’ size.
    controls[0].setFixedSize(*COACH_BUTTON_SIZE)
    for b in controls:
        b.setEnabled(enabled)
        b.show()
        b.clearFocus()
    qapp.processEvents()
    reference = controls[0].grab().toImage()
    for button in controls:
        assert (button.width(), button.height()) == COACH_BUTTON_SIZE == (150, 58)
        assert button.underMouse() == controls[0].underMouse()
        assert button.hasFocus() == controls[0].hasFocus()
        actual = button.grab().toImage()
        # Unobstructed fill and glint locations, away from distinctive icons and labels.
        for x, y in ((15, 24), (15, 35), (135, 30), (42, 8), (98, 8)):
            assert actual.pixelColor(x, y) == reference.pixelColor(x, y)
    assert controls[1].accessibleName() == "Dynamic Performance Curve"
    assert "Fast Flick" in controls[2].accessibleName()
    assert "Micro Aim" in controls[3].accessibleName()
    assert controls[1].isCheckable()
    assert controls[1].cursor().shape() == Qt.CursorShape.PointingHandCursor
    for b in controls:
        b.close()
    qapp.setStyleSheet("")
