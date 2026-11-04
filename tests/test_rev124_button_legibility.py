"""Preset buttons retain room for their full labels."""
from __future__ import annotations

import pytest
from fps_coach.ui.guide_book import COACH_BUTTON_LABEL_SIZE, COACH_BUTTON_SIZE, GUIDE_BUTTON_SIZE
from fps_coach.ui.pages_coach import PresetTile
from fps_coach.ui.styles import build_style
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_larger_controls_have_space_for_bold_two_line_labels(qapp, theme):
    qapp.setStyleSheet(build_style("luxurious", theme))
    buttons = [
        (PresetTile("fast_flicks", "Optimize for\nFast Flick"), ("Optimize for", "Fast Flick")),
        (PresetTile("precision", "Precision for\nMicro Aim"), ("Precision for", "Micro Aim")),
    ]
    _KEEP.extend(b for b, _ in buttons)
    assert COACH_BUTTON_SIZE == (150, 58)
    assert GUIDE_BUTTON_SIZE == (140, 42)  # Dashboard Start remains unchanged.
    assert COACH_BUTTON_LABEL_SIZE == 8.5  # Larger than the prior 7-point labels.
    for button, lines in buttons:
        button.show()
        qapp.processEvents()
        assert (button.width(), button.height()) == COACH_BUTTON_SIZE
        font = button.font()
        font.setBold(True)
        font.setPointSizeF(COACH_BUTTON_LABEL_SIZE)
        metrics = QFontMetricsF(font)
        assert max(metrics.horizontalAdvance(line) for line in lines) <= button.width() - 16
        assert 2 * metrics.lineSpacing() <= 30
        button.close()
    qapp.setStyleSheet("")
