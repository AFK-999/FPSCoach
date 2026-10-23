"""Generate recommendation: navigates to Calibration and draws the new curve there."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

RECOMMENDATION = {
    "sensitivity_multiplier": 1.2,
    "confidence": 0.8,
    "title": "Test recommendation",
    "reasons": ["Test reason"],
    "points": [(0.0, 0.2), (0.25, 0.4), (0.5, 0.6), (0.75, 0.8), (1.0, 1.0)],
}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qapp, monkeypatch):
    from fps_coach.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    analyzer = w.recommendation_page._backend.analyzer
    monkeypatch.setattr(analyzer, "summary", lambda: {"trial_count": 12})
    monkeypatch.setattr(analyzer, "recommend", lambda: dict(RECOMMENDATION))
    yield w
    w.close()


def test_generate_button_opens_calibration_and_draws_the_curve(window, qapp) -> None:
    from fps_coach.ui.widgets import _curve_samples

    window._select_page("recommendation")
    window.recommendation_page.generate_btn.click()
    qapp.processEvents()
    assert window.stack.currentWidget() is window.calibration_page
    assert window.calibration_page.chart.points == _curve_samples(RECOMMENDATION["points"])


def test_applied_curve_keeps_the_old_curve_for_comparison(window, qapp) -> None:
    old = [0.1, 0.1, 0.1, 0.1, 0.1]
    window.calibration_page._set_chart(old)
    window._select_page("recommendation")
    window.recommendation_page.generate_btn.click()
    qapp.processEvents()
    assert list(window.calibration_page.chart.previous_points) == old
    assert window.calibration_page.hint.text()  # the page says what was applied


def test_applied_curve_can_be_undone(window, qapp) -> None:
    before = len(window.calibration_page._undo)
    window._select_page("recommendation")
    window.recommendation_page.generate_btn.click()
    qapp.processEvents()
    assert len(window.calibration_page._undo) == before + 1


def test_no_calibration_data_stays_on_recommendation(window, qapp, monkeypatch) -> None:
    analyzer = window.recommendation_page._backend.analyzer
    monkeypatch.setattr(analyzer, "summary", lambda: {"trial_count": 0})
    window._select_page("recommendation")
    before_points = list(window.calibration_page.chart.points)
    window.recommendation_page.generate_btn.click()
    qapp.processEvents()
    assert window.stack.currentWidget() is window.recommendation_page
    assert window.calibration_page.chart.points == before_points


def test_finishing_a_calibration_still_opens_recommendation(window, qapp) -> None:
    # Regression: the calibration-finished path must not jump to Calibration.
    summary = {"trial_count": 12}
    window._on_calibration_result(summary, dict(RECOMMENDATION))
    qapp.processEvents()
    assert window.stack.currentWidget() is window.recommendation_page


def test_generate_signal_is_not_emitted_for_calibration_results(window, qapp) -> None:
    seen: list[dict] = []
    window.recommendation_page.apply_requested.connect(seen.append)
    window.recommendation_page.update_result({"trial_count": 12}, dict(RECOMMENDATION))
    assert seen == []
