"""Regression coverage for the standalone Crosshair page after rev147."""
import json

from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

_KEEP = []


def test_crosshair_page_stays_in_main_stack_and_edits_work(qapp, monkeypatch, tmp_path):
    from fps_coach.ui import pages_practice
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    _KEEP.append(window)
    window.show()
    page = window.crosshair_page
    index = window.stack.indexOf(page)
    window.dashboard_page._open_calibration_wizard()
    dialog = window.dashboard_page._wizard_dialog
    dialog.ai_enabled.setChecked(False)
    for _ in range(5):
        dialog.go_next()
    assert dialog.step == dialog.REVIEW_STEP
    assert window.stack.indexOf(page) == index and page.parentWidget() is window.stack
    dialog.reject()
    window._select_page("crosshair")
    assert window.stack.currentWidget() is page
    page.thickness.setValue(7)
    page.length.setValue(31)
    page.opacity.setValue(70)
    page.outline.setChecked(True)
    monkeypatch.setattr(pages_practice.QColorDialog, "getColor", lambda *args: QColor("#ff22aa"))
    page.color_btn.click()
    assert page._model.thickness == 7 and page._model.color == "#ff22aa"
    assert page.preview._cfg == page._model
    assert json.loads(page.s.value("crosshair/config"))["thickness"] == 7
    page.mag_disclosure.setChecked(True)
    page.mag_diameter.setValue(260)
    assert page.s.value("crosshair/magnification_diameter", type=int) == 260
    assert not page.magnification_toggle.isChecked() and not page.mag_hotkey_arm.isChecked()
    exported, image = tmp_path / "crosshair.json", tmp_path / "crosshair.png"
    original = page._model.to_dict()
    page.export_json(str(exported))
    assert page.export_png(str(image)) and image.stat().st_size > 0
    page.thickness.setValue(8)
    page.import_json(str(exported))
    assert page._model.to_dict() == original
    window.close()
    QTest.qWait(10)
