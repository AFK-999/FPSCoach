"""Live vision: still frames from the capture preview reach the coach (rev79).

Everything here runs without a display, a model, or Qt (except the two page
tests): the frame source is a dict generator, the chat client is a stub, and the
loop is stopped from inside its own callback. The point of the suite is that the
loop always sends the *newest* frame, never floods the model, and stops when
told — the three things that go wrong in this kind of feature.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fps_coach import visual_coach  # noqa: E402
from fps_coach.visual_coach import (  # noqa: E402
    COACH_PROMPT,
    LOOK_PROMPT,
    FrameError,
    LiveVisionSession,
    VisionSettings,
    describe_frame,
    encode_frame,
    frame_delta,
    frame_signature,
)


def make_frame(width=64, height=36, value=40, sequence=1, stride=None):
    """A BGRA frame dict in the shape the capture backends produce."""
    stride = stride or width * 4
    pixel = bytes([value, value, value, 255])
    return {
        "sequence": sequence,
        "width": width,
        "height": height,
        "stride": stride,
        "bgra": pixel * (width * height),
    }


class FakeChat:
    """Stands in for AIChatClient. Records every call, answers in order."""

    def __init__(self, answers=None, error=None, delay=0.0):
        self.calls = []
        self.answers = list(answers or ["Looks fine."])
        self.error = error
        self.delay = delay
        self._lock = threading.Lock()

    def chat(self, messages, curve_context="", images=(), progress=None):
        with self._lock:
            self.calls.append(
                {"messages": messages, "curve_context": curve_context, "images": list(images)}
            )
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if len(self.answers) > 1:
            return self.answers.pop(0)
        return self.answers[0]


def wait_for_qt(predicate, timeout=8.0):
    """Like wait_for, but lets the Qt event loop deliver queued thread signals."""
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def run_session(session, timeout=5.0):
    thread = threading.Thread(target=session.run, daemon=True)
    thread.start()
    return thread


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- frame reading ---------------------------------------------------------


def test_signature_is_a_stable_luma_grid():
    signature = frame_signature(make_frame(64, 36, value=40))
    assert len(signature) == visual_coach.GRID_W * visual_coach.GRID_H
    assert set(signature) == {40}
    # Same picture, same signature; different picture, different signature.
    assert frame_signature(make_frame(64, 36, value=40)) == signature
    assert frame_signature(make_frame(64, 36, value=170)) != signature


def test_signature_honours_stride_padding():
    """A padded row (stride > width*4) must read the same picture as a tight one."""
    width, height, stride = 64, 36, 64 * 4 + 16
    row = bytes([40, 40, 40, 255]) * width + b"\x00" * (stride - width * 4)
    padded = {
        "width": width, "height": height, "stride": stride, "bgra": row * height, "sequence": 1,
    }
    assert frame_signature(padded) == frame_signature(make_frame(width, height, value=40))


def test_delta_is_zero_for_identical_and_one_for_unrelated():
    a = frame_signature(make_frame(32, 18, value=0))
    b = frame_signature(make_frame(32, 18, value=255))
    assert frame_delta(a, a) == 0.0
    assert frame_delta(a, b) == pytest.approx(1.0)
    assert frame_delta(a, b"") == 1.0  # mismatched input is "different", never a crash


def test_broken_frames_raise_frame_error():
    for broken in (
        None,
        {},
        {"width": 4, "height": 4, "bgra": b"short"},
        {"width": 0, "height": 4, "bgra": b"x" * 16},
        {"width": 4, "height": 4, "stride": 4, "bgra": b"x" * 64},
        {"width": 4, "height": 4, "bgra": "not bytes"},
    ):
        with pytest.raises(FrameError):
            frame_signature(broken)


def test_describe_frame_reports_tone_and_contrast_not_contents():
    dark = describe_frame(make_frame(32, 18, value=10))
    bright = describe_frame(make_frame(32, 18, value=240))
    assert "dark" in dark and "bright" in bright
    assert "32x18" in dark
    for forbidden in ("enemy", "player", "target", "aim"):
        assert forbidden not in dark.lower()


# --- encoding --------------------------------------------------------------


def test_encode_frame_produces_a_real_jpeg():
    encoded = encode_frame(make_frame(640, 360), max_width=320, quality=70)
    assert encoded.jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert encoded.jpeg[-2:] == b"\xff\xd9"  # EOI
    assert (encoded.width, encoded.height) == (320, 180)
    assert (encoded.source_width, encoded.source_height) == (640, 360)
    assert encoded.scale == pytest.approx(0.5)
    assert encoded.as_meta()["bytes"] == len(encoded.jpeg)


def test_encode_frame_does_not_upscale_small_frames():
    encoded = encode_frame(make_frame(100, 60), max_width=768)
    assert (encoded.width, encoded.height) == (100, 60)


def test_encode_frame_clamps_quality_and_keeps_decodable_output(monkeypatch):
    encoded = encode_frame(make_frame(64, 36), max_width=64, quality=1000)
    assert encoded.quality == 95
    assert encoded.jpeg[:2] == b"\xff\xd8"


def test_qt_fallback_encodes_when_opencv_is_absent(monkeypatch):
    """A plain install (no recorder extra) still gets a JPEG, through Qt."""
    monkeypatch.setattr(visual_coach, "_cv2", None)
    monkeypatch.setattr(visual_coach, "_np", None)
    encoded = encode_frame(make_frame(160, 90, value=90), max_width=80)
    assert encoded.jpeg[:2] == b"\xff\xd8"
    assert (encoded.width, encoded.height) == (80, 45)


def test_no_encoder_at_all_says_what_to_install(monkeypatch):
    monkeypatch.setattr(visual_coach, "_cv2", None)
    monkeypatch.setattr(visual_coach, "_np", None)
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("PySide6"):
            raise ImportError("blocked for the test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(visual_coach.VisualEncodeError) as excinfo:
        encode_frame(make_frame(64, 36))
    assert "recorder" in str(excinfo.value)


# --- the loop --------------------------------------------------------------


def test_one_frame_one_reply_then_stop():
    chat = FakeChat(["The picture is very flat; check the in-game brightness."])
    replies = []
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01),
        on_reply=lambda text, meta: (replies.append((text, meta)), session.stop()),
    )
    run_session(session).join(5)
    assert replies and replies[0][0].startswith("The picture is very flat")
    assert len(chat.calls) == 1
    call = chat.calls[0]
    assert call["images"] and call["images"][0][:2] == b"\xff\xd8"
    assert call["messages"][0]["role"] == "user"
    assert "still frame" in call["messages"][0]["content"].lower()
    meta = replies[0][1]
    assert meta["asked"] == "live" and meta["width"] == 64


def test_unchanged_frames_are_skipped_when_asked_to():
    """A paused game must not be re-sent: identical frames are counted and dropped."""
    chat = FakeChat(["Same as before."])
    counters = {}

    def provider():
        # Always the same picture, new sequence numbers.
        counters["n"] = counters.get("n", 0) + 1
        return make_frame(64, 36, value=40, sequence=counters["n"])

    session = LiveVisionSession(
        frame_provider=provider,
        chat=chat,
        settings=VisionSettings(
            min_interval=0.0, poll_interval=0.01, skip_unchanged=True, change_threshold=0.01
        ),
    )

    def stop_after_a_while():
        assert wait_for(lambda: session.counters.skipped >= 5, timeout=5)
        session.stop()

    watcher = threading.Thread(target=stop_after_a_while, daemon=True)
    watcher.start()
    run_session(session).join(6)
    watcher.join(2)
    assert len(chat.calls) == 1, "the identical follow-up frames must not reach the model"
    assert session.counters.skipped >= 5


def test_a_changed_picture_is_sent_even_when_skip_is_on():
    frames = [make_frame(64, 36, value=10, sequence=2), make_frame(64, 36, value=200, sequence=3)]
    chat = FakeChat(["Bright now.", "Still bright."])
    replies = []
    session = LiveVisionSession(
        frame_provider=lambda: frames[0] if len(chat.calls) == 0 else frames[1],
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01, skip_unchanged=True),
        on_reply=lambda text, meta: (
            replies.append(text),
            session.stop() if len(replies) == 2 else None,
        ),
    )
    run_session(session).join(6)
    assert replies == ["Bright now.", "Still bright."]
    assert len(chat.calls) == 2


def test_ask_now_bypasses_the_unchanged_check():
    chat = FakeChat(["Looked.", "Looked again."])
    replies = []
    settings = VisionSettings(min_interval=30.0, poll_interval=0.01)  # long gap on purpose
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=settings,
        on_reply=lambda text, meta: replies.append((text, meta)),
    )
    thread = run_session(session)
    assert wait_for(lambda: len(replies) == 1)
    session.ask_now()  # must wake the 30 s wait immediately
    assert wait_for(lambda: len(replies) == 2, timeout=3)
    session.stop()
    thread.join(3)
    assert replies[0][1]["asked"] == "live"
    assert replies[1][1]["asked"] == "now"
    assert "user asked you to look" in chat.calls[1]["messages"][0]["content"].lower()


def test_history_is_included_and_capped():
    chat = FakeChat(["First.", "Second.", "Third."])
    replies = []
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01, history=2, skip_unchanged=False),
        on_reply=lambda text, meta: (replies.append(text), session.stop() if len(replies) == 3 else None),
    )
    run_session(session).join(6)
    assert replies == ["First.", "Second.", "Third."]
    third_prompt = chat.calls[2]["messages"][0]["content"]
    assert "First." in third_prompt and "Second." in third_prompt
    assert COACH_PROMPT.split("{")[0].strip()[:20] in third_prompt


def test_static_frame_is_resent_when_the_change_check_is_off():
    """Regression: the loop used to wait forever for a new sequence number."""
    chat = FakeChat(["One.", "Two.", "Three."])
    replies = []
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36, sequence=7),
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01, skip_unchanged=False),
        on_reply=lambda text, meta: (
            replies.append(text),
            session.stop() if len(replies) == 3 else None,
        ),
    )
    run_session(session).join(6)
    assert replies == ["One.", "Two.", "Three."]


def test_no_frame_yet_is_reported_once_and_waits():
    statuses = []
    session = LiveVisionSession(
        frame_provider=lambda: None,
        chat=FakeChat(),
        settings=VisionSettings(min_interval=0.0, poll_interval=0.02),
        on_status=statuses.append,
    )
    thread = run_session(session)
    assert wait_for(lambda: any("No frame yet" in text for text in statuses))
    session.stop()
    thread.join(3)
    assert not thread.is_alive()
    assert any("Live vision stopped" in text for text in statuses)


def test_missing_frame_after_frames_appear_reports_running_counters():
    chat = FakeChat(["Fine."])
    frame = {"value": None}

    def provider():
        return frame["value"]

    session = LiveVisionSession(
        frame_provider=provider,
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01),
    )
    thread = run_session(session)
    assert wait_for(lambda: session.counters.sent == 0)
    frame["value"] = make_frame(64, 36)
    assert wait_for(lambda: session.counters.sent == 1)
    session.stop()
    thread.join(3)
    line = session.counters.line()
    assert "1 frame sent" in line and "1 reply" in line


def test_provider_errors_are_reported_not_raised():
    errors = []

    def provider():
        raise RuntimeError("capture backend died")

    session = LiveVisionSession(
        frame_provider=provider,
        chat=FakeChat(),
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01),
        on_error=errors.append,
    )
    thread = run_session(session)
    assert wait_for(lambda: errors)
    session.stop()
    thread.join(3)
    assert "capture backend died" in errors[0]


def test_consecutive_provider_failures_stop_the_loop():
    chat = FakeChat(error=RuntimeError("model exploded"))
    errors = []
    statuses = []
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=VisionSettings(
            min_interval=0.0,
            poll_interval=0.01,
            skip_unchanged=False,
            backoff_factor=1.0,
            max_consecutive_errors=3,
        ),
        on_error=errors.append,
        on_status=statuses.append,
    )
    thread = run_session(session)
    thread.join(6)
    assert not thread.is_alive(), "the loop must give up rather than spin on a broken provider"
    assert len(errors) == 3
    assert any("Stopped after 3 failed requests" in text for text in statuses)
    assert session.counters.errors == 3


def test_empty_reply_is_reported_and_does_not_stall():
    chat = FakeChat(["", "Real answer."])
    replies = []
    statuses = []
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01, skip_unchanged=False),
        on_reply=lambda text, meta: (replies.append(text), session.stop()),
        on_status=statuses.append,
    )
    run_session(session).join(6)
    assert replies == ["Real answer."]
    assert any("empty reply" in text for text in statuses)
    assert session.counters.empty == 1


def test_curve_context_reaches_the_model():
    chat = FakeChat(["Got it."])
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=VisionSettings(min_interval=0.0, poll_interval=0.01),
        context_provider=lambda: "In-Game Mode is ON: VALORANT 0.4 at 800 DPI",
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(5)
    assert "VALORANT" in chat.calls[0]["curve_context"]


def test_settings_are_normalised():
    normalised = VisionSettings(
        min_interval=-5, max_width=1, quality=999, change_threshold=5, history=99,
        poll_interval=0, backoff_factor=0.1, max_consecutive_errors=0,
    ).normalised()
    assert normalised.min_interval == 0.0
    assert normalised.max_width == 64
    assert normalised.quality == 95
    assert normalised.change_threshold == 1.0
    assert normalised.history == 6
    assert normalised.poll_interval > 0
    assert normalised.backoff_factor == 1.0
    assert normalised.max_consecutive_errors == 1


def test_prompts_carry_the_fair_play_line():
    for prompt in (COACH_PROMPT, LOOK_PROMPT):
        text = prompt.lower()
        assert "do not identify targets" in text
        assert "aim" in text
        assert "{description}" in prompt  # formatted per frame


def test_stop_before_any_frame_exits_promptly():
    session = LiveVisionSession(
        frame_provider=lambda: None,
        chat=FakeChat(),
        settings=VisionSettings(poll_interval=30.0),
    )
    thread = run_session(session)
    time.sleep(0.05)
    started = time.monotonic()
    session.stop()
    thread.join(3)
    assert not thread.is_alive()
    assert time.monotonic() - started < 2.0, "stop() must break a long poll wait"


# --- the page --------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def saved(monkeypatch, tmp_path):
    from fps_coach.ui import pages_coach
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "chat.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(pages_coach, "_settings", lambda: store)
    return store


class FakeBackend:
    """Only what Live vision needs from CoreBackend."""

    def __init__(self, frame=None):
        self.frame = frame
        self.native_available = False

    def latest_frame(self):
        return self.frame


def test_page_shows_the_model_warning_and_starts_off(qapp, saved):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    try:
        page.set_capture_source(FakeBackend(make_frame(64, 36)))
        assert page.live_toggle.isChecked() is False, "live vision must be off at launch"
        assert page.live_note.isHidden() is False
        assert "cannot see images" in page.live_note.text()
        assert "gemma3" in page.live_note.text()
    finally:
        page.shutdown()


def test_page_says_frames_stay_local_for_a_vision_model_on_ollama(qapp, saved):
    from fps_coach.ui.pages_coach import AIChatPage

    saved.setValue("ai/model", "gemma3:4b")
    page = AIChatPage()
    try:
        page.set_capture_source(FakeBackend(make_frame(64, 36)))
        assert "stay on this machine" in page.live_note.text()
    finally:
        page.shutdown()


def test_page_without_a_capture_source_explains_itself(qapp, saved):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    try:
        page.set_capture_source(None)
        assert "needs the capture backend" in page.live_note.text()
        page._ask_live_now()
        assert "nothing to look at" in page.live_status.text()
    finally:
        page.shutdown()


def test_toggling_live_vision_posts_a_reply_into_the_transcript(qapp, saved, monkeypatch):
    from fps_coach.ui import pages_coach

    saved.setValue("ai/model", "gemma3:4b")
    page = pages_coach.AIChatPage()
    fake_chat = FakeChat(["HUD is cluttered along the bottom edge."])
    monkeypatch.setattr(pages_coach, "AIChatClient", lambda settings: fake_chat)
    page.set_capture_source(FakeBackend(make_frame(128, 72)))
    try:
        page.live_interval.setValue(2)  # the smallest allowed gap keeps the test quick
        page.live_toggle.setChecked(True)
        assert wait_for_qt(
            lambda: "HUD is cluttered" in page.transcript.toPlainText(), timeout=10
        ), page.transcript.toPlainText()
        text = page.transcript.toPlainText()
        assert "Coach (live)" in text
        assert page.live_thumb.pixmap() is not None
        assert not page.live_thumb.pixmap().isNull(), "the page must show what was sent"
    finally:
        page.live_toggle.setChecked(False)
        page.shutdown()
    assert page.live_toggle.isChecked() is False
