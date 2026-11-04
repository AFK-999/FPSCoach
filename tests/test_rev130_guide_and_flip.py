"""Theme-aware guide chrome and Recommendation's reversible performance flip."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QAbstractAnimation, QPoint, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_KEEP = []


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import dynamic_curve, main_window, pages_setup

    def create(theme="dark", animations=True, mode="luxurious"):
        settings = QSettings(str(tmp_path / f"{theme}-{animations}-{mode}.ini"), QSettings.Format.IniFormat)
        settings.setValue("ui/theme", theme)
        settings.setValue("ui/animations", animations)
        settings.setValue("ui/mode", mode)
        for module in (dynamic_curve, main_window, pages_setup):
            monkeypatch.setattr(module, "_settings", lambda: settings)
        monkeypatch.setattr(main_window.MainWindow, "_apply_voice_commands", lambda self: None)
        win = main_window.MainWindow()
        _KEEP.append(win)
        win.resize(1400, 900)
        win.show()
        QTest.qWait(25)
        return win
    return create


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_guide_uses_live_theme_rounded_chrome_and_close_discards(make_window, theme):
    from fps_coach.ui.styles import PALETTES

    w = make_window(theme)
    w.dashboard_page._open_calibration_wizard()
    d = w.dashboard_page._wizard_dialog
    QTest.qWait(25)
    assert d.isVisible() and d.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not d.mask().isEmpty() and not d.mask().contains(QPoint(0, 0))
    assert d.mask().contains(QPoint(d.width() // 2, 0))
    bar = d.title_bar
    assert bar.close_button.width() == 26 and bar.close_button.accessibleName() == "Close calibration guide"
    assert bar.close_button.x() > bar.width() / 2
    im = d.grab().toImage()
    assert im.pixelColor(bar.x(), bar.y()) == QColor(PALETTES[theme]["bg"])
    assert im.pixelColor(bar.x() + 100, bar.y() + 4) == QColor(PALETTES[theme]["surface2"])
    d.ai_enabled.setChecked(False)
    first = next(iter(d.cards[0].values()))
    first.click()
    assert d.answers["problems"]
    bar.close_button.click()
    assert w.dashboard_page._wizard_dialog is None
    assert not w.dashboard_page._wizard_completed
    assert not w.dashboard_page._wizard_answers["problems"]
    w.close()


def test_guide_theme_updates_and_window_mask_resizes(make_window):
    w = make_window("dark")
    w.dashboard_page._open_calibration_wizard()
    d = w.dashboard_page._wizard_dialog
    QTest.qWait(25)
    before = d.grab().toImage().pixelColor(400, 60)
    w.s.setValue("ui/theme", "light")
    w._apply_theme()
    QTest.qWait(20)
    after = d.grab().toImage().pixelColor(400, 60)
    assert before != after and after.lightness() > before.lightness()
    d.resize(1300, 800)
    QTest.qWait(20)
    assert d.mask().contains(QPoint(d.width() // 2, 0))
    assert not d.mask().contains(QPoint(d.width()-1, d.height()-1))
    d.reject()
    w.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_dynamic_panel_flips_both_ways_without_enabling_or_applying(make_window, theme):
    w = make_window(theme)
    w._select_page("chat")
    assert not hasattr(w.chat_page, "dynamic_curve_section")
    assert w.chat_page.fast_flick_btn and w.chat_page.precision_btn
    w._select_page("recommendation")
    page = w.recommendation_page
    deck = page.curve_deck
    assert deck.currentIndex() == 0 and not w.dynamic_curve.enabled
    assert page.dynamic_curve_btn.isVisible() and page.dynamic_curve_btn.height() == 42
    page.dynamic_curve_btn.click()
    assert deck.animation.state() == QAbstractAnimation.State.Running
    assert deck.overlay.isVisible() and deck.currentIndex() == 0
    deck.animation.pause()
    deck.animation.setCurrentTime(300)
    assert deck.currentIndex() == 1 and deck.overlay.isVisible()
    deck.animation.setCurrentTime(deck.animation.duration())
    assert deck.currentIndex() == 1 and not deck.overlay.isVisible()
    panel = page.dynamic_curve_panel
    assert panel.isVisible() and panel.chart.width() == panel.chart.height()
    assert panel.chart._plot_rect().width() == panel.chart._plot_rect().height()
    assert not w.dynamic_curve.enabled and w.calibration_page._profile is None
    panel.enabled_box.setChecked(True)
    assert w.training_page.dynamic_curve_panel.enabled_box.isChecked()
    panel.accuracy.setValue(77)
    assert w.training_page.dynamic_curve_panel.accuracy.value() == 77
    page.dynamic_back_btn.click()
    deck.animation.setCurrentTime(deck.animation.duration())
    assert deck.currentIndex() == 0 and w.dynamic_curve.enabled
    w.dynamic_curve.set_enabled(False)
    w.close()


@pytest.mark.parametrize("animations,mode", [(False, "luxurious"), (True, "performance")])
def test_flip_respects_animation_preferences_and_restores_suggested_chart(make_window, animations, mode):
    w = make_window(animations=animations, mode=mode)
    w._select_page("recommendation")
    page = w.recommendation_page
    page.dynamic_curve_btn.click()
    assert page.curve_deck.currentIndex() == 1 and not page.curve_deck.overlay.isVisible()
    assert page.curve_deck.animation.state() == QAbstractAnimation.State.Stopped
    page.dynamic_back_btn.click()
    assert page.curve_deck.currentIndex() == 0 and page.empty_label.isVisible()
    w.close()


def test_mid_flip_toggle_and_disabling_animation_finish_safely(make_window):
    w = make_window()
    w._select_page("recommendation")
    page = w.recommendation_page
    page.dynamic_curve_btn.click()
    assert page.curve_deck.overlay.isVisible()
    page.dynamic_curve_btn.click()  # reverse even before the midpoint
    assert page.curve_deck._target == 0
    w.s.setValue("ui/animations", False)
    w._apply_theme()
    assert page.curve_deck.currentIndex() == 0 and not page.curve_deck.overlay.isVisible()
    assert page.curve_deck.animation.state() == QAbstractAnimation.State.Stopped
    w.close()


def test_guide_titlebar_pointer_handlers_allow_release_and_non_left_click(make_window):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    w = make_window()
    w.dashboard_page._open_calibration_wizard()
    d = w.dashboard_page._wizard_dialog
    QTest.qWait(15)
    bar = d.title_bar
    local = QPointF(100, 18)
    global_pos = QPointF(bar.mapToGlobal(local.toPoint()))
    for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, local, global_pos, button, button,
                            Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, local, global_pos, button,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        bar.mousePressEvent(press)
        bar.mouseReleaseEvent(release)
        assert bar._drag_offset is None
    d.reject()
    w.close()
