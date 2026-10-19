"""ValueSlider shows the exact value live, and the Settings temperature reads back correctly."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _readout(slider) -> str:
    return slider._readout.text()


def test_readout_follows_value_and_emits(qapp) -> None:
    from fps_coach.ui.widgets import ValueSlider

    slider = ValueSlider(1, 12, fmt=lambda v: f"{v} px")
    seen: list[int] = []
    slider.valueChanged.connect(seen.append)

    assert _readout(slider) == "1 px"
    slider.setValue(7)
    assert slider.value() == 7
    assert _readout(slider) == "7 px"
    assert seen == [7]


def test_readout_updates_while_dragging(qapp) -> None:
    """Dragging emits valueChanged for every step, so the readout must track each one."""
    from fps_coach.ui.widgets import ValueSlider

    slider = ValueSlider(0, 100, fmt=lambda v: f"{v}%")
    for step in (10, 11, 12, 40):
        slider._slider.setValue(step)  # what a drag does through the inner QSlider
        assert _readout(slider) == f"{step}%"


def test_temperature_format_shows_stored_value(qapp) -> None:
    """Settings stores temperature as 0.00-1.50; the slider works in hundredths."""
    from fps_coach.ui.widgets import ValueSlider

    temp = ValueSlider(0, 150, fmt=lambda v: f"{v / 100:.2f}")
    temp.setValue(35)
    assert _readout(temp) == "0.35"
    temp.setValue(150)
    assert _readout(temp) == "1.50"
    temp.setValue(0)
    assert _readout(temp) == "0.00"


def test_readout_width_fits_widest_value(qapp) -> None:
    from PySide6.QtGui import QFontMetrics

    from fps_coach.ui.widgets import ValueSlider

    slider = ValueSlider(10, 100, fmt=lambda v: f"{v}%")
    metrics = QFontMetrics(slider._readout.font())
    assert slider._readout.minimumWidth() >= metrics.horizontalAdvance("100%")
