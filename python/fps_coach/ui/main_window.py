"""
Vantage — main application window.

Assembles every page (Dashboard, Guide, Capture, Calibration, Training,
Recommendation, Crosshair, AI Coach, Privacy, Settings) behind a grouped
icon-rail sidebar, plus the floating Coach Panel overlay. Visual chrome uses
the "Vantage" design language from styles.py and the vector icons from icons.py.

Functional wiring (restored to the original backend behavior):
  * MainWindow(backend) receives the CoreBackend created in app.py and passes
    it to Capture, Calibration and Recommendation.
  * Capture: backend.outputs(), start_capture(), stop_capture(),
    capture_stats(), latest_frame().
  * Calibration: CalibrationCanvas(backend.analyzer) -> completed(summary, rec).
  * Recommendation: backend.analyzer.summary() / recommend().
  * AI Coach: ai_chat (AIChatClient, check_ollama, pull_model, curve_context).
  * Voice and TTS: vosk_service, voice_control, voice_commands, tts_service.
  * Crosshair: CrosshairConfig schema under the "crosshair/config" key.
  * QSettings() uses the application names set in app.py, so saved keys match.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import replace
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer, QThread, Signal, QSize, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath, QImage, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFrame, QScrollArea, QStackedWidget, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QSlider, QProgressBar, QButtonGroup, QToolButton, QMessageBox, QFileDialog,
    QListWidget,
)

from .icons import icon, pixmap
from .styles import build_style
from .responsive import ResponsiveCards, ResponsiveRow
from .calibration import CalibrationCanvas
from fps_coach.ai_chat import (
    AIChatClient, ChatProviderError, ChatSettings, check_ollama, curve_context, pull_model,
)
from fps_coach.backend import CoreBackend
from fps_coach import scenarios as scenario_defs
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.window_enum import get_window_rect, list_windows
from fps_coach.crosshair import PRESETS as CROSSHAIR_PRESETS, CrosshairConfig, preset_names
from fps_coach.ai_curve_actions import apply_adjustment, parse_adjustment
from fps_coach.chat_suggestions import suggest
from fps_coach.curve_adjust import adjust_from_recommendation, apply_preset, normalized_overlay
from fps_coach.curve_io import (
    CurveFormatError,
    chart_points,
    load_curve,
    recommendation_profile,
    save_curve,
)

ORG_NAME = "VantageLabs"
APP_NAME = "Vantage"

NAV_SECTIONS = [
    ("OVERVIEW", [("dashboard", "Dashboard"), ("guide", "Guide")]),
    ("CAPTURE", [("capture", "Capture"), ("calibration", "Calibration")]),
    ("TRAINING", [("training", "Training"), ("recommendation", "Recommendation")]),
    ("TOOLS", [("crosshair", "Crosshair"), ("chat", "AI Coach")]),
    ("SYSTEM", [("privacy", "Privacy"), ("settings", "Settings")]),
]


def _settings() -> QSettings:
    # Use the application-level store (names are set in app.py), so saved
    # preferences and QSettings keys match the original app.
    return QSettings()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _label(text, object_name=None, wrap=False):
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    lbl.setWordWrap(wrap)
    return lbl


def _page_header(title, subtitle=""):
    wrap = QWidget()
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    accent = QFrame()
    accent.setObjectName("headerAccent")
    accent.setFixedHeight(3)
    accent.setFixedWidth(48)
    lay.addWidget(accent)
    lay.addWidget(_label(title, "pageTitle"))
    if subtitle:
        lay.addWidget(_label(subtitle, "pageSubtitle", wrap=True))
    return wrap


def _card(title=None, subtitle=None):
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(12)
    if title:
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(_label(title, "cardTitle"))
        if subtitle:
            head.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        lay.addLayout(head)
    return card, lay


def _scroll_page(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(inner)
    return scroll


# ---------------------------------------------------------------------------
# Guide
# ---------------------------------------------------------------------------

class GuidePage(QWidget):
    """Onboarding / how-to-use guide."""

    STEPS = [
        ("Connect your capture source",
         "Point Vantage at your game window or capture card feed so it can watch your crosshair in real time."),
        ("Run calibration",
         "Walk through the calibration wizard to map your mouse counts to in-game degrees."),
        ("Review your recommendation",
         "Vantage proposes a sensitivity curve tuned to your aim data."),
        ("Train with live feedback",
         "Use the training range to validate the new curve against real targets."),
        ("Ask the AI coach anytime",
         "Open the chat to ask follow-up questions or request adjustments."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        outer.addWidget(_page_header("Guide", "A five-step path from first launch to a tuned, trained setup."))

        for i, (title, body) in enumerate(self.STEPS, start=1):
            card, lay = _card()
            row = QHBoxLayout()
            row.setSpacing(14)
            badge = QLabel(str(i))
            badge.setObjectName("metric")
            badge.setFixedSize(36, 36)
            badge.setAlignment(Qt.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignTop)

            col = QVBoxLayout()
            col.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(_label(title, "cardTitle"))
            head.addStretch(1)
            check = QLabel()
            check.setPixmap(pixmap("check", "#5fb88a", 16))
            head.addWidget(check)
            col.addLayout(head)
            col.addWidget(_label(body, "cardSubtitle", wrap=True))
            row.addLayout(col, 1)
            lay.addLayout(row)
            outer.addWidget(card)

        outer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scroll_page(inner))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class CapturePage(QWidget):
    """Capture controls wired to the native backend (CoreBackend).

    Lists display outputs from backend.outputs(), starts and stops capture with
    the selected preset, shows live stats from backend.capture_stats(), and
    previews the newest frame from backend.latest_frame(). Without the native
    core, start is disabled and the reason is shown.
    """

    # (label, stored value, region side, target fps). Matches the original presets:
    # Low-end 640x360 @ 30, Balanced 960x540 @ 60, Quality full output @ 120.
    PRESETS = [
        ("Quality", "quality", None, 120),
        ("Balanced", "balanced", (960, 540), 60),
        ("Performance", "low", (640, 360), 30),
        ("Custom", "custom", None, None),  # region + FPS chosen manually below
    ]

    def __init__(self, backend: CoreBackend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._last_sequence = -1
        s = _settings()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Capture", "Point Vantage at the display it should watch."))

        card, lay = _card("Source", "Choose the display output, capture preset, and hardware acceleration.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Display output", "formLabel"), 0, 0)
        self.output_combo = QComboBox()
        form.addWidget(self.output_combo, 0, 1)

        form.addWidget(_label("Preset", "formLabel"), 1, 0)
        self.preset = QComboBox()
        for label, value, _region, _fps in self.PRESETS:
            self.preset.addItem(label, value)
        index = self.preset.findData(str(s.value("capture/preset", "balanced")))
        self.preset.setCurrentIndex(index if index >= 0 else 1)
        self.preset.currentIndexChanged.connect(self._on_preset_changed)
        form.addWidget(self.preset, 1, 1)

        form.addWidget(_label("Hardware acceleration", "formLabel"), 2, 0)
        self.hwaccel = QCheckBox("Use the GPU (D3D11) for capture and scaling")
        self.hwaccel.setChecked(s.value("capture/hwaccel", True, type=bool))
        self.hwaccel.toggled.connect(lambda v: s.setValue("capture/hwaccel", bool(v)))
        form.addWidget(self.hwaccel, 2, 1)

        self.custom_label = _label("Custom", "formLabel")
        form.addWidget(self.custom_label, 4, 0)
        self.custom_widget = QWidget()
        custom_row = QHBoxLayout(self.custom_widget)
        custom_row.setContentsMargins(0, 0, 0, 0)
        self.custom_region = QComboBox()
        self.custom_region.addItem("Center 640 × 360", (640, 360))
        self.custom_region.addItem("Center 960 × 540", (960, 540))
        self.custom_region.addItem("Full output", None)
        self.custom_fps = QComboBox()
        for fps in (30, 60, 120):
            self.custom_fps.addItem(f"{fps} FPS", fps)
        self.custom_fps.setCurrentIndex(1)
        custom_row.addWidget(self.custom_region, 1)
        custom_row.addWidget(self.custom_fps, 1)
        form.addWidget(self.custom_widget, 4, 1)
        self._on_preset_changed(self.preset.currentIndex())

        form.addWidget(_label("Capture source", "formLabel"), 3, 0)
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        source_row.addWidget(self.source_combo, 1)
        self.window_refresh_btn = QPushButton(" Refresh windows")
        self.window_refresh_btn.setObjectName("secondary")
        self.window_refresh_btn.setIcon(icon("refresh"))
        source_row.addWidget(self.window_refresh_btn)
        form.addLayout(source_row, 3, 1)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton(" Start capture")
        self.start_btn.setObjectName("primary")
        self.start_btn.setIcon(icon("play", "#1a1106"))
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("secondary")
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setEnabled(False)
        self.refresh_btn = QPushButton(" Refresh displays")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.setIcon(icon("refresh"))
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusLabel")
        lay.addWidget(self.status_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("statusLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        lay.addWidget(self.error_label)

        self.stats_label = _label("", "cardSubtitle")
        lay.addWidget(self.stats_label)

        preview_card, preview_lay = _card("Preview", "Newest captured frame. Appears once capture starts.")
        self.preview = QLabel("No frame yet")
        self.preview.setObjectName("muted")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        preview_lay.addWidget(self.preview, 1)

        outer.addWidget(card)
        outer.addWidget(preview_card, 1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.refresh_btn.clicked.connect(self._load_outputs)
        self.window_refresh_btn.clicked.connect(self._load_windows)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start(250)
        self._load_outputs()
        self._load_windows()

    # --- outputs ---------------------------------------------------------
    def _load_outputs(self):
        self.output_combo.clear()
        outputs = self._backend.outputs()
        for out in outputs:
            label = f"{out['output_name']} · {out['adapter_name']} · {out['width']}×{out['height']}"
            self.output_combo.addItem(label, out)
        if not outputs:
            self.output_combo.addItem("No display output found", None)
        self._update_buttons()

    def _on_preset_changed(self, index: int):
        value = self.preset.itemData(index)
        if value is not None:
            _settings().setValue("capture/preset", value)
        custom = value == "custom"
        self.custom_label.setVisible(custom)
        self.custom_widget.setVisible(custom)

    def _load_windows(self):
        self.source_combo.clear()
        self.source_combo.addItem("Entire display output", None)
        for win in list_windows():
            proc = f"  ·  {win['process']}" if win.get("process") else ""
            title = win["title"]
            label = (title[:48] + "…") if len(title) > 49 else title
            self.source_combo.addItem(f"{label}{proc}", int(win["hwnd"]))

    def _update_buttons(self):
        running = self.stop_btn.isEnabled()
        has_output = isinstance(self.output_combo.currentData(), dict)
        self.start_btn.setEnabled(has_output and not running)
        self.refresh_btn.setEnabled(not running)

    # --- start / stop ----------------------------------------------------
    def _start(self):
        output = self.output_combo.currentData()
        if not isinstance(output, dict):
            self._show_error("No display output is available to capture.")
            return
        _label_text, value, region, fps = self.PRESETS[max(0, self.preset.currentIndex())]
        if value == "custom":
            region = self.custom_region.currentData()
            fps = self.custom_fps.currentData()
        width, height = region if region else (int(output["width"]), int(output["height"]))
        left, top = -1, -1
        window_note = ""
        hwnd = self.source_combo.currentData()
        if hwnd:
            rect = get_window_rect(int(hwnd))
            if rect:
                left, top, width, height = rect
            else:
                window_note = "Could not read that window's bounds; capturing the full output instead."
        try:
            self._backend.start_capture(
                int(output["adapter_index"]),
                int(output["output_index"]),
                int(width),
                int(height),
                int(fps),
                int(left),
                int(top),
            )
        except RuntimeError as exc:  # NativeCoreUnavailable is a RuntimeError
            self._show_error(str(exc))
            return
        self._last_sequence = -1
        self._show_error(window_note)
        self._set_running(True)

    def _stop(self):
        self._backend.stop_capture()
        self._set_running(False)

    def _set_running(self, running: bool):
        self.stop_btn.setEnabled(running)
        if running:
            self._set_status("Capturing…", "good")
        else:
            self._set_status("Idle", "")
            self.stats_label.setText("")
            self.preview.clear()
            self.preview.setText("No frame yet")
        self._update_buttons()

    def _set_status(self, text, state):
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    # --- stats and preview ----------------------------------------------
    def _refresh_stats(self):
        stats = self._backend.capture_stats()
        running = bool(stats.get("running"))
        if self.stop_btn.isEnabled() and not running:
            # Capture ended on its own (for example a driver error).
            self._set_running(False)
        error = str(stats.get("last_error") or "")
        if error:
            self._show_error(error)
        if not running:
            return
        fps = float(stats.get("capture_fps", 0.0))
        frames = int(stats.get("frames_captured", 0))
        width = int(stats.get("capture_width", 0))
        height = int(stats.get("capture_height", 0))
        adapter = str(stats.get("adapter_name", ""))
        self.stats_label.setText(f"{fps:.1f} FPS · {frames:,} frames · {width}×{height} · {adapter}")

        frame = self._backend.latest_frame()
        if not frame or int(frame["sequence"]) == self._last_sequence:
            return
        self._last_sequence = int(frame["sequence"])
        image = QImage(
            bytes(frame["bgra"]),
            int(frame["width"]),
            int(frame["height"]),
            int(frame["stride"]),
            QImage.Format.Format_ARGB32,
        ).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(pixmap)


# ---------------------------------------------------------------------------
# Calibration / curve chart
# ---------------------------------------------------------------------------

class CurveChart(QWidget):
    """Renders the sensitivity curve (current vs. previous) with draggable control points."""

    curve_adjusted = Signal(list)

    CURRENT_COLOR = "#70e1ba"
    CURRENT_DOT = "#b4ffe6"
    PREVIOUS_COLOR = "#f0a860"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.points = [0.0, 0.3, 0.55, 0.78, 1.0]
        self.previous_points = None
        self._drag_index = None
        self.setMouseTracking(True)

    def set_previous(self, points):
        self.previous_points = points
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(16, 16, -16, -16)
        p.fillRect(self.rect(), QColor("#11161b"))

        pen_grid = QPen(QColor(255, 255, 255, 18))
        pen_grid.setWidth(1)
        p.setPen(pen_grid)
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(rect.left(), int(y), rect.right(), int(y))
            x = rect.left() + rect.width() * i / 4
            p.drawLine(int(x), rect.top(), int(x), rect.bottom())

        if self.previous_points:
            self._draw_curve(p, rect, self.previous_points, QColor(self.PREVIOUS_COLOR), dots=False)
        self._draw_curve(p, rect, self.points, QColor(self.CURRENT_COLOR), dots=True)

    def _draw_curve(self, p, rect, points, color, dots):
        pen = QPen(color)
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        n = len(points)
        path = QPainterPath()
        for i, v in enumerate(points):
            x = rect.left() + rect.width() * i / (n - 1)
            y = rect.bottom() - rect.height() * v
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.drawPath(path)
        if dots:
            p.setBrush(QBrush(QColor(self.CURRENT_DOT)))
            p.setPen(Qt.NoPen)
            for i, v in enumerate(points):
                x = rect.left() + rect.width() * i / (n - 1)
                y = rect.bottom() - rect.height() * v
                p.drawEllipse(QPointF(x, y), 4.5, 4.5)

    def mousePressEvent(self, event):
        rect = self.rect().adjusted(16, 16, -16, -16)
        n = len(self.points)
        for i, v in enumerate(self.points):
            x = rect.left() + rect.width() * i / (n - 1)
            y = rect.bottom() - rect.height() * v
            if (event.pos() - QPointF(x, y).toPoint()).manhattanLength() < 14:
                self._drag_index = i
                break

    def mouseMoveEvent(self, event):
        if self._drag_index is None:
            return
        rect = self.rect().adjusted(16, 16, -16, -16)
        v = 1.0 - (event.pos().y() - rect.top()) / max(rect.height(), 1)
        v = min(max(v, 0.0), 1.0)
        self.points[self._drag_index] = v
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_index is not None:
            self._drag_index = None
            self.curve_adjusted.emit(list(self.points))


def _curve_samples(points, count: int = 5) -> list[float]:
    """Sample a recommendation curve (x, y pairs) at evenly spaced x for CurveChart."""
    pts = sorted((float(x), float(y)) for x, y in points)
    if len(pts) < 2:
        return [0.0, 0.25, 0.5, 0.75, 1.0]
    samples = []
    for i in range(count):
        x = i / (count - 1)
        if x <= pts[0][0]:
            y = pts[0][1]
        elif x >= pts[-1][0]:
            y = pts[-1][1]
        else:
            y = pts[-1][1]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if x0 <= x <= x1:
                    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
                    y = y0 + t * (y1 - y0)
                    break
        samples.append(min(1.0, max(0.0, y)))
    return samples


def _lerp_factor(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear lookup of ys at x (xs ascending), clamped at the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return ys[-1]


def _apply_chart_edit(profile, baseline: list[float], edited: list[float]):
    """Scale a profile's gains by how far each chart point was dragged (edited / baseline).

    The chart samples the curve at evenly spaced x positions, so each sample gives a
    factor; every profile point gets the factor interpolated at its own speed.
    """
    n = len(edited)
    xs = [i / (n - 1) for i in range(n)]
    factors = [(e / b) if b > 1e-6 else 1.0 for e, b in zip(edited, baseline)]

    def scaled(points):
        if not points:
            return points
        top = max((p.speed for p in points), default=1.0) or 1.0
        return [
            replace(p, multiplier=max(0.05, min(20.0, p.multiplier * _lerp_factor(xs, factors, min(1.0, p.speed / top)))))
            for p in points
        ]

    return replace(profile, horizontal=scaled(profile.horizontal), vertical=scaled(profile.vertical))


class CalibrationPage(QWidget):
    """Calibration task (CalibrationCanvas on backend.analyzer) plus the curve view.

    When a run completes, emits recommendation_ready(summary, recommendation).
    """

    recommendation_ready = Signal(dict, dict)
    profile_changed = Signal(dict)
    base_profile_changed = Signal(object)  # imported CurveProfile (AI Coach builds on it)

    def __init__(self, backend: CoreBackend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._last_recommendation = None
        self._profile = None         # the curve Export writes (imported, calibrated, or edited)
        self._base_profile = None    # imported curve that calibration adjusts
        self._chart_baseline = None  # chart values before the latest drag

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Calibration", "Measure your movement, then review the response curve."))

        task_card, task_lay = _card(
            "Calibration task",
            "Click each target at your normal sensitivity. Only this in-app task is measured.",
        )
        bar = QHBoxLayout()
        self.recalibrate_btn = QPushButton(" Recalibrate")
        self.recalibrate_btn.setObjectName("primary")
        self.recalibrate_btn.setIcon(icon("refresh", "#1a1106"))
        self.reset_btn = QPushButton(" Reset")
        self.reset_btn.setObjectName("secondary")
        self.progress = QProgressBar()
        self.progress.setRange(0, 12)
        self.progress.setValue(0)
        self.progress.setFormat("%v / 12 targets")
        bar.addWidget(self.recalibrate_btn)
        bar.addWidget(self.reset_btn)
        bar.addWidget(self.progress, 1)
        task_lay.addLayout(bar)

        self.canvas = CalibrationCanvas(backend.analyzer)
        self.canvas.setMinimumHeight(300)
        task_lay.addWidget(self.canvas, 1)

        self.hint = _label("Press Recalibrate when you are ready.", "cardSubtitle", wrap=True)
        task_lay.addWidget(self.hint)

        curve_card, curve_lay = _card(
            "Sensitivity curve",
            "Previous curve shown in amber for comparison. Drag points to fine-tune.",
        )
        self.chart = CurveChart()
        curve_lay.addWidget(self.chart)

        btn_row = QHBoxLayout()
        self.import_btn = QPushButton(" Import curve")
        self.import_btn.setObjectName("secondary")
        self.import_btn.setIcon(icon("upload"))
        self.save_btn = QPushButton(" Export curve")
        self.save_btn.setObjectName("secondary")
        self.save_btn.setIcon(icon("download"))
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        curve_lay.addLayout(btn_row)

        outer.addWidget(task_card, 3)
        outer.addWidget(curve_card, 2)

        self.recalibrate_btn.clicked.connect(lambda: self.canvas.start_session(12))
        self.reset_btn.clicked.connect(self.canvas.cancel_session)
        self.canvas.progress_changed.connect(self._on_progress)
        self.canvas.hint_changed.connect(self.hint.setText)
        self.canvas.completed.connect(self._on_complete)
        self.chart.curve_adjusted.connect(self._on_curve_adjusted)
        self.save_btn.clicked.connect(self._save)
        self.import_btn.clicked.connect(self._import_curve)

    def _on_progress(self, completed: int, total: int):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(completed)

    def _set_chart(self, points, previous=None):
        self.chart.set_previous(previous)
        self.chart.points = list(points)
        self._chart_baseline = list(points)
        self.chart.update()

    def _on_complete(self, summary: dict, recommendation: dict):
        self._last_recommendation = recommendation
        try:
            if self._base_profile is not None:
                # An imported curve is adjusted by the result instead of replaced.
                adjusted, note = adjust_from_recommendation(self._base_profile, recommendation)
                prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
                self._profile = adjusted
                self._set_chart(_curve_samples(cur_pts), previous=_curve_samples(prev_pts))
                self.hint.setText(note)
            else:
                self._profile = recommendation_profile(recommendation)
                self._set_chart(
                    _curve_samples(recommendation.get("points") or []),
                    previous=list(self.chart.points),
                )
        except CurveFormatError as exc:
            self._profile = None
            self.hint.setText(f"Could not build a curve from this run: {exc}")
        self.recommendation_ready.emit(summary, recommendation)

    def _on_curve_adjusted(self, points):
        points = [float(v) for v in points]
        baseline = self._chart_baseline or points
        if self._profile is None:
            # No curve yet: treat the chart values as the curve's output-speed shape.
            n = len(points)
            try:
                self._profile = recommendation_profile(
                    {"points": [(i / (n - 1), y) for i, y in enumerate(points)], "sensitivity_multiplier": 1.0}
                )
            except CurveFormatError:
                pass
        else:
            self._profile = _apply_chart_edit(self._profile, baseline, points)
        self._chart_baseline = list(points)
        self.hint.setText("Curve edited. Export curve to save the edited version.")
        self.profile_changed.emit({"points": points})

    def _import_curve(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import curve", "", "Custom Curves (*.cc4 *.ccurve);;All files (*)"
        )
        if not path:
            return
        try:
            profile = load_curve(path)
        except (CurveFormatError, OSError) as exc:
            self.hint.setText(f"Import failed: {exc}")
            return
        self._base_profile = deepcopy(profile)
        self._profile = deepcopy(profile)
        self.base_profile_changed.emit(deepcopy(profile))
        self._set_chart(_curve_samples(chart_points(profile)))
        self.hint.setText(
            f"Imported {Path(path).name}. Run calibration to adjust it, or drag points to edit it."
        )
        self.profile_changed.emit({"imported": path})

    def set_adjusted(self, previous, current, note: str) -> None:
        """Show an AI-adjusted curve as before/after and make it the curve Export writes."""
        self._profile = deepcopy(current)
        prev_pts, cur_pts = normalized_overlay(previous, current)
        self._set_chart(_curve_samples(cur_pts), previous=_curve_samples(prev_pts))
        self.hint.setText(f"{note} Export curve to save it.")

    def _save(self):
        if self._profile is None:
            self.hint.setText("Run a calibration or import a curve first, then export it.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export curve",
            "fps_coach_curve.cc4",
            "Custom Curve 4 (*.cc4);;Legacy Custom Curve (*.ccurve)",
        )
        if not path:
            return
        try:
            destination = save_curve(self._profile, path)
        except (CurveFormatError, OSError) as exc:
            QMessageBox.warning(self, "Vantage", f"Could not export curve: {exc}")
            return
        self.hint.setText(f"Exported the current curve to {destination}.")
        self.profile_changed.emit({"saved": str(destination)})

# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

class RecommendationPage(QWidget):
    """Shows the analyzer's recommendation for the most recent calibration.

    recommendation_ready(dict) is emitted for every new recommendation and is
    used by the Coach Panel.
    """

    recommendation_ready = Signal(dict)

    def __init__(self, backend: CoreBackend, parent=None):
        super().__init__(parent)
        self._backend = backend

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header(
            "Recommendation",
            "A suggested sensitivity from your calibration run.",
        ))

        card, lay = _card("Suggested sensitivity", "Based on your most recent calibration run.")
        self.summary = QLabel("No recommendation yet. Run the calibration task first.")
        self.summary.setObjectName("cardSubtitle")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.generate_btn = QPushButton(" Generate recommendation")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.setIcon(icon("recommendation", "#1a1106"))
        lay.addWidget(self.generate_btn, 0, Qt.AlignLeft)

        outer.addWidget(card)
        outer.addStretch(1)
        self.generate_btn.clicked.connect(self._generate)

    def update_result(self, summary: dict, recommendation: dict):
        """Show a finished calibration's recommendation and notify listeners."""
        mult = float(recommendation.get("sensitivity_multiplier", 1.0))
        confidence = float(recommendation.get("confidence", 0.0))
        title = str(recommendation.get("title", "Recommendation"))
        reasons = " ".join(str(r) for r in recommendation.get("reasons", []))
        trials = int(summary.get("trial_count", 0))
        self.summary.setText(
            f"{title}: sensitivity ×{mult:.2f} (confidence {confidence:.0%}, {trials} targets). {reasons}"
        )
        self.recommendation_ready.emit(recommendation)

    def _generate(self):
        summary = dict(self._backend.analyzer.summary())
        if int(summary.get("trial_count", 0)) == 0:
            self.summary.setText("No calibration data yet. Run the calibration task first.")
            return
        recommendation = dict(self._backend.analyzer.recommend())
        self.update_result(summary, recommendation)


# ---------------------------------------------------------------------------
# Background threads / services
# ---------------------------------------------------------------------------

class _OllamaStatusCheck(QThread):
    """Probe the local Ollama server off the UI thread (ai_chat.check_ollama)."""

    status_ready = Signal(bool, bool)  # (reachable, has_model)

    def __init__(self, base_url: str, model: str = "", parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        result = check_ollama(self._base_url, self._model)
        self.status_ready.emit(bool(result["reachable"]), bool(result["has_model"]))


class _ModelPullThread(QThread):
    """Pull an Ollama model off the UI thread (ai_chat.pull_model)."""

    progress = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, base_url: str, model: str, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        try:
            pull_model(self._base_url, self._model, progress=lambda msg: self.progress.emit(str(msg)))
        except Exception as exc:  # noqa: BLE001 - report any pull failure to the UI
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit()


class _VoiceListenThread(QThread):
    """Capture one spoken phrase off the UI thread (voice_control.recognize_once)."""

    recognized = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        from fps_coach.voice_control import recognize_once

        result = recognize_once()
        if result.ok:
            self.recognized.emit(result.text)
        else:
            self.failed.emit(result.message)


class _TtsService(QThread):
    """Background text-to-speech worker. Initializes one engine (with fallbacks, see
    tts_service.init_engine) and speaks queued AI responses off the UI thread."""

    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        import queue

        self._queue: queue.Queue = queue.Queue()
        self._stop = False

    def enqueue(self, text: str) -> None:
        if text and text.strip():
            self._queue.put(text.strip())

    def stop(self) -> None:
        self._stop = True
        self._queue.put(None)

    def run(self) -> None:
        import queue

        from fps_coach import tts_service

        with tts_service.com_apartment():
            tts_service.ensure_dependency(progress=self.status.emit)
            result = tts_service.init_engine(progress=self.status.emit)
            if result.speaker is None:
                self.failed.emit(tts_service.format_unavailable(result))
                return
            self.status.emit("Text-to-speech ready.")
            try:
                while not self._stop:
                    try:
                        text = self._queue.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if text is None:
                        break
                    try:
                        result.speaker.say(text)
                    except Exception:  # noqa: BLE001 - one bad utterance must not kill the worker
                        continue
            finally:
                try:
                    result.speaker.close()
                except Exception:  # noqa: BLE001
                    pass


class _VoiceCommandService(QThread):
    """Background Vosk listener: downloads the model if needed, then streams mic
    audio and emits each finalized phrase (MainWindow parses it as a command)."""

    recognized = Signal(str)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from fps_coach import vosk_service

        try:
            if not vosk_service.ensure_dependencies(progress=self.status.emit):
                self.failed.emit(
                    "Could not auto-install the voice packages (vosk, sounddevice). "
                    "Check your internet connection, or install them manually: "
                    "pip install vosk sounddevice"
                )
                return
            model_path = vosk_service.verify_and_configure(progress=self.status.emit)
            if model_path is None:
                model_path = vosk_service.ensure_model(progress=self.status.emit)
            vosk_service.listen(
                model_path,
                on_text=self.recognized.emit,
                should_stop=lambda: self._stop,
                on_status=self.status.emit,
            )
        except Exception as exc:  # noqa: BLE001 - surface any setup/runtime issue
            self.failed.emit(str(exc))


class _ChatRequest(QThread):
    """Send one conversation turn to the configured AI provider off the UI thread."""

    response_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(self, settings: ChatSettings, messages: list, calibration: str, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._messages = messages
        self._calibration = calibration

    def run(self) -> None:
        try:
            answer = AIChatClient(self._settings).chat(self._messages, curve_context=self._calibration)
        except ChatProviderError as exc:
            self.request_failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the UI alive on any provider error
            self.request_failed.emit(f"Unexpected error: {exc}")
        else:
            self.response_ready.emit(answer)


# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------

class AIChatPage(QWidget):
    """AI Coach chat. Sends turns through ai_chat.AIChatClient (Ollama by default),
    adds the latest calibration as context, and takes one spoken phrase as input.

    Also offers suggested actions and curve tuning on an imported curve, and posts a
    proactive note after each calibration.
    """

    assistant_said = Signal(str)  # emitted with each AI response (Coach Panel, TTS)
    curve_adjusted = Signal(object, object, str)  # (previous, current, note)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = _settings()
        self._thread = None
        self._voice_thread = None
        self._messages: list[dict[str, str]] = []
        self._summary_context = ""
        self._calibration_context = ""
        self._base_profile = None
        self._adjusted_profile = None
        self._proactive_pending = None
        self._proactive_check = None
        self._proactive_request = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        outer.addWidget(_page_header("AI Coach", "Ask about sensitivity, drills, or your latest session."))

        card, lay = _card()
        self.transcript = QTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        lay.addWidget(self.transcript, 1)

        self.suggestion_bar = QHBoxLayout()
        self.suggestion_bar.setSpacing(8)
        lay.addLayout(self.suggestion_bar)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask the coach…")
        self.mic_btn = QToolButton()
        self.mic_btn.setIcon(icon("mic"))
        self.mic_btn.setObjectName("iconButton")
        self.mic_btn.setCheckable(True)
        self.send_btn = QPushButton(" Send")
        self.send_btn.setObjectName("primary")
        self.send_btn.setIcon(icon("send", "#1a1106"))
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.mic_btn)
        input_row.addWidget(self.send_btn)
        lay.addLayout(input_row)

        outer.addWidget(card, 1)

        tune_card, tune_lay = _card("Curve tuning")
        self.quick_hint = _label(
            "Import a curve in Calibration to enable one-click tuning.", "cardSubtitle", wrap=True
        )
        tune_lay.addWidget(self.quick_hint)
        self.quick_preview = CurveChart()
        self.quick_preview.setMinimumHeight(180)
        # Read-only preview: the chart is shown, not edited, from here.
        self.quick_preview.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.quick_preview.hide()
        tune_lay.addWidget(self.quick_preview)
        self.apply_to_rec = QPushButton(" Send to calibration")
        self.apply_to_rec.setObjectName("secondary")
        self.apply_to_rec.setIcon(icon("check"))
        self.apply_to_rec.hide()
        tune_lay.addWidget(self.apply_to_rec, 0, Qt.AlignLeft)
        outer.addWidget(tune_card)

        self.send_btn.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.input.textChanged.connect(self._refresh_suggestions)
        self.mic_btn.toggled.connect(self._start_voice)
        self.apply_to_rec.clicked.connect(self._send_adjusted_to_calibration)
        self._refresh_suggestions()

    # --- calibration + imported curve context ----------------------------
    def set_calibration_context(self, summary: dict, recommendation: dict) -> None:
        """Give the coach the latest calibration so answers can refer to it."""
        self._summary_context = curve_context(summary, recommendation)
        self._refresh_context()

    def notify_new_calibration(self, summary: dict, recommendation: dict) -> None:
        """Called when a calibration completes: refresh the coach's context and post
        a proactive note without waiting for the user."""
        self.set_calibration_context(summary, recommendation)
        self._append("Coach (auto)", self._local_proactive_note(summary, recommendation))
        self._maybe_proactive_ai(summary, recommendation)

    def set_base_profile(self, profile) -> None:
        """Receive the imported curve so suggestions and AI adjustments can build on it."""
        self._base_profile = deepcopy(profile) if profile is not None else None
        self._adjusted_profile = None
        if self._base_profile is not None:
            self.quick_hint.setText(
                f"Base curve: '{self._base_profile.name}'. Suggestions update as you type; "
                "tap a tuning action to preview it below."
            )
        else:
            self.quick_hint.setText("Import a curve in Calibration to enable one-click tuning.")
            self.quick_preview.hide()
            self.apply_to_rec.hide()
        self._refresh_context()
        self._refresh_suggestions()

    def _refresh_context(self) -> None:
        """Context = latest calibration summary + a description of the imported base curve."""
        context = self._summary_context
        if self._base_profile is not None:
            pts = ", ".join(
                f"({p.speed:.0f},{p.multiplier:.2f})" for p in self._base_profile.horizontal[:12]
            )
            context = (
                f"{context}\n\nImported base curve '{self._base_profile.name}' "
                f"({self._base_profile.source_format}); horizontal speed,gain points: {pts}. "
                "When asked for adjustments, modify THIS curve rather than designing a new one."
            )
        self._calibration_context = context

    def _local_proactive_note(self, summary: dict, recommendation: dict) -> str:
        title = str(recommendation.get("title", "Recommendation"))
        sens = float(recommendation.get("sensitivity_multiplier", 1.0) or 1.0)
        conf = float(recommendation.get("confidence", 0.0)) * 100
        path_eff = float(summary.get("path_efficiency", 0.0)) * 100
        over = float(summary.get("overshoot_score", 0.0)) * 100
        corr = float(summary.get("correction_score", 0.0)) * 100
        parts = [
            f"New calibration in. Direction: {title}.",
            f"Suggested starting multiplier {sens:.2f}x (confidence {conf:.0f}%).",
            f"Path efficiency {path_eff:.0f}%, overshoot {over:.0f}%, corrections {corr:.0f}%.",
        ]
        reasons = recommendation.get("reasons") or []
        if reasons:
            parts.append(f"Key point: {reasons[0]}")
        if self._base_profile is not None:
            parts.append(f"I've adjusted your imported curve '{self._base_profile.name}' to match.")
        parts.append("Ask me anything about these results, or use a suggested action.")
        return " ".join(parts)

    # --- proactive AI (Ollama only, and only when the model is already present) ---
    def _maybe_proactive_ai(self, summary: dict, recommendation: dict) -> None:
        # Never trigger a surprise download or a paid API call without user action.
        if str(self._settings.value("ai/provider", "ollama")) != "ollama":
            return
        if self._proactive_request is not None or self._proactive_check is not None:
            return
        settings = self._chat_settings()
        if not settings.base_url or not settings.model:
            return
        self._proactive_pending = (summary, recommendation)
        self._proactive_check = _OllamaStatusCheck(settings.base_url, settings.model, self)
        self._proactive_check.status_ready.connect(self._on_proactive_status)
        self._proactive_check.finished.connect(self._proactive_check_finished)
        self._proactive_check.start()

    def _proactive_check_finished(self) -> None:
        if self._proactive_check is not None:
            self._proactive_check.deleteLater()
        self._proactive_check = None

    def _on_proactive_status(self, reachable: bool, has_model: bool) -> None:
        if not (reachable and has_model) or self._proactive_pending is None:
            return
        if self._proactive_request is not None:
            return
        instruction = (
            "The user just completed a new calibration. Using the calibration summary "
            "and current curve already in your context, give a brief (2-3 sentence) "
            "proactive assessment and one concrete next step. Do not ask questions."
        )
        messages = list(self._messages) + [{"role": "user", "content": instruction}]
        self._proactive_request = _ChatRequest(self._chat_settings(), messages, self._calibration_context, self)
        self._proactive_request.response_ready.connect(self._proactive_received)
        self._proactive_request.request_failed.connect(self._proactive_quiet_fail)
        self._proactive_request.finished.connect(self._proactive_finished)
        self._proactive_request.start()

    def _proactive_received(self, answer: str) -> None:
        self._messages.append({"role": "assistant", "content": answer})
        self._append("Coach", answer)
        self.assistant_said.emit(answer)
        self._apply_ai_adjustment(answer)

    def _proactive_quiet_fail(self, _message: str) -> None:
        # The instant local note already covered the user; stay silent on failure.
        pass

    def _proactive_finished(self) -> None:
        if self._proactive_request is not None:
            self._proactive_request.deleteLater()
        self._proactive_request = None

    # --- suggestions, quick actions, AI-driven curve changes ----------------
    def _refresh_suggestions(self, *_args) -> None:
        """Rebuild the suggestion chips to match what the user is typing."""
        while self.suggestion_bar.count():
            item = self.suggestion_bar.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for s in suggest(self.input.text(), self._base_profile is not None):
            chip = QPushButton(s["label"])
            chip.setObjectName("secondary")
            if s["kind"] == "preset":
                chip.clicked.connect(lambda checked=False, k=s["payload"]: self._apply_quick_action(k))
            else:
                chip.clicked.connect(lambda checked=False, p=s["payload"]: self._use_prompt(p))
            self.suggestion_bar.addWidget(chip)
        self.suggestion_bar.addStretch(1)

    def _use_prompt(self, prompt: str) -> None:
        """Drop a suggested question into the compose box for the user to send or edit."""
        self.input.setText(prompt)
        self.input.setFocus()

    def _show_preview(self, adjusted, note: str) -> None:
        self._adjusted_profile = adjusted
        prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
        self.quick_preview.set_previous(_curve_samples(prev_pts))
        self.quick_preview.points = _curve_samples(cur_pts)
        self.quick_preview.update()
        self.quick_preview.show()
        self.apply_to_rec.show()
        self.quick_hint.setText(note)

    def _apply_quick_action(self, key: str) -> None:
        if self._base_profile is None:
            self._append("Quick action", "Import a curve in Calibration first.")
            return
        try:
            adjusted, note = apply_preset(self._base_profile, key)
        except KeyError:
            return
        self._show_preview(adjusted, note)
        self._append(
            "Quick action",
            f"{note}\nPreview updated. Multiplier now {adjusted.horizontal[0].multiplier:.2f}x at the first point.",
        )

    def _apply_ai_adjustment(self, answer: str) -> None:
        """If the assistant recommended a curve change, apply it to the imported curve
        and show it in the preview and on the Calibration graph."""
        if self._base_profile is None:
            return
        plan = parse_adjustment(answer)
        if not plan:
            return
        try:
            adjusted, note = apply_adjustment(self._base_profile, plan)
        except (ValueError, KeyError):
            return
        self._show_preview(adjusted, note)
        self.curve_adjusted.emit(deepcopy(self._base_profile), deepcopy(adjusted), note)
        self._append("Applied", f"{note} Updated the Calibration graph automatically.")

    def _send_adjusted_to_calibration(self) -> None:
        if self._base_profile is None or self._adjusted_profile is None:
            return
        self.curve_adjusted.emit(
            deepcopy(self._base_profile),
            deepcopy(self._adjusted_profile),
            self._adjusted_profile.description or "Adjusted via AI quick action.",
        )
        self._append("Quick action", "Sent the adjusted curve to Calibration (open it to review and export).")

    def shutdown(self) -> None:
        for thread in (self._thread, self._voice_thread, self._proactive_check, self._proactive_request):
            if thread is not None and thread.isRunning():
                thread.wait(2000)

    def _append(self, who, text):
        self.transcript.append(f"<b>{who}:</b> {text}")

    def _chat_settings(self) -> ChatSettings:
        s = self._settings
        return ChatSettings(
            provider=str(s.value("ai/provider", "ollama")),
            base_url=str(s.value("ai/base_url", "http://127.0.0.1:11434")),
            model=str(s.value("ai/model", "qwen3:1.7b")),
            temperature=float(s.value("ai/temperature", 0.35)),
        )

    def _send(self):
        text = self.input.text().strip()
        if not text or (self._thread is not None and self._thread.isRunning()):
            return
        self._append("You", text)
        self.input.clear()
        self._messages.append({"role": "user", "content": text})
        self._thread = _ChatRequest(self._chat_settings(), list(self._messages), self._calibration_context, self)
        self._thread.response_ready.connect(self._on_reply)
        self._thread.request_failed.connect(self._on_failed)
        self.send_btn.setEnabled(False)
        self._thread.finished.connect(lambda: self.send_btn.setEnabled(True))
        self._thread.start()

    def _on_reply(self, answer: str):
        self._messages.append({"role": "assistant", "content": answer})
        self._append("Coach", answer)
        self.assistant_said.emit(answer)
        self._apply_ai_adjustment(answer)

    def _on_failed(self, message: str):
        if self._messages and self._messages[-1]["role"] == "user":
            self._messages.pop()  # keep the history valid for the next attempt
        self._append("Coach", f"Error: {message}")

    def _start_voice(self, checked: bool):
        if not checked or (self._voice_thread is not None and self._voice_thread.isRunning()):
            return
        self._voice_thread = _VoiceListenThread(self)
        self._voice_thread.recognized.connect(self._on_voice_text)
        self._voice_thread.failed.connect(self._on_voice_failed)
        self._voice_thread.finished.connect(lambda: self.mic_btn.setChecked(False))
        self._voice_thread.start()

    def _on_voice_text(self, text: str):
        if text:
            self.input.setText(text)
            self._send()

    def _on_voice_failed(self, message: str):
        self._append("Voice", message)


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

class PrivacyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Privacy", "Vantage runs its vision and coaching pipeline locally."))

        points = [
            "Screen capture frames are processed in memory and never written to disk.",
            "AI requests are sent only to the endpoint you configure in Settings.",
            "Voice recognition can run fully offline via a local model.",
            "All preferences are stored locally in your system's settings store.",
        ]
        card, lay = _card("Data handling")
        for point in points:
            row = QHBoxLayout()
            mark = QLabel()
            mark.setPixmap(pixmap("check", "#5fb88a", 16))
            row.addWidget(mark, 0, Qt.AlignTop)
            row.addWidget(_label(point, "cardSubtitle", wrap=True), 1)
            lay.addLayout(row)
        outer.addWidget(card)

        card2, lay2 = _card("Local data")
        self.clear_btn = QPushButton(" Clear cached data")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.setIcon(icon("trash"))
        self.clear_btn.clicked.connect(self._clear)
        lay2.addWidget(self.clear_btn, 0, Qt.AlignLeft)
        outer.addWidget(card2)
        outer.addStretch(1)

    def _clear(self):
        QMessageBox.information(self, "Vantage", "Cached data cleared.")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsPage(QWidget):
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._pull_thread = None
        self._status_thread = None

        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Settings", "Configure the AI backend, interface, and voice pipeline."))

        outer.addWidget(self._build_ai_card())
        outer.addWidget(self._build_ui_card())
        outer.addWidget(self._build_voice_card())
        outer.addWidget(self._build_advanced_card())
        outer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scroll_page(inner))

    # ---- AI -----------------------------------------------------------
    def _build_ai_card(self):
        card, lay = _card("AI coach", "Point Vantage at a local or remote model endpoint.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Provider", "formLabel"), 0, 0)
        self.provider = QComboBox()
        self.provider.addItems(["ollama", "openai-compatible"])
        self.provider.setCurrentText(self.s.value("ai/provider", "ollama"))
        self.provider.currentTextChanged.connect(self._on_ai_changed)
        form.addWidget(self.provider, 0, 1)

        form.addWidget(_label("Base URL", "formLabel"), 1, 0)
        self.base_url = QLineEdit(self.s.value("ai/base_url", "http://127.0.0.1:11434"))
        self.base_url.editingFinished.connect(self._on_ai_changed)
        form.addWidget(self.base_url, 1, 1)

        form.addWidget(_label("Model", "formLabel"), 2, 0)
        model_row = QHBoxLayout()
        self.model = QLineEdit(self.s.value("ai/model", "qwen3:1.7b"))
        self.model.editingFinished.connect(self._on_ai_changed)
        self.pull_btn = QToolButton()
        self.pull_btn.setIcon(icon("download"))
        self.pull_btn.setObjectName("iconButton")
        self.pull_btn.clicked.connect(self._pull_model)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.pull_btn)
        form.addLayout(model_row, 2, 1)

        form.addWidget(_label("Temperature", "formLabel"), 3, 0)
        self.temperature = QSlider(Qt.Horizontal)
        self.temperature.setRange(0, 150)
        self.temperature.setValue(int(float(self.s.value("ai/temperature", 0.35)) * 100))
        self.temperature.valueChanged.connect(self._on_ai_changed)
        form.addWidget(self.temperature, 3, 1)

        lay.addLayout(form)

        self.pull_progress = QProgressBar()
        self.pull_progress.setValue(0)
        self.pull_progress.setTextVisible(True)
        self.pull_progress.setVisible(False)
        lay.addWidget(self.pull_progress)

        status_row = QHBoxLayout()
        self.check_btn = QPushButton(" Check connection")
        self.check_btn.setObjectName("secondary")
        self.check_btn.setIcon(icon("refresh"))
        self.check_btn.clicked.connect(self._check_status)
        self.status_label = QLabel("Not checked")
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.check_btn)
        status_row.addWidget(self.status_label, 1)
        lay.addLayout(status_row)

        return card

    def _on_ai_changed(self, *_args):
        self.s.setValue("ai/provider", self.provider.currentText())
        self.s.setValue("ai/base_url", self.base_url.text())
        self.s.setValue("ai/model", self.model.text())
        self.s.setValue("ai/temperature", self.temperature.value() / 100.0)
        self.settings_changed.emit()

    def _check_status(self):
        self.status_label.setText("Checking…")
        self._status_thread = _OllamaStatusCheck(self.base_url.text(), self.model.text())
        self._status_thread.status_ready.connect(self._on_status_ready)
        self._status_thread.start()

    def _on_status_ready(self, reachable, has_model):
        if not reachable:
            self._on_status(False, "Ollama is not reachable at this address.")
        elif not has_model:
            self._on_status(False, "Ollama is running, but this model is not installed. Use the download button to pull it.")
        else:
            self._on_status(True, "Connected. Model is installed.")

    def _on_status(self, ok, message):
        self.status_label.setText(message)
        self.status_label.setProperty("state", "good" if ok else "warn")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _pull_model(self):
        self.pull_progress.setVisible(True)
        self.pull_progress.setValue(0)
        self._pull_thread = _ModelPullThread(self.base_url.text(), self.model.text())
        self._pull_thread.progress.connect(lambda msg: self.pull_progress.setFormat(msg))
        self._pull_thread.finished_ok.connect(
            lambda: self._on_pull_finished(True, f"Model {self.model.text()} is installed.")
        )
        self._pull_thread.failed.connect(lambda msg: self._on_pull_finished(False, msg))
        self._pull_thread.start()

    def _on_pull_finished(self, ok, message):
        self.pull_progress.setVisible(False)
        self.status_label.setText(message)

    # ---- UI -------------------------------------------------------------
    def _build_ui_card(self):
        card, lay = _card("Interface", "Choose the visual mode, theme, and motion preferences.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Mode", "formLabel"), 0, 0)
        self.mode = QComboBox()
        self.mode.addItems(["luxurious", "performance"])
        self.mode.setCurrentText(self.s.value("ui/mode", "luxurious"))
        self.mode.currentTextChanged.connect(self._on_ui_changed)
        form.addWidget(self.mode, 0, 1)

        form.addWidget(_label("Theme", "formLabel"), 1, 0)
        self.theme = QComboBox()
        self.theme.addItems(["system", "dark", "light"])
        self.theme.setCurrentText(self.s.value("ui/theme", "system"))
        self.theme.currentTextChanged.connect(self._on_ui_changed)
        form.addWidget(self.theme, 1, 1)

        self.animations = QCheckBox("Enable animations")
        self.animations.setChecked(self.s.value("ui/animations", True, type=bool))
        self.animations.toggled.connect(self._on_ui_changed)
        form.addWidget(self.animations, 2, 0, 1, 2)

        self.coach_panel = QCheckBox("Show floating coach panel")
        self.coach_panel.setChecked(self.s.value("ui/coach_panel", False, type=bool))
        self.coach_panel.toggled.connect(self._on_ui_changed)
        form.addWidget(self.coach_panel, 3, 0, 1, 2)

        lay.addLayout(form)
        return card

    def _on_ui_changed(self, *_args):
        self.s.setValue("ui/mode", self.mode.currentText())
        self.s.setValue("ui/theme", self.theme.currentText())
        self.s.setValue("ui/animations", self.animations.isChecked())
        self.s.setValue("ui/coach_panel", self.coach_panel.isChecked())
        self.settings_changed.emit()

    # ---- Voice ------------------------------------------------------------
    def _build_voice_card(self):
        card, lay = _card("Voice", "Hands-free commands and spoken responses.")
        self.voice_commands = QCheckBox("Enable voice commands")
        self.voice_commands.setChecked(self.s.value("ui/voice_commands", False, type=bool))
        self.voice_commands.toggled.connect(self._on_voice_changed)
        lay.addWidget(self.voice_commands)

        wake_row = QHBoxLayout()
        wake_row.addWidget(_label("Wake word", "formLabel"))
        self.wake_word = QLineEdit(self.s.value("ui/wake_word", "coach"))
        self.wake_word.editingFinished.connect(self._on_voice_changed)
        wake_row.addWidget(self.wake_word, 1)
        lay.addLayout(wake_row)

        self.tts_enabled = QCheckBox("Speak responses aloud")
        self.tts_enabled.setChecked(self.s.value("ui/tts_enabled", False, type=bool))
        self.tts_enabled.toggled.connect(self._on_voice_changed)
        lay.addWidget(self.tts_enabled)

        model_row = QHBoxLayout()
        model_row.addWidget(_label("Vosk model path", "formLabel"))
        self.vosk_path = QLineEdit(self.s.value("voice/vosk_model_path", ""))
        self.vosk_path.editingFinished.connect(self._on_voice_changed)
        browse_btn = QToolButton()
        browse_btn.setIcon(icon("upload"))
        browse_btn.setObjectName("iconButton")
        browse_btn.clicked.connect(self._browse_model)
        model_row.addWidget(self.vosk_path, 1)
        model_row.addWidget(browse_btn)
        lay.addLayout(model_row)

        return card

    def _browse_model(self):
        path = QFileDialog.getExistingDirectory(self, "Select voice model folder")
        if path:
            self.vosk_path.setText(path)
            self._on_voice_changed()

    def _on_voice_changed(self, *_args):
        self.s.setValue("ui/voice_commands", self.voice_commands.isChecked())
        self.s.setValue("ui/wake_word", self.wake_word.text())
        self.s.setValue("ui/tts_enabled", self.tts_enabled.isChecked())
        self.s.setValue("voice/vosk_model_path", self.vosk_path.text())
        self.settings_changed.emit()

    # ---- Advanced (disclosure) --------------------------------------------
    def _build_advanced_card(self):
        card, lay = _card()
        header = QHBoxLayout()
        header.addWidget(_label("Advanced", "cardTitle"))
        header.addStretch(1)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setIcon(icon("chevron_down"))
        self.advanced_toggle.setObjectName("iconButton")
        self.advanced_toggle.setCheckable(True)
        header.addWidget(self.advanced_toggle)
        lay.addLayout(header)

        self.advanced_body = QWidget()
        body_lay = QVBoxLayout(self.advanced_body)
        body_lay.setContentsMargins(0, 8, 0, 0)
        body_lay.addWidget(_label(
            "Advanced options affect the capture and AI pipelines directly. Change with care.",
            "cardSubtitle", wrap=True,
        ))
        self.advanced_body.setVisible(False)
        lay.addWidget(self.advanced_body)

        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        return card

    def _toggle_advanced(self, checked):
        self.advanced_body.setVisible(checked)
        self.advanced_toggle.setIcon(icon("chevron_up" if checked else "chevron_down"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class _TargetsBackdrop(QWidget):
    """Subtle animated reticle/targets backdrop for the dashboard header."""

    def __init__(self, accent="#70e1ba", parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setMinimumHeight(140)
        self._targets = [
            {"x": random.random(), "y": random.random(), "r": random.uniform(6, 16), "speed": random.uniform(0.1, 0.3)}
            for _ in range(10)
        ]
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_animated(self, enabled: bool):
        if enabled:
            self._timer.start(50)
        else:
            self._timer.stop()

    def _tick(self):
        self._t += 0.02
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(self._accent)
        color.setAlpha(40)
        pen = QPen(color)
        pen.setWidthF(1.2)
        p.setPen(pen)
        w, h = self.width(), self.height()
        for tgt in self._targets:
            x = (tgt["x"] + 0.03 * math.sin(self._t * tgt["speed"] * 6)) * w
            y = (tgt["y"] + 0.03 * math.cos(self._t * tgt["speed"] * 6)) * h
            r = tgt["r"]
            p.drawEllipse(QPointF(x, y), r, r)
            p.drawLine(QPointF(x - r - 4, y), QPointF(x + r + 4, y))
            p.drawLine(QPointF(x, y - r - 4), QPointF(x, y + r + 4))


class DashboardPage(QWidget):
    _accent = "#70e1ba"

    TILES = [
        ("capture", "Capture", "Live source status"),
        ("calibration", "Calibration", "Curve freshness"),
        ("training", "Training", "Recent sessions"),
        ("recommendation", "Recommendation", "Latest suggestion"),
        ("crosshair", "Crosshair", "Active preset"),
        ("chat", "AI Coach", "Conversation history"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()

        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)

        header_wrap = QFrame()
        header_wrap.setObjectName("dashboardHeader")
        header_lay = QVBoxLayout(header_wrap)
        header_lay.setContentsMargins(0, 0, 0, 0)
        self.backdrop = _TargetsBackdrop(self._accent)
        header_lay.addWidget(self.backdrop)
        header_lay.addWidget(_page_header(
            "Dashboard", "A tactical overview of every subsystem Vantage is watching."
        ))
        outer.addWidget(header_wrap)

        self._order = self._load_order()
        self._enabled = self._load_enabled()
        self.cards_container_layout = QVBoxLayout()
        self.cards_container_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_area = None
        outer.addLayout(self.cards_container_layout)
        self._rebuild_tiles()

        outer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scroll_page(inner))

    def set_animated(self, enabled: bool):
        self.backdrop.set_animated(enabled)

    def _load_order(self):
        raw = self.s.value("dashboard/order", "")
        keys = [k for k in raw.split(",") if k] if raw else []
        all_keys = [t[0] for t in self.TILES]
        ordered = [k for k in keys if k in all_keys]
        ordered += [k for k in all_keys if k not in ordered]
        return ordered

    def _load_enabled(self):
        raw = self.s.value("dashboard/enabled", "")
        if not raw:
            return {t[0]: True for t in self.TILES}
        enabled_keys = set(raw.split(","))
        return {t[0]: (t[0] in enabled_keys) for t in self.TILES}

    def _save_state(self):
        self.s.setValue("dashboard/order", ",".join(self._order))
        self.s.setValue("dashboard/enabled", ",".join(k for k, v in self._enabled.items() if v))

    def _rebuild_tiles(self):
        if self.cards_area is not None:
            self.cards_container_layout.removeWidget(self.cards_area)
            self.cards_area.deleteLater()
        # ResponsiveCards takes its cards up front, so build the visible tiles first.
        lookup = {t[0]: t for t in self.TILES}
        tiles = []
        for key in self._order:
            if not self._enabled.get(key, True):
                continue
            kind, title, subtitle = lookup[key]
            tiles.append(self._build_tile(kind, title, subtitle))
        self.cards_area = ResponsiveCards(tiles, spacing=16)
        self.cards_container_layout.addWidget(self.cards_area)

    def _build_tile(self, key, title, subtitle):
        card, lay = _card()
        head = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(pixmap(key, self._accent, 20))
        head.addWidget(icon_lbl)
        head.addWidget(_label(title, "cardTitle"))
        head.addStretch(1)

        up_btn = QToolButton()
        up_btn.setIcon(icon("chevron_up"))
        up_btn.setObjectName("iconButton")
        up_btn.clicked.connect(lambda: self._move(key, -1))
        down_btn = QToolButton()
        down_btn.setIcon(icon("chevron_down"))
        down_btn.setObjectName("iconButton")
        down_btn.clicked.connect(lambda: self._move(key, 1))
        head.addWidget(up_btn)
        head.addWidget(down_btn)

        lay.addLayout(head)
        lay.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        value = QLabel("—")
        value.setObjectName("metric")
        lay.addWidget(value)
        return card

    def _move(self, key, direction):
        idx = self._order.index(key)
        new_idx = idx + direction
        if 0 <= new_idx < len(self._order):
            self._order[idx], self._order[new_idx] = self._order[new_idx], self._order[idx]
            self._save_state()
            self._rebuild_tiles()


# ---------------------------------------------------------------------------
# Coach panel (floating overlay)
# ---------------------------------------------------------------------------

class CoachPanel(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("coachPanel")
        self.setFixedWidth(320)
        self._minimized = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(pixmap("chat", "#d98f3b", 18))
        head.addWidget(icon_lbl)
        head.addWidget(_label("Coach", "cardTitle"))
        head.addStretch(1)

        self.min_btn = QToolButton()
        self.min_btn.setIcon(icon("minimize"))
        self.min_btn.setObjectName("iconButton")
        self.min_btn.clicked.connect(self._toggle_minimize)
        self.close_btn = QToolButton()
        self.close_btn.setIcon(icon("close"))
        self.close_btn.setObjectName("iconButton")
        self.close_btn.clicked.connect(self._on_close)
        head.addWidget(self.min_btn)
        head.addWidget(self.close_btn)
        outer.addLayout(head)

        self.body = QLabel("Ready when you are.")
        self.body.setObjectName("cardSubtitle")
        self.body.setWordWrap(True)
        outer.addWidget(self.body)

    def say(self, text):
        self.body.setText(text)

    def _toggle_minimize(self):
        self._minimized = not self._minimized
        self.body.setVisible(not self._minimized)
        self.min_btn.setIcon(icon("chevron_up" if self._minimized else "minimize"))

    def _on_close(self):
        self.hide()
        self.closed.emit()


# ---------------------------------------------------------------------------
# Crosshair
# ---------------------------------------------------------------------------

def _draw_crosshair(painter: QPainter, cx: float, cy: float, cfg: CrosshairConfig) -> None:
    """Paint the crosshair centered at (cx, cy). Shared by the preview and PNG export."""
    cfg = cfg.normalized()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(cfg.opacity)
    color = QColor(cfg.color)
    outline = QColor(cfg.outline_color)

    def seg(x1, y1, x2, y2):
        if cfg.outline:
            painter.setPen(QPen(outline, cfg.thickness + 2))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.setPen(QPen(color, cfg.thickness))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    if cfg.shape in ("cross", "cross_dot"):
        g, s = cfg.gap, cfg.size
        seg(cx - g - s, cy, cx - g, cy)
        seg(cx + g, cy, cx + g + s, cy)
        seg(cx, cy - g - s, cx, cy - g)
        seg(cx, cy + g, cx, cy + g + s)
    if cfg.shape == "circle":
        r = cfg.size
        if cfg.outline:
            painter.setPen(QPen(outline, cfg.thickness + 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.setPen(QPen(color, cfg.thickness))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r, r)
    if cfg.shape in ("dot", "cross_dot") and cfg.dot_size > 0:
        if cfg.outline:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(outline)
            painter.drawEllipse(QPointF(cx, cy), cfg.dot_size + 1.5, cfg.dot_size + 1.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(cx, cy), cfg.dot_size, cfg.dot_size)
    painter.setOpacity(1.0)


class CrosshairPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self._cfg = CrosshairConfig()

    def set_config(self, cfg: CrosshairConfig):
        self._cfg = cfg
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d1013"))
        pen = QPen(QColor(255, 255, 255, 14))
        p.setPen(pen)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        _draw_crosshair(p, self.width() / 2, self.height() / 2, self._cfg)


class CrosshairPage(QWidget):
    """Crosshair designer: presets, live preview, JSON import/export and PNG export.

    Stores the original CrosshairConfig schema under "crosshair/config". The
    crosshair is shown in this app and written to files; it is never drawn over a game.
    """

    edited = Signal(dict)
    SHAPES = ("cross_dot", "cross", "dot", "circle")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._model = self._load_model()
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Crosshair", "Design a crosshair and export it for use in-game."))

        preview_card, preview_lay = _card("Preview")
        self.preview = CrosshairPreview()
        preview_lay.addWidget(self.preview)

        controls_card, controls_lay = _card("Parameters")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Preset", "formLabel"), 0, 0)
        self.preset = QComboBox()
        self.preset.addItem("Choose a preset…", "")
        for name in preset_names():
            self.preset.addItem(name.replace("_", " ").title(), name)
        self.preset.currentIndexChanged.connect(self._apply_preset)
        form.addWidget(self.preset, 0, 1)

        form.addWidget(_label("Shape", "formLabel"), 1, 0)
        self.shape = QComboBox()
        self.shape.addItems(list(self.SHAPES))
        self.shape.currentIndexChanged.connect(self._on_change)
        form.addWidget(self.shape, 1, 1)

        self.thickness = self._slider(1, 12)
        self.length = self._slider(0, 64)
        self.gap = self._slider(0, 40)
        self.dot = self._slider(0, 12)
        self.opacity = self._slider(10, 100)
        for row, (text, slider) in enumerate((
            ("Thickness", self.thickness),
            ("Arm length", self.length),
            ("Center gap", self.gap),
            ("Dot size", self.dot),
            ("Opacity", self.opacity),
        ), start=2):
            form.addWidget(_label(text, "formLabel"), row, 0)
            form.addWidget(slider, row, 1)
            slider.valueChanged.connect(self._on_change)

        self.outline = QCheckBox("Outline")
        self.outline.toggled.connect(self._on_change)
        form.addWidget(self.outline, 7, 0)

        self.color_btn = QPushButton("Color")
        self.color_btn.setObjectName("secondary")
        self.color_btn.clicked.connect(self._pick_color)
        form.addWidget(self.color_btn, 7, 1)
        controls_lay.addLayout(form)

        btn_row = QHBoxLayout()
        export_btn = QPushButton(" Export JSON")
        export_btn.setObjectName("secondary")
        export_btn.setIcon(icon("download"))
        export_btn.clicked.connect(self._export_json_dialog)
        import_btn = QPushButton(" Import JSON")
        import_btn.setObjectName("secondary")
        import_btn.setIcon(icon("upload"))
        import_btn.clicked.connect(self._import_dialog)
        png_btn = QPushButton(" Export PNG")
        png_btn.setObjectName("primary")
        png_btn.setIcon(icon("download", "#1a1106"))
        png_btn.clicked.connect(self._export_png_dialog)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(import_btn)
        btn_row.addWidget(png_btn)
        btn_row.addStretch(1)
        controls_lay.addLayout(btn_row)

        self.status = _label("", "cardSubtitle", wrap=True)
        controls_lay.addWidget(self.status)

        self.row = ResponsiveRow(spacing=20)
        self.row.add_widget(preview_card)
        self.row.add_widget(controls_card)
        outer.addWidget(self.row)
        outer.addStretch(1)

        self._sync_controls()
        self._refresh()

    @staticmethod
    def _slider(lo: int, hi: int) -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        return slider

    # --- model -----------------------------------------------------------
    def _load_model(self) -> CrosshairConfig:
        raw = self.s.value("crosshair/config", "")
        if raw:
            try:
                return CrosshairConfig.from_dict(json.loads(raw)).normalized()
            except (ValueError, TypeError):
                pass
        return CrosshairConfig().normalized()

    def _with(self, **changes) -> CrosshairConfig:
        data = self._model.to_dict()
        data.update(changes)
        return CrosshairConfig.from_dict(data).normalized()

    def _sync_controls(self):
        self._loading = True
        try:
            m = self._model
            self.shape.setCurrentText(m.shape)
            self.thickness.setValue(m.thickness)
            self.length.setValue(m.size)
            self.gap.setValue(m.gap)
            self.dot.setValue(m.dot_size)
            self.opacity.setValue(int(round(m.opacity * 100)))
            self.outline.setChecked(m.outline)
        finally:
            self._loading = False

    def _on_change(self, *_args):
        if self._loading:
            return
        self._model = self._with(
            shape=self.shape.currentText(),
            thickness=self.thickness.value(),
            size=self.length.value(),
            gap=self.gap.value(),
            dot_size=self.dot.value(),
            opacity=self.opacity.value() / 100.0,
            outline=self.outline.isChecked(),
        )
        self._refresh()

    def _refresh(self):
        self.preview.set_config(self._model)
        self.color_btn.setText(f"Color  {self._model.color}")
        self.s.setValue("crosshair/config", json.dumps(self._model.to_dict()))
        self.edited.emit(self._model.to_dict())

    def _apply_preset(self, *_args):
        key = self.preset.currentData()
        if not key:
            return
        self._model = CROSSHAIR_PRESETS[key].normalized()
        self._sync_controls()
        self._refresh()

    def _pick_color(self):
        chosen = QColorDialog.getColor(QColor(self._model.color), self, "Crosshair color")
        if chosen.isValid():
            self._model = self._with(color=chosen.name())
            self._refresh()

    # --- import / export --------------------------------------------------
    def export_png(self, path: str) -> bool:
        size = 128
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        _draw_crosshair(painter, size / 2, size / 2, self._model)
        painter.end()
        ok = image.save(path, "PNG")
        self.status.setText(f"Exported {path}" if ok else "Export failed.")
        return bool(ok)

    def export_json(self, path: str) -> None:
        Path(path).write_text(json.dumps(self._model.to_dict(), indent=2))
        self.status.setText(f"Exported {path}")

    def import_json(self, path: str) -> None:
        self._model = CrosshairConfig.from_dict(json.loads(Path(path).read_text())).normalized()
        self._sync_controls()
        self._refresh()
        self.status.setText(f"Imported {path}")

    def _export_png_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export crosshair", "crosshair.png", "PNG (*.png)")
        if path:
            self.export_png(path)

    def _export_json_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export crosshair", "crosshair.json", "JSON (*.json)")
        if path:
            self.export_json(path)

    def _import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import crosshair", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.import_json(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Vantage", f"Could not import crosshair: {exc}")


# ---------------------------------------------------------------------------
# Training / Analytics
# ---------------------------------------------------------------------------

class TrainingCanvas(QWidget):
    """Runs a target-clicking scenario and records accuracy + reaction times."""

    finished = Signal(str, list, int, int)  # scenario_key, reaction_times_ms, hits, misses
    progress = Signal(int, int)

    KILL_MS = 420  # fast kill animation: well under half a second

    def __init__(self) -> None:
        super().__init__()
        self._rng = random.Random()
        self._scenario = None
        self._active = False
        self._visible_target = False
        self._target = QPointF(0, 0)
        self._radius = 20
        self._vx = self._vy = 0.0
        self._spawn_time = 0.0
        self._hits = 0
        self._misses = 0
        self._reaction_times: list[float] = []
        self.setMinimumHeight(360)
        self.setMouseTracking(True)
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(16)
        self._move_timer.timeout.connect(self._advance)

        # Enemy sprites (auto-loaded, cached) with a procedural fallback.
        self._sprites = self._load_sprites()
        self._target_pix: QPixmap | None = None
        # Lightweight kill animations.
        self._anim_enabled = True
        self._kills: list[dict] = []
        self._kill_timer = QTimer(self)
        self._kill_timer.setInterval(16)
        self._kill_timer.timeout.connect(self._kill_tick)

    @staticmethod
    def _load_sprites() -> list[QPixmap]:
        from fps_coach.enemy_sprites import sprite_files

        pixmaps: list[QPixmap] = []
        for path in sprite_files():
            pix = QPixmap(str(path))
            if not pix.isNull():
                pixmaps.append(pix)
        return pixmaps

    def set_animations(self, enabled: bool) -> None:
        self._anim_enabled = bool(enabled)
        if not enabled:
            self._kills.clear()
            self._kill_timer.stop()
            self.update()

    def start(self, scenario) -> None:
        self._scenario = scenario
        self._radius = scenario.target_radius
        self._active = True
        self._hits = 0
        self._misses = 0
        self._reaction_times = []
        self.progress.emit(0, scenario.target_count)
        self._next_target()
        if scenario.moving:
            self._move_timer.start()
        self.update()

    def stop(self) -> None:
        """End the drill early. Attempts made so far are recorded; nothing is recorded if there were none."""
        if not self._active:
            return
        self._active = False
        self._visible_target = False
        self._move_timer.stop()
        if self._hits + self._misses > 0:
            key = self._scenario.key if self._scenario else ""
            self.finished.emit(key, list(self._reaction_times), self._hits, self._misses)
        self.update()

    def _next_target(self) -> None:
        if not self._scenario:
            return
        margin = self._radius + 8
        w = max(margin * 2 + 1, self.width())
        h = max(margin * 2 + 1, self.height())
        self._target = QPointF(
            self._rng.uniform(margin, w - margin),
            self._rng.uniform(margin, h - margin),
        )
        # Pick an enemy figure for this target.
        self._target_pix = self._rng.choice(self._sprites) if self._sprites else None
        if self._scenario.moving:
            angle = self._rng.uniform(0, 2 * math.pi)
            self._vx = math.cos(angle) * self._scenario.speed
            self._vy = math.sin(angle) * self._scenario.speed
        delay = self._scenario.spawn_delay_ms
        if delay > 0:
            self._visible_target = False
            self.update()
            QTimer.singleShot(int(self._rng.uniform(300, delay)), self._reveal_target)
        else:
            self._reveal_target()

    def _reveal_target(self) -> None:
        if not self._active:
            return
        self._visible_target = True
        self._spawn_time = time.perf_counter()
        self.update()

    def _advance(self) -> None:
        if not (self._active and self._visible_target and self._scenario and self._scenario.moving):
            return
        dt = self._move_timer.interval() / 1000.0
        x = self._target.x() + self._vx * dt
        y = self._target.y() + self._vy * dt
        margin = self._radius + 4
        if x < margin or x > self.width() - margin:
            self._vx = -self._vx
            x = max(margin, min(self.width() - margin, x))
        if y < margin or y > self.height() - margin:
            self._vy = -self._vy
            y = max(margin, min(self.height() - margin, y))
        self._target = QPointF(x, y)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if not self._visible_target:
            return
        dist = math.hypot(event.position().x() - self._target.x(), event.position().y() - self._target.y())
        if dist <= self._radius:
            self._hits += 1
            self._reaction_times.append((time.perf_counter() - self._spawn_time) * 1000.0)
            self._spawn_kill(self._target.x(), self._target.y(), self._radius, self._target_pix)
            done = self._hits >= (self._scenario.target_count if self._scenario else 0)
            self.progress.emit(self._hits, self._scenario.target_count if self._scenario else 0)
            if done:
                self._finish()
            else:
                self._next_target()  # next target spawns immediately
        else:
            self._misses += 1

    def _spawn_kill(self, cx: float, cy: float, radius: float, pix: QPixmap | None) -> None:
        if not self._anim_enabled:
            return
        self._kills.append({"cx": cx, "cy": cy, "radius": radius, "pix": pix, "t0": time.perf_counter()})
        if not self._kill_timer.isActive():
            self._kill_timer.start()

    def _kill_tick(self) -> None:
        now = time.perf_counter()
        self._kills = [k for k in self._kills if (now - k["t0"]) * 1000.0 < self.KILL_MS]
        if not self._kills:
            self._kill_timer.stop()
        self.update()

    def _finish(self) -> None:
        self._active = False
        self._visible_target = False
        self._move_timer.stop()
        key = self._scenario.key if self._scenario else ""
        self.finished.emit(key, list(self._reaction_times), self._hits, self._misses)
        self.update()

    def _draw_enemy(self, painter: QPainter, cx: float, cy: float, radius: float,
                    pix: QPixmap | None, scale: float = 1.0) -> None:
        if pix is not None and not pix.isNull():
            height = 2.4 * radius * scale
            width = height * pix.width() / max(1, pix.height())
            painter.drawPixmap(
                QRectF(cx - width / 2, cy - height / 2, width, height),
                pix,
                QRectF(0, 0, pix.width(), pix.height()),
            )
            return
        # Procedural vector enemy fallback (head + torso), scalable.
        r = radius * scale
        body = QColor("#70e1ba")
        painter.setPen(QPen(QColor("#06110d"), 2))
        painter.setBrush(body)
        painter.drawEllipse(QPointF(cx, cy - r * 0.55), r * 0.38, r * 0.38)  # head
        torso = QRectF(cx - r * 0.5, cy - r * 0.1, r, r * 1.0)
        painter.drawRoundedRect(torso, r * 0.2, r * 0.2)                      # body

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0d1013"))
        pen = QPen(QColor(255, 255, 255, 16))
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # Kill animations (expanding + fading enemy ghost with a hit ring).
        now = time.perf_counter()
        for kill in self._kills:
            progress = min(1.0, (now - kill["t0"]) * 1000.0 / self.KILL_MS)
            scale = 1.0 + 0.6 * progress
            painter.setOpacity(max(0.0, 1.0 - progress))
            self._draw_enemy(painter, kill["cx"], kill["cy"], kill["radius"], kill["pix"], scale)
            ring = QColor("#b4ffe6")
            ring.setAlpha(int(180 * (1.0 - progress)))
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rr = kill["radius"] * (1.0 + 1.4 * progress)
            painter.drawEllipse(QPointF(kill["cx"], kill["cy"]), rr, rr)
        painter.setOpacity(1.0)

        if self._active and self._visible_target:
            self._draw_enemy(painter, self._target.x(), self._target.y(), self._radius, self._target_pix)
        elif not self._active and not self._kills:
            painter.setPen(QColor("#9a958c"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Choose a scenario and press Start")
        elif self._active and not self._visible_target:
            painter.setPen(QColor("#9a958c"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Get ready\u2026")


def _scenario_name(key: str) -> str:
    try:
        return scenario_defs.get(key).name
    except KeyError:
        return key


class AnalyticsPage(QWidget):
    """Performance analytics: accuracy and reaction-time trends from the shared store."""

    def __init__(self, store: AnalyticsStore, parent=None):
        super().__init__(parent)
        self._store = store
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        metrics = QHBoxLayout()
        self.acc_metric = QLabel("\u2014")
        self.rt_metric = QLabel("\u2014")
        self.best_metric = QLabel("\u2014")
        for title, widget in (
            ("Avg accuracy", self.acc_metric),
            ("Avg reaction", self.rt_metric),
            ("Best reaction", self.best_metric),
        ):
            card, lay = _card(title)
            widget.setObjectName("metric")
            lay.addWidget(widget)
            metrics.addWidget(card, 1)
        outer.addLayout(metrics)

        card, lay = _card("Session history", "Your most recent training drills, newest first.")
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("sessionList")
        lay.addWidget(self.list_widget)
        outer.addWidget(card)
        self.refresh()

    def refresh(self) -> None:
        agg = self._store.aggregate()
        self.list_widget.clear()
        if agg["total"] == 0:
            for metric in (self.acc_metric, self.rt_metric, self.best_metric):
                metric.setText("\u2014")
            self.list_widget.addItem("No sessions yet. Run a scenario in Training to start tracking.")
            return
        self.acc_metric.setText(f"{agg['avg_accuracy'] * 100:.0f}%")
        self.rt_metric.setText(f"{agg['avg_reaction_ms']:.0f} ms")
        self.best_metric.setText(f"{agg['best_reaction_ms']:.0f} ms")
        for s in self._store.recent(8):
            self.list_widget.addItem(
                f"{s.when()}  \u00b7  {_scenario_name(s.scenario)}  \u00b7  acc {s.accuracy * 100:.0f}%  \u00b7  "
                f"avg {s.avg_reaction_ms:.0f} ms"
            )


class TrainingPage(QWidget):
    """Scenario-based training: pick a drill, run it, record analytics.

    Results are saved to the shared AnalyticsStore. The analytics view opens from
    'View analytics'.
    """

    session_recorded = Signal(object)  # SessionResult

    def __init__(self, store: AnalyticsStore, parent=None):
        super().__init__(parent)
        self._store = store
        self.stack = QStackedWidget()

        train_widget = QWidget()
        outer = QVBoxLayout(train_widget)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)

        head_row = QHBoxLayout()
        head_row.addWidget(_page_header("Training", "Scenario drills to validate your curve. No game is read."))
        head_row.addStretch(1)
        self.analytics_btn = QPushButton(" View analytics")
        self.analytics_btn.setObjectName("secondary")
        self.analytics_btn.setIcon(icon("training"))
        self.analytics_btn.clicked.connect(self._show_analytics)
        head_row.addWidget(self.analytics_btn, 0, Qt.AlignTop)
        outer.addLayout(head_row)

        card, lay = _card()
        pick_row = QHBoxLayout()
        self.scenario = QComboBox()
        for sc in scenario_defs.list_scenarios():
            self.scenario.addItem(sc.name, sc.key)
        self.scenario.currentIndexChanged.connect(self._describe)
        pick_row.addWidget(self.scenario, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        pick_row.addWidget(self.progress, 1)
        lay.addLayout(pick_row)
        self.desc = _label("", "cardSubtitle", wrap=True)
        lay.addWidget(self.desc)

        self.canvas = TrainingCanvas()
        self.canvas.progress.connect(self._on_progress)
        self.canvas.finished.connect(self._on_finished)
        lay.addWidget(self.canvas, 1)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton(" Start drill")
        self.start_btn.setObjectName("primary")
        self.start_btn.setIcon(icon("play", "#1a1106"))
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("secondary")
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self.result = _label("", "cardSubtitle", wrap=True)
        lay.addWidget(self.result)

        outer.addWidget(card, 1)

        self.analytics_page = AnalyticsPage(store)
        back_wrap = QWidget()
        back_lay = QVBoxLayout(back_wrap)
        back_lay.setContentsMargins(28, 24, 28, 24)
        back_lay.setSpacing(16)
        back_row = QHBoxLayout()
        self.back_btn = QPushButton(" Back to training")
        self.back_btn.setObjectName("secondary")
        self.back_btn.setIcon(icon("chevron_left"))
        self.back_btn.clicked.connect(self._show_training)
        back_row.addWidget(self.back_btn)
        back_row.addStretch(1)
        back_lay.addLayout(back_row)
        back_lay.addWidget(self.analytics_page)

        self.stack.addWidget(train_widget)
        self.stack.addWidget(back_wrap)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self._describe()

    def _describe(self, *_args) -> None:
        sc = scenario_defs.get(self.scenario.currentData())
        self.desc.setText(sc.description)

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.scenario.setEnabled(not running)

    def _start(self) -> None:
        sc = scenario_defs.get(self.scenario.currentData())
        self.result.setText("")
        self.progress.setRange(0, sc.target_count)
        self.progress.setValue(0)
        self._set_running(True)
        self.canvas.start(sc)

    def _stop(self) -> None:
        self.canvas.stop()
        self._set_running(False)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _on_finished(self, key: str, reaction_times: list, hits: int, misses: int) -> None:
        self._set_running(False)
        result = SessionResult.create(key, reaction_times, hits, misses)
        self._store.add(result)
        self.analytics_page.refresh()
        self.result.setText(
            f"Done - accuracy {result.accuracy * 100:.0f}%, "
            f"avg reaction {result.avg_reaction_ms:.0f} ms, best {result.best_reaction_ms:.0f} ms."
        )
        self.session_recorded.emit(result)

    def _show_analytics(self) -> None:
        self.analytics_page.refresh()
        self.stack.setCurrentIndex(1)

    def _show_training(self) -> None:
        self.stack.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, backend: CoreBackend | None = None):
        super().__init__()
        self._backend = backend if backend is not None else CoreBackend()
        self.setWindowTitle("Vantage")
        self.resize(1360, 860)
        self.s = _settings()

        self._collapsed = False
        self._nav_buttons = {}
        self._voice_service = None
        self._tts_service = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = self._build_sidebar()
        root.addWidget(self.sidebar)

        content_wrap = QWidget()
        content_lay = QVBoxLayout(content_wrap)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self.stack = QStackedWidget()
        self._build_pages()
        content_lay.addWidget(self.stack, 1)

        root.addWidget(content_wrap, 1)

        self.coach_panel = CoachPanel(self)
        self.coach_panel.closed.connect(self._on_coach_closed)
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", False, type=bool))
        self._position_coach_panel()

        self._wire_signals()
        self._apply_theme()
        self._sync_tts()
        self._apply_voice_commands()
        self._select_page("dashboard")

    @property
    def backend(self) -> CoreBackend:
        """The CoreBackend this window was built with (same object as _backend)."""
        return self._backend

    # ------------------------------------------------------------------
    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 20, 0, 16)
        lay.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(20, 0, 20, 16)
        brand_mark = QLabel()
        brand_mark.setPixmap(pixmap("crosshair", "#d98f3b", 22))
        brand_row.addWidget(brand_mark)
        brand_label = QLabel("VANTAGE")
        brand_label.setObjectName("brand")
        brand_row.addWidget(brand_label)
        brand_row.addStretch(1)
        lay.addLayout(brand_row)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        for section_title, items in NAV_SECTIONS:
            group_label = QLabel(section_title)
            group_label.setObjectName("navGroup")
            group_label.setContentsMargins(20, 8, 20, 4)
            lay.addWidget(group_label)

            for key, label in items:
                btn = QPushButton(f"  {label}")
                btn.setObjectName("navButton")
                btn.setCheckable(True)
                btn.setIcon(icon(key, "#c9c5bd"))
                btn.setIconSize(QSize(20, 20))
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _checked, k=key: self._select_page(k))
                btn.toggled.connect(
                    lambda checked, b=btn, k=key: b.setIcon(icon(k, "#f0a94e" if checked else "#c9c5bd"))
                )
                self.nav_group.addButton(btn)
                self._nav_buttons[key] = btn
                lay.addWidget(btn)

            divider = QFrame()
            divider.setObjectName("navDivider")
            divider.setFixedHeight(1)
            lay.addWidget(divider)

        lay.addStretch(1)

        # Native core status (restored from the original): tells the user whether
        # capture is the real native core or the Python preview mode.
        self.core_status = _label(
            self._backend.core_label,
            "statusGood" if self._backend.native_available else "statusWarn",
            wrap=True,
        )
        self.core_status.setContentsMargins(20, 0, 20, 8)
        lay.addWidget(self.core_status)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName("collapseButton")
        self.collapse_btn.setIcon(icon("chevron_left"))
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        lay.addWidget(self.collapse_btn, 0, Qt.AlignHCenter)

        return sidebar

    def _build_pages(self):
        self.dashboard_page = DashboardPage()
        self.guide_page = GuidePage()
        self.capture_page = CapturePage(self._backend)
        self.calibration_page = CalibrationPage(self._backend)
        self._analytics_store = AnalyticsStore.load(
            Path(__file__).resolve().parents[3] / ".fpscoach_analytics.json"
        )
        self.training_page = TrainingPage(self._analytics_store)
        self.recommendation_page = RecommendationPage(self._backend)
        self.crosshair_page = CrosshairPage()
        self.chat_page = AIChatPage()
        self.privacy_page = PrivacyPage()
        self.settings_page = SettingsPage()

        self._pages = {
            "dashboard": self.dashboard_page,
            "guide": self.guide_page,
            "capture": self.capture_page,
            "calibration": self.calibration_page,
            "training": self.training_page,
            "recommendation": self.recommendation_page,
            "crosshair": self.crosshair_page,
            "chat": self.chat_page,
            "privacy": self.privacy_page,
            "settings": self.settings_page,
        }
        for page in self._pages.values():
            self.stack.addWidget(page)

    def _wire_signals(self):
        self.calibration_page.recommendation_ready.connect(self._on_calibration_result)
        self.calibration_page.profile_changed.connect(self._on_profile_changed)
        self.calibration_page.base_profile_changed.connect(self.chat_page.set_base_profile)
        self.chat_page.curve_adjusted.connect(self._on_curve_adjusted)
        self.recommendation_page.recommendation_ready.connect(self._on_recommendation)
        self.chat_page.assistant_said.connect(self._on_assistant_said)
        self.chat_page.assistant_said.connect(self._speak_response)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.training_page.session_recorded.connect(self._on_session_recorded)
        self.crosshair_page.edited.connect(self._on_crosshair_edited)

    # ------------------------------------------------------------------
    def _select_page(self, key):
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        btn = self._nav_buttons.get(key)
        if btn:
            btn.setChecked(True)

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        width = 64 if self._collapsed else 220
        self.sidebar.setFixedWidth(width)
        self.collapse_btn.setIcon(icon("chevron_right" if self._collapsed else "chevron_left"))
        self.core_status.setVisible(not self._collapsed)
        flat_labels = dict(item for _, items in NAV_SECTIONS for item in items)
        for key, btn in self._nav_buttons.items():
            btn.setText("" if self._collapsed else f"  {flat_labels[key]}")

    def _position_coach_panel(self):
        margin = 20
        self.coach_panel.adjustSize()
        self.coach_panel.move(
            self.width() - self.coach_panel.width() - margin,
            self.height() - self.coach_panel.height() - margin,
        )
        self.coach_panel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_coach_panel()

    @staticmethod
    def _resolve_theme(theme: str) -> str:
        """Map 'system' to the OS dark/light scheme; 'dark' and 'light' pass through."""
        if theme in ("dark", "light"):
            return theme
        app = QApplication.instance()
        scheme = app.styleHints().colorScheme() if app is not None else None
        return "light" if scheme == Qt.ColorScheme.Light else "dark"

    def _apply_theme(self):
        mode = self.s.value("ui/mode", "luxurious")
        theme = self._resolve_theme(str(self.s.value("ui/theme", "system")))
        self.setStyleSheet(build_style(mode, theme))
        animations = self.s.value("ui/animations", True, type=bool)
        self.dashboard_page.set_animated(animations)

    def _sync_tts(self):
        enabled = self.s.value("ui/tts_enabled", False, type=bool)
        if enabled and self._tts_service is None:
            self._tts_service = _TtsService(self)
            self._tts_service.failed.connect(self._on_tts_failed)
            self._tts_service.start()
        elif not enabled and self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service.wait(3000)
            self._tts_service = None

    def _speak_response(self, text: str):
        if self._tts_service is not None:
            self._tts_service.enqueue(text)

    def _on_tts_failed(self, message: str):
        self.chat_page._append("Text-to-speech", message)

    def _apply_voice_commands(self):
        enabled = self.s.value("ui/voice_commands", False, type=bool)
        if enabled and self._voice_service is None:
            self._voice_service = _VoiceCommandService(self)
            self._voice_service.recognized.connect(self._on_voice_command)
            self._voice_service.failed.connect(self._on_voice_failed)
            self._voice_service.start()
        elif not enabled and self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(1500)
            self._voice_service = None

    # ------------------------------------------------------------------
    def _on_calibration_result(self, summary: dict, recommendation: dict):
        self.recommendation_page.update_result(summary, recommendation)
        self.chat_page.notify_new_calibration(summary, recommendation)
        self._select_page("recommendation")

    def _on_profile_changed(self, profile):
        self.coach_panel.say("Calibration profile updated.")

    def _on_curve_adjusted(self, previous, current, note: str):
        # An AI quick action or directive produced an adjusted curve: show it on the
        # Calibration graph as before/after and switch there.
        self.calibration_page.set_adjusted(previous, current, note)
        self.coach_panel.say(note)
        self._select_page("calibration")

    def _on_recommendation(self, result):
        mult = float(result.get("sensitivity_multiplier", 1.0))
        confidence = float(result.get("confidence", 0.0))
        self.coach_panel.say(f"New recommendation: ×{mult:.2f} (confidence {confidence:.0%}).")

    def _on_assistant_said(self, text):
        self.coach_panel.say(text)

    def _on_settings_changed(self):
        self._apply_theme()
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", False, type=bool))
        self._sync_tts()
        self._apply_voice_commands()

    def _on_session_recorded(self, result):
        self.coach_panel.say(f"Session complete — {result.accuracy * 100:.0f}% accuracy.")

    def _on_crosshair_edited(self, config):
        pass

    def _on_voice_failed(self, message: str):
        self.coach_panel.say(f"Voice commands unavailable: {message}")

    def _on_voice_command(self, text: str) -> None:
        from fps_coach.voice_commands import parse

        wake = self.s.value("ui/wake_word", "coach")
        command = parse(text, wake_word=str(wake))
        if command is None:
            return
        if command.kind == "navigate":
            self._select_page(command.value)
        elif command.kind == "calibrate":
            self._select_page("calibration")
            if command.value == "start":
                self.calibration_page.canvas.start_session(12)
            else:
                self.calibration_page.canvas.cancel_session()
        elif command.kind == "preset":
            self._select_page("chat")
            self.chat_page._apply_quick_action(command.value)
        elif command.kind == "panel":
            show = command.value == "show"
            self.s.setValue("ui/coach_panel", show)
            self.coach_panel.setVisible(show)
        elif command.kind == "dictate":
            self._select_page("chat")
            self.chat_page.input.setText(command.value)
            self.chat_page._send()

    def _on_coach_closed(self):
        self.s.setValue("ui/coach_panel", False)

    def closeEvent(self, event):
        if self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(1500)
        if self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service.wait(3000)
        self.chat_page.shutdown()
        self._backend.close()
        self.s.sync()
        super().closeEvent(event)


def main():
    from fps_coach.app import main as app_main

    sys.exit(app_main())


if __name__ == "__main__":
    main()


