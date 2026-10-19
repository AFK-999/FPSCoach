"""The training figures are drawn in code and must have transparent backgrounds."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_target_figures_have_transparent_corners(qapp) -> None:
    from fps_coach.ui import figures

    pixmaps = figures.target_pixmaps()
    assert len(pixmaps) == 2
    for pix in pixmaps:
        assert not pix.isNull()
        image = pix.toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0
        # The figure itself must have visible pixels.
        opaque = sum(
            1
            for x in range(0, image.width(), 4)
            for y in range(0, image.height(), 4)
            if image.pixelColor(x, y).alpha() > 128
        )
        assert opaque > 50


def test_training_canvas_uses_drawn_figures(qapp) -> None:
    from fps_coach.ui.pages_practice import TrainingCanvas

    canvas = TrainingCanvas()
    assert len(canvas._sprites) == 2
