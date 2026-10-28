"""AI Coach draws curve proposals beside chat and waits for explicit confirmation."""

from __future__ import annotations

from copy import deepcopy

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_KEEP_ALIVE: list = []


def _page(qapp):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    _KEEP_ALIVE.append(page)
    return page


def _recommendation():
    points = [
        (0.0, 0.0),
        (0.06, 0.025),
        (0.18, 0.10),
        (0.33, 0.21),
        (0.49, 0.36),
        (0.66, 0.54),
        (0.82, 0.75),
        (1.0, 1.0),
    ]
    return {
        "points": points,
        "captured_sample_count": 128,
        "sensitivity_multiplier": 1.04,
        "confidence": 0.7,
        "title": "Measured curve",
        "reasons": ["The captured movements support a modest change."],
    }


def _summary():
    return {
        "trial_count": 8,
        "path_efficiency": 0.91,
        "overshoot_score": 0.12,
        "underreach_score": 0.08,
        "correction_score": 0.1,
        "tremor_score": 0.05,
    }


def test_calibration_recommendation_appears_as_a_full_curve_in_the_adjacent_panel(qapp):
    page = _page(qapp)
    recommendation = _recommendation()

    page.set_calibration_context(_summary(), recommendation)

    assert not page.quick_preview.isHidden()
    assert len(page.quick_preview.points) == len(recommendation["points"])
    assert len(page.quick_preview.xs) == len(recommendation["points"])
    assert page.quick_preview.previous_points is None
    assert "128 captured movement samples" in page.quick_hint.text()
    assert "8 data-derived control points" in page.quick_hint.text()
    assert page.apply_to_rec.isHidden()


def test_ai_refinement_previews_all_points_but_does_not_send_until_clicked(qapp):
    page = _page(qapp)
    page.set_calibration_context(_summary(), _recommendation())
    emitted: list[tuple] = []
    page.curve_adjusted.connect(lambda *args: emitted.append(args))

    page._apply_ai_adjustment("Try a small increase. [ADJUST: sensitivity +5%]")

    assert len(page.quick_preview.points) == len(_recommendation()["points"])
    assert len(page.quick_preview.previous_points) == len(_recommendation()["points"])
    assert len(page.quick_preview.xs) == len(_recommendation()["points"])
    assert not page.apply_to_rec.isHidden()
    assert "AI curve proposal" in page.quick_hint.text()
    assert emitted == []

    page.apply_to_rec.click()

    assert len(emitted) == 1
    previous, adjusted, note = emitted[0]
    assert len(previous.horizontal) == len(adjusted.horizontal) == len(_recommendation()["points"])
    assert note


def test_proactive_ai_curve_recommendation_previews_without_sending_to_calibration(qapp):
    page = _page(qapp)
    page.set_calibration_context(_summary(), _recommendation())
    emitted: list[tuple] = []
    page.curve_adjusted.connect(lambda *args: emitted.append(args))

    page._proactive_received("Try a small precision change. [ADJUST: precision]")

    assert not page.apply_to_rec.isHidden()
    assert "AI curve proposal" in page.quick_hint.text()
    assert "ADJUST" not in page.transcript.toPlainText()
    assert emitted == []


def test_system_prompt_requests_a_non_automatic_visual_curve_proposal():
    from fps_coach.ai_chat import SYSTEM_PROMPT

    assert "[ADJUST: sensitivity +5%]" in SYSTEM_PROMPT
    assert "not applied automatically" in SYSTEM_PROMPT
    assert "adjacent curve-tuning panel" in SYSTEM_PROMPT


def test_imported_curve_preview_is_not_resampled_to_five_or_seven_points(qapp):
    from fps_coach.curve_io import CurvePoint, CurveProfile

    page = _page(qapp)
    points = [CurvePoint(speed=float(i * 10), multiplier=1.0 + i / 100) for i in range(60)]
    profile = CurveProfile(
        name="Imported 60 point curve",
        horizontal=deepcopy(points),
        vertical=deepcopy(points),
        source_format="cc4",
    )

    page.set_base_profile(profile)

    assert not page.quick_preview.isHidden()
    assert len(page.quick_preview.points) == 60
    assert len(page.quick_preview.xs) == 60
    assert page.quick_preview.previous_points is None


def test_user_and_coach_bubbles_use_opposite_alignment_and_escape_html(qapp):
    page = _page(qapp)
    page._append("You", "What about <sensitivity> & control?\nSecond line.")
    page._append("Coach", "I can show a curve proposal.")

    import re

    markup = page.transcript.toHtml().lower()
    transcript = page.transcript.toPlainText()
    bubbles = re.findall(r"<table[^>]*>.*?</table>", markup, flags=re.DOTALL)
    assert len(bubbles) == 2
    assert 'align="right"' in bubbles[0]  # the user's bubble is right-aligned
    assert 'align="right"' not in bubbles[1]  # the coach's default alignment is left
    assert "What about <sensitivity> & control?" in transcript
    assert "Second line." in transcript
    assert "I can show a curve proposal." in transcript
