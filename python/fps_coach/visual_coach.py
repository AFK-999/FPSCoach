"""Live vision: hand the newest capture frame to the AI coach (rev79).

The Capture tab already keeps one fresh frame per display (`mss` or Desktop
Duplication). This module turns that frame into something a model can look at and
runs the loop that keeps doing it while the preview is on:

    newest frame ──► downscale ──► JPEG ──► AI provider ──► coaching reply

Three pieces, none of which import Qt, so they can be tested without a display:

``frame_signature`` / ``frame_delta``
    A 32×18 luma grid sampled straight out of the BGRA buffer (no resize, no
    dependency). Two signatures give a 0..1 "how much changed" figure, which is
    what stops a paused game from being re-sent to the model forever.

``encode_frame``
    BGRA → JPEG, capped at ``max_width``. OpenCV when the recorder extra is
    installed, otherwise Qt's JPEG writer (PySide6 is a hard dependency), so the
    feature works on a plain install too.

``LiveVisionSession``
    The loop. Always uses the *newest* frame — a slow model never builds a queue
    of stale images. Waits for a frame if capture is not running yet, skips
    unchanged frames, backs off when the provider errors, and stops promptly.

What the coach is allowed to do with the picture is defined in
``ai_chat.VISION_SYSTEM_PROMPT``: describe the visible setup, UI, lighting and
picture quality, and advise on those. No target, enemy, aim or recoil analysis —
that boundary is not negotiable, and it is enforced in the prompt rather than
left to the model's discretion.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:  # optional, but normally present with the recorder extra
    import numpy as _np
except ImportError:  # pragma: no cover - exercised by the blocked-import tests
    _np = None

try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover - exercised by the blocked-import tests
    _cv2 = None

#: Signature grid. 32×18 samples is enough to notice a screen change and cheap
#: enough to run several times a second in pure Python.
GRID_W, GRID_H = 32, 18
#: Default cap for the JPEG sent to the model. Bigger costs tokens and changes
#: nothing the coach is allowed to comment on.
DEFAULT_MAX_WIDTH = 768
DEFAULT_QUALITY = 70


class VisualError(RuntimeError):
    """Base class for vision-pipeline failures."""


class FrameError(VisualError):
    """The frame dict handed in is unusable."""


class VisualEncodeError(VisualError):
    """No available backend could encode the frame."""


@dataclass(slots=True)
class EncodedFrame:
    """A frame ready to send, plus what it took to get there."""

    jpeg: bytes
    width: int
    height: int
    source_width: int
    source_height: int
    quality: int

    @property
    def scale(self) -> float:
        return self.width / self.source_width if self.source_width else 1.0

    def as_meta(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "bytes": len(self.jpeg),
            "quality": self.quality,
        }


def _unpack(frame: Any) -> tuple[int, int, int, bytes]:
    """Validate a capture frame dict and return ``(width, height, stride, bgra)``."""
    if not isinstance(frame, dict):
        raise FrameError("frame must be the dict the capture backend returns")
    try:
        width = int(frame["width"])
        height = int(frame["height"])
        data = frame["bgra"]
    except (KeyError, TypeError, ValueError) as exc:
        raise FrameError("frame is missing width/height/bgra") from exc
    stride = int(frame.get("stride") or width * 4)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise FrameError("frame bgra must be bytes")
    data = bytes(data)
    if width <= 0 or height <= 0:
        raise FrameError(f"frame has invalid size {width}x{height}")
    if stride < width * 4:
        raise FrameError(f"stride {stride} is too small for width {width}")
    if len(data) < stride * height:
        raise FrameError(
            f"frame buffer is {len(data)} bytes; {stride * height} are needed for {width}x{height}"
        )
    return width, height, stride, data


def frame_signature(frame: Any) -> bytes:
    """A 32×18 luma thumbnail sampled directly from the BGRA buffer.

    No resize and no dependency: one pixel per cell, centred in its cell. Only
    used to answer "did the picture change?", never shown to the user.
    """
    width, height, stride, data = _unpack(frame)
    step_x = max(1, width // GRID_W)
    step_y = max(1, height // GRID_H)
    out = bytearray(GRID_W * GRID_H)
    index = 0
    for row in range(GRID_H):
        y = min(height - 1, row * step_y + step_y // 2)
        base = y * stride
        for column in range(GRID_W):
            x = min(width - 1, column * step_x + step_x // 2)
            offset = base + x * 4
            blue = data[offset]
            green = data[offset + 1]
            red = data[offset + 2]
            out[index] = (red * 77 + green * 150 + blue * 29) >> 8
            index += 1
    return bytes(out)


def frame_delta(previous: bytes, current: bytes) -> float:
    """Mean absolute luma difference between two signatures, 0.0 .. 1.0."""
    if not previous or not current or len(previous) != len(current):
        return 1.0
    total = 0
    for a, b in zip(previous, current, strict=False):
        total += abs(a - b)
    return total / (len(previous) * 255.0)


def describe_frame(frame: Any) -> str:
    """A short, honest description of the picture's statistics.

    Sent alongside the image. It is deliberately about the *picture* (size,
    brightness, contrast, colour) and never about its contents.
    """
    signature = frame_signature(frame)
    width, height, _stride, _data = _unpack(frame)
    mean = sum(signature) / len(signature)
    low = min(signature)
    high = max(signature)
    if mean < 55:
        tone = "dark"
    elif mean < 120:
        tone = "mid-tone"
    else:
        tone = "bright"
    if high - low < 30:
        flatness = "very flat (little contrast)"
    elif high - low < 90:
        flatness = "moderate contrast"
    else:
        flatness = "high contrast"
    return (
        f"{width}x{height}; brightness {mean:.0f}/255 ({tone}); {flatness} "
        f"(range {low}-{high}); luma spread {high - low}/255."
    )


def encode_frame(
    frame: Any,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    quality: int = DEFAULT_QUALITY,
) -> EncodedFrame:
    """BGRA capture frame → downscaled JPEG.

    OpenCV first (fast and exact), then Qt's writer. Raises
    :class:`VisualEncodeError` when neither is available, which in practice means
    the app was built without PySide6 — impossible for this application, but the
    error says what to install rather than failing obscurely.
    """
    width, height, stride, data = _unpack(frame)
    quality = max(10, min(95, int(quality)))
    max_width = max(64, int(max_width))
    scale = min(1.0, max_width / float(width))
    target_w = max(1, int(round(width * scale)))
    target_h = max(1, int(round(height * scale)))

    if _cv2 is not None and _np is not None:
        buffer = _np.frombuffer(data, dtype=_np.uint8, count=stride * height)
        picture = buffer.reshape(height, stride)[:, : width * 4].reshape(height, width, 4)
        gray_or_bgr = picture[:, :, :3]  # BGRA -> BGR, no copy
        if (target_w, target_h) != (width, height):
            gray_or_bgr = _cv2.resize(
                gray_or_bgr, (target_w, target_h), interpolation=_cv2.INTER_AREA
            )
        ok, encoded = _cv2.imencode(
            ".jpg", gray_or_bgr, [int(_cv2.IMWRITE_JPEG_QUALITY), quality]
        )
        if not ok or encoded is None:
            raise VisualEncodeError("OpenCV could not encode this frame as JPEG")
        return EncodedFrame(
            jpeg=bytes(encoded.tobytes()),
            width=target_w,
            height=target_h,
            source_width=width,
            source_height=height,
            quality=quality,
        )

    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage
    except ImportError as exc:  # pragma: no cover - PySide6 is a hard dependency
        raise VisualEncodeError(
            "No JPEG encoder is available. Install the recorder extra "
            '(pip install -e ".[recorder]") or run inside the app (PySide6).'
        ) from exc

    # QImage does not copy the buffer, so `data` must stay alive until save() returns.
    image = QImage(data, width, height, stride, QImage.Format.Format_ARGB32)
    if image.isNull():
        raise FrameError("Qt could not wrap this frame; the stride or size is wrong")
    if (target_w, target_h) != (width, height):
        image = image.scaled(
            target_w,
            target_h,
            _qt_aspect(),
            _qt_smooth(),
        )
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        saved = image.save(buffer, "JPEG", quality)
    finally:
        buffer.close()
    if not saved:
        raise VisualEncodeError("Qt could not encode this frame as JPEG")
    return EncodedFrame(
        jpeg=bytes(payload),
        width=target_w,
        height=target_h,
        source_width=width,
        source_height=height,
        quality=quality,
    )


def _qt_aspect():
    from PySide6.QtCore import Qt

    return Qt.AspectRatioMode.IgnoreAspectRatio


def _qt_smooth():
    from PySide6.QtCore import Qt

    return Qt.TransformationMode.SmoothTransformation


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

#: What the model is asked each time a frame goes out. Short on purpose: every
#: extra sentence is paid for on every frame.
COACH_PROMPT = (
    "A live still frame from the user's own capture preview is attached.\n"
    "Frame statistics: {description}\n"
    "{history}"
    "Reply with at most two sentences of coaching observation about what this frame shows: "
    "the setup or picture itself (layout, HUD clutter, lighting, whether the image looks "
    "correct or washed out, what screen or mode appears to be open). "
    "Do not identify targets, enemies, players, or crosshair placement, and do not give aim, "
    "recoil, or movement guidance. If the frame shows nothing you can comment on, say so in "
    "one sentence."
)

#: Same picture, different question: the user pressed "Look at this now".
LOOK_PROMPT = (
    "The user asked you to look at their capture preview. A still frame is attached.\n"
    "Frame statistics: {description}\n"
    "{history}"
    "Answer in at most three sentences. Describe what the frame shows about the setup or "
    "picture (screen, mode, layout, readability, HUD clutter, lighting) and what you would "
    "change, if anything. Do not identify targets, enemies, or players, and do not give aim, "
    "recoil, or movement guidance."
)


@dataclass(slots=True)
class VisionSettings:
    """Everything the loop needs, so it can be constructed from QSettings."""

    min_interval: float = 6.0
    """Seconds to wait after a reply before asking again."""

    max_width: int = DEFAULT_MAX_WIDTH
    quality: int = DEFAULT_QUALITY
    skip_unchanged: bool = True
    change_threshold: float = 0.004
    """Mean luma change (0..1) below which a frame counts as unchanged: 0.4%."""

    history: int = 2
    """How many previous comments to remind the model of, so it does not repeat itself."""

    poll_interval: float = 0.2
    """How often to look for a new frame while waiting."""

    backoff_factor: float = 2.0
    max_backoff: float = 60.0
    max_consecutive_errors: int = 5

    def normalised(self) -> VisionSettings:
        return VisionSettings(
            min_interval=max(0.0, float(self.min_interval)),
            max_width=max(64, int(self.max_width)),
            quality=max(10, min(95, int(self.quality))),
            skip_unchanged=bool(self.skip_unchanged),
            change_threshold=max(0.0, min(1.0, float(self.change_threshold))),
            history=max(0, min(6, int(self.history))),
            poll_interval=max(0.02, float(self.poll_interval)),
            backoff_factor=max(1.0, float(self.backoff_factor)),
            max_backoff=max(1.0, float(self.max_backoff)),
            max_consecutive_errors=max(1, int(self.max_consecutive_errors)),
        )


@dataclass(slots=True)
class VisionCounters:
    sent: int = 0
    replies: int = 0
    skipped: int = 0
    errors: int = 0
    empty: int = 0
    last_latency: float = 0.0

    def line(self) -> str:
        if not self.sent:
            return "Waiting for the first frame."
        parts = [f"{self.sent} frame{'s' if self.sent != 1 else ''} sent"]
        parts.append(f"{self.replies} repl{'ies' if self.replies != 1 else 'y'}")
        if self.skipped:
            parts.append(f"{self.skipped} unchanged skipped")
        if self.last_latency:
            parts.append(f"last {self.last_latency:.1f} s")
        return " · ".join(parts)


class LiveVisionSession:
    """Streams the newest capture frame to a chat client, one reply at a time.

    Constructed with plain callables so it can be unit-tested without Qt, a
    display, or a model:

    ``frame_provider``
        ``() -> frame dict | None``; the capture backend's ``latest_frame``.
    ``chat``
        Anything shaped like :class:`fps_coach.ai_chat.AIChatClient` — it needs
        ``chat(messages, curve_context=..., images=...)``.
    ``context_provider``
        ``() -> str``; the same calibration / in-game context the text chat uses.

    ``on_reply``, ``on_error``, ``on_status`` and ``on_frame`` are called from
    the worker thread. The Qt layer wraps them in signals.
    """

    def __init__(
        self,
        *,
        frame_provider: Callable[[], Any],
        chat: Any,
        settings: VisionSettings | None = None,
        context_provider: Callable[[], str] | None = None,
        on_reply: Callable[[str, dict[str, Any]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_frame: Callable[[bytes, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._frame_provider = frame_provider
        self._chat = chat
        self._settings = (settings or VisionSettings()).normalised()
        self._context_provider = context_provider or (lambda: "")
        self._on_reply = on_reply
        self._on_error = on_error
        self._on_status = on_status
        self._on_frame = on_frame
        self._clock = clock

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force = threading.Event()
        self._lock = threading.Lock()
        self._history: deque[str] = deque(maxlen=self._settings.history)
        self._previous_signature: bytes | None = None
        self._last_sequence: int | None = None
        self._counters = VisionCounters()
        self._waiting_reported = False

    # -- control ----------------------------------------------------------
    @property
    def counters(self) -> VisionCounters:
        """A snapshot; the loop keeps mutating its own copy."""
        with self._lock:
            current = self._counters
            return VisionCounters(
                sent=current.sent,
                replies=current.replies,
                skipped=current.skipped,
                errors=current.errors,
                empty=current.empty,
                last_latency=current.last_latency,
            )

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        """Ask the loop to finish; safe from any thread and repeatable."""
        self._stop.set()
        self._wake.set()

    def ask_now(self) -> None:
        """Send the next frame even if the picture has not changed."""
        self._force.set()
        self._wake.set()

    # -- the loop ---------------------------------------------------------
    def run(self) -> None:
        """Blocking loop; call it from a worker thread."""
        consecutive_errors = 0
        while not self._stop.is_set():
            frame, state = self._poll_frame()

            if state == "error":
                self._wait(self._settings.poll_interval)
                continue

            if state == "waiting":
                # No capture yet: say so once, then keep polling quietly.
                self._report("No frame yet — start the preview on the Capture tab.")
                self._wait(self._settings.poll_interval)
                continue

            self._waiting_reported = False
            forced = self._force.is_set()
            if forced:
                self._force.clear()
            if state == "same" and not forced:
                with self._lock:
                    self._counters.skipped += 1
                self._wait(self._settings.poll_interval)
                continue

            try:
                signature = frame_signature(frame)
            except FrameError as exc:
                self._fail(f"Could not read the preview frame: {exc}")
                self._wait(self._settings.min_interval)
                continue

            if (
                self._settings.skip_unchanged
                and not forced
                and self._previous_signature is not None
                and frame_delta(self._previous_signature, signature) < self._settings.change_threshold
            ):
                with self._lock:
                    self._counters.skipped += 1
                self._wait(self._settings.poll_interval)
                continue

            try:
                encoded = encode_frame(
                    frame, max_width=self._settings.max_width, quality=self._settings.quality
                )
            except VisualError as exc:
                self._fail(str(exc))
                self._wait(self._settings.min_interval)
                continue

            # Taken: a newer frame arriving during the request is what gets sent next.
            self._last_sequence = frame.get("sequence") if isinstance(frame, dict) else None
            if self._on_frame is not None:
                self._safe(self._on_frame, encoded.jpeg, encoded.as_meta())

            prompt = (LOOK_PROMPT if forced else COACH_PROMPT).format(
                description=describe_frame(frame), history=self._history_text()
            )
            started = self._clock()
            with self._lock:
                self._counters.sent += 1
            try:
                answer = self._chat.chat(
                    [{"role": "user", "content": prompt}],
                    curve_context=self._context_provider(),
                    images=[encoded.jpeg],
                )
            except Exception as exc:  # noqa: BLE001 - any provider failure is reported
                with self._lock:
                    self._counters.errors += 1
                consecutive_errors += 1
                # Let the same picture be retried rather than waiting for a new one.
                self._last_sequence = None
                self._fail(str(exc) or exc.__class__.__name__)
                if consecutive_errors >= self._settings.max_consecutive_errors:
                    self._report(
                        f"Stopped after {consecutive_errors} failed requests. Check the AI "
                        "settings, then start watching again."
                    )
                    break
                self._wait(self._backoff(consecutive_errors))
                continue

            consecutive_errors = 0
            self._previous_signature = signature
            latency = max(0.0, self._clock() - started)
            text = str(answer or "").strip()
            with self._lock:
                self._counters.replies += 1
                self._counters.last_latency = latency
                if not text:
                    self._counters.empty += 1
            if not text:
                self._report("The model returned an empty reply; is it a vision model?")
            else:
                self._history.append(text)
                meta = {
                    "latency": latency,
                    "width": encoded.width,
                    "height": encoded.height,
                    "frame": self.counters.sent,
                    "asked": "now" if forced else "live",
                }
                if self._on_reply is not None:
                    self._safe(self._on_reply, text, meta)
            self._wait(self._settings.min_interval)

        self._report("Live vision stopped.")

    # -- helpers ----------------------------------------------------------
    def _backoff(self, failures: int) -> float:
        delay = self._settings.min_interval * (self._settings.backoff_factor ** max(0, failures - 1))
        return min(self._settings.max_backoff, max(self._settings.min_interval, delay))

    def _history_text(self) -> str:
        if not self._history:
            return ""
        lines = "\n".join(f"- {item}" for item in self._history)
        return f"Your previous comments, newest last (do not repeat them):\n{lines}\n"

    def _poll_frame(self) -> tuple[Any, str]:
        """One look at the capture backend.

        Returns ``(frame, state)`` with state one of ``ok``, ``same`` (the frame
        we already handled), ``waiting`` (no capture running) or ``error``.
        """
        try:
            frame = self._frame_provider()
        except Exception as exc:  # noqa: BLE001 - a broken backend must not kill the loop
            self._fail(f"Could not read the capture: {exc}")
            return None, "error"
        if not isinstance(frame, dict) or not frame.get("bgra"):
            return None, "waiting"
        sequence = frame.get("sequence")
        if (
            self._settings.skip_unchanged
            and sequence is not None
            and sequence == self._last_sequence
        ):
            return frame, "same"
        return frame, "ok"

    def _wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        # Wakes early on stop() or ask_now(), both of which set _wake.
        self._wake.wait(seconds)
        self._wake.clear()

    def _report(self, message: str) -> None:
        if self._on_status is not None:
            self._safe(self._on_status, message)

    def _fail(self, message: str) -> None:
        if self._on_error is not None:
            self._safe(self._on_error, message)

    @staticmethod
    def _safe(callback: Callable[..., None], *args: Any) -> None:
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - a UI callback must never kill the loop
            pass


def suggested_vision_models() -> tuple[str, ...]:
    """Small vision models that work in Ollama and fit a gaming PC."""
    return ("gemma3:4b", "qwen2.5vl:3b", "llava:7b", "moondream")
