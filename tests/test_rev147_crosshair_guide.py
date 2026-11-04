"""The calibration wizard and the application Crosshair feature guide are separate."""
from PySide6.QtWidgets import QLabel

_KEEP = []


def test_five_questions_then_review_with_no_crosshair_page(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard

    guide = CalibrationWizard()
    _KEEP.append(guide)
    guide.ai_enabled.setChecked(False)
    assert guide.stack.count() == 6
    assert len(guide.nav_labels) == 6
    assert all("crosshair" not in label.text().lower() for label in guide.nav_labels)
    for i in range(5):
        assert guide.step == i and guide.counter.text() == f"SECTION {i + 1} / 5"
        guide.skip_step()
    assert guide.step == guide.REVIEW_STEP and guide.counter.text() == "SETUP PREVIEW"
    assert guide.title.text() == "Your setup at a glance"
    guide.go_back()
    assert guide.step == 4
    guide.go_next()
    assert guide.step == 5
    guide.go_next()
    assert guide.result() == guide.DialogCode.Accepted


def test_app_guide_is_structured_and_button_navigates_without_activation(qapp):
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    _KEEP.append(window)
    page = window.guide_page
    texts = "\n".join(label.text() for label in page.findChildren(QLabel))
    for phrase in ("Crosshair feature guide", "Design and preview", "Overlay and position",
                   "Import and export", "Dynamic Magnification", "Check the rules",
                   "does not enable magnification", "background polling", "anti-cheat"):
        assert phrase in texts
    index = window.stack.indexOf(window.crosshair_page)
    before = window.crosshair_page._model.to_dict()
    active = window.crosshair_page.activation.isChecked()
    window._select_page("guide")
    page.open_crosshair_btn.click()
    assert window.stack.currentWidget() is window.crosshair_page
    assert window.stack.indexOf(window.crosshair_page) == index
    assert window.crosshair_page._model.to_dict() == before
    assert window.crosshair_page.activation.isChecked() == active
    assert not window.crosshair_page.magnification_toggle.isChecked()
    assert not window.crosshair_page.mag_hotkey_arm.isChecked()
    window.close()


def test_wizard_never_reparents_or_changes_crosshair(qapp):
    from fps_coach.ui.main_window import MainWindow

    window = MainWindow()
    _KEEP.append(window)
    page = window.crosshair_page
    page.thickness.setValue(6)
    model = page._model.to_dict()
    index = window.stack.indexOf(page)
    was_active = page.activation.isChecked()
    window.dashboard_page._open_calibration_wizard()
    guide = window.dashboard_page._wizard_dialog
    guide.ai_enabled.setChecked(False)
    for i in range(5):
        assert guide.step == i
        assert page.parentWidget() is window.stack and window.stack.indexOf(page) == index
        guide.go_next()
    assert guide.step == 5 and page._model.to_dict() == model
    guide.reject()
    assert page.parentWidget() is window.stack and window.stack.indexOf(page) == index
    assert page._model.to_dict() == model and page.activation.isChecked() == was_active
    assert not page.magnification_toggle.isChecked() and not page.mag_hotkey_arm.isChecked()
    window.close()
