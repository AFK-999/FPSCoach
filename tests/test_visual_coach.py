"""Live vision: the capture preview reaches the coach as a moving clip (rev80).

Everything here runs without a display, a model, or Qt (except the page tests):
the frame source is a generator, the chat client is a stub, and the loop is
stopped from inside its own callback.

The feature is "the coach watches the video", which for a local model means
*sampled ordered frames*, so that is what these tests pin down: a window really
contains several frames with a measurable span, motion is measured between them,
a still screen stops costing requests, and a reply can never be produced from a
stale window.
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
    MotionReport,
    VisionSettings,
    describe_frame,
    encode_frame,
    frame_delta,
    frame_signature,
    motion_report,
)

#: No wall-clock waiting inside a window and no idle skipping, so tests are fast
#: and deterministic. Individual tests override what they are exercising.
FAST = dict(min_interval=0.0, poll_interval=0.01, sample_interval=0.01, adaptive=False)


def make_frame(width=64, height=36, value=40, sequence=1, stride=None, band=None):
    """A BGRA frame dict in the shape the capture backends produce.

    ``band`` paints the named third of the picture white, so motion has a place
    to happen.
    """
    stride = stride or width * 4
    rows = []
    for y in range(height):
        row_value = value
        if band == "top" and y < height // 3:
            row_value = 235
        elif band == "middle" and height // 3 <= y < (2 * height) // 3:
            row_value = 235
        elif band == "bottom" and y >= (2 * height) // 3:
            row_value = 235
        rows.append(bytes([row_value, row_value, row_value, 255]) * width)
    return {
        "sequence": sequence,
        "width": width,
        "height": height,
        "stride": stride,
        "bgra": b"".join(rows),
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


def run_session(session):
    thread = threading.Thread(target=session.run, daemon=True)
    thread.start()
    return thread


def wait_for(predicate, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def wait_for_qt(predicate, timeout=12.0):
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


class MovingScreen:
    """A frame provider whose picture changes every time it is read."""

    def __init__(self, width=64, height=36):
        self.width = width
        self.height = height
        self.tick = 0

    def __call__(self):
        self.tick += 1
        # Alternate a bright band between the top and bottom third: real movement.
        band = "top" if self.tick % 2 else "bottom"
        frame = make_frame(self.width, self.height, value=30, sequence=self.tick, band=band)
        return frame


# --- reading a frame -------------------------------------------------------


def test_signature_is_a_stable_luma_grid():
    signature = frame_signature(make_frame(64, 36, value=40))
    assert len(signature) == visual_coach.GRID_W * visual_coach.GRID_H
    assert set(signature) == {40}
    assert frame_signature(make_frame(64, 36, value=40)) == signature
    assert frame_signature(make_frame(64, 36, value=170)) != signature


def test_signature_honours_stride_padding():
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
    assert frame_delta(a, b"") == 1.0


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


# --- motion between frames -------------------------------------------------


def test_motion_is_still_for_an_unchanged_screen():
    signature = frame_signature(make_frame(64, 36, value=90))
    report = motion_report(signature, signature)
    assert report.level == 0.0
    assert report.word == "still"
    assert report.changed_cells == 0
    assert report.band == "none"


def test_motion_detects_where_the_change_is():
    before = frame_signature(make_frame(96, 54, value=20))
    after_top = frame_signature(make_frame(96, 54, value=20, band="top"))
    after_bottom = frame_signature(make_frame(96, 54, value=20, band="bottom"))
    top = motion_report(before, after_top)
    bottom = motion_report(before, after_bottom)
    assert top.level > 0.05 and top.word in {"medium", "high"}
    assert top.band == "top", "the changed third must be identified"
    assert bottom.band == "bottom"
    assert top.changed_cells > 0


def test_motion_ignores_grain_below_the_threshold():
    before = bytes([100] * (visual_coach.GRID_W * visual_coach.GRID_H))
    after = bytes([103] * (visual_coach.GRID_W * visual_coach.GRID_H))  # +3 luma everywhere
    report = motion_report(before, after)
    assert report.changed_cells == 0, "dither must not read as movement"
    assert report.band == "none"


def test_motion_word_scales_with_the_change():
    assert MotionReport(level=0.001).word == "still"
    assert MotionReport(level=0.02).word == "low"
    assert MotionReport(level=0.06).word == "medium"
    assert MotionReport(level=0.4).word == "high"


# --- encoding --------------------------------------------------------------


def test_encode_frame_produces_a_real_jpeg():
    encoded = encode_frame(make_frame(640, 360), max_width=320, quality=70)
    assert encoded.jpeg[:2] == b"\xff\xd8"
    assert encoded.jpeg[-2:] == b"\xff\xd9"
    assert (encoded.width, encoded.height) == (320, 180)
    assert (encoded.source_width, encoded.source_height) == (640, 360)
    assert encoded.as_meta()["bytes"] == len(encoded.jpeg)


def test_encode_frame_does_not_upscale_small_frames():
    encoded = encode_frame(make_frame(100, 60), max_width=768)
    assert (encoded.width, encoded.height) == (100, 60)


def test_encode_frame_clamps_quality():
    assert encode_frame(make_frame(64, 36), max_width=64, quality=1000).quality == 95


def test_qt_fallback_encodes_when_opencv_is_absent(monkeypatch):
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


# --- the streaming loop ----------------------------------------------------


def test_a_look_is_a_clip_not_a_snapshot():
    """The headline behaviour: one request carries several ordered frames."""
    chat = FakeChat(["The bottom strip keeps redrawing."])
    windows = []
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=4),
        on_reply=lambda text, meta: (windows.append(meta), session.stop()),
    )
    run_session(session).join(8)
    assert len(chat.calls) == 1
    images = chat.calls[0]["images"]
    assert len(images) == 4, "a four-frame clip must send four images"
    assert all(image[:2] == b"\xff\xd8" for image in images)
    assert images[0] != images[3], "the frames must differ, or there is no video in it"
    meta = windows[0]
    assert meta["frames"] == 4
    assert meta["span"] >= 0.0
    assert meta["motion"] > 0.0
    assert "s" in session.counters.line() or session.counters.frames == 4


def test_the_window_is_announced_before_the_request():
    chat = FakeChat(["Seen."])
    published = []
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=3),
        on_window=lambda frames, meta: published.append((len(frames), meta)),
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(8)
    assert published, "the UI must be told what was sent"
    count, meta = published[0]
    assert count == 3
    assert [item["age"] for item in meta["frames"]] == sorted(
        [item["age"] for item in meta["frames"]], reverse=True
    ), "oldest frame first, newest last"


def test_prompt_describes_the_clip_and_its_motion():
    chat = FakeChat(["Fine."])
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=3),
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(8)
    prompt = chat.calls[0]["messages"][0]["content"]
    assert "3 ordered frames" in prompt
    assert "sampled every" in prompt
    assert "Motion across the window" in prompt
    for word in ("still", "low", "medium", "high"):
        if word in prompt:
            break
    else:
        pytest.fail(f"no motion description in: {prompt[:200]}")


def test_a_single_frame_window_says_there_is_no_motion():
    chat = FakeChat(["Fine."])
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=1),
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(8)
    prompt = chat.calls[0]["messages"][0]["content"]
    assert "1 frame" in prompt
    assert "no movement to compare" in prompt


class NoisyScreen:
    """A busy, high-detail picture: JPEG cannot compress it, so payloads are large."""

    def __init__(self, width=320, height=180):
        self.width = width
        self.height = height
        self.tick = 0

    def __call__(self):
        import random

        self.tick += 1
        rnd = random.Random(self.tick)
        row = bytes(rnd.randrange(256) for _ in range(self.width * 4))
        return {
            "sequence": self.tick,
            "width": self.width,
            "height": self.height,
            "stride": self.width * 4,
            "bgra": row * self.height,
        }


def test_payload_cap_drops_the_oldest_frames():
    chat = FakeChat(["Fine."])
    session = LiveVisionSession(
        frame_provider=NoisyScreen(),
        chat=chat,
        settings=VisionSettings(
            **FAST, frames_per_request=6, max_width=320, max_payload_bytes=64_000
        ),
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(10)
    sent = chat.calls[0]["images"]
    total = sum(len(image) for image in sent)
    assert 1 <= len(sent) < 6, "the request must stay under the payload cap"
    assert total <= 64_000, f"kept {total} bytes; the cap is 64 000"
    # The newest frame is the one that matters, so it is never the one dropped.
    assert sent[-1] == chat.calls[0]["images"][-1]
    assert len(sent[-1]) > 1000


def test_a_still_screen_stops_costing_requests():
    """Adaptive mode: the same picture is not re-sent look after look."""
    chat = FakeChat(["Nothing notable."])
    ticks = {"n": 0}

    def still_picture():
        ticks["n"] += 1
        return make_frame(64, 36, value=55, sequence=ticks["n"])  # same picture, new frame

    session = LiveVisionSession(
        frame_provider=still_picture,
        chat=chat,
        settings=VisionSettings(**{**FAST, "adaptive": True}, frames_per_request=3, idle_wait=0.02),
    )

    def stop_when_idle():
        assert wait_for(lambda: session.counters.replies >= 1, timeout=8)
        assert wait_for(lambda: session.counters.idle >= 1, timeout=8)
        session.stop()

    watcher = threading.Thread(target=stop_when_idle, daemon=True)
    watcher.start()
    run_session(session).join(10)
    watcher.join(3)
    assert len(chat.calls) == 1, "a still screen must not be re-sent"
    assert session.counters.skipped >= 1


def test_a_moving_screen_keeps_being_sent():
    chat = FakeChat(["One.", "Two."])
    replies = []
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=2),
        on_reply=lambda text, meta: (
            replies.append(text),
            session.stop() if len(replies) == 2 else None,
        ),
    )
    run_session(session).join(8)
    assert len(chat.calls) == 2
    assert len(chat.calls[1]["images"]) == 2


def test_look_now_takes_a_window_immediately():
    chat = FakeChat(["Looked.", "Looked again."])
    replies = []
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**{**FAST, "min_interval": 30.0}, frames_per_request=2),
        on_reply=lambda text, meta: replies.append((text, meta)),
    )
    thread = run_session(session)
    assert wait_for(lambda: len(replies) == 1)
    session.ask_now()  # must break the 30 s wait
    assert wait_for(lambda: len(replies) == 2, timeout=4)
    session.stop()
    thread.join(4)
    assert replies[0][1]["asked"] == "live"
    assert replies[1][1]["asked"] == "now"
    assert "user asked you to look" in chat.calls[1]["messages"][0]["content"].lower()


def test_ask_now_still_sends_when_the_screen_is_still():
    chat = FakeChat(["Looked."])
    replies = []
    session = LiveVisionSession(
        frame_provider=lambda: make_frame(64, 36, value=30, sequence=1),
        chat=chat,
        settings=VisionSettings(**{**FAST, "adaptive": True}, frames_per_request=2, idle_wait=0.02),
        on_reply=lambda text, meta: (replies.append(meta), session.stop()),
    )
    thread = run_session(session)
    assert wait_for(lambda: len(replies) == 1, timeout=8)
    session.stop()
    thread.join(4)
    assert replies[0]["asked"] == "live" or replies[0]["asked"] == "now"


def test_history_is_included_and_capped():
    chat = FakeChat(["First.", "Second.", "Third."])
    replies = []
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=1, history=2),
        on_reply=lambda text, meta: (
            replies.append(text),
            session.stop() if len(replies) == 3 else None,
        ),
    )
    run_session(session).join(10)
    assert replies == ["First.", "Second.", "Third."]
    third = chat.calls[2]["messages"][0]["content"]
    assert "First." in third and "Second." in third


def test_no_frame_yet_is_reported_and_waits():
    statuses = []
    session = LiveVisionSession(
        frame_provider=lambda: None,
        chat=FakeChat(),
        settings=VisionSettings(**FAST),
    )
    session._on_status = statuses.append
    thread = run_session(session)
    assert wait_for(lambda: any("No frame yet" in text for text in statuses))
    session.stop()
    thread.join(3)
    assert not thread.is_alive()
    stopped_reported = wait_for(
        lambda: any("Live vision stopped" in text for text in statuses), timeout=2
    )
    assert stopped_reported


def test_counters_read_like_a_status_line():
    chat = FakeChat(["Fine."])
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=2),
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(8)
    line = session.counters.line()
    assert "1 look" in line
    assert "2 frames" in line
    assert "1 reply" in line


def test_provider_errors_are_reported_not_raised():
    errors = []

    def provider():
        raise RuntimeError("capture backend died")

    session = LiveVisionSession(
        frame_provider=provider,
        chat=FakeChat(),
        settings=VisionSettings(**FAST),
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
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(
            **FAST, frames_per_request=1, max_consecutive_errors=3, backoff_factor=1.0
        ),
        on_error=errors.append,
        on_status=statuses.append,
    )
    run_session(session).join(8)
    assert len(errors) == 3
    assert any("Stopped after 3 failed requests" in text for text in statuses)
    assert session.counters.errors == 3


def test_empty_reply_is_reported_and_does_not_stall():
    chat = FakeChat(["", "Real answer."])
    replies = []
    statuses = []
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=1),
        on_reply=lambda text, meta: (replies.append(text), session.stop()),
        on_status=statuses.append,
    )
    run_session(session).join(10)
    assert replies == ["Real answer."]
    assert any("empty reply" in text for text in statuses)
    assert session.counters.empty == 1


def test_curve_context_reaches_the_model():
    chat = FakeChat(["Got it."])
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**FAST, frames_per_request=1),
        context_provider=lambda: "In-Game Mode is ON: VALORANT 0.4 at 800 DPI",
        on_reply=lambda text, meta: session.stop(),
    )
    run_session(session).join(8)
    assert "VALORANT" in chat.calls[0]["curve_context"]


def test_settings_are_normalised():
    normalised = VisionSettings(
        frames_per_request=99,
        sample_interval=0.0,
        min_interval=-5,
        idle_motion=3,
        max_width=1,
        quality=999,
        change_threshold=5,
        history=99,
        poll_interval=0,
        max_payload_bytes=1,
        backoff_factor=0.1,
        max_consecutive_errors=0,
    ).normalised()
    assert normalised.frames_per_request == 8
    assert normalised.sample_interval == 0.05
    assert normalised.min_interval == 0.0
    assert normalised.idle_motion == 1.0
    assert normalised.max_width == 64
    assert normalised.quality == 95
    assert normalised.change_threshold == 1.0
    assert normalised.history == 6
    assert normalised.poll_interval > 0
    assert normalised.max_payload_bytes == 64_000
    assert normalised.backoff_factor == 1.0
    assert normalised.max_consecutive_errors == 1


def test_prompts_carry_the_fair_play_line():
    for prompt in (COACH_PROMPT, LOOK_PROMPT):
        text = prompt.lower()
        assert "do not identify targets" in text
        assert "aim" in text
        assert "{frame_label}" in prompt  # formatted per window


def test_stop_before_any_frame_exits_promptly():
    session = LiveVisionSession(
        frame_provider=lambda: None,
        chat=FakeChat(),
        settings=VisionSettings(**{**FAST, "poll_interval": 30.0}),
    )
    thread = run_session(session)
    time.sleep(0.05)
    started = time.monotonic()
    session.stop()
    thread.join(3)
    assert not thread.is_alive()
    assert time.monotonic() - started < 2.0, "stop() must break a long poll wait"


def test_stop_during_a_window_does_not_send():
    chat = FakeChat(["Should not happen."])
    session = LiveVisionSession(
        frame_provider=MovingScreen(),
        chat=chat,
        settings=VisionSettings(**{**FAST, "sample_interval": 0.2}, frames_per_request=8),
    )
    thread = run_session(session)
    time.sleep(0.1)  # mid-window: one or two frames collected, nowhere near eight
    session.stop()
    thread.join(4)
    assert not thread.is_alive()
    assert chat.calls == [], "a stopped window must not reach the model"


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

    def __init__(self, provider=None):
        self.provider = provider or MovingScreen()
        self.native_available = False

    def latest_frame(self):
        return self.provider()


def test_page_shows_the_model_warning_and_starts_off(qapp, saved):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    try:
        page.set_capture_source(FakeBackend())
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
        page.set_capture_source(FakeBackend())
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


def test_clip_controls_default_to_a_multi_frame_window(qapp, saved):
    from fps_coach.ui.pages_coach import AIChatPage

    page = AIChatPage()
    try:
        assert page.live_frames.value() >= 2, "the default look must be a clip, not a snapshot"
        assert page.live_sample.value() > 0
        settings = page._live_vision_settings()
        assert settings.frames_per_request == page.live_frames.value()
        assert settings.sample_interval == pytest.approx(page.live_sample.value())
        assert settings.adaptive is page.live_change_only.isChecked()
    finally:
        page.shutdown()


def test_watching_posts_a_reply_and_fills_the_filmstrip(qapp, saved, monkeypatch):
    from fps_coach.ui import pages_coach

    saved.setValue("ai/model", "gemma3:4b")
    saved.setValue("ai/live_frames", 3)
    saved.setValue("ai/live_sample", 0.1)
    page = pages_coach.AIChatPage()
    fake_chat = FakeChat(["The HUD strip redraws along the bottom edge."])
    monkeypatch.setattr(pages_coach, "AIChatClient", lambda settings: fake_chat)
    page.set_capture_source(FakeBackend())
    try:
        page.live_toggle.setChecked(True)
        assert wait_for_qt(
            lambda: "HUD strip redraws" in page.transcript.toPlainText(), timeout=15
        ), page.transcript.toPlainText()
        text = page.transcript.toPlainText()
        assert "Coach (live)" in text
        assert "3 frames" in text, "the stamp must say the answer came from a clip"
        shown = [slot.pixmap() for slot in page.live_slots if not slot.pixmap().isNull()]
        assert len(shown) == 3, "the filmstrip must show every frame that was sent"
        assert len(fake_chat.calls[0]["images"]) == 3
    finally:
        page.live_toggle.setChecked(False)
        page.shutdown()
    assert page.live_toggle.isChecked() is False
