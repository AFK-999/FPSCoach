"""Privacy > Local data: clear the calibration and recommendation history.

Every test points the file at a temporary path. The real .fpscoach_calibration_history.json in
the project folder is never written by these tests.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from fps_coach.local_data import (  # noqa: E402
    CALIBRATION_HISTORY_NAME,
    clear_calibration_history,
    count_calibration_records,
)

RECOMMENDATION = {
    "sensitivity_multiplier": 1.2,
    "confidence": 0.8,
    "title": "Test recommendation",
    "reasons": ["Test reason"],
    "points": [(0.0, 0.2), (0.25, 0.4), (0.5, 0.6), (0.75, 0.8), (1.0, 1.0)],
}
SUMMARY = {"trial_count": 12}


def _records(n: int) -> list[dict]:
    return [{"timestamp": f"2026-10-19T08:0{i}:00", "score": 60.0 + i} for i in range(n)]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# -- the file helper ------------------------------------------------------------------

def test_the_history_file_name_is_the_one_rev14_wrote() -> None:
    assert CALIBRATION_HISTORY_NAME == ".fpscoach_calibration_history.json"


def test_missing_file_counts_zero_and_is_not_created(tmp_path) -> None:
    path = tmp_path / "missing.json"
    assert count_calibration_records(path) == 0
    assert clear_calibration_history(path) == 0
    assert not path.exists()


def test_none_path_is_a_no_op() -> None:
    assert count_calibration_records(None) == 0
    assert clear_calibration_history(None) == 0


def test_clear_empties_the_saved_records(tmp_path) -> None:
    path = tmp_path / "hist.json"
    path.write_text(json.dumps(_records(3)), encoding="utf-8")
    assert count_calibration_records(path) == 3
    assert clear_calibration_history(path) == 3
    assert json.loads(path.read_text(encoding="utf-8")) == []
    assert count_calibration_records(path) == 0


def test_unreadable_file_counts_zero_and_is_reset_to_empty(tmp_path) -> None:
    path = tmp_path / "hist.json"
    path.write_text("{not json", encoding="utf-8")
    assert count_calibration_records(path) == 0
    assert clear_calibration_history(path) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == []


# -- the Privacy page -----------------------------------------------------------------

@pytest.fixture()
def privacy(qapp, tmp_path):
    from fps_coach.ui.pages_coach import PrivacyPage

    path = tmp_path / CALIBRATION_HISTORY_NAME
    path.write_text(json.dumps(_records(2)), encoding="utf-8")
    page = PrivacyPage(None, None, calibration_path=path)
    yield page, path
    page.close()


def _local_data_card(page):
    from PySide6.QtWidgets import QLabel

    for label in page.findChildren(QLabel):
        if label.text() == "Local data":
            return label.parentWidget()
    raise AssertionError("no Local data card")


def test_button_sits_in_the_local_data_card(privacy) -> None:
    page, _ = privacy
    card = _local_data_card(page)
    assert page.clear_calibration_btn in card.findChildren(type(page.clear_calibration_btn))
    assert page.clear_calibration_btn.text().strip() == "Clear calibration and recommendation history"


def test_count_shows_the_saved_results(privacy) -> None:
    page, _ = privacy
    assert page.calibration_count_label.text() == "2 saved calibration results."


def test_yes_clears_the_file_and_tells_the_page(privacy, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    page, path = privacy
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: (asked.append(a[2]), QMessageBox.StandardButton.Yes)[1],
    )
    signalled: list[int] = []
    page.calibration_cleared.connect(lambda: signalled.append(1))

    page.clear_calibration_btn.click()

    assert "cannot be undone" in asked[0]
    assert json.loads(path.read_text(encoding="utf-8")) == []
    assert signalled == [1]
    assert page.calibration_count_label.text() == "0 saved calibration results."
    assert page.calibration_status.text().startswith("Cleared 2 saved calibration results")


def test_no_changes_nothing(privacy, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    page, path = privacy
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    signalled: list[int] = []
    page.calibration_cleared.connect(lambda: signalled.append(1))

    page.clear_calibration_btn.click()

    assert path.read_text(encoding="utf-8") == before
    assert signalled == []
    assert page.calibration_status.text() == ""


# -- the in-session state the pages keep ------------------------------------------------

def test_recommendation_page_returns_to_awaiting_state(qapp) -> None:
    from fps_coach.backend import CoreBackend
    from fps_coach.ui.pages_setup import RecommendationPage

    page = RecommendationPage(CoreBackend())
    page.update_result(SUMMARY, RECOMMENDATION)
    assert not page.chart.isHidden()
    assert page.title_label.text() == "Test recommendation"

    page.clear_result()

    assert page.title_label.text() == "Awaiting calibration"
    assert page.multiplier_value.text() == "\u2014"
    assert page.chart.isHidden()
    assert not page.empty_label.isHidden()
    page.close()


def test_calibration_page_forgets_undo_but_keeps_the_curve(qapp) -> None:
    from fps_coach.backend import CoreBackend
    from fps_coach.ui.pages_setup import CalibrationPage

    page = CalibrationPage(CoreBackend())
    page.apply_recommendation(RECOMMENDATION)
    page._checkpoint()
    curve_before = list(page.chart.points)
    assert page._undo, "the test needs at least one undo step"
    assert page._last_recommendation is None  # set only when a run completes

    page._last_recommendation = dict(RECOMMENDATION)
    page.clear_recommendation_state()

    assert page._undo == []
    assert page._redo == []
    assert page._last_recommendation is None
    assert page.chart.points == curve_before
    assert not page.undo_btn.isEnabled()
    page.close()


# -- the main window ------------------------------------------------------------------

def test_main_window_clears_analyzer_trials_and_shown_recommendation(qapp, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.privacy_page.calibration_path = tmp_path / CALIBRATION_HISTORY_NAME
        window.privacy_page.calibration_path.write_text(json.dumps(_records(1)), encoding="utf-8")
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

        analyzer = window._backend.analyzer
        analyzer.reset()
        for i in range(3):  # three real trials on the analyzer
            analyzer.begin_trial(0.0, 0.0, 100.0, 0.0, float(i))
            analyzer.add_sample(50.0, 0.0, float(i) + 0.1)
            analyzer.end_trial(100.0, 0.0, float(i) + 0.3)
        trials_before = int(analyzer.summary()["trial_count"])
        assert trials_before == 3

        window._on_calibration_result(dict(SUMMARY), dict(RECOMMENDATION))
        assert window.recommendation_page.title_label.text() == "Test recommendation"

        window.privacy_page.clear_calibration_btn.click()

        assert int(analyzer.summary()["trial_count"]) == 0
        assert window._last_recommendation is None
        assert window._last_summary is None
        assert window.recommendation_page.title_label.text() == "Awaiting calibration"
        assert window.recommendation_page.chart.isHidden()
        assert window.calibration_page._undo == []
        assert json.loads(window.privacy_page.calibration_path.read_text(encoding="utf-8")) == []
    finally:
        window.close()


def test_privacy_page_gets_the_project_folder_path(qapp) -> None:
    from pathlib import Path

    import fps_coach.ui.main_window as mw

    window = mw.MainWindow()
    try:
        expected = Path(mw.__file__).resolve().parents[3] / CALIBRATION_HISTORY_NAME
        assert window.privacy_page.calibration_path == expected
    finally:
        window.close()
