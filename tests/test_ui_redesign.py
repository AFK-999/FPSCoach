"""Offscreen checks for the redesigned window: navigation, icons, layout, and wiring."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from fps_coach.ui import icons  # noqa: E402
from fps_coach.ui.styles import PALETTES, build_style  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_every_named_icon_draws_pixels(qapp) -> None:
    for name in icons.available():
        image = icons.pixmap(name, "#FFFFFF", 24).toImage()
        opaque = sum(1 for x in range(24) for y in range(24) if image.pixelColor(x, y).alpha() > 0)
        assert opaque > 10, f"icon {name} drew almost nothing"


def test_light_theme_is_soft_gray() -> None:
    assert PALETTES["light"]["bg"] == "#E4E6E9"
    assert PALETTES["light"]["bg"] in build_style("luxurious", "light")


@pytest.fixture(scope="module")
def window(qapp):
    from fps_coach.backend import CoreBackend
    from fps_coach.ui.main_window import MainWindow

    win = MainWindow(CoreBackend())
    win.resize(1280, 820)
    win.show()
    qapp.processEvents()
    yield win
    win.close()


def test_top_navigation_has_seven_tabs_and_three_utilities(window) -> None:
    from fps_coach.ui.main_window import NAV_TABS, UTILITY

    assert [k for k, _ in NAV_TABS] == [
        "dashboard", "capture", "calibration", "recommendation", "training", "crosshair", "chat",
    ]
    assert [k for k, _ in UTILITY] == ["guide", "privacy", "settings"]
    assert set(window._nav_buttons) == {k for k, _ in NAV_TABS}


def test_every_page_switches(window) -> None:
    for key in ["dashboard", "capture", "calibration", "recommendation", "training",
                "crosshair", "chat", "guide", "privacy", "settings"]:
        window._select_page(key)
        assert window.stack.currentWidget() is window._pages[key]


def test_recommendation_summary_and_chart_stay_side_by_side(window) -> None:
    from PySide6.QtWidgets import QApplication

    page = window.recommendation_page
    window._select_page("recommendation")
    for width in (1120, 900):
        window.resize(width, 820)
        QApplication.processEvents()
        summary_x = page.generate_btn.mapTo(page, page.generate_btn.rect().topLeft()).x()
        chart_x = page.empty_label.mapTo(page, page.empty_label.rect().topLeft()).x()
        assert chart_x > summary_x
        assert page.generate_btn.mapTo(page, page.generate_btn.rect().topLeft()).y() > 0
    window.resize(1280, 820)


def test_recommendation_result_fills_metrics_and_dashboard(window) -> None:
    summary = {"trial_count": 12}
    rec = {
        "title": "Slightly faster",
        "sensitivity_multiplier": 1.12,
        "confidence": 0.74,
        "reasons": ["Overshoot is low."],
        "points": [(0.0, 0.2), (0.5, 0.5), (1.0, 0.9)],
    }
    window._on_calibration_result(summary, rec)
    assert window.recommendation_page.multiplier_value.text() == "\u00d71.12"
    assert window.dashboard_page._value_labels["recommendation"].text() == "\u00d71.12"
    assert window.dashboard_page._value_labels["calibration"].text() == "12 targets"


def test_privacy_clear_deletes_saved_sessions(window, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from fps_coach.analytics import SessionResult

    window._analytics_store.add(SessionResult.create("flick", [200.0, 240.0], 2, 1))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._select_page("privacy")
    window.privacy_page._clear()
    assert window._analytics_store.sessions == []
