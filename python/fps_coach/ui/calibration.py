from __future__ import annotations

import math
import random
import time
from collections import deque
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QKeySequence, QMouseEvent, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QWidget

from fps_coach.adaptive_difficulty import AdaptiveDifficulty
from fps_coach.performance_telemetry import PerformanceTelemetry

from .styles import STAGE

CALIBRATION_SAMPLE_RATES_HZ = (125, 250, 500, 1000)
DEFAULT_CALIBRATION_SAMPLE_RATE_HZ = 1000
_TRAIL_SECONDS = 0.72  # 90 points at the former 125 Hz default
_REPAINT_INTERVAL_SECONDS = 1 / 60  # collect at the selected rate; only paint at display cadence


class CalibrationCanvas(QWidget):
    """In-app target task; it observes only this explicit calibration session."""

    progress_changed = Signal(int, int)
    completed = Signal(dict, dict)
    recommendation_updated = Signal(dict, dict)
    session_started = Signal()
    session_cancelled = Signal()
    hint_changed = Signal(str)
    raw_resume_changed = Signal(bool)
    performance_started = Signal()
    performance_updated = Signal(dict)
    performance_ended = Signal(bool)  # True: completed; False: cancelled

    def __init__(self, analyzer: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._analyzer = analyzer
        self._performance = PerformanceTelemetry("calibration")
        self._performance_enabled = False
        self._input_mode = "desktop"
        self._raw_capture = None
        self._raw_release_error = None
        self._raw_device = None  # An ephemeral Windows handle, never persisted or transmitted.
        self._raw_waiting_for_motion = False
        self._arena_size_limits = None
        self._raw_pos = QPointF(0, 0)
        self._capture_window = None
        self._rng = random.Random()
        self._target = QPointF(0, 0)
        self._target_radius = 18.0
        self._adaptive_enabled = False
        self._difficulty = AdaptiveDifficulty()
        self._target_direction = QPointF(1, 0)
        self._target_last_time = None
        self._sample_rate_hz = DEFAULT_CALIBRATION_SAMPLE_RATE_HZ
        self._trail: deque[QPointF] = deque(
            maxlen=round(DEFAULT_CALIBRATION_SAMPLE_RATE_HZ * _TRAIL_SECONDS)
        )
        self._last_repaint_at = 0.0
        self._active = False
        self._paused_for_navigation = False
        self._outside_arena = False
        self._completed_trials = 0
        self._total_trials = 12
        self.setMinimumHeight(390)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._release_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._release_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._release_shortcut.activated.connect(self._escape_raw)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._sample_timer = QTimer(self)
        self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sample_timer.setInterval(1000 // self._sample_rate_hz)
        self._sample_timer.timeout.connect(self._sample_cursor)

        self._target_timer = QTimer(self)
        self._target_timer.setInterval(16)
        self._target_timer.timeout.connect(self._advance_target)

        # Ensure the last event in a burst is eventually painted, even if it
        # arrived inside the display-rate throttle interval.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._repaint_timer.timeout.connect(self._flush_repaint)

    def set_performance_enabled(self, enabled: bool) -> None:
        self._performance_enabled = bool(enabled)
        self._performance.reset()
        if enabled and self._active and not self._paused_for_navigation:
            self._performance.begin_target(time.perf_counter())

    @property
    def sample_rate_hz(self) -> int:
        """The requested cursor sampling rate; OS scheduling can make the achieved rate lower."""
        return self._sample_rate_hz

    def set_sample_rate(self, hz: int) -> None:
        """Set a supported target rate and retune the precise timer without stopping a trial."""
        try:
            rate = int(hz)
        except (TypeError, ValueError) as exc:
            raise ValueError("Choose a calibration rate between 125 and 1000 Hz.") from exc
        if rate not in CALIBRATION_SAMPLE_RATES_HZ:
            raise ValueError("Choose a calibration rate between 125 and 1000 Hz.")
        was_active = self._sample_timer.isActive()
        self._sample_rate_hz = rate
        self._sample_timer.setInterval(1000 // rate)
        self._trail = deque(self._trail, maxlen=round(rate * _TRAIL_SECONDS))
        if was_active:
            self._sample_timer.start()

    @property
    def input_mode(self) -> str:
        return self._input_mode

    def set_input_mode(self, mode: str) -> None:
        if mode not in ("desktop", "raw"):
            raise ValueError("Unknown calibration input mode")
        if self._active:
            raise RuntimeError("Cancel calibration before changing input mode")
        self._input_mode = mode
        self.update()

    @property
    def adaptive_enabled(self) -> bool:
        return self._adaptive_enabled

    def set_adaptive_enabled(self, enabled: bool) -> None:
        if self._active:
            raise RuntimeError("Cancel calibration before changing difficulty mode")
        if enabled and not callable(getattr(self._analyzer, "set_target", None)):
            raise RuntimeError("Adaptive targets require an updated analyzer. Rebuild the native extension.")
        self._adaptive_enabled = bool(enabled)
        self._difficulty.reset()
        self._target_radius = self._difficulty.radius if enabled else 18.0
        self.update()

    def _advance_target(self) -> None:
        """Move only within the active in-app task; never poll background input."""
        now = time.perf_counter()
        if (not self._active or self._paused_for_navigation or not self._adaptive_enabled
                or (self._input_mode == "desktop" and
                    (self._outside_arena or not self.isVisible() or not self.window().isActiveWindow()))):
            self._target_last_time = None
            return
        previous, self._target_last_time = self._target_last_time, now
        if previous is None:
            return
        dt = min(.1, max(0.0, now - previous))
        radius = self._target_radius
        xmax, ymax = max(0.0, self.width() - 1.0), max(0.0, self.height() - 1.0)
        left, right = min(radius, xmax / 2), max(xmax - radius, xmax / 2)
        top, bottom = min(radius, ymax / 2), max(ymax - radius, ymax / 2)
        x = self._target.x() + self._target_direction.x() * self._difficulty.speed * dt
        y = self._target.y() + self._target_direction.y() * self._difficulty.speed * dt
        if x < left or x > right:
            self._target_direction.setX(-self._target_direction.x())
            x = max(left, min(right, x))
        if y < top or y > bottom:
            self._target_direction.setY(-self._target_direction.y())
            y = max(top, min(bottom, y))
        self._target = QPointF(x, y)
        self._request_repaint()

    def _raw_device_matches(self, device: int) -> bool:
        # A null handle cannot be tied to a physical source. Reject mixed mice as
        # well; a fresh session is required after switching or reconnecting.
        if not device:
            return False
        if self._raw_device is None:
            self._raw_device = device
        return self._raw_device == device

    def _lock_arena_size(self) -> None:
        if self._arena_size_limits is None:
            self._arena_size_limits = (self.minimumSize(), self.maximumSize())
            self.setFixedSize(self.size())

    def _unlock_arena_size(self) -> None:
        if self._arena_size_limits is not None:
            minimum, maximum = self._arena_size_limits
            self._arena_size_limits = None
            self.setMinimumSize(minimum)
            self.setMaximumSize(maximum)

    def _start_raw_capture(self) -> None:
        from .raw_mouse_capture import RawMouseCapture

        capture = RawMouseCapture(self, self._raw_move, self._raw_click, self._raw_lost,
                                  on_device=self._raw_device_matches, on_waiting=self._raw_waiting)
        # Install the receiver before start: Qt may dispatch native events while
        # creating native windows or acquiring capture.
        self._raw_capture = capture
        try:
            capture.start()  # fails closed; no silent fallback to desktop sampling
        except Exception:
            self._raw_capture = None
            raise
        self._raw_waiting_for_motion = False
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._capture_window = self.window()
        self._capture_window.installEventFilter(self)
        if self._capture_window is not self:
            self.installEventFilter(self)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _release_raw_capture(self) -> str | None:
        if self._capture_window is not None:
            try:
                self._capture_window.removeEventFilter(self)
                if self._capture_window is not self:
                    self.removeEventFilter(self)
            except RuntimeError:
                pass
            self._capture_window = None
        capture, self._raw_capture = self._raw_capture, None
        if capture is not None:
            self.unsetCursor()
            error = capture.stop()
            if error:
                self._raw_release_error = error
            return error
        return None

    def _pause_raw(self, message: str) -> None:
        if not self._active or self._input_mode != "raw":
            return
        error = self._release_raw_capture()
        self._paused_for_navigation = True
        self._performance.end_target()
        self._trail.clear()
        self.raw_resume_changed.emit(self._raw_release_error is None)
        self.hint_changed.emit(self._raw_release_error or error or message)
        self.update()

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt API
        capture = getattr(self, "_raw_capture", None)
        if capture is not None:
            capture.handle_native_event(event_type, message)
        # WM_INPUT must reach the default Windows handler for cleanup.
        return super().nativeEvent(event_type, message)

    def _raw_waiting(self) -> None:
        if self._active and not self._paused_for_navigation and self._input_mode == "raw":
            self._raw_waiting_for_motion = True
            self.hint_changed.emit(
                "No raw movement received yet. Move the mouse; if the cursor remains still, "
                "press Esc, then Reset to unlock Input Path and try Desktop pointer."
            )

    def _raw_lost(self, message: str) -> None:
        if message.startswith("Windows could not release cursor confinement"):
            self._raw_release_error = message
        self._pause_raw(message + " Resume to restart this target, or cancel calibration.")

    def _escape_raw(self) -> None:
        if self._active and self._input_mode == "raw" and not self._paused_for_navigation:
            self._pause_raw("Raw input released. Resume to restart this target, or cancel calibration.")

    def eventFilter(self, watched, event):  # noqa: N802
        if (watched is self._capture_window or watched is self) and event.type() in (
            QEvent.Type.WindowDeactivate, QEvent.Type.Move, QEvent.Type.Resize,
            QEvent.Type.Hide, QEvent.Type.Close,
        ):
            self._pause_raw("Raw input released because the window changed; resume to restart this target.")
        return super().eventFilter(watched, event)

    def resume_raw_capture(self) -> None:
        if (not self._active or self._input_mode != "raw" or not self._paused_for_navigation
                or self._raw_release_error is not None):
            return
        try:
            self._start_raw_capture()
        except Exception as exc:  # noqa: BLE001 - safely expose platform/capture failure
            self.hint_changed.emit(f"Cannot resume raw input: {exc}")
            return
        self._paused_for_navigation = False
        self._raw_pos = QPointF(self.width() / 2, self.height() / 2)
        self._choose_target()
        self._begin_trial()
        self.raw_resume_changed.emit(False)
        self.hint_changed.emit("Raw input resumed; the unfinished target has restarted. Esc releases the cursor.")
        self.update()

    def _raw_move(self, dx: int, dy: int) -> None:
        if not self._active or self._paused_for_navigation or self._input_mode != "raw":
            return
        if self._raw_waiting_for_motion:
            self._raw_waiting_for_motion = False
            self.hint_changed.emit("Raw movement detected. Move the virtual cursor and click the target. Esc releases capture.")
        # 1 delivered count -> 1 logical pixel until the virtual cursor meets
        # the arena edge. No OS pointer acceleration or curve gain is applied.
        self._raw_pos.setX(min(max(0.0, self._raw_pos.x() + dx), max(0.0, self.width() - 1)))
        self._raw_pos.setY(min(max(0.0, self._raw_pos.y() + dy), max(0.0, self.height() - 1)))
        now = time.perf_counter()
        self._analyzer.add_sample(self._raw_pos.x(), self._raw_pos.y(), now)
        if self._performance_enabled:
            self._performance.move(self._raw_pos.x(), self._raw_pos.y(), now)
        self._append_trail(QPointF(self._raw_pos))
        self._request_repaint()

    def _raw_click(self) -> None:
        if self._active and not self._paused_for_navigation and self._input_mode == "raw":
            self._accept_click(QPointF(self._raw_pos), QPointF(self._raw_pos))

    def start_session(self, trials: int = 12) -> None:
        if self._active:
            return
        if self._raw_release_error:
            self.hint_changed.emit(self._raw_release_error)
            return
        # Hints and controls can reflow during either input path. Keep the task
        # geometry stable throughout the run and restore its previous limits.
        self._lock_arena_size()
        if self._input_mode == "raw":
            self._raw_device = None
            try:
                self._start_raw_capture()
            except Exception as exc:  # noqa: BLE001 - report unavailable raw input without falling back
                self._unlock_arena_size()
                self.hint_changed.emit(f"Raw calibration could not start: {exc}")
                return
            self._raw_pos = QPointF(self.width() / 2, self.height() / 2)
        self._analyzer.reset()
        self._performance.reset()
        self._difficulty.reset()
        self._target_radius = self._difficulty.radius if self._adaptive_enabled else 18.0
        self._target_last_time = None
        self._completed_trials = 0
        self._total_trials = max(3, trials)
        self._active = True
        self._paused_for_navigation = False
        self._outside_arena = False
        self._trail.clear()
        self._choose_target()
        self._begin_trial()
        if self._input_mode == "desktop":
            self._outside_arena = not self._inside(self.mapFromGlobal(QCursor.pos()))
            self._sample_timer.start()
        if self._adaptive_enabled:
            self._target_timer.start()
        self.raw_resume_changed.emit(False)
        self.session_started.emit()
        self.performance_started.emit()
        self.progress_changed.emit(0, self._total_trials)
        self.hint_changed.emit(
            "Move the virtual cursor and click the target. Esc releases raw capture."
            if self._input_mode == "raw" else
            "Move naturally and click the mint target. Do not try to be artificially perfect."
        )
        self.update()

    def cancel_session(self) -> None:
        self._active = False
        self._paused_for_navigation = False
        self._outside_arena = False
        self._sample_timer.stop()
        self._target_timer.stop()
        self._target_last_time = None
        error = self._release_raw_capture()
        self._unlock_arena_size()
        self._raw_device = None
        self.raw_resume_changed.emit(False)
        self._analyzer.reset()
        self._performance.end_target()
        self._trail.clear()
        self.session_cancelled.emit()
        self.performance_ended.emit(False)
        self.hint_changed.emit(self._raw_release_error or error or "Calibration cancelled; the previous curve was restored.")
        self.update()

    def pause_for_navigation(self) -> None:
        """Pause sampling off the calibration page; completed targets remain valid."""
        if self._active and not self._paused_for_navigation:
            if self._input_mode == "raw":
                self._pause_raw("Raw input released while away. Return and resume to restart the unfinished target.")
                return
            self._paused_for_navigation = True
            self._performance.end_target()
            self._sample_timer.stop()
            self._outside_arena = True
            self.hint_changed.emit("Calibration paused while you view another page; the current target restarts on return.")

    def resume_after_navigation(self) -> None:
        if self._active and self._paused_for_navigation:
            if self._input_mode == "raw":
                self.hint_changed.emit(self._raw_release_error or "Raw input is paused. Click Resume raw input to restart the unfinished target.")
                self.raw_resume_changed.emit(self._raw_release_error is None)
                return
            self._paused_for_navigation = False
            self._choose_target()
            self._begin_trial()
            self._outside_arena = True  # restart once the pointer re-enters the arena
            self._sample_timer.start()
            self.hint_changed.emit("Calibration resumed. Move into the task area to start the current target again.")
            self.update()

    def _cursor_global(self) -> QPointF:
        point = QCursor.pos()
        return QPointF(float(point.x()), float(point.y()))

    def _target_global(self) -> QPointF:
        # Qt 6 maps floating-point widget coordinates without rounding away
        # the drawn moving target's subpixel center.
        return self.mapToGlobal(QPointF(self._target))

    def _begin_trial(self, event_global: QPointF | None = None, event_local: QPointF | None = None) -> None:
        start = (QPointF(self._raw_pos) if self._input_mode == "raw" else
                 QPointF(event_global) if event_global is not None else self._cursor_global())
        target = QPointF(self._target) if self._input_mode == "raw" else self._target_global()
        now = time.perf_counter()
        self._analyzer.begin_trial(start.x(), start.y(), target.x(), target.y(), now)
        if self._performance_enabled:
            local = (self._raw_pos if self._input_mode == "raw" else event_local
                     if event_local is not None else self.mapFromGlobal(QPoint(round(start.x()), round(start.y()))))
            self._performance.begin_target(now, local.x(), local.y())
        self._trail.clear()
        local_start = (QPointF(start) if self._input_mode == "raw" else QPointF(event_local)
                       if event_local is not None else QPointF(self.mapFromGlobal(QPoint(round(start.x()), round(start.y())))))
        self._trail.append(local_start)

    def _choose_target(self) -> None:
        width, height = max(1, self.width()), max(1, self.height())
        # Respect the actual canvas even in an unusually small window; do not
        # invent a larger arena and place targets outside the reachable area.
        margin_x = min(46.0, max(0.0, (width - 1) / 2.0))
        margin_y = min(46.0, max(0.0, (height - 1) / 2.0))
        cursor_local = self._raw_pos if self._input_mode == "raw" else self.mapFromGlobal(QCursor.pos())
        # Keep a meaningful start-to-target distance even if all random draws
        # are unlucky. This also keeps under-sampled retries recoverable.
        corners = [QPointF(x, y) for x in (margin_x, width - 1 - margin_x)
                   for y in (margin_y, height - 1 - margin_y)]
        candidate = max(corners, key=lambda point: math.hypot(
            point.x() - cursor_local.x(), point.y() - cursor_local.y()))
        best_distance = math.hypot(candidate.x() - cursor_local.x(), candidate.y() - cursor_local.y())
        for _ in range(12):
            drawn = QPointF(
                self._rng.uniform(margin_x, width - 1 - margin_x),
                self._rng.uniform(margin_y, height - 1 - margin_y),
            )
            distance = math.hypot(drawn.x() - cursor_local.x(), drawn.y() - cursor_local.y())
            if distance > 140:
                candidate = drawn
                break
            if distance > best_distance:
                candidate, best_distance = drawn, distance
        self._target = candidate
        if self._adaptive_enabled:
            angle = self._rng.uniform(0, 2 * math.pi)
            self._target_direction = QPointF(math.cos(angle), math.sin(angle))
            self._target_last_time = None

    def _append_trail(self, point: QPointF) -> None:
        """Keep a consistent visual trail duration as the data sample rate changes."""
        if not self._trail or self._trail[-1] != point:
            self._trail.append(point)

    def _request_repaint(self) -> None:
        """Throttle drawing to the screen cadence; sampling continues at 1,000 Hz if selected."""
        now = time.perf_counter()
        remaining = _REPAINT_INTERVAL_SECONDS - (now - self._last_repaint_at)
        if remaining <= 0:
            self._repaint_timer.stop()
            self._flush_repaint(now)
        elif not self._repaint_timer.isActive():
            self._repaint_timer.start(max(1, math.ceil(remaining * 1000)))

    def _flush_repaint(self, now: float | None = None) -> None:
        self._last_repaint_at = time.perf_counter() if now is None else now
        self.update()

    def _sample_cursor(self) -> None:
        if not self._active or self._paused_for_navigation or self._input_mode == "raw":
            return
        if not self.isVisible() or not self.window().isActiveWindow():
            self._outside_arena = True
            return
        point = self._cursor_global()
        local = self.mapFromGlobal(QPoint(round(point.x()), round(point.y())))
        if not self.rect().contains(local):
            self._outside_arena = True
            return
        if self._outside_arena:
            self._outside_arena = False
            self._begin_trial()
            return
        now = time.perf_counter()
        self._analyzer.add_sample(point.x(), point.y(), now)
        if self._performance_enabled:
            self._performance.move(local.x(), local.y(), now)
        self._append_trail(QPointF(local))
        self._request_repaint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active and not self._paused_for_navigation and self._input_mode == "desktop":
            if not self._inside(event.position()):
                self._outside_arena = True
                return super().mouseMoveEvent(event)
            if self._outside_arena:
                self._outside_arena = False
                self._begin_trial(event.globalPosition(), event.position())
                return super().mouseMoveEvent(event)
            global_point = event.globalPosition()
            now = time.perf_counter()
            self._analyzer.add_sample(global_point.x(), global_point.y(), now)
            if self._performance_enabled:
                self._performance.move(event.position().x(), event.position().y(), now)
            self._append_trail(event.position())
            self._request_repaint()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._active and self._input_mode == "desktop":
            self._outside_arena = True
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._input_mode == "raw":
            # Raw button-down is handled in WM_INPUT, so Qt's duplicate must not score.
            return
        if not self._active or self._paused_for_navigation or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if not self._inside(event.position()):
            self._outside_arena = True
            return
        if self._outside_arena:
            self._outside_arena = False
            self._begin_trial(event.globalPosition(), event.position())
        self._accept_click(event.position(), event.globalPosition())

    def _inside(self, point: QPointF) -> bool:
        return 0 <= point.x() < self.width() and 0 <= point.y() < self.height()

    def _accept_click(self, local: QPointF, measured: QPointF) -> None:
        if not self._inside(local):
            return
        distance = math.hypot(local.x() - self._target.x(), local.y() - self._target.y())
        visible_radius = self._target_radius
        observed_target_speed = self._difficulty.speed if self._adaptive_enabled else 0.0
        hit_limit = visible_radius * 1.65
        now = time.perf_counter()
        if self._performance_enabled:
            snapshot = self._performance.click(local.x(), local.y(), self._target.x(), self._target.y(),
                                               self._target_radius, now,
                                               target_speed_px_s=observed_target_speed if self._adaptive_enabled else None)
            if snapshot is not None:
                snapshot["input_path"] = self._input_mode
                snapshot["target_radius_px"] = self._target_radius
                snapshot["task_acceptance_radius_px"] = self._target_radius * 1.65
                snapshot["adaptive_calibration"] = self._adaptive_enabled
                snapshot["moving_target"] = self._adaptive_enabled
                self.performance_updated.emit(snapshot)
        if self._adaptive_enabled:
            self._difficulty.record(distance <= visible_radius)
            self._target_radius = self._difficulty.radius
        if distance > hit_limit:
            self.hint_changed.emit("That click missed the target; continue the same movement and try again.")
            return
        if self._adaptive_enabled:
            target = QPointF(self._target) if self._input_mode == "raw" else self._target_global()
            self._analyzer.set_target(target.x(), target.y())
        accepted = self._analyzer.end_trial(measured.x(), measured.y(), now)
        if not accepted:
            # The rejected endpoint is typically already inside the target.
            # Restarting the same target from that endpoint makes its direct
            # distance nearly zero, so the analyzer can reject every retry.
            # Spawn a fresh target instead; never invent missing raw samples.
            self._choose_target()
            self._begin_trial()
            self.hint_changed.emit("Not enough movement data, or the target was too close. Move to the new target and try again.")
            self.update()
            return
        self._completed_trials += 1
        self.progress_changed.emit(self._completed_trials, self._total_trials)
        summary = dict(self._analyzer.summary())
        summary["input_path"] = self._input_mode
        summary["dpi_telemetry"] = "unavailable"
        summary["adaptive_calibration"] = self._adaptive_enabled
        if self._adaptive_enabled:
            summary["adaptive_target_radius_px"] = visible_radius
            summary["adaptive_target_speed_px_s"] = observed_target_speed
            summary["adaptive_recent_accuracy_pct"] = 100 * self._difficulty.accuracy
        recommendation = dict(self._analyzer.recommend())
        if self._completed_trials >= self._total_trials:
            self._active = False
            self._sample_timer.stop()
            self._target_timer.stop()
            self._target_last_time = None
            error = self._release_raw_capture()
            self._unlock_arena_size()
            self._raw_device = None
            self.raw_resume_changed.emit(False)
            self.hint_changed.emit(self._raw_release_error or error or "Calibration complete. Review the recommendation before changing settings.")
            self._performance.end_target()
            self.completed.emit(summary, recommendation)
            self.performance_ended.emit(True)
            self.update()
            return
        self.recommendation_updated.emit(summary, recommendation)
        self._choose_target()
        self._begin_trial()
        extra = (f" Adaptive: {self._target_radius:.1f}px radius, {self._difficulty.speed:.0f}px/s; "
                 f"recent visible-target accuracy {100 * self._difficulty.accuracy:.0f}%."
                 if self._adaptive_enabled else "")
        self.hint_changed.emit(f"Good. {self._total_trials - self._completed_trials} targets remaining." + extra)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(STAGE["stage_bg"]))

        grid_pen = QPen(QColor(STAGE["stage_grid"]), 1)
        painter.setPen(grid_pen)
        for x in range(24, self.width(), 24):
            painter.drawLine(x, 0, x, self.height())
        for y in range(24, self.height(), 24):
            painter.drawLine(0, y, self.width(), y)

        if len(self._trail) > 1:
            painter.setPen(QPen(QColor(215, 242, 73, 90), 2))
            points = list(self._trail)
            for first, second in zip(points, points[1:], strict=False):
                painter.drawLine(first, second)

        if self._active:
            if self._input_mode == "raw":
                painter.setPen(QPen(QColor("#f3f5f2"), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(self._raw_pos, 5, 5)
            painter.setPen(QPen(QColor(STAGE["stage_accent"]), 2))
            painter.setBrush(QColor(215, 242, 73, 34))
            outer = QRectF(
                self._target.x() - self._target_radius,
                self._target.y() - self._target_radius,
                self._target_radius * 2,
                self._target_radius * 2,
            )
            painter.drawEllipse(outer)
            painter.setBrush(QColor(STAGE["stage_accent"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self._target, 4.0, 4.0)
        else:
            painter.setPen(QColor(STAGE["stage_text"]))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Press Recalibrate to begin the 12-target movement task",
            )
