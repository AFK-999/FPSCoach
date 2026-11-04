"""In-app A/B ghost-cursor simulator with temporary foreground mouse capture."""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from copy import deepcopy

from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QMouseEvent, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach.curve_io import CurveFormatError, CurveProfile, load_curve
from fps_coach.test_mode import ComparisonSimulation, neutral_profile

from .test_mouse_capture import RawMouseCapture
from .widgets import _label


class TestArena(QWidget):
    """Apply each foreground raw mouse packet to both profiles while captured."""

    changed = Signal()
    stopped = Signal()
    capture_lost = Signal(str)
    _TARGET_RADIUS = 20.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(330)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.simulation: ComparisonSimulation | None = None
        self.active = False
        self._capture: RawMouseCapture | None = None
        self._previous_time: float | None = None
        self._last_stats_time = 0.0
        self._rng = random.Random(17)
        self.target = QPointF(100, 100)

    def start(self, a: CurveProfile, b: CurveProfile, reference: float, scale: float = 1.0) -> None:
        if self.active:
            return
        simulation = ComparisonSimulation(a, b, max(1, self.width()), max(1, self.height()), reference, scale)
        if self._capture is None:
            self._capture = RawMouseCapture(self, self._raw_move, self._raw_click, self._capture_lost)
        self._capture.start()  # Fail closed: never start an unconfined session.
        self.active = True
        try:
            self.simulation = simulation
            self._rng = random.Random(17)
            self._previous_time = None
            self._last_stats_time = 0.0
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self._choose_target()
            self.changed.emit()
            self.update()
        except Exception:
            self.stop()
            raise

    def stop(self) -> str | None:
        error = None
        if self.active:
            self.active = False
            if self._capture is not None:
                error = self._capture.stop()
            self._previous_time = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.stopped.emit()
            self.changed.emit()
            self.update()
        return error

    def recenter(self) -> None:
        if self.simulation is not None:
            self.simulation.recenter()
            self._previous_time = None
            self.changed.emit()
            self.update()

    def _choose_target(self) -> None:
        margin = 42.0
        w, h = max(2 * margin, float(self.width())), max(2 * margin, float(self.height()))
        self.target = QPointF(self._rng.uniform(margin, w - margin), self._rng.uniform(margin, h - margin))

    def _raw_move(self, dx: int, dy: int) -> None:
        if not self.active or self.simulation is None:
            return
        now = time.monotonic()
        # Deliver the first packet too; raw input has no per-packet hardware timestamp.
        dt = max(0.0001, now - self._previous_time) if self._previous_time is not None else 1 / 125
        self._previous_time = now
        self.simulation.move(dx, dy, dt)
        # Keep every input packet, but avoid repainting the stats label 1,000 times/s.
        if now - self._last_stats_time >= 1 / 30:
            self._last_stats_time = now
            self.changed.emit()
        self.update()

    def _raw_click(self) -> None:
        if not self.active or self.simulation is None:
            return
        self.simulation.click(self.target.x(), self.target.y(), self._TARGET_RADIUS)
        self._choose_target()
        self.changed.emit()
        self.update()

    def _capture_lost(self, reason: str) -> None:
        error = self.stop()
        self.capture_lost.emit(error or reason)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.active:
            # WM_INPUT already scored the physical press; avoid scoring its Qt duplicate.
            event.accept()
            return
        super().mousePressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.UngrabMouse and getattr(self, "active", False):
            self._capture_lost("Mouse released because mouse capture was lost.")
        return super().event(event)

    def hideEvent(self, event) -> None:
        self.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        if self.active:
            self._capture_lost("Mouse capture released because the test arena was resized.")
        if self.simulation is not None:
            self.simulation.width, self.simulation.height = max(1, self.width()), max(1, self.height())
            self.simulation.positions = [
                (min(self.simulation.width, x), min(self.simulation.height, y))
                for x, y in self.simulation.positions
            ]
            self.target = QPointF(min(max(0, self.target.x()), self.width()), min(max(0, self.target.y()), self.height()))
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#101317"))
        painter.setPen(QPen(QColor("#252b31"), 1))
        for x in range(0, self.width(), 32):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 32):
            painter.drawLine(0, y, self.width(), y)
        if not self.active or self.simulation is None:
            painter.setPen(QColor("#a6adb5"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Start comparison to show both simulated cursors")
            return
        painter.setBrush(QColor("#e5f85a"))
        painter.setPen(QPen(QColor("#e5f85a"), 2))
        painter.drawEllipse(self.target, self._TARGET_RADIUS, self._TARGET_RADIUS)
        painter.setBrush(QColor("#101317"))
        painter.drawEllipse(self.target, self._TARGET_RADIUS - 5, self._TARGET_RADIUS - 5)
        colors = (QColor("#85e9ff"), QColor("#fd9af1"))
        # Different ring sizes remain identifiable even when both cursors overlap.
        for i, (x, y) in enumerate(self.simulation.positions):
            center = QPointF(x, y)
            painter.setPen(QPen(colors[i], 2.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = 9.0 if i == 0 else 14.0
            painter.drawEllipse(center, radius, radius)
            painter.drawLine(QPointF(x - 5, y), QPointF(x + 5, y))
            painter.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))
            painter.drawText(QPointF(x + radius + 3, y - radius), "A" if i == 0 else "B")


class TestModeDialog(QDialog):
    """Non-modal comparison window. Slots are snapshots until explicitly replaced."""

    def __init__(self, current_profile: Callable[[], CurveProfile | None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Test Mode — compare curves")
        self.resize(1040, 720)
        self._current_profile = current_profile
        self.profiles = [neutral_profile(), neutral_profile()]
        self.profile_names = ["Neutral 1×", "Neutral 1×"]
        current = current_profile()
        if current is not None:
            self.profiles[0] = deepcopy(current)
            self.profile_names[0] = f"Current: {current.name}"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(_label("TEST MODE", "eyebrow"))
        title = _label("Compare two curve profiles", "pageTitle")
        layout.addWidget(title)
        description = _label(
            "A is cyan and B is pink; both ghosts receive the same raw mouse movement. "
            "Starting temporarily locks the cursor in this arena. Press Esc to release it; R recenters the ghosts. "
            "Click to score. Capture is released on focus loss or closing.", "cardSubtitle", wrap=True,
        )
        layout.addWidget(description)
        row = QHBoxLayout()
        self.slot_labels: list[QLabel] = []
        for i, letter in enumerate(("A", "B")):
            card = QFrame()
            card.setObjectName("card")
            card_layout = QVBoxLayout(card)
            label = _label("", "cardTitle", wrap=True)
            label.setText(f"{letter} · {self.profile_names[i]}")
            self.slot_labels.append(label)
            card_layout.addWidget(label)
            controls = QHBoxLayout()
            current_button = QPushButton("Use current")
            current_button.setObjectName("secondary")
            current_button.clicked.connect(lambda _checked=False, slot=i: self.use_current(slot))
            import_button = QPushButton("Import profile")
            import_button.setObjectName("secondary")
            import_button.clicked.connect(lambda _checked=False, slot=i: self.import_slot(slot))
            neutral_button = QPushButton("Neutral")
            neutral_button.setObjectName("secondary")
            neutral_button.clicked.connect(lambda _checked=False, slot=i: self.set_slot(slot, neutral_profile(), "Neutral 1×"))
            for button in (current_button, import_button, neutral_button):
                controls.addWidget(button)
            controls.addStretch(1)
            card_layout.addLayout(controls)
            row.addWidget(card, 1)
        layout.addLayout(row)
        bar = QHBoxLayout()
        self.start_btn = QPushButton("Start comparison")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start_comparison)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("secondary")
        self.stop_btn.clicked.connect(self.stop_comparison)
        self.recenter_btn = QPushButton("Recenter ghosts")
        self.recenter_btn.setObjectName("secondary")
        self.recenter_btn.clicked.connect(lambda: self.arena.recenter())
        self.speed_reference = QDoubleSpinBox()
        self.speed_reference.setRange(100, 20000)
        self.speed_reference.setDecimals(0)
        self.speed_reference.setSingleStep(100)
        self.speed_reference.setValue(1800)
        self.speed_reference.setSuffix(" counts/s")
        self.speed_reference.setToolTip("Raw mouse counts per second mapped to the maximum profile speed; delivery timing is approximate.")
        self.speed_reference.valueChanged.connect(self._reference_changed)
        bar.addWidget(self.start_btn)
        bar.addWidget(self.stop_btn)
        bar.addWidget(self.recenter_btn)
        bar.addStretch(1)
        bar.addWidget(_label("Full-speed reference", "formLabel"))
        bar.addWidget(self.speed_reference)
        self.input_scale = QDoubleSpinBox()
        self.input_scale.setRange(0.05, 10.0)
        self.input_scale.setDecimals(2)
        self.input_scale.setSingleStep(0.1)
        self.input_scale.setValue(1.0)
        self.input_scale.setSuffix(" px/count")
        self.input_scale.setToolTip("Pixels per raw count before the selected curve multiplier.")
        self.input_scale.valueChanged.connect(self._reference_changed)
        bar.addWidget(_label("Input scale", "formLabel"))
        bar.addWidget(self.input_scale)
        layout.addLayout(bar)
        self.arena = TestArena()
        self.arena.changed.connect(self._refresh_stats)
        self.arena.stopped.connect(self._refresh_buttons)
        self.arena.capture_lost.connect(self._capture_lost)
        layout.addWidget(self.arena, 1)
        self.stats_label = _label("No attempts yet.", "cardSubtitle", wrap=True)
        layout.addWidget(self.stats_label)
        self.notice = _label(
            "Simulation: speed is mapped to each profile's range and gains are linearly interpolated; "
            "it is not exact Custom Curve or in-game behavior. Windows raw relative mouse input is used "
            "only while the focused test is captured; the OS pointer is temporarily confined. "
            "Press Esc to release. Shared input favors "
            "the ghost you aim at; scores are not a controlled performance comparison. Results are temporary.",
            "cardSubtitle", wrap=True,
        )
        layout.addWidget(self.notice)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self._escape)
        self.recenter_shortcut = QShortcut(QKeySequence("R"), self)
        self.recenter_shortcut.activated.connect(self._recenter_shortcut)
        self._refresh_buttons()

    def _escape(self) -> None:
        if self.arena.active:
            self.stop_comparison()
        else:
            self.close()

    def _recenter_shortcut(self) -> None:
        if self.arena.active:
            self.arena.recenter()

    def _capture_lost(self, reason: str) -> None:
        self.stats_label.setText(reason)
        self._refresh_buttons()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "arena") and self.arena.active:
            self.arena._capture_lost("Mouse released because Test Mode lost focus.")
        return super().event(event)

    def moveEvent(self, event) -> None:
        if hasattr(self, "arena") and self.arena.active:
            self.arena._capture_lost("Mouse released because the Test Mode window moved.")
        super().moveEvent(event)

    def set_slot(self, slot: int, profile: CurveProfile, name: str) -> None:
        self.arena.stop()
        self.profiles[slot] = deepcopy(profile)
        self.profile_names[slot] = str(name)
        self.slot_labels[slot].setText(f"{'AB'[slot]} · {name}")
        self.arena.simulation = None
        self.stats_label.setText("Profile changed; start a new comparison.")
        self.arena.update()
        self._refresh_buttons()

    def use_current(self, slot: int) -> None:
        profile = self._current_profile()
        if profile is None:
            self.stats_label.setText("No current curve yet. Complete calibration or import one first.")
            return
        self.set_slot(slot, profile, f"Current: {profile.name}")

    def import_slot(self, slot: int) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose curve profile", "", "Custom Curves (*.cc4 *.ccurve);;All files (*)")
        if not path:
            return
        try:
            profile = load_curve(path)
        except (CurveFormatError, OSError, ValueError) as exc:
            self.stats_label.setText(f"Could not load profile: {exc}")
            return
        self.set_slot(slot, profile, f"Imported: {profile.name}")

    def _reference_changed(self, _value: float) -> None:
        if self.arena.active:
            self.arena.stop()
            self.stats_label.setText("Reference changed; start a new comparison.")
            self.arena.simulation = None
            self.arena.update()
        self._refresh_buttons()

    def start_comparison(self) -> None:
        try:
            self.arena.start(self.profiles[0], self.profiles[1], self.speed_reference.value(), self.input_scale.value())
        except Exception as exc:
            self.stats_label.setText(f"Mouse capture unavailable: {exc}")
        self._refresh_buttons()

    def stop_comparison(self) -> None:
        error = self.arena.stop()
        self._refresh_buttons()
        if error:
            self.stats_label.setText(error)
        else:
            self._refresh_stats()
            self.stats_label.setText(self.stats_label.text() + "   |   Mouse released; Start resets scores.")

    def _refresh_buttons(self) -> None:
        running = self.arena.active
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.recenter_btn.setEnabled(running)

    def _refresh_stats(self) -> None:
        simulation = self.arena.simulation
        if simulation is None:
            self.stats_label.setText("No attempts yet.")
            return
        parts = []
        for letter, stats, (gx, gy) in zip("AB", simulation.stats, simulation.gains, strict=True):
            mean = f"{stats.mean_error:.1f} px" if stats.attempts else "—"
            parts.append(f"{letter}: {stats.hits}/{stats.attempts} hits · mean error {mean} · gain {gx:.2f}× / {gy:.2f}×")
        self.stats_label.setText("   |   ".join(parts))

    def closeEvent(self, event) -> None:
        self.arena.stop()
        super().closeEvent(event)
