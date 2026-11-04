"""The final guide review is faithful to self-reports and never invents setup data."""

from __future__ import annotations

import pytest
from fps_coach.aiming_motion import BY_KEY as MOTIONS
from fps_coach.calibration_wizard import GRIPS, clone_answers


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP = []


@pytest.mark.parametrize("grip", ["palm", "claw", "fingertip", "unsure", ""])
@pytest.mark.parametrize("motion", ["wrist", "fingers", "arm", "shoulder_hybrid", "unsure", ""])
def test_review_accurately_reports_each_grip_and_motion(qapp, grip, motion):
    from fps_coach.ui.setup_preview import SetupPreview

    preview = SetupPreview()
    _KEEP.append(preview)
    preview.set_answers({"grip": grip, "motion": motion})
    expected_grip = GRIPS[grip][0] if grip else "Not provided"
    expected_motion = (
        MOTIONS[motion].label if motion in MOTIONS else "Not sure" if motion else "Not provided"
    )
    assert preview.grip_value.text() == expected_grip
    assert preview.motion_value.text() == expected_motion
    assert preview.scene.grip == grip and preview.scene.motion == motion
    preview.close()


def test_review_setup_is_correct_after_back_edit_and_only_commits_on_finish(qapp):
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    _KEEP.append(page)
    page._open_calibration_wizard()
    dialog = page._wizard_dialog
    dialog.ai_enabled.setChecked(False)
    dialog.cards[0]["shaky"].click()
    dialog.cards[1]["fingertip"].click()
    dialog.cards[2]["wrist"].click()
    dialog.cards[3]["small"].click()
    for _ in range(4):
        dialog.go_next()
    dialog.mouse_model.setText("Mouse model 123")
    dialog.game.setText("Example game")
    dialog.dpi.setText("1600")
    dialog.sensitivity.setText("0.123456789")
    dialog.pad_width.setText("400.123456789")
    dialog.go_next()
    assert dialog.step == 5 and not page._wizard_completed
    review = dialog.setup_preview
    assert review.grip_value.text() == "Fingertip grip"
    assert review.motion_value.text() == "Wrist"
    assert review.mouse_value.text() == "Mouse model 123"
    assert review.pad_value.text() == "Small mousepad"
    assert review.metrics["Sensitivity"].text() == "0.123456789"
    assert review.metrics["DPI"].text() == "1600"
    assert review.metrics["Usable mouse travel"].text() == "400.123456789 mm"
    for _ in range(3):
        dialog.go_back()
    assert dialog.step == 2
    dialog.cards[2]["shoulder_hybrid"].click()
    for _ in range(3):
        dialog.go_next()
    assert review.motion_value.text() == "Shoulder / Hybrid"
    assert review.scene.motion == "shoulder_hybrid"
    assert page._wizard_answers == clone_answers(None)
    dialog.next_btn.click()
    assert page._wizard_completed and page._wizard_answers["motion"] == "shoulder_hybrid"
    page.close()


def test_review_distinguishes_unsure_and_skipped(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard

    dialog = CalibrationWizard()
    _KEEP.append(dialog)
    dialog.ai_enabled.setChecked(False)
    dialog.cards[1]["unsure"].click()
    for _ in range(5):
        dialog.go_next()
    assert dialog.setup_preview.grip_value.text() == "Not sure"
    assert dialog.setup_preview.motion_value.text() == "Not provided"
    dialog.reject()


def test_setup_unknown_controls_do_not_claim_hardware_or_modify_game(qapp):
    from fps_coach.ui.calibration_wizard import CalibrationWizard

    dialog = CalibrationWizard()
    _KEEP.append(dialog)
    dialog.ai_enabled.setChecked(False)
    for _ in range(4):
        dialog.go_next()
    assert dialog.dpi.text() == "" and dialog.sensitivity.text() == ""
    dialog.dpi_up.click()
    assert dialog.dpi.text() == "800"  # an explicitly clicked local input control
    dialog.dpi_down.click()
    assert dialog.dpi.text() == "700"
    dialog.sensitivity.setText("0.7")
    dialog.dpi_unknown.setChecked(True)
    dialog.sensitivity_unknown.setChecked(True)
    assert not dialog.dpi.isEnabled() and not dialog.dpi_up.isEnabled()
    assert not dialog.sensitivity.isEnabled()
    dialog.go_next()
    dialog.go_next()
    assert dialog.setup_preview.metrics["DPI"].text() == "Not provided"
    assert dialog.setup_preview.metrics["Sensitivity"].text() == "Not provided"
    assert "cm/360" not in dialog.setup_preview.scene.accessibleName()
    dialog.reject()


def test_start_next_to_dashboard_and_saved_actions_in_guide(qapp):
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    _KEEP.append(page)
    page.resize(1200, 900)
    page.show()
    qapp.processEvents()
    book = page.wizard_btn
    assert book.text() == "Start" and not book.icon().isNull()
    assert book.parentWidget() is page.dashboard_title.parentWidget()
    assert book.geometry().left() > page.dashboard_title.geometry().right()
    assert not hasattr(page, "guide_card")
    page._open_calibration_wizard()
    dialog = page._wizard_dialog
    dialog.ai_enabled.setChecked(False)
    dialog.cards[1]["palm"].click()
    for _ in range(7):
        dialog.go_next()
    assert page._wizard_completed
    page._open_calibration_wizard()
    assert not page._wizard_dialog.saved_clear_btn.isHidden()
    assert not page._wizard_dialog.saved_discuss_btn.isHidden()
    page._wizard_dialog.saved_clear_btn.click()
    assert not page._wizard_completed and page._wizard_answers == clone_answers(None)
    page.close()


def test_finish_open_calibration_commits_but_does_not_start_capture(qapp):
    from fps_coach.ui.pages_setup import DashboardPage

    page = DashboardPage()
    _KEEP.append(page)
    selected = []
    page.navigate.connect(selected.append)
    page._open_calibration_wizard()
    dialog = page._wizard_dialog
    dialog.ai_enabled.setChecked(False)
    dialog.cards[2]["fingers"].click()
    for _ in range(5):
        dialog.go_next()
    assert dialog.step == 5 and not selected
    dialog.start_calibration_btn.click()
    assert page._wizard_answers["motion"] == "fingers" and selected == ["calibration"]
    page.close()


def test_user_supplied_text_is_plain_and_long_names_have_wrapped_space(qapp):
    from fps_coach.ui.setup_preview import SetupPreview
    from PySide6.QtCore import Qt

    preview = SetupPreview()
    _KEEP.append(preview)
    model = "Super long mouse model " * 3
    preview.set_answers({"mouse_model": model, "game": "<b>Literal game</b>"})
    preview.resize(900, 520)
    preview.show()
    qapp.processEvents()
    assert preview.mouse_value.text() == model.strip()
    assert preview.mouse_value.parentWidget().height() >= preview.mouse_value.minimumHeight()
    assert preview.metrics["Game"].text() == "<b>Literal game</b>"
    assert preview.metrics["Game"].textFormat() == Qt.TextFormat.PlainText
    preview.close()
