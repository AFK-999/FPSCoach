from __future__ import annotations

from collections import deque
import math
import random
import time
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .styles import STAGE


CALIBRATION_SAMPLE_RATES_HZ = (125, 250, 500, 1000)
DEFAULT_CALIBRATION_SAMPLE_RATE_HZ = 1000
_TRAIL_SECONDS = 0.72  # 90 points at the former 125 Hz default
_REPAINT_INTERVAL_SECONDS = 1 / 60  # collect at the selected rate; only paint at display cadence


class CalibrationCanvas(QWidget):
    """In-app target task; it observes only this explicit calibration session."""

    progress_changed = Signal(int, int)
    completed = Signal(dict, dict)
    hint_changed = Signal(str)

    def __init__(self, analyzer: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._analyzer = analyzer
        self._rng = random.Random()
        self._target = QPointF(0, 0)
        self._target_radius = 18.0
        self._sample_rate_hz = DEFAULT_CALIBRATION_SAMPLE_RATE_HZ
        self._trail: deque[QPointF] = deque(
            maxlen=round(DEFAULT_CALIBRATION_SAMPLE_RATE_HZ * _TRAIL_SECONDS)
        )
        self._last_repaint_at = 0.0
        self._active = False
        self._completed_trials = 0
        self._total_trials = 12
        self.setMinimumHeight(390)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._sample_timer = QTimer(self)
        self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sample_timer.setInterval(1000 // self._sample_rate_hz)
        self._sample_timer.timeout.connect(self._sample_cursor)

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

    def start_session(self, trials: int = 12) -> None:
        self._analyzer.reset()
        self._completed_trials = 0
        self._total_trials = max(3, trials)
        self._active = True
        self._trail.clear()
        self._choose_target()
        self._begin_trial()
        self._sample_timer.start()
        self.progress_changed.emit(0, self._total_trials)
        self.hint_changed.emit("Move naturally and click the mint target. Do not try to be artificially perfect.")
        self.update()

    def cancel_session(self) -> None:
        self._active = False
        self._sample_timer.stop()
        self._analyzer.reset()
        self._trail.clear()
        self.hint_changed.emit("Calibration reset.")
        self.update()

    def _cursor_global(self) -> QPointF:
        point = QCursor.pos()
        return QPointF(float(point.x()), float(point.y()))

    def _target_global(self) -> QPointF:
        local = QPoint(round(self._target.x()), round(self._target.y()))
        point = self.mapToGlobal(local)
        return QPointF(float(point.x()), float(point.y()))

    def _begin_trial(self) -> None:
        start = self._cursor_global()
        target = self._target_global()
        self._analyzer.begin_trial(
            start.x(), start.y(), target.x(), target.y(), time.perf_counter()
        )
        self._trail.clear()
        local_start = self.mapFromGlobal(QPoint(round(start.x()), round(start.y())))
        self._trail.append(QPointF(local_start))

    def _choose_target(self) -> None:
        margin = 46
        width = max(margin * 2 + 1, self.width())
        height = max(margin * 2 + 1, self.height())
        cursor_local = self.mapFromGlobal(QCursor.pos())
        candidate = QPointF(width / 2, height / 2)
        for _ in range(12):
            candidate = QPointF(
                self._rng.uniform(margin, width - margin),
                self._rng.uniform(margin, height - margin),
            )
            if math.hypot(candidate.x() - cursor_local.x(), candidate.y() - cursor_local.y()) > 140:
                break
        self._target = candidate

    def _append_trail(self, point: QPointF) -> None:
        """Keep a consistent visual trail duration as the data sample rate changes."""
        if not self._trail or self._trail[-1] != point:
            self._trail.append(point)

    def _request_repaint(self) -> None:
        """Throttle drawing to the screen cadence; sampling continues at 1,000 Hz if selected."""
        now = time.perf_counter()
        if now - self._last_repaint_at >= _REPAINT_INTERVAL_SECONDS:
            self._last_repaint_at = now
            self.update()

    def _sample_cursor(self) -> None:
        if not self._active:
            return
        point = self._cursor_global()
        self._analyzer.add_sample(point.x(), point.y(), time.perf_counter())
        local = self.mapFromGlobal(QPoint(round(point.x()), round(point.y())))
        self._append_trail(QPointF(local))
        self._request_repaint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active:
            global_point = event.globalPosition()
            self._analyzer.add_sample(
                global_point.x(), global_point.y(), time.perf_counter()
            )
            self._append_trail(event.position())
            self._request_repaint()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        distance = math.hypot(
            event.position().x() - self._target.x(),
            event.position().y() - self._target.y(),
        )
        if distance > self._target_radius * 1.65:
            self.hint_changed.emit("That click missed the target; continue the same movement and try again.")
            return

        point = event.globalPosition()
        accepted = self._analyzer.end_trial(point.x(), point.y(), time.perf_counter())
        if not accepted:
            self.hint_changed.emit("Not enough movement samples. Try the target once more.")
            self._begin_trial()
            return

        self._completed_trials += 1
        self.progress_changed.emit(self._completed_trials, self._total_trials)
        if self._completed_trials >= self._total_trials:
            self._active = False
            self._sample_timer.stop()
            summary = dict(self._analyzer.summary())
            recommendation = dict(self._analyzer.recommend())
            self.hint_changed.emit("Calibration complete. Review the recommendation before changing settings.")
            self.completed.emit(summary, recommendation)
            self.update()
            return

        self._choose_target()
        self._begin_trial()
        self.hint_changed.emit(f"Good. {self._total_trials - self._completed_trials} targets remaining.")
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
            for first, second in zip(points, points[1:]):
                painter.drawLine(first, second)

        if self._active:
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
                "Press Start calibration to begin the 12-target movement task",
            )
