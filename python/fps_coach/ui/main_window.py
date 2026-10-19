from __future__ import annotations

import html
import math
import random
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from fps_coach import scenarios as scenario_defs
from fps_coach.ai_chat import (
    OLLAMA_DOWNLOAD_URL,
    AIChatClient,
    ChatProviderError,
    ChatSettings,
    check_ollama,
    curve_context,
    pull_model,
)
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.backend import CoreBackend, NativeCoreUnavailable
from fps_coach.crosshair import PRESETS as CROSSHAIR_PRESETS
from fps_coach.crosshair import CrosshairConfig
from fps_coach.curve_adjust import (
    adjust_from_recommendation,
    apply_preset,
    normalized_overlay,
)
from fps_coach.curve_io import (
    CurveFormatError,
    CurveProfile,
    chart_points,
    load_curve,
    recommendation_profile,
    save_curve,
)
from fps_coach.dashboard_config import move as dashboard_move
from fps_coach.dashboard_config import normalize_enabled as dashboard_normalize_enabled
from fps_coach.dashboard_config import normalize_order as dashboard_normalize_order
from fps_coach.dashboard_config import tool_description as dashboard_tool_description
from fps_coach.dashboard_config import tool_label as dashboard_tool_label
from fps_coach.ui.calibration import CalibrationCanvas
from fps_coach.ui.responsive import ResponsiveCards
from fps_coach.ui.styles import PALETTES, build_style
from fps_coach.window_enum import get_window_rect, list_windows


def _label(text: str, object_name: str | None = None, wrap: bool = True) -> QLabel:
    value = QLabel(text)
    value.setWordWrap(wrap)
    if object_name:
        value.setObjectName(object_name)
    return value


def _page_header(eyebrow: str, title: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(5)
    layout.addWidget(_label(eyebrow.upper(), "eyebrow"))
    layout.addWidget(_label(title, "pageTitle"))
    layout.addWidget(_label(subtitle, "pageSubtitle"))
    layout.addSpacing(16)
    return layout


def _card(title: str, body: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(9)
    layout.addWidget(_label(title, "cardTitle"))
    layout.addWidget(_label(body, "muted"))
    return frame


def _scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


class GuidePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Start here",
                "A calmer way to tune your aim",
                "Measure first, make one small change, and verify it. This tool never aims or changes input for you.",
            )
        )

        steps = ResponsiveCards(
            [
                _card(
                    "01  Verify capture",
                    "Choose the display that owns your borderless game. The native core uses Windows Desktop Duplication and keeps only a bounded center region by default.",
                ),
                _card(
                    "02  Run calibration",
                    "Click 12 targets inside this app. Move as you normally would—the local model measures efficiency, overshoot, corrections, and small oscillations.",
                ),
                _card(
                    "03  Review, don't automate",
                    "Use the suggested curve as a starting point in your own approved mouse or game settings. Change one variable at a time and keep your original profile.",
                ),
                _card(
                    "Good baseline",
                    "Disable pointer acceleration for the calibration, use the DPI and polling rate you normally play with, warm up first, and repeat after a hardware change.",
                ),
            ]
        )
        root.addWidget(steps)
        root.addSpacing(12)

        boundaries = _card(
            "Designed as a coach—not a game automation tool",
            "No process injection, memory reading, synthetic input, target detection, or automatic setting changes. Screen pixels remain local and the calibration runs only while you explicitly start it.",
        )
        root.addWidget(boundaries)

        root.addSpacing(18)
        root.addLayout(
            _page_header(
                "What's new",
                "New features in this build",
                "Everything below works automatically—here is what each one does.",
            )
        )
        whats_new = ResponsiveCards(
            [
                _card(
                    "Lite & Full install packages",
                    "Two installers are available. Lite is a small download with no AI bundled—you install Ollama and the model is fetched on first use. Full embeds the Ollama runtime and the Qwen 3 1.7B model so the AI works offline right out of the box.",
                ),
                _card(
                    "Startup self-repair",
                    "If you move or rename the FPS Coach folder, the app detects the change on startup and repairs its own installation automatically—no reinstall, no manual scripts.",
                ),
                _card(
                    "AI chat profile setup",
                    "The assistant is pre-configured from config/ai_profile.toml, which holds its persona, coaching instructions, and recommendations. Edit that file to retune the coach; your built-in safety rules always stay in effect.",
                ),
                _card(
                    "Automatic Ollama launch",
                    "When the app starts it quietly launches the local Ollama service in the background, so the AI coach is ready the moment you open the AI chat—nothing to start by hand.",
                ),
                _card(
                    "One-click model download",
                    "If the selected model isn't on your PC yet, a Download button appears right next to the model picker in AI chat. One click fetches it, with live progress.",
                ),
                _card(
                    "Appearance & performance",
                    "Open Settings to switch between Luxurious visuals and High-performance mode, and to turn animations on or off—tune the look to your taste and your hardware.",
                ),
            ]
        )
        root.addWidget(whats_new)
        root.addStretch()


class CapturePage(QWidget):
    def __init__(self, backend: CoreBackend) -> None:
        super().__init__()
        self._backend = backend
        self._last_sequence = -1
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Native pipeline",
                "Low-latency screen capture",
                "A dedicated C++ worker owns DXGI and D3D11 so Python stays out of the frame loop.",
            )
        )

        config_card = QFrame()
        config_card.setObjectName("card")
        config = QGridLayout(config_card)
        config.setContentsMargins(20, 18, 20, 18)
        config.setHorizontalSpacing(14)
        config.setVerticalSpacing(10)

        config.addWidget(_label("Display output", "muted"), 0, 0)
        config.addWidget(_label("Capture region", "muted"), 0, 1)
        config.addWidget(_label("Frame cap", "muted"), 0, 2)
        self.output_combo = QComboBox()
        self.region_combo = QComboBox()
        self.region_combo.addItem("Center 640 × 360 · iGPU friendly", (640, 360))
        self.region_combo.addItem("Center 960 × 540", (960, 540))
        self.region_combo.addItem("Full output · high bandwidth", (0, 0))
        self.fps_combo = QComboBox()
        self.fps_combo.addItem("30 FPS", 30)
        self.fps_combo.addItem("60 FPS", 60)
        self.fps_combo.addItem("120 FPS", 120)
        self.fps_combo.setCurrentIndex(1)
        config.addWidget(self.output_combo, 1, 0)
        config.addWidget(self.region_combo, 1, 1)
        config.addWidget(self.fps_combo, 1, 2)

        # Capture source: restrict the preview to a specific application window.
        config.addWidget(_label("Capture source", "muted"), 2, 0)
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        source_row.addWidget(self.source_combo, 1)
        refresh_windows = QPushButton("Refresh windows")
        refresh_windows.setObjectName("secondary")
        refresh_windows.clicked.connect(self._load_windows)
        source_row.addWidget(refresh_windows)
        config.addLayout(source_row, 2, 1, 1, 2)

        # Performance: low-end preset + hardware-acceleration preference.
        config.addWidget(_label("Performance", "muted"), 3, 0)
        perf_row = QHBoxLayout()
        self.perf_combo = QComboBox()
        self.perf_combo.addItem("Low-end  \u00b7  640\u00d7360 @ 30 FPS", "low")
        self.perf_combo.addItem("Balanced  \u00b7  960\u00d7540 @ 60 FPS", "balanced")
        self.perf_combo.addItem("Quality  \u00b7  full @ 120 FPS", "quality")
        self.perf_combo.addItem("Custom", "custom")
        self.perf_combo.currentIndexChanged.connect(self._apply_perf_preset)
        perf_row.addWidget(self.perf_combo)
        self.hwaccel = QCheckBox("Hardware acceleration")
        self.hwaccel.setChecked(True)
        self.hwaccel.setToolTip("Use the GPU (D3D11) for capture/scaling. Turn off only to debug.")
        perf_row.addWidget(self.hwaccel)
        perf_row.addStretch(1)
        config.addLayout(perf_row, 3, 1, 1, 2)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start capture")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._toggle_capture)
        refresh = QPushButton("Refresh displays")
        refresh.setObjectName("secondary")
        refresh.clicked.connect(self._load_outputs)
        controls.addWidget(self.start_button)
        controls.addWidget(refresh)
        controls.addStretch()
        config.addLayout(controls, 4, 0, 1, 3)
        root.addWidget(config_card)

        # Restore saved performance preferences.
        saved = QSettings()
        self.hwaccel.setChecked(saved.value("capture/hwaccel", True, type=bool))
        preset_index = max(0, self.perf_combo.findData(str(saved.value("capture/preset", "balanced"))))
        self.perf_combo.setCurrentIndex(preset_index)
        self.hwaccel.toggled.connect(lambda v: QSettings().setValue("capture/hwaccel", bool(v)))
        self._load_windows()
        root.addSpacing(12)

        content = QHBoxLayout()
        preview_card = QFrame()
        preview_card.setObjectName("card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.addWidget(_label("Latest bounded frame", "cardTitle"))
        self.preview = QLabel("Start capture to preview the selected output")
        self.preview.setObjectName("muted")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        self.preview.setStyleSheet("background:#080b10; border-radius:8px;")
        preview_layout.addWidget(self.preview, 1)
        content.addWidget(preview_card, 3)

        stats_card = QFrame()
        stats_card.setObjectName("card")
        stats = QVBoxLayout(stats_card)
        stats.setContentsMargins(18, 18, 18, 18)
        stats.addWidget(_label("Pipeline status", "cardTitle"))
        self.status_label = _label("Stopped", "statusWarn")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        stats.addWidget(self.status_label)
        stats.addSpacing(8)
        self.fps_label = _label("0.0", "metric")
        stats.addWidget(self.fps_label)
        stats.addWidget(_label("captured frames / second", "muted"))
        self.frame_count_label = _label("0 frames", "muted")
        self.region_label = _label("—", "muted")
        self.adapter_label = _label("—", "muted")
        stats.addSpacing(10)
        stats.addWidget(self.frame_count_label)
        stats.addWidget(self.region_label)
        stats.addWidget(self.adapter_label)
        stats.addStretch()
        self.error_label = _label("", "muted")
        stats.addWidget(self.error_label)
        content.addWidget(stats_card, 1)
        root.addLayout(content, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(250)  # UI preview rate, not capture rate.
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start()
        self._load_outputs()

    def _load_windows(self) -> None:
        self.source_combo.clear()
        self.source_combo.addItem("Entire display output", None)
        for win in list_windows():
            proc = f"  \u00b7  {win['process']}" if win.get("process") else ""
            title = win["title"]
            label = (title[:48] + "\u2026") if len(title) > 49 else title
            self.source_combo.addItem(f"{label}{proc}", int(win["hwnd"]))

    def _apply_perf_preset(self) -> None:
        preset = self.perf_combo.currentData()
        QSettings().setValue("capture/preset", str(preset))
        if preset == "low":
            self.region_combo.setCurrentIndex(0)   # 640x360
            self.fps_combo.setCurrentIndex(0)      # 30
        elif preset == "balanced":
            self.region_combo.setCurrentIndex(1)   # 960x540
            self.fps_combo.setCurrentIndex(1)      # 60
        elif preset == "quality":
            self.region_combo.setCurrentIndex(2)   # full
            self.fps_combo.setCurrentIndex(2)      # 120
        # 'custom' leaves the region/fps combos under manual control.

    def _load_outputs(self) -> None:
        self.output_combo.clear()
        outputs = self._backend.outputs()
        for output in outputs:
            name = f"{output['output_name']} · {output['adapter_name']} · {output['width']}×{output['height']}"
            self.output_combo.addItem(name, output)
        if not outputs:
            self.output_combo.addItem("No native Windows output detected", None)
            self.start_button.setEnabled(False)
        else:
            self.start_button.setEnabled(True)

    def _toggle_capture(self) -> None:
        stats = self._backend.capture_stats()
        if stats.get("running"):
            self._backend.stop_capture()
            self.start_button.setText("Start capture")
            return

        output = self.output_combo.currentData()
        if not output:
            return
        width, height = self.region_combo.currentData()
        left, top = -1, -1
        # If a specific window is selected, restrict capture to its screen region.
        hwnd = self.source_combo.currentData()
        if hwnd:
            rect = get_window_rect(int(hwnd))
            if rect:
                left, top, width, height = rect
            else:
                self.error_label.setText("Could not read that window's bounds; capturing the full region instead.")
        try:
            self._backend.start_capture(
                int(output["adapter_index"]),
                int(output["output_index"]),
                int(width),
                int(height),
                int(self.fps_combo.currentData()),
                int(left),
                int(top),
            )
            self.start_button.setText("Stop capture")
            self.error_label.clear()
        except NativeCoreUnavailable as error:
            self.error_label.setText(str(error))

    def _refresh_stats(self) -> None:
        stats = self._backend.capture_stats()
        running = bool(stats.get("running"))
        self.status_label.setText("Running" if running else "Stopped")
        self.status_label.setObjectName("statusGood" if running else "statusWarn")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.fps_label.setText(f"{float(stats.get('capture_fps', 0)):.1f}")
        self.frame_count_label.setText(f"{int(stats.get('frames_captured', 0)):,} frames retained/processed")
        width = int(stats.get("capture_width", 0))
        height = int(stats.get("capture_height", 0))
        self.region_label.setText(f"Region  {width} × {height}" if width else "Region  —")
        self.adapter_label.setText(f"Adapter  {stats.get('adapter_name') or '—'}")
        error = str(stats.get("last_error") or "")
        self.error_label.setText(error)

        if not running:
            self.start_button.setText("Start capture")
            return
        frame = self._backend.latest_frame()
        if not frame or int(frame["sequence"]) == self._last_sequence:
            return
        self._last_sequence = int(frame["sequence"])
        image = QImage(
            frame["bgra"],
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


class CalibrationPage(QWidget):
    recommendation_ready = Signal(dict, dict)

    def __init__(self, backend: CoreBackend) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Local movement model",
                "12-target calibration",
                "The analyzer sees timestamped pointer coordinates from this task—not game memory or raw input hooks.",
            )
        )

        toolbar = QHBoxLayout()
        self.start_button = QPushButton("Start calibration")
        self.start_button.setObjectName("primary")
        self.cancel_button = QPushButton("Reset")
        self.cancel_button.setObjectName("secondary")
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.cancel_button)
        toolbar.addSpacing(14)
        self.progress = QProgressBar()
        self.progress.setRange(0, 12)
        self.progress.setValue(0)
        self.progress.setFormat("%v / 12 targets")
        toolbar.addWidget(self.progress, 1)
        root.addLayout(toolbar)
        root.addSpacing(10)

        self.canvas = CalibrationCanvas(backend.analyzer)
        root.addWidget(self.canvas, 1)
        self.hint = _label("Start when you are comfortable and using your normal sensitivity.", "muted")
        root.addWidget(self.hint)

        self.start_button.clicked.connect(lambda: self.canvas.start_session(12))
        self.cancel_button.clicked.connect(self.canvas.cancel_session)
        self.canvas.progress_changed.connect(self._set_progress)
        self.canvas.hint_changed.connect(self.hint.setText)
        self.canvas.completed.connect(self._on_complete)

    def _set_progress(self, completed: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(completed)
        self.progress.setFormat(f"%v / {total} targets")

    def _on_complete(self, summary: dict, recommendation: dict) -> None:
        self.recommendation_ready.emit(summary, recommendation)


class CurveChart(QWidget):
    # Distinct colors for the before/after comparison.
    CURRENT_COLOR = "#70e1ba"   # mint - current / adjusted curve
    CURRENT_DOT = "#b4ffe6"
    PREVIOUS_COLOR = "#f0a860"  # amber - previous / imported curve

    # Emitted while/after the user drags a point in edit mode (normalized points).
    edited = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self._points: list[tuple[float, float]] = []
        self._previous: list[tuple[float, float]] = []
        self._current_label = "Current"
        self._previous_label = "Previous"
        self._editable = False
        self._drag_index = -1
        self.setMinimumHeight(290)

    # --- Manual edit mode -------------------------------------------------
    def set_editable(self, enabled: bool) -> None:
        self._editable = bool(enabled)
        self._drag_index = -1
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def _plot_rect(self):
        return self.rect().adjusted(48, 24, -24, -38)

    def _nearest_index(self, pos, plot) -> int:
        best, best_dist = -1, 1e9
        for i, (x, y) in enumerate(self._points):
            sx = plot.left() + x * plot.width()
            sy = plot.bottom() - y * plot.height()
            dist = math.hypot(sx - pos.x(), sy - pos.y())
            if dist < best_dist:
                best, best_dist = i, dist
        return best if best_dist <= 16 else -1

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if self._editable and self._points and event.button() == Qt.MouseButton.LeftButton:
            self._drag_index = self._nearest_index(event.position(), self._plot_rect())
            if self._drag_index >= 0:
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._editable and self._drag_index >= 0:
            plot = self._plot_rect()
            y = (plot.bottom() - event.position().y()) / max(1.0, plot.height())
            y = max(0.0, min(1.0, y))
            x = self._points[self._drag_index][0]
            self._points[self._drag_index] = (x, y)
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._editable and self._drag_index >= 0:
            self._drag_index = -1
            self.edited.emit(list(self._points))
            return
        super().mouseReleaseEvent(event)

    def set_points(self, points: list[tuple[float, float]]) -> None:
        """Show a single curve (clears any previous/comparison curve)."""
        self._points = [(float(x), float(y)) for x, y in points]
        self._previous = []
        self.update()

    def set_curves(
        self,
        current: list[tuple[float, float]],
        previous: list[tuple[float, float]] | None = None,
        current_label: str = "Current",
        previous_label: str = "Previous",
    ) -> None:
        """Show the current curve, optionally overlaid with a previous curve for
        a visual before/after comparison with distinct colors and labels."""
        self._points = [(float(x), float(y)) for x, y in current]
        self._previous = [(float(x), float(y)) for x, y in (previous or [])]
        self._current_label = current_label
        self._previous_label = previous_label
        self.update()

    def _draw_curve(self, painter: QPainter, plot, points: list[tuple[float, float]],
                    color: str, dot: str | None, width: int, dashed: bool) -> None:
        screen_points = [
            QPointF(plot.left() + x * plot.width(), plot.bottom() - y * plot.height())
            for x, y in points
        ]
        pen = QPen(QColor(color), width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for first, second in zip(screen_points, screen_points[1:]):
            painter.drawLine(first, second)
        if dot is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(dot))
            for point in screen_points:
                painter.drawEllipse(point, 4, 4)

    def _draw_legend(self, painter: QPainter, plot) -> None:
        entries = [(self._current_label, self.CURRENT_COLOR)]
        if self._previous:
            entries.insert(0, (self._previous_label, self.PREVIOUS_COLOR))
        x = plot.right() - 150
        y = plot.top() + 6
        for label, color in entries:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(x, y, 14, 14), 3, 3)
            painter.setPen(QColor("#cdd6e3"))
            painter.drawText(QRectF(x + 20, y - 3, 130, 20), Qt.AlignmentFlag.AlignLeft, label)
            y += 20

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c1118"))
        plot = self.rect().adjusted(48, 24, -24, -38)
        painter.setPen(QPen(QColor("#263241"), 1))
        for step in range(5):
            x = plot.left() + plot.width() * step / 4
            y = plot.bottom() - plot.height() * step / 4
            painter.drawLine(round(x), plot.top(), round(x), plot.bottom())
            painter.drawLine(plot.left(), round(y), plot.right(), round(y))
        painter.setPen(QColor("#758399"))
        painter.drawText(QRectF(0, plot.bottom() + 8, self.width(), 24), Qt.AlignmentFlag.AlignCenter, "Input speed")
        painter.save()
        painter.translate(18, self.height() / 2 + 30)
        painter.rotate(-90)
        painter.drawText(QRectF(0, 0, 100, 22), "Output speed")
        painter.restore()

        if not self._points:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Complete calibration to generate a curve")
            return
        # Draw the previous (imported) curve first, dimmer and dashed, then the
        # current (adjusted) curve on top.
        if self._previous:
            self._draw_curve(painter, plot, self._previous, self.PREVIOUS_COLOR, None, 2, dashed=True)
        self._draw_curve(painter, plot, self._points, self.CURRENT_COLOR, self.CURRENT_DOT, 3, dashed=False)
        self._draw_legend(painter, plot)
        if self._editable:
            # Larger draggable handles + a hint while editing.
            painter.setPen(QPen(QColor("#07110e"), 2))
            painter.setBrush(QColor(self.CURRENT_DOT))
            for x, y in self._points:
                center = QPointF(plot.left() + x * plot.width(), plot.bottom() - y * plot.height())
                painter.drawEllipse(center, 6, 6)
            painter.setPen(QColor("#8ff0cf"))
            painter.drawText(
                QRectF(plot.left(), plot.top() - 2, plot.width(), 18),
                Qt.AlignmentFlag.AlignLeft,
                "Edit mode: drag points up/down",
            )


class RecommendationPage(QWidget):
    # Emitted whenever the displayed curve changes (import, calibration, or an AI
    # adjustment) so the AI chat always has the current curve as its base.
    profile_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._profile: CurveProfile | None = None
        self._base_profile: CurveProfile | None = None  # the imported curve to adjust
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Explainable result",
                "Recommended response curve",
                "A local model turns movement features into a conservative curve. Import and export remain explicit user actions.",
            )
        )

        # Fixed side-by-side: recommendation/chart on the LEFT, calibration status
        # on the RIGHT. This stays horizontal when the window is resized/minimized
        # (it does not stack vertically).
        row = QHBoxLayout()
        row.setSpacing(16)
        chart_card = QFrame()
        chart_card.setObjectName("card")
        chart_card.setMinimumWidth(320)
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(16, 16, 16, 16)
        chart_header = QHBoxLayout()
        chart_header.addWidget(_label("Normalized curve geometry", "cardTitle"))
        chart_header.addStretch(1)
        self.edit_button = QPushButton("Edit")
        self.edit_button.setObjectName("secondary")
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip("Manually drag the curve points to adjust them")
        self.edit_button.toggled.connect(self._toggle_edit)
        chart_header.addWidget(self.edit_button)
        chart_layout.addLayout(chart_header)
        self.chart = CurveChart()
        self.chart.edited.connect(self._on_curve_edited)
        chart_layout.addWidget(self.chart)
        self._edit_max_gain = 1.0
        row.addWidget(chart_card, 3)

        summary_card = QFrame()
        summary_card.setObjectName("card")
        summary_card.setMinimumWidth(260)
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        self.title = _label("No calibration yet", "cardTitle")
        self.multiplier = _label("1.00×", "metric")
        self.confidence = _label("Confidence —", "muted")
        self.metrics = _label("Run calibration or import a curve.", "muted")
        self.reasons = _label("", "muted")
        summary_layout.addWidget(self.title)
        summary_layout.addSpacing(8)
        summary_layout.addWidget(self.multiplier)
        summary_layout.addWidget(_label("starting sensitivity multiplier", "muted"))
        summary_layout.addWidget(self.confidence)
        summary_layout.addSpacing(12)
        summary_layout.addWidget(self.metrics)
        summary_layout.addSpacing(8)
        summary_layout.addWidget(self.reasons)
        summary_layout.addStretch()
        row.addWidget(summary_card, 2)
        root.addLayout(row, 1)

        io_card = QFrame()
        io_card.setObjectName("card")
        io_layout = QHBoxLayout(io_card)
        io_layout.setContentsMargins(16, 14, 16, 14)
        io_layout.addWidget(_label("Compatibility", "cardTitle"))
        self.compatibility = QComboBox()
        self.compatibility.addItem("Custom Curve 4 (.cc4)", "cc4")
        self.compatibility.addItem("Custom Curve 3 / legacy (.ccurve)", "ccurve")
        io_layout.addWidget(self.compatibility, 1)
        import_button = QPushButton("Import")
        import_button.setObjectName("secondary")
        import_button.clicked.connect(self._import_curve)
        export_button = QPushButton("Export")
        export_button.setObjectName("primary")
        export_button.clicked.connect(self._export_curve)
        io_layout.addWidget(import_button)
        io_layout.addWidget(export_button)
        self.file_status = _label("Choose the format used by your installed Custom Curve version.", "muted")
        io_layout.addWidget(self.file_status, 2)
        root.addWidget(io_card)
        root.addWidget(
            _label(
                "Curve files are only written when you press Export. FPS Coach never applies a driver profile or edits game settings.",
                "muted",
            )
        )

    # --- Manual curve editing --------------------------------------------
    def _toggle_edit(self, checked: bool) -> None:
        if checked:
            if self._profile is None:
                self.edit_button.setChecked(False)
                self.file_status.setText("Import a curve or run calibration before editing.")
                return
            # Show just the current curve, normalized by its own gain, and allow
            # dragging the points up/down.
            self._edit_max_gain = max(
                (p.multiplier for p in self._profile.horizontal), default=1.0
            ) or 1.0
            self.chart.set_points(chart_points(self._profile))
            self.chart.set_editable(True)
            self.edit_button.setText("Done")
            self.file_status.setText("Edit mode: drag points up/down, then press Done.")
        else:
            self.chart.set_editable(False)
            self.edit_button.setText("Edit")
            if self._profile is not None:
                self.file_status.setText("Manual edits applied. Export when ready.")

    def _on_curve_edited(self, points: list) -> None:
        if self._profile is None:
            return
        # Map the edited normalized output back onto the curve multipliers
        # (speeds are unchanged; only gains are dragged).
        axis = self._profile.horizontal
        for i, (_x, y) in enumerate(points):
            if i < len(axis):
                axis[i].multiplier = max(0.05, min(20.0, float(y) * self._edit_max_gain))
        self._profile.vertical = deepcopy(self._profile.horizontal)
        self.multiplier.setText(f"{axis[0].multiplier:.2f}\u00d7")
        self.title.setText(self._profile.name)
        self.profile_changed.emit(deepcopy(self._profile))

    def _selected_format(self) -> str:
        return str(self.compatibility.currentData())

    def _import_curve(self) -> None:
        selected = self._selected_format()
        label = "Custom Curve 4 (*.cc4)" if selected == "cc4" else "Custom Curve 3 (*.ccurve)"
        path, _ = QFileDialog.getOpenFileName(self, "Import curve", "", label)
        if not path:
            return
        try:
            self._profile = load_curve(path, selected)
        except (CurveFormatError, OSError) as error:
            self.file_status.setText(f"Import failed: {error}")
            return
        # Remember the imported curve as the base for later adjustments.
        self._base_profile = deepcopy(self._profile)
        self.chart.set_points(chart_points(self._profile))
        self.title.setText(self._profile.name)
        first_gain = self._profile.horizontal[0].multiplier
        self.multiplier.setText(f"{first_gain:.2f}×")
        self.confidence.setText(f"Imported {self._profile.source_format} profile")
        self.metrics.setText(
            f"{len(self._profile.horizontal)} horizontal points\n"
            f"{len(self._profile.vertical)} vertical points\n"
            f"DPI metadata  {self._profile.mouse_dpi if self._profile.use_mouse_dpi else 'disabled'}"
        )
        self.reasons.setText(
            (self._profile.description or "Imported without a description.")
            + "\n\nStart calibration to adjust this curve, or use the AI chat quick actions."
        )
        self.file_status.setText(f"Imported {path}")
        self.profile_changed.emit(deepcopy(self._base_profile))

    def set_adjusted(self, previous: CurveProfile, current: CurveProfile, note: str) -> None:
        """Display an adjusted curve as a before/after comparison and make it the
        profile that Export will write."""
        self._profile = deepcopy(current)
        prev_pts, cur_pts = normalized_overlay(previous, current)
        self.chart.set_curves(
            cur_pts, prev_pts,
            current_label="Current (adjusted)",
            previous_label="Previous (imported)",
        )
        self.title.setText(current.name)
        self.multiplier.setText(f"{current.horizontal[0].multiplier:.2f}×")
        self.confidence.setText("Adjusted from your imported curve")
        self.reasons.setText(note)
        self.file_status.setText("Adjusted curve ready to export. Choose CC4 or legacy compatibility.")

    def _export_curve(self) -> None:
        if self._profile is None:
            self.file_status.setText("Complete calibration or import a curve before exporting.")
            return
        selected = self._selected_format()
        suffix = ".cc4" if selected == "cc4" else ".ccurve"
        label = "Custom Curve 4 (*.cc4)" if selected == "cc4" else "Custom Curve 3 (*.ccurve)"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export curve", f"fps-coach-curve{suffix}", label
        )
        if not path:
            return
        try:
            destination = save_curve(self._profile, path, selected)
        except (CurveFormatError, OSError) as error:
            self.file_status.setText(f"Export failed: {error}")
            return
        self.file_status.setText(f"Exported {destination}")

    def update_result(self, summary: dict[str, Any], recommendation: dict[str, Any]) -> None:
        # If the user imported a curve, ADJUST it instead of generating a new one.
        if self._base_profile is not None:
            adjusted, note = adjust_from_recommendation(self._base_profile, recommendation)
            prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
            self.chart.set_curves(
                cur_pts, prev_pts,
                current_label="Current (adjusted)",
                previous_label="Previous (imported)",
            )
            self._profile = adjusted
            self.title.setText(adjusted.name)
            self.multiplier.setText(f"{adjusted.horizontal[0].multiplier:.2f}×")
            self.confidence.setText(
                f"Adjusted imported curve  ·  confidence {float(recommendation.get('confidence', 0)) * 100:.0f}%"
            )
            self.metrics.setText(
                "Path efficiency  "
                f"{float(summary.get('path_efficiency', 0)) * 100:.0f}%\n"
                "Overshoot score  "
                f"{float(summary.get('overshoot_score', 0)) * 100:.0f}%\n"
                "Correction score  "
                f"{float(summary.get('correction_score', 0)) * 100:.0f}%\n"
                "Mean movement time  "
                f"{float(summary.get('mean_duration_ms', 0)):.0f} ms"
            )
            self.reasons.setText(note)
            self.file_status.setText("Adjusted curve ready to export. Choose CC4 or legacy compatibility.")
            self.profile_changed.emit(deepcopy(self._profile))
            return

        self.chart.set_points(list(recommendation.get("points", [])))
        self.title.setText(str(recommendation.get("title", "Recommendation")))
        self.multiplier.setText(f"{float(recommendation.get('sensitivity_multiplier', 1)):.2f}×")
        self.confidence.setText(f"Confidence  {float(recommendation.get('confidence', 0)) * 100:.0f}%")
        self.metrics.setText(
            "Path efficiency  "
            f"{float(summary.get('path_efficiency', 0)) * 100:.0f}%\n"
            "Overshoot score  "
            f"{float(summary.get('overshoot_score', 0)) * 100:.0f}%\n"
            "Correction score  "
            f"{float(summary.get('correction_score', 0)) * 100:.0f}%\n"
            "Mean movement time  "
            f"{float(summary.get('mean_duration_ms', 0)):.0f} ms"
        )
        reasons = recommendation.get("reasons", [])
        self.reasons.setText("\n\n".join(f"• {reason}" for reason in reasons))
        try:
            self._profile = recommendation_profile(recommendation)
            self.file_status.setText("Recommendation ready to export. Choose CC4 or legacy compatibility.")
        except CurveFormatError as error:
            self._profile = None
            self.file_status.setText(str(error))
        if self._profile is not None:
            self.profile_changed.emit(deepcopy(self._profile))


class _OllamaStatusCheck(QThread):
    """Probe the local Ollama server off the UI thread."""

    status_ready = Signal(bool, bool)  # (reachable, has_model)

    def __init__(self, base_url: str, model: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        status = check_ollama(self._base_url, self._model)
        self.status_ready.emit(bool(status["reachable"]), bool(status["has_model"]))


class _ModelPullThread(QThread):
    """Download the required model via Ollama, streaming progress."""

    progress = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, base_url: str, model: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        try:
            pull_model(self._base_url, self._model, progress=self.progress.emit)
        except (ChatProviderError, OSError, ValueError) as error:
            self.failed.emit(str(error))
            return
        self.finished_ok.emit()


class _VoiceListenThread(QThread):
    """Capture one spoken phrase off the UI thread."""

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
    """Background text-to-speech worker: auto-installs pyttsx3, inits one engine,
    and speaks queued AI responses so audio never blocks the UI thread."""

    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QWidget) -> None:
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
        from fps_coach import tts_service

        if not tts_service.ensure_dependency(progress=self.status.emit):
            self.failed.emit(
                "Could not auto-install text-to-speech (pyttsx3). Check your internet "
                "connection, or install it manually: pip install pyttsx3"
            )
            return
        engine = tts_service.create_engine()
        if engine is None:
            self.failed.emit("Text-to-speech engine could not be initialized on this system.")
            return
        self.status.emit("Text-to-speech ready.")
        while not self._stop:
            try:
                text = self._queue.get(timeout=0.3)
            except Exception:
                continue
            if text is None:
                break
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception:
                # Never let a single utterance kill the worker.
                continue


class _VoiceCommandService(QThread):
    """Background Vosk listener: downloads the model if needed, then streams mic
    audio and emits each finalized phrase as a voice command."""

    recognized = Signal(str)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QWidget) -> None:
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
            # Verify/lock the exact model path (no download); download only if
            # it's genuinely absent. This eliminates 'installed but not configured'.
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
    response_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(
        self,
        settings: ChatSettings,
        messages: list[dict[str, str]],
        calibration_context: str,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._messages = messages
        self._context = calibration_context

    def run(self) -> None:
        try:
            answer = AIChatClient(self._settings).chat(self._messages, self._context)
        except (ChatProviderError, OSError, ValueError) as error:
            self.request_failed.emit(str(error))
            return
        self.response_ready.emit(answer)


class AIChatPage(QWidget):
    # Emitted when a quick-action applies an adjusted curve, so the Recommendation
    # page can show the before/after and make it exportable.
    curve_adjusted = Signal(object, object, str)  # (previous, current, note)
    assistant_said = Signal(str)  # emitted with each AI response for text-to-speech

    def __init__(self) -> None:
        super().__init__()
        self._messages: list[dict[str, str]] = []
        self._calibration_context = "No completed calibration is available."
        self._request: _ChatRequest | None = None
        self._base_profile: CurveProfile | None = None   # imported base curve
        self._adjusted_profile: CurveProfile | None = None
        # Proactive (no-user-input) feedback machinery.
        self._proactive_request: _ChatRequest | None = None
        self._proactive_check: _OllamaStatusCheck | None = None
        self._proactive_pending: tuple[dict, dict] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Optional assistant",
                "AI curve coach",
                "Use a local Ollama model such as Qwen3 1.7B or Phi-4-mini, or configure an OpenAI-compatible online API.",
            )
        )

        # Collapsible "drop-down" that condenses provider, base URL, model,
        # API key and temperature into a single expandable section.
        self.settings_toggle = QPushButton("\u25b8  Connection \u0026 model settings")
        self.settings_toggle.setObjectName("secondary")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(False)
        self.settings_toggle.toggled.connect(self._toggle_settings)
        root.addWidget(self.settings_toggle)

        settings_card = QFrame()
        settings_card.setObjectName("card")
        self._settings_card = settings_card
        settings_layout = QFormLayout(settings_card)
        settings_layout.setContentsMargins(18, 14, 18, 14)
        self.provider = QComboBox()
        self.provider.addItem("Local Ollama", "ollama")
        self.provider.addItem("Online / OpenAI-compatible API", "openai")
        self.base_url = QLineEdit()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(["qwen3:1.7b", "phi4-mini"])
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Not saved; required only for online APIs")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setDecimals(2)
        # Model row: selector + an inline one-click download button that appears
        # whenever the selected model is not detected locally.
        model_row = QWidget()
        model_row_layout = QHBoxLayout(model_row)
        model_row_layout.setContentsMargins(0, 0, 0, 0)
        model_row_layout.setSpacing(8)
        model_row_layout.addWidget(self.model, 1)
        self.model_download = QPushButton("Download")
        self.model_download.setObjectName("primary")
        self.model_download.setToolTip("Download the selected model to this PC")
        self.model_download.clicked.connect(self._start_model_download)
        self.model_download.hide()
        model_row_layout.addWidget(self.model_download)
        settings_layout.addRow("Provider", self.provider)
        settings_layout.addRow("Base URL", self.base_url)
        settings_layout.addRow("Model", model_row)
        settings_layout.addRow("API key", self.api_key)
        settings_layout.addRow("Temperature", self.temperature)
        root.addWidget(settings_card)
        settings_card.setVisible(False)  # collapsed by default to condense the UI

        # --- Setup banner: one-click download when Ollama/model is missing -----
        self._status_check: _OllamaStatusCheck | None = None
        self._pull_thread: _ModelPullThread | None = None
        self.setup_banner = QFrame()
        self.setup_banner.setObjectName("card")
        banner_layout = QVBoxLayout(self.setup_banner)
        banner_layout.setContentsMargins(18, 14, 18, 14)
        self.setup_message = _label("", "muted")
        banner_layout.addWidget(self.setup_message)
        banner_buttons = QHBoxLayout()
        self.setup_action = QPushButton("Download")
        self.setup_action.setObjectName("primary")
        self.setup_action.clicked.connect(self._on_setup_action)
        self.setup_recheck = QPushButton("Re-check")
        self.setup_recheck.setObjectName("secondary")
        self.setup_recheck.clicked.connect(self._refresh_ai_status)
        banner_buttons.addWidget(self.setup_action)
        banner_buttons.addWidget(self.setup_recheck)
        banner_buttons.addStretch(1)
        banner_layout.addLayout(banner_buttons)
        self.setup_banner.hide()
        root.addWidget(self.setup_banner)

        saved = QSettings()
        provider_value = str(saved.value("ai/provider", "ollama"))
        provider_index = max(0, self.provider.findData(provider_value))
        self.provider.setCurrentIndex(provider_index)
        self.base_url.setText(str(saved.value("ai/base_url", "http://127.0.0.1:11434")))
        self.model.setEditText(str(saved.value("ai/model", "qwen3:1.7b")))
        self.temperature.setValue(float(saved.value("ai/temperature", 0.35)))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.model.currentTextChanged.connect(self._refresh_ai_status)
        self.base_url.editingFinished.connect(self._refresh_ai_status)
        self._update_provider_note()

        # --- Dynamic quick-action suggestions (refresh as the user types) -----
        # Collapsible "drop-down" header for the Suggested actions card (which
        # holds the chips, the before/after chart preview, and the
        # 'Send adjusted curve to Recommendation' button).
        self.quick_toggle = QPushButton("\u25be  Suggested actions")
        self.quick_toggle.setObjectName("secondary")
        self.quick_toggle.setCheckable(True)
        self.quick_toggle.setChecked(True)
        self.quick_toggle.toggled.connect(self._toggle_quick_actions)
        root.addWidget(self.quick_toggle)

        self.quick_card = QFrame()
        self.quick_card.setObjectName("card")
        quick_layout = QVBoxLayout(self.quick_card)
        quick_layout.setContentsMargins(16, 12, 16, 14)
        quick_layout.setSpacing(8)
        self.quick_hint = _label(
            "Suggestions update as you type. Import a curve to enable one-click tuning.",
            "muted",
        )
        quick_layout.addWidget(self.quick_hint)
        # Container rebuilt on every keystroke by _refresh_suggestions().
        self.suggestion_bar = QHBoxLayout()
        self.suggestion_bar.setSpacing(8)
        quick_layout.addLayout(self.suggestion_bar)
        # Compact before/after preview of the adjusted curve.
        self.quick_preview = CurveChart()
        self.quick_preview.setMinimumHeight(170)
        self.quick_preview.hide()
        quick_layout.addWidget(self.quick_preview)
        self.apply_to_rec = QPushButton("Send adjusted curve to Recommendation")
        self.apply_to_rec.setObjectName("primary")
        self.apply_to_rec.clicked.connect(self._send_adjusted_to_rec)
        self.apply_to_rec.hide()
        quick_layout.addWidget(self.apply_to_rec)
        root.addWidget(self.quick_card)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setPlaceholderText(
            "Ask how to interpret your calibration or how to test a curve safely.\n\n"
            "For local use, install Ollama and run:  ollama pull qwen3:1.7b"
        )
        root.addWidget(self.transcript, 1)
        compose = QHBoxLayout()
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Ask about your current calibration…")
        self.prompt.setMaximumHeight(86)
        self.prompt.textChanged.connect(self._refresh_suggestions)
        compose.addWidget(self.prompt, 1)
        buttons = QVBoxLayout()
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self._send)
        self.voice_button = QPushButton("\U0001f3a4 Speak")
        self.voice_button.setObjectName("secondary")
        self.voice_button.setToolTip("Dictate your message to the coach (push to talk)")
        self.voice_button.clicked.connect(self._start_voice)
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("secondary")
        clear_button.clicked.connect(self._clear)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.voice_button)
        buttons.addWidget(clear_button)
        compose.addLayout(buttons)
        self._voice_thread: _VoiceListenThread | None = None
        root.addLayout(compose)
        self.provider_note = _label("", "muted")
        root.addWidget(self.provider_note)
        self._update_provider_note()
        self._refresh_ai_status()
        self._refresh_suggestions()  # seed the default suggestions

    def _provider_changed(self) -> None:
        if self.provider.currentData() == "ollama":
            self.base_url.setText("http://127.0.0.1:11434")
            self.model.setEditText("qwen3:1.7b")
        else:
            self.base_url.setText("https://api.openai.com")
            self.model.setEditText("gpt-4o-mini")
        self._update_provider_note()
        self._refresh_ai_status()

    # --- AI setup detection + one-click download --------------------------
    def _refresh_ai_status(self) -> None:
        """Check whether local Ollama + the required model are available and show
        or hide the download controls accordingly. No-op while a pull is running."""
        if self._pull_thread is not None:
            return
        if self.provider.currentData() != "ollama":
            self.setup_banner.hide()
            self.model_download.hide()
            return
        if self._status_check is not None:
            return  # a check is already in flight
        base_url = self.base_url.text().strip()
        model = self.model.currentText().strip()
        if not base_url or not model:
            return
        self._status_check = _OllamaStatusCheck(base_url, model, self)
        self._status_check.status_ready.connect(self._on_status)
        self._status_check.finished.connect(self._status_check_finished)
        self._status_check.start()

    def _status_check_finished(self) -> None:
        if self._status_check is not None:
            self._status_check.deleteLater()
        self._status_check = None

    def _on_status(self, reachable: bool, has_model: bool) -> None:
        model = self.model.currentText().strip()
        if not reachable:
            # Ollama itself is missing: guide to the download page via the banner.
            self.model_download.hide()
            self.setup_message.setText(
                "Ollama was not detected on this PC. Install it to use the local AI coach, "
                "then click Re-check."
            )
            self.setup_action.setText("Download Ollama")
            self.setup_action.setEnabled(True)
            self.setup_recheck.show()
            self.setup_banner.show()
        elif not has_model:
            # Ollama is running but the chosen model is missing: show the inline
            # one-click download button right next to the model selector.
            self.setup_banner.hide()
            self.model_download.setText("Download")
            self.model_download.setEnabled(True)
            self.model_download.setToolTip(f"Download '{model}' (about 1 GB) to this PC")
            self.model_download.show()
            # Auto-expand the condensed settings so the inline button is visible.
            if not self.settings_toggle.isChecked():
                self.settings_toggle.setChecked(True)
        else:
            self.setup_banner.hide()
            self.model_download.hide()

    def _on_setup_action(self) -> None:
        """Banner action: open the Ollama runtime download page."""
        QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))
        self.setup_message.setText(
            "Opened the Ollama download page in your browser. After installing "
            "and starting Ollama, click Re-check."
        )

    def _start_model_download(self) -> None:
        """One-click download of the selected model via the inline button."""
        if self._pull_thread is not None:
            return
        base_url = self.base_url.text().strip()
        model = self.model.currentText().strip()
        if not base_url or not model:
            return
        self.model_download.setEnabled(False)
        self.model_download.setText("Downloading\u2026")
        self._append("Setup", f"Downloading model '{model}'\u2026")
        self._pull_thread = _ModelPullThread(base_url, model, self)
        self._pull_thread.progress.connect(self._on_pull_progress)
        self._pull_thread.finished_ok.connect(self._on_pull_done)
        self._pull_thread.failed.connect(self._on_pull_failed)
        self._pull_thread.finished.connect(self._pull_thread_finished)
        self._pull_thread.start()

    def _on_pull_progress(self, status: str) -> None:
        self.model_download.setText(f"{status[:14]}\u2026")

    def _on_pull_done(self) -> None:
        self._append("Setup", "Model downloaded. You can start chatting now.")
        self.model_download.hide()

    def _on_pull_failed(self, message: str) -> None:
        self._append("Download failed", message)
        self.model_download.setText("Retry download")
        self.model_download.setEnabled(True)
        self.model_download.show()

    def _pull_thread_finished(self) -> None:
        if self._pull_thread is not None:
            self._pull_thread.deleteLater()
        self._pull_thread = None
        self.model_download.setEnabled(True)
        self._refresh_ai_status()
        self._refresh_ai_status()

    def _toggle_settings(self, checked: bool) -> None:
        self._settings_card.setVisible(checked)
        arrow = "\u25be  " if checked else "\u25b8  "
        self.settings_toggle.setText(arrow + "Connection \u0026 model settings")

    def _toggle_quick_actions(self, checked: bool) -> None:
        self.quick_card.setVisible(checked)
        arrow = "\u25be  " if checked else "\u25b8  "
        self.quick_toggle.setText(arrow + "Suggested actions")

    def _refresh_suggestions(self) -> None:
        """Rebuild the dynamic quick-action chips to match what the user is typing."""
        from fps_coach.chat_suggestions import suggest

        # Clear existing chips.
        while self.suggestion_bar.count():
            item = self.suggestion_bar.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        text = self.prompt.toPlainText() if hasattr(self, "prompt") else ""
        for s in suggest(text, self._base_profile is not None):
            chip = QPushButton(s["label"])
            chip.setObjectName("secondary")
            if s["kind"] == "preset":
                chip.clicked.connect(lambda checked=False, k=s["payload"]: self._apply_quick_action(k))
            else:
                chip.clicked.connect(lambda checked=False, p=s["payload"]: self._use_prompt(p))
            self.suggestion_bar.addWidget(chip)
        self.suggestion_bar.addStretch(1)

    def _use_prompt(self, prompt: str) -> None:
        """Drop a suggested question into the compose box for the user to send/edit."""
        self.prompt.setPlainText(prompt)
        self.prompt.setFocus()

    def _update_provider_note(self) -> None:
        if self.provider.currentData() == "ollama":
            self.api_key.setEnabled(False)
            message = "Local mode: prompts stay on this PC and are sent only to the configured Ollama server."
        else:
            self.api_key.setEnabled(True)
            message = "Online mode: chat and the current calibration summary are sent to the configured API. The key is not saved."
        if hasattr(self, "provider_note"):
            self.provider_note.setText(message)

    def set_calibration_context(self, summary: dict[str, Any], recommendation: dict[str, Any]) -> None:
        self._calibration_context = curve_context(summary, recommendation)
        self._append_base_curve_context()

    # --- Persistent context + proactive feedback --------------------------
    def notify_new_calibration(
        self, summary: dict[str, Any], recommendation: dict[str, Any]
    ) -> None:
        """Called automatically when a calibration completes. Refreshes the AI's
        context and posts proactive feedback without waiting for the user."""
        self.set_calibration_context(summary, recommendation)
        self._append("Coach (auto)", self._local_proactive_note(summary, recommendation))
        self._maybe_proactive_ai(summary, recommendation)

    def _local_proactive_note(
        self, summary: dict[str, Any], recommendation: dict[str, Any]
    ) -> str:
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
            parts.append(
                f"I've adjusted your imported curve '{self._base_profile.name}' to match."
            )
        parts.append("Ask me anything about these results, or use a suggested action.")
        return " ".join(parts)

    def _maybe_proactive_ai(
        self, summary: dict[str, Any], recommendation: dict[str, Any]
    ) -> None:
        # Only for local Ollama, and only if the model is already present - never
        # trigger a surprise download or a paid API call without user action.
        if self.provider.currentData() != "ollama":
            return
        if self._proactive_request is not None or self._proactive_check is not None:
            return
        base_url = self.base_url.text().strip()
        model = self.model.currentText().strip()
        if not base_url or not model:
            return
        self._proactive_pending = (summary, recommendation)
        self._proactive_check = _OllamaStatusCheck(base_url, model, self)
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
        self._proactive_request = _ChatRequest(
            self._settings(), messages, self._calibration_context, self
        )
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

    def shutdown(self) -> None:
        """Let background proactive threads finish before the app closes."""
        for thread in (self._proactive_check, self._proactive_request):
            if thread is not None:
                thread.wait(2000)

    # --- Imported curve as a base for AI adjustments ----------------------
    def set_base_profile(self, profile: CurveProfile | None) -> None:
        """Receive the imported curve so quick actions and the AI can use it as a
        base when making adjustments."""
        self._base_profile = deepcopy(profile) if profile is not None else None
        self._adjusted_profile = None
        if self._base_profile is not None:
            self.quick_hint.setText(
                f"Base curve: '{self._base_profile.name}'. Suggestions update as you type; "
                "tap a tuning action to see previous vs current below."
            )
        else:
            self.quick_hint.setText(
                "Suggestions update as you type. Import a curve to enable one-click tuning."
            )
            self.quick_preview.hide()
            self.apply_to_rec.hide()
        self._refresh_suggestions()
        self._append_base_curve_context()

    def _append_base_curve_context(self) -> None:
        """Fold a short description of the imported base curve into the context the
        AI receives, so it adjusts that curve rather than inventing a new one."""
        if self._base_profile is None:
            return
        pts = ", ".join(
            f"({p.speed:.0f},{p.multiplier:.2f})" for p in self._base_profile.horizontal[:12]
        )
        self._calibration_context = (
            f"{self._calibration_context}\n\n"
            f"Imported base curve '{self._base_profile.name}' "
            f"({self._base_profile.source_format}); horizontal speed,gain points: {pts}. "
            "When asked for adjustments, modify THIS curve rather than designing a new one."
        )

    def _apply_quick_action(self, key: str) -> None:
        if self._base_profile is None:
            return
        try:
            adjusted, note = apply_preset(self._base_profile, key)
        except KeyError:
            return
        self._adjusted_profile = adjusted
        prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
        self.quick_preview.set_curves(
            cur_pts, prev_pts,
            current_label="Current (adjusted)",
            previous_label="Previous (imported)",
        )
        self.quick_preview.show()
        self.apply_to_rec.show()
        self._append("Quick action", f"{note}\nPreview updated. Multiplier now "
                     f"{adjusted.horizontal[0].multiplier:.2f}x at the first point.")

    def _send_adjusted_to_rec(self) -> None:
        if self._base_profile is None or self._adjusted_profile is None:
            return
        self.curve_adjusted.emit(
            deepcopy(self._base_profile), deepcopy(self._adjusted_profile),
            self._adjusted_profile.description or "Adjusted via AI quick action.",
        )
        self._append("Quick action", "Sent the adjusted curve to the Recommendation page "
                     "(open it to review and export).")

    def _settings(self) -> ChatSettings:
        settings = ChatSettings(
            provider=str(self.provider.currentData()),
            base_url=self.base_url.text().strip(),
            model=self.model.currentText().strip(),
            api_key=self.api_key.text(),
            temperature=self.temperature.value(),
        )
        saved = QSettings()
        saved.setValue("ai/provider", settings.provider)
        saved.setValue("ai/base_url", settings.base_url)
        saved.setValue("ai/model", settings.model)
        saved.setValue("ai/temperature", settings.temperature)
        return settings

    def _append(self, speaker: str, text: str) -> None:
        safe_text = html.escape(text).replace("\n", "<br>")
        self.transcript.append(f"<p><b>{html.escape(speaker)}</b><br>{safe_text}</p>")

    # --- Voice input ------------------------------------------------------
    def _start_voice(self) -> None:
        if self._voice_thread is not None:
            return
        self.voice_button.setEnabled(False)
        self.voice_button.setText("\U0001f3a4 Listening\u2026")
        self._voice_thread = _VoiceListenThread(self)
        self._voice_thread.recognized.connect(self._on_voice_text)
        self._voice_thread.failed.connect(self._on_voice_failed)
        self._voice_thread.finished.connect(self._voice_finished)
        self._voice_thread.start()

    def _on_voice_text(self, text: str) -> None:
        existing = self.prompt.toPlainText().strip()
        self.prompt.setPlainText(f"{existing} {text}".strip() if existing else text)
        self.prompt.setFocus()

    def _on_voice_failed(self, message: str) -> None:
        self._append("Voice", message)

    def _voice_finished(self) -> None:
        if self._voice_thread is not None:
            self._voice_thread.deleteLater()
        self._voice_thread = None
        self.voice_button.setEnabled(True)
        self.voice_button.setText("\U0001f3a4 Speak")

    def _send(self) -> None:
        text = self.prompt.toPlainText().strip()
        if not text or self._request is not None:
            return
        self.prompt.clear()
        self._messages.append({"role": "user", "content": text})
        self._append("You", text)
        self.send_button.setEnabled(False)
        self.send_button.setText("Thinking…")
        self._request = _ChatRequest(
            self._settings(), list(self._messages), self._calibration_context, self
        )
        self._request.response_ready.connect(self._received)
        self._request.request_failed.connect(self._failed)
        self._request.finished.connect(self._request_finished)
        self._request.start()

    def _received(self, answer: str) -> None:
        self._messages.append({"role": "assistant", "content": answer})
        self._append("Coach", answer)
        self.assistant_said.emit(answer)
        self._apply_ai_adjustment(answer)

    def _apply_ai_adjustment(self, answer: str) -> None:
        """If the assistant recommended a curve change, apply it to the imported
        curve and push the result to the Recommendation tab in real-time."""
        if self._base_profile is None:
            return
        from fps_coach.ai_curve_actions import apply_adjustment, parse_adjustment

        plan = parse_adjustment(answer)
        if not plan:
            return
        try:
            adjusted, note = apply_adjustment(self._base_profile, plan)
        except (ValueError, KeyError):
            return
        self._adjusted_profile = adjusted
        prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
        self.quick_preview.set_curves(
            cur_pts, prev_pts,
            current_label="Current (adjusted)",
            previous_label="Previous",
        )
        self.quick_preview.show()
        self.apply_to_rec.show()
        # Push to the Recommendation tab graph immediately.
        self.curve_adjusted.emit(
            deepcopy(self._base_profile), deepcopy(adjusted), note
        )
        self._append("Applied", f"{note} Updated the Recommendation tab graph automatically.")

    def _failed(self, message: str) -> None:
        self._append("Connection error", message)
        # A failure often means Ollama/model is missing - re-check so the
        # one-click download banner appears.
        self._refresh_ai_status()

    def _request_finished(self) -> None:
        if self._request is not None:
            self._request.deleteLater()
        self._request = None
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")

    def _clear(self) -> None:
        if self._request is not None:
            return
        self._messages.clear()
        self.transcript.clear()


class PrivacyPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Boundaries",
                "Privacy, compatibility, and fair play",
                "Know exactly what the foundation does—and what intentionally stays out of scope.",
            )
        )
        root.addWidget(
            _card(
                "Local by default",
                "Frames and movement samples stay on this PC. Chat uses only the provider you configure: local Ollama keeps prompts local, while online mode sends chat and the displayed calibration summary to your chosen API.",
            )
        )
        root.addWidget(
            _card(
                "Anti-cheat friendly boundary",
                "The application captures the composed desktop through a documented Windows API. It does not attach to, inspect, inject into, or send input to a game. Always follow each game's rules; close capture if a title prohibits external capture tools.",
            )
        )
        root.addWidget(
            _card(
                "Capture compatibility",
                "Use borderless-windowed mode. Protected video, the secure desktop, HDR/color conversions, and some exclusive-fullscreen paths may not preview correctly in this foundation.",
            )
        )
        root.addWidget(
            _card(
                "Integrated graphics",
                "Select the output's owning adapter and begin with the 640×360 center region at 30 or 60 FPS. Full-output CPU readback is intentionally optional because it consumes much more shared memory bandwidth.",
            )
        )
        root.addStretch()


class SettingsPage(QWidget):
    """Appearance & performance preferences (persisted in QSettings)."""

    settings_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Appearance",
                "Look & performance",
                "Tune the interface to your taste and your hardware. Changes apply instantly.",
            )
        )

        saved = QSettings()
        mode = str(saved.value("ui/mode", "luxurious"))
        animations = saved.value("ui/animations", True, type=bool)
        theme = str(saved.value("ui/theme", "system"))

        theme_card = QFrame()
        theme_card.setObjectName("card")
        theme_layout = QVBoxLayout(theme_card)
        theme_layout.setContentsMargins(20, 18, 20, 18)
        theme_layout.setSpacing(10)
        theme_layout.addWidget(_label("Theme", "cardTitle"))
        self.theme_system = QRadioButton("Adaptive  \u2014  follow my system's dark/light setting")
        self.theme_dark = QRadioButton("Dark  \u2014  the default, low-glare appearance")
        self.theme_light = QRadioButton("Light")
        {"light": self.theme_light, "dark": self.theme_dark}.get(
            theme, self.theme_system
        ).setChecked(True)
        theme_layout.addWidget(self.theme_system)
        theme_layout.addWidget(self.theme_dark)
        theme_layout.addWidget(self.theme_light)
        root.addWidget(theme_card)
        root.addSpacing(12)

        mode_card = QFrame()
        mode_card.setObjectName("card")
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(20, 18, 20, 18)
        mode_layout.setSpacing(10)
        mode_layout.addWidget(_label("Visual mode", "cardTitle"))
        self.mode_lux = QRadioButton("Luxurious  \u2014  gradients, soft shadows, smooth animations")
        self.mode_perf = QRadioButton("High performance  \u2014  flat, lightweight painting for maximum FPS")
        if mode == "performance":
            self.mode_perf.setChecked(True)
        else:
            self.mode_lux.setChecked(True)
        mode_layout.addWidget(self.mode_lux)
        mode_layout.addWidget(self.mode_perf)
        root.addWidget(mode_card)
        root.addSpacing(12)

        anim_card = QFrame()
        anim_card.setObjectName("card")
        anim_layout = QVBoxLayout(anim_card)
        anim_layout.setContentsMargins(20, 18, 20, 18)
        anim_layout.setSpacing(10)
        anim_layout.addWidget(_label("Animations", "cardTitle"))
        self.animations_toggle = QCheckBox("Enable interface animations (page transitions)")
        self.animations_toggle.setChecked(bool(animations))
        anim_layout.addWidget(self.animations_toggle)
        anim_layout.addWidget(
            _label(
                "Turn this off for the snappiest possible interface, or when running on "
                "low-powered hardware. High-performance mode also disables animations.",
                "muted",
            )
        )
        root.addWidget(anim_card)
        root.addSpacing(12)

        panel_card = QFrame()
        panel_card.setObjectName("card")
        panel_layout = QVBoxLayout(panel_card)
        panel_layout.setContentsMargins(20, 18, 20, 18)
        panel_layout.setSpacing(10)
        panel_layout.addWidget(_label("On-screen coach panel", "cardTitle"))
        self.coach_panel_toggle = QCheckBox("Show an always-on-top coach panel")
        self.coach_panel_toggle.setChecked(saved.value("ui/coach_panel", False, type=bool))
        panel_layout.addWidget(self.coach_panel_toggle)
        panel_layout.addWidget(
            _label(
                "A small, draggable window that shows YOUR latest calibration result, "
                "current sensitivity, and a reminder - handy on a second monitor or in "
                "borderless mode. It never reads or analyzes the game; it only shows your "
                "own training data.",
                "muted",
            )
        )
        root.addWidget(panel_card)
        root.addSpacing(12)

        voice_card = QFrame()
        voice_card.setObjectName("card")
        voice_layout = QVBoxLayout(voice_card)
        voice_layout.setContentsMargins(20, 18, 20, 18)
        voice_layout.setSpacing(10)
        voice_layout.addWidget(_label("Voice commands (offline)", "cardTitle"))
        self.voice_commands_toggle = QCheckBox("Listen for voice commands with Vosk")
        self.voice_commands_toggle.setChecked(saved.value("ui/voice_commands", False, type=bool))
        voice_layout.addWidget(self.voice_commands_toggle)
        self.tts_toggle = QCheckBox("Speak AI responses aloud (text-to-speech)")
        self.tts_toggle.setChecked(saved.value("ui/tts_enabled", False, type=bool))
        self.tts_toggle.setToolTip(
            "Reads every AI reply through your speakers. Installs pyttsx3 once, automatically."
        )
        voice_layout.addWidget(self.tts_toggle)
        wake_row = QHBoxLayout()
        wake_row.addWidget(_label("Wake word", "muted"))
        self.wake_word_edit = QLineEdit(str(saved.value("ui/wake_word", "coach")))
        self.wake_word_edit.setPlaceholderText("coach")
        self.wake_word_edit.setMaximumWidth(160)
        wake_row.addWidget(self.wake_word_edit)
        wake_row.addStretch(1)
        voice_layout.addLayout(wake_row)
        voice_layout.addWidget(
            _label(
                "Fully offline speech recognition. On first use it downloads a ~40 MB "
                "model. Say things like 'open calibration', 'start calibration', or "
                "'optimize for fast flicks'. Prefix a chat question with your wake word, "
                "e.g. 'coach how do I improve'. Needs: pip install vosk sounddevice.",
                "muted",
            )
        )
        root.addWidget(voice_card)
        root.addStretch()

        self.mode_lux.toggled.connect(self._changed)
        self.animations_toggle.toggled.connect(self._changed)
        self.theme_system.toggled.connect(self._changed)
        self.theme_dark.toggled.connect(self._changed)
        self.coach_panel_toggle.toggled.connect(self._changed)
        self.voice_commands_toggle.toggled.connect(self._changed)
        self.tts_toggle.toggled.connect(self._changed)
        self.wake_word_edit.editingFinished.connect(self._changed)

    def mode(self) -> str:
        return "performance" if self.mode_perf.isChecked() else "luxurious"

    def theme(self) -> str:
        if self.theme_dark.isChecked():
            return "dark"
        if self.theme_light.isChecked():
            return "light"
        return "system"

    def animations_enabled(self) -> bool:
        return self.animations_toggle.isChecked()

    def coach_panel_enabled(self) -> bool:
        return self.coach_panel_toggle.isChecked()

    def voice_commands_enabled(self) -> bool:
        return self.voice_commands_toggle.isChecked()

    def tts_enabled(self) -> bool:
        return self.tts_toggle.isChecked()

    def wake_word(self) -> str:
        return self.wake_word_edit.text().strip() or "coach"

    def _changed(self) -> None:
        saved = QSettings()
        saved.setValue("ui/mode", self.mode())
        saved.setValue("ui/theme", self.theme())
        saved.setValue("ui/animations", self.animations_enabled())
        saved.setValue("ui/coach_panel", self.coach_panel_enabled())
        saved.setValue("ui/voice_commands", self.voice_commands_enabled())
        saved.setValue("ui/wake_word", self.wake_word())
        saved.setValue("ui/tts_enabled", self.tts_enabled())
        self.settings_changed.emit()


class DashboardPage(QWidget):
    """Customizable landing hub: choose which tool tiles appear and their order."""

    def __init__(self) -> None:
        super().__init__()
        self._navigate = None
        self._key_to_index: dict[str, int] = {}
        self._order = dashboard_normalize_order(self._saved_order())
        self._enabled = dashboard_normalize_enabled(self._saved_enabled())

        # Animated "targets" backdrop (subtle, immersive; respects the
        # animations/performance setting via set_animations()).
        self._anim_enabled = True
        self._accent = "#70e1ba"
        self._phase = 0.0
        self._targets = [
            {"nx": 0.12, "ny": 0.22, "r": 34, "po": 0.0},
            {"nx": 0.78, "ny": 0.16, "r": 26, "po": 1.1},
            {"nx": 0.30, "ny": 0.72, "r": 30, "po": 2.0},
            {"nx": 0.63, "ny": 0.60, "r": 22, "po": 3.2},
            {"nx": 0.88, "ny": 0.82, "r": 38, "po": 4.1},
            {"nx": 0.46, "ny": 0.34, "r": 18, "po": 5.0},
        ]
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(45)  # ~22 fps - gentle and light
        self._anim_timer.timeout.connect(self._tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        header = QHBoxLayout()
        header.addLayout(
            _page_header(
                "Workspace",
                "Dashboard",
                "Jump straight to any tool. Use Customize to pick which tiles show and their order.",
            )
        )
        header.addStretch(1)
        self.customize_toggle = QPushButton("Customize")
        self.customize_toggle.setObjectName("secondary")
        self.customize_toggle.setCheckable(True)
        self.customize_toggle.toggled.connect(self._toggle_customize)
        top = QVBoxLayout()
        top.addWidget(self.customize_toggle)
        top.addStretch(1)
        header.addLayout(top)
        root.addLayout(header)

        self._tiles_host = QVBoxLayout()
        root.addLayout(self._tiles_host)

        self.customize_card = QFrame()
        self.customize_card.setObjectName("card")
        self._customize_layout = QVBoxLayout(self.customize_card)
        self._customize_layout.setContentsMargins(18, 14, 18, 14)
        self._customize_layout.setSpacing(6)
        self.customize_card.hide()
        root.addWidget(self.customize_card)
        root.addStretch(1)

    # persistence (order/enabled stored as comma-joined strings) -----------
    @staticmethod
    def _saved_order() -> list[str] | None:
        raw = QSettings().value("dashboard/order", "")
        return [k for k in str(raw).split(",") if k] or None

    @staticmethod
    def _saved_enabled() -> list[str] | None:
        raw = QSettings().value("dashboard/enabled", None)
        if raw is None:
            return None
        return [k for k in str(raw).split(",") if k]

    def _save(self) -> None:
        saved = QSettings()
        saved.setValue("dashboard/order", ",".join(self._order))
        saved.setValue("dashboard/enabled", ",".join(sorted(self._enabled)))

    def configure(self, navigate, key_to_index: dict[str, int]) -> None:
        self._navigate = navigate
        self._key_to_index = dict(key_to_index)
        self._rebuild()

    def _toggle_customize(self, checked: bool) -> None:
        self.customize_card.setVisible(checked)
        self.customize_toggle.setText("Done" if checked else "Customize")

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild(self) -> None:
        # Tiles for the enabled tools, in order.
        self._clear(self._tiles_host)
        tiles: list[QPushButton] = []
        for key in self._order:
            if key not in self._enabled or key not in self._key_to_index:
                continue
            tile = QPushButton(dashboard_tool_label(key))
            tile.setObjectName("secondary")
            tile.setMinimumHeight(64)
            tile.setToolTip(dashboard_tool_description(key))
            target = self._key_to_index[key]
            tile.clicked.connect(lambda checked=False, i=target: self._go(i))
            tiles.append(tile)
        if tiles:
            self._tiles_host.addWidget(ResponsiveCards(tiles, min_col_width=240))
        else:
            self._tiles_host.addWidget(
                _label("No tiles enabled yet. Press Customize to add some.", "muted")
            )
        # Customize rows: enable checkbox + reorder up/down.
        self._clear(self._customize_layout)
        self._customize_layout.addWidget(_label("Choose tiles and order", "cardTitle"))
        for key in self._order:
            row = QHBoxLayout()
            check = QCheckBox(dashboard_tool_label(key))
            check.setChecked(key in self._enabled)
            check.toggled.connect(lambda on, k=key: self._set_enabled(k, on))
            row.addWidget(check, 1)
            up = QPushButton("\u2191")
            up.setObjectName("secondary")
            up.setFixedWidth(40)
            up.clicked.connect(lambda checked=False, k=key: self._move(k, -1))
            down = QPushButton("\u2193")
            down.setObjectName("secondary")
            down.setFixedWidth(40)
            down.clicked.connect(lambda checked=False, k=key: self._move(k, 1))
            row.addWidget(up)
            row.addWidget(down)
            container = QWidget()
            container.setLayout(row)
            self._customize_layout.addWidget(container)

    def _go(self, index: int) -> None:
        if self._navigate is not None:
            self._navigate(index)

    def _set_enabled(self, key: str, on: bool) -> None:
        if on:
            self._enabled.add(key)
        else:
            self._enabled.discard(key)
        self._save()
        self._rebuild()

    def _move(self, key: str, delta: int) -> None:
        self._order = dashboard_move(self._order, key, delta)
        self._save()
        self._rebuild()

    # --- Animated targets backdrop ----------------------------------------
    def set_animations(self, enabled: bool, accent: str = "#70e1ba") -> None:
        self._anim_enabled = bool(enabled)
        self._accent = accent or self._accent
        if self._anim_enabled and self.isVisible():
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._anim_timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase += 0.05
        self.update()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if self._anim_enabled and not self._anim_timer.isActive():
            self._anim_timer.start()

    def hideEvent(self, event) -> None:  # noqa: ANN001
        super().hideEvent(event)
        self._anim_timer.stop()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if not self._targets:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        base = QColor(self._accent)
        for target in self._targets:
            phase = self._phase + target["po"]
            cx = target["nx"] * width + math.sin(phase * 0.6) * 16
            cy = target["ny"] * height + math.cos(phase * 0.5) * 12
            pulse = target["r"] * (1.0 + 0.12 * math.sin(phase * 2.0))
            center = QPointF(cx, cy)
            for ring, alpha in ((1.0, 24), (1.55, 15), (2.1, 9)):
                ring_color = QColor(base)
                ring_color.setAlpha(alpha)
                painter.setPen(QPen(ring_color, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                radius = pulse * ring
                painter.drawEllipse(center, radius, radius)
            # Center dot + crosshair ticks for a target feel.
            dot = QColor(base)
            dot.setAlpha(38)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(center, 3.0, 3.0)
        painter.end()


class CoachPanel(QWidget):
    """Compliant on-screen companion: a small, draggable, always-on-top window
    that shows ONLY the user's own data (latest calibration result, current
    sensitivity, a reminder). It never captures or analyzes the game."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_offset = None
        self.setFixedWidth(260)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(_label("FPS COACH", "eyebrow"))
        top.addStretch(1)
        self.mini_btn = QPushButton("\u2013")
        self.mini_btn.setObjectName("secondary")
        self.mini_btn.setFixedWidth(30)
        self.mini_btn.setToolTip("Compact / expand the panel")
        self.mini_btn.clicked.connect(self._toggle_mini)
        top.addWidget(self.mini_btn)
        close_btn = QPushButton("\u00d7")
        close_btn.setObjectName("secondary")
        close_btn.setFixedWidth(30)
        close_btn.setToolTip("Hide the coach panel")
        close_btn.clicked.connect(self._on_close)
        top.addWidget(close_btn)
        layout.addLayout(top)

        self.title_label = _label("Run a calibration to see feedback", "cardTitle")
        layout.addWidget(self.title_label)
        self.score_label = _label("\u2014", "metric")
        layout.addWidget(self.score_label)
        self.caption_label = _label("starting sensitivity multiplier", "muted")
        layout.addWidget(self.caption_label)
        self.detail_label = _label("", "muted")
        layout.addWidget(self.detail_label)
        self.reminder_label = _label(
            "Warm up, change one thing at a time, then re-test.", "muted"
        )
        layout.addWidget(self.reminder_label)
        self._mini = False

    def _toggle_mini(self) -> None:
        self._mini = not self._mini
        for widget in (self.title_label, self.caption_label, self.detail_label, self.reminder_label):
            widget.setVisible(not self._mini)
        self.mini_btn.setText("\u25a1" if self._mini else "\u2013")
        self.adjustSize()

    def update_feedback(self, summary: dict, recommendation: dict) -> None:
        self.title_label.setText(str(recommendation.get("title", "Recommendation")))
        self.score_label.setText(f"{float(recommendation.get('sensitivity_multiplier', 1)):.2f}\u00d7")
        path_eff = float(summary.get("path_efficiency", 0.0)) * 100
        trials = int(summary.get("trial_count", 0) or 0)
        self.detail_label.setText(f"Path efficiency {path_eff:.0f}%  \u00b7  {trials} trials")

    def update_curve(self, current, note: str) -> None:
        """Refresh from an applied curve adjustment (AI / quick action / manual)."""
        try:
            first = current.horizontal[0].multiplier
            name = current.name
        except Exception:
            return
        self.title_label.setText(name)
        self.score_label.setText(f"{first:.2f}\u00d7")
        self.detail_label.setText((note or "Curve updated.")[:60])

    def place_top_right(self) -> None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            geo = screen.availableGeometry()
            self.adjustSize()
            self.move(geo.right() - self.width() - 24, geo.top() + 24)

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    # Draggable frameless window.
    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._drag_offset = None


def _draw_crosshair(painter: QPainter, cx: float, cy: float, cfg: CrosshairConfig) -> None:
    """Paint the crosshair centered at (cx, cy). Shared by preview + PNG export."""
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
    def __init__(self) -> None:
        super().__init__()
        self._cfg = CrosshairConfig()
        self.setMinimumHeight(220)

    def set_config(self, cfg: CrosshairConfig) -> None:
        self._cfg = cfg
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0c1118"))
        _draw_crosshair(painter, self.width() / 2, self.height() / 2, self._cfg)


class CrosshairPage(QWidget):
    """Customizable crosshair generator with a live preview and PNG export."""

    def __init__(self) -> None:
        super().__init__()
        self._color = "#70e1ba"
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Make it yours",
                "Crosshair generator",
                "Fine-tune a crosshair and export a PNG. You apply it yourself in your game "
                "or overlay of choice - FPS Coach never draws on a game.",
            )
        )
        row = QHBoxLayout()
        form_card = QFrame()
        form_card.setObjectName("card")
        form = QFormLayout(form_card)
        form.setContentsMargins(18, 16, 18, 16)
        self.shape = QComboBox()
        self.shape.addItems(["cross_dot", "cross", "dot", "circle"])
        self.size = self._spin(0, 64, 10)
        self.thickness = self._spin(1, 12, 2)
        self.gap = self._spin(0, 40, 3)
        self.dot = self._spin(0, 12, 2)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.1, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setValue(1.0)
        self.outline = QCheckBox("Outline")
        self.outline.setChecked(True)
        self.color_btn = QPushButton("Pick color")
        self.color_btn.setObjectName("secondary")
        self.color_btn.clicked.connect(self._pick_color)
        self.preset = QComboBox()
        self.preset.addItem("Presets\u2026", "")
        for name in CROSSHAIR_PRESETS:
            self.preset.addItem(name.replace("_", " ").title(), name)
        self.preset.currentIndexChanged.connect(self._apply_preset)
        form.addRow("Preset", self.preset)
        form.addRow("Shape", self.shape)
        form.addRow("Arm length", self.size)
        form.addRow("Thickness", self.thickness)
        form.addRow("Center gap", self.gap)
        form.addRow("Dot size", self.dot)
        form.addRow("Opacity", self.opacity)
        form.addRow("", self.outline)
        form.addRow("Color", self.color_btn)
        row.addWidget(form_card, 2)

        preview_card = QFrame()
        preview_card.setObjectName("card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.addWidget(_label("Live preview", "cardTitle"))
        self.preview = CrosshairPreview()
        preview_layout.addWidget(self.preview, 1)
        export_btn = QPushButton("Export PNG")
        export_btn.setObjectName("primary")
        export_btn.clicked.connect(self._export)
        preview_layout.addWidget(export_btn)
        self.status = _label("", "muted")
        preview_layout.addWidget(self.status)
        row.addWidget(preview_card, 3)
        root.addLayout(row, 1)

        for widget in (self.shape,):
            widget.currentIndexChanged.connect(self._refresh)
        for spin in (self.size, self.thickness, self.gap, self.dot):
            spin.valueChanged.connect(self._refresh)
        self.opacity.valueChanged.connect(self._refresh)
        self.outline.toggled.connect(self._refresh)
        self._load()
        self._refresh()

    @staticmethod
    def _spin(lo: int, hi: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(value)
        return spin

    def _config(self) -> CrosshairConfig:
        return CrosshairConfig(
            shape=self.shape.currentText(),
            size=self.size.value(),
            thickness=self.thickness.value(),
            gap=self.gap.value(),
            dot_size=self.dot.value(),
            color=self._color,
            outline=self.outline.isChecked(),
            opacity=self.opacity.value(),
        ).normalized()

    def _refresh(self) -> None:
        cfg = self._config()
        self.preview.set_config(cfg)
        self.color_btn.setText(f"Color  {cfg.color}")
        import json
        QSettings().setValue("crosshair/config", json.dumps(cfg.to_dict()))

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Crosshair color")
        if chosen.isValid():
            self._color = chosen.name()
            self._refresh()

    def _apply_preset(self) -> None:
        key = self.preset.currentData()
        if not key or key not in CROSSHAIR_PRESETS:
            return
        cfg = CROSSHAIR_PRESETS[key].normalized()
        self._set_from_config(cfg)

    def _set_from_config(self, cfg: CrosshairConfig) -> None:
        self._color = cfg.color
        self.shape.setCurrentText(cfg.shape)
        self.size.setValue(cfg.size)
        self.thickness.setValue(cfg.thickness)
        self.gap.setValue(cfg.gap)
        self.dot.setValue(cfg.dot_size)
        self.opacity.setValue(cfg.opacity)
        self.outline.setChecked(cfg.outline)
        self._refresh()

    def _load(self) -> None:
        import json
        raw = QSettings().value("crosshair/config", "")
        if raw:
            try:
                self._set_from_config(CrosshairConfig.from_dict(json.loads(raw)))
            except Exception:
                pass

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export crosshair", "crosshair.png", "PNG (*.png)")
        if not path:
            return
        size = 128
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        _draw_crosshair(painter, size / 2, size / 2, self._config())
        painter.end()
        if image.save(path, "PNG"):
            self.status.setText(f"Exported {path}")
        else:
            self.status.setText("Export failed.")


class TrainingCanvas(QWidget):
    """Runs a target-clicking scenario and records accuracy + reaction times."""

    finished = Signal(str, list, int, int)  # scenario_key, reaction_times_ms, hits, misses
    progress = Signal(int, int)

    KILL_MS = 420  # fast kill animation: well under half a second

    def __init__(self) -> None:
        super().__init__()
        self._rng = random.Random()
        self._scenario: scenario_defs.Scenario | None = None
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

    def start(self, scenario: scenario_defs.Scenario) -> None:
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
        painter.fillRect(self.rect(), QColor("#0c1118"))

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
            painter.setPen(QColor("#718095"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Choose a scenario and press Start")
        elif self._active and not self._visible_target:
            painter.setPen(QColor("#718095"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Get ready\u2026")


class TrainingPage(QWidget):
    """Scenario-based training: pick a drill, run it, record analytics."""

    session_recorded = Signal(object)  # SessionResult

    def __init__(self, store: AnalyticsStore) -> None:
        super().__init__()
        self._store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Targeted practice",
                "Scenario training",
                "In-app drills for specific skills. Results feed your Analytics - no game is read.",
            )
        )
        bar = QHBoxLayout()
        self.scenario = QComboBox()
        for sc in scenario_defs.list_scenarios():
            self.scenario.addItem(sc.name, sc.key)
        self.scenario.currentIndexChanged.connect(self._describe)
        bar.addWidget(self.scenario, 1)
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        bar.addWidget(self.start_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        bar.addWidget(self.progress, 1)
        root.addLayout(bar)
        self.desc = _label("", "muted")
        root.addWidget(self.desc)
        self.canvas = TrainingCanvas()
        self.canvas.progress.connect(self._on_progress)
        self.canvas.finished.connect(self._on_finished)
        root.addWidget(self.canvas, 1)
        self.result = _label("", "muted")
        root.addWidget(self.result)
        self._describe()

    def _describe(self) -> None:
        sc = scenario_defs.get(self.scenario.currentData())
        self.desc.setText(sc.description)

    def _start(self) -> None:
        sc = scenario_defs.get(self.scenario.currentData())
        self.result.setText("")
        self.progress.setRange(0, sc.target_count)
        self.progress.setValue(0)
        self.canvas.start(sc)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _on_finished(self, key: str, reaction_times: list, hits: int, misses: int) -> None:
        result = SessionResult.create(key, reaction_times, hits, misses)
        self._store.add(result)
        self.result.setText(
            f"Done - accuracy {result.accuracy * 100:.0f}%, "
            f"avg reaction {result.avg_reaction_ms:.0f} ms, best {result.best_reaction_ms:.0f} ms."
        )
        self.session_recorded.emit(result)


class AnalyticsPage(QWidget):
    """Performance analytics: aim accuracy and reaction-time trends."""

    def __init__(self, store: AnalyticsStore) -> None:
        super().__init__()
        self._store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Progress",
                "Performance analytics",
                "Accuracy and reaction times from your in-app drills, tracked over time.",
            )
        )
        metrics = QHBoxLayout()
        self.acc_metric = _label("\u2014", "metric")
        self.rt_metric = _label("\u2014", "metric")
        self.best_metric = _label("\u2014", "metric")
        metric_specs = (
            ("avg accuracy", self.acc_metric),
            ("avg reaction", self.rt_metric),
            ("best reaction", self.best_metric),
        )
        for title, widget in metric_specs:
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 16, 18, 16)
            cl.addWidget(widget)
            cl.addWidget(_label(title, "muted"))
            metrics.addWidget(card)
        root.addLayout(metrics)
        list_card = QFrame()
        list_card.setObjectName("card")
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(18, 16, 18, 16)
        ll.addWidget(_label("Recent sessions", "cardTitle"))
        self.recent = _label("", "muted")
        ll.addWidget(self.recent)
        root.addWidget(list_card)
        root.addStretch()
        self.refresh()

    def refresh(self) -> None:
        agg = self._store.aggregate()
        if agg["total"] == 0:
            for metric in (self.acc_metric, self.rt_metric, self.best_metric):
                metric.setText("\u2014")
            self.recent.setText("No sessions yet. Run a scenario in Training to start tracking.")
            return
        self.acc_metric.setText(f"{agg['avg_accuracy'] * 100:.0f}%")
        self.rt_metric.setText(f"{agg['avg_reaction_ms']:.0f} ms")
        self.best_metric.setText(f"{agg['best_reaction_ms']:.0f} ms")
        lines = [
            f"{s.when()}  \u00b7  {s.scenario}  \u00b7  acc {s.accuracy * 100:.0f}%  \u00b7  "
            f"avg {s.avg_reaction_ms:.0f} ms"
            for s in self._store.recent(8)
        ]
        self.recent.setText("\n".join(lines))


class MainWindow(QMainWindow):
    def __init__(self, backend: CoreBackend) -> None:
        super().__init__()
        self._backend = backend
        self.setWindowTitle("FPS Coach")
        self.resize(1220, 780)
        self.setMinimumSize(1020, 680)

        root_widget = QWidget()
        root_widget.setObjectName("root")
        root = QHBoxLayout(root_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(root_widget)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        self._sidebar = sidebar
        self._sidebar_collapsed = False
        self._sidebar_anim: QVariantAnimation | None = None
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 22, 16, 18)
        # Retract / expand toggle.
        self._collapse_btn = QPushButton("\u00ab")
        self._collapse_btn.setObjectName("navButton")
        self._collapse_btn.setToolTip("Collapse / expand the sidebar")
        self._collapse_btn.clicked.connect(self._toggle_sidebar)
        sidebar_layout.addWidget(self._collapse_btn)
        self._brand = _label("FPS COACH", "brand")
        sidebar_layout.addWidget(self._brand)

        self.stack = QStackedWidget()
        calibration = CalibrationPage(backend)
        self._calibration_page = calibration
        self._voice_cmd_service: _VoiceCommandService | None = None
        self._tts_service: _TtsService | None = None
        self.recommendation = RecommendationPage()
        self.chat_page = AIChatPage()
        self.settings_page = SettingsPage()
        self.dashboard_page = DashboardPage()
        # Shared analytics store (Training writes, Analytics reads).
        self._analytics_store = AnalyticsStore.load(
            Path(__file__).resolve().parents[3] / ".fpscoach_analytics.json"
        )
        self.training_page = TrainingPage(self._analytics_store)
        self.analytics_page = AnalyticsPage(self._analytics_store)
        self.crosshair_page = CrosshairPage()
        # Combined Training + Analytics page: training on top, analytics beneath,
        # the whole thing scrollable (handled via scrolled_keys below).
        training_combined = QWidget()
        combined_layout = QVBoxLayout(training_combined)
        combined_layout.setContentsMargins(0, 0, 0, 0)
        combined_layout.setSpacing(0)
        combined_layout.addWidget(self.training_page)
        combined_layout.addWidget(self.analytics_page)

        # (tool key, nav label, widget). Dashboard is the landing page at index 0.
        pages: list[tuple[str, str, QWidget]] = [
            ("dashboard", "Dashboard", self.dashboard_page),
            ("guide", "Beginner guide", GuidePage()),
            ("capture", "Capture", CapturePage(backend)),
            ("calibration", "Calibration", calibration),
            ("training", "Training & analytics", training_combined),
            ("recommendation", "Recommendation", self.recommendation),
            ("crosshair", "Crosshair", self.crosshair_page),
            ("chat", "AI chat", self.chat_page),
            ("privacy", "Privacy & limits", PrivacyPage()),
            ("settings", "Settings", self.settings_page),
        ]
        scrolled_keys = {"dashboard", "guide", "training", "crosshair", "privacy", "settings"}
        key_to_index = {key: i for i, (key, _name, _w) in enumerate(pages)}

        self.nav_buttons: list[QPushButton] = []
        self._nav_names: list[str] = []
        for index, (key, name, page) in enumerate(pages):
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._show_page(i))
            sidebar_layout.addWidget(button)
            self.nav_buttons.append(button)
            self._nav_names.append(name)
            self.stack.addWidget(_scroll_page(page) if key in scrolled_keys else page)

        self._idx_recommendation = key_to_index["recommendation"]
        self._idx_chat = key_to_index["chat"]
        self._key_to_index = key_to_index
        self.dashboard_page.configure(self._show_page, key_to_index)
        # Refresh analytics whenever a training session completes.
        self.training_page.session_recorded.connect(lambda _r: self.analytics_page.refresh())
        sidebar_layout.addStretch()
        # Background setup indicator (shows during voice package/model download).
        self._setup_label = _label("", "muted")
        self._setup_label.hide()
        self._setup_bar = QProgressBar()
        self._setup_bar.setRange(0, 0)  # indeterminate "busy" animation
        self._setup_bar.setTextVisible(False)
        self._setup_bar.setFixedHeight(8)
        self._setup_bar.hide()
        self._setup_active = False
        sidebar_layout.addWidget(self._setup_label)
        sidebar_layout.addWidget(self._setup_bar)
        self._listening_label = _label("", "statusGood")
        self._listening_label.hide()
        self._listening_active = False
        sidebar_layout.addWidget(self._listening_label)
        core_status = _label(backend.core_label, "statusGood" if backend.native_available else "statusWarn")
        sidebar_layout.addWidget(core_status)
        subtitle_status = _label("On-device · no input injection", "muted")
        sidebar_layout.addWidget(subtitle_status)
        self._status_labels = [core_status, subtitle_status]
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)

        calibration.recommendation_ready.connect(self._show_recommendation)
        self.settings_page.settings_changed.connect(self.apply_visuals)
        # Share the imported curve so the AI/quick-actions adjust it; route the
        # chat's adjusted curve back to the Recommendation page for review/export.
        self.recommendation.profile_changed.connect(self.chat_page.set_base_profile)
        self.chat_page.curve_adjusted.connect(self._on_curve_adjusted)
        # Speak AI responses aloud when text-to-speech is enabled.
        self.chat_page.assistant_said.connect(self._speak_response)
        # Compliant on-screen companion (user's own data only).
        self.coach_panel = CoachPanel(self)
        self.coach_panel.closed.connect(self._disable_coach_panel)
        # Keep the panel in sync with any curve change (import / manual edit).
        self.recommendation.profile_changed.connect(
            lambda profile: self.coach_panel.update_curve(profile, "Curve updated.")
            if profile is not None else None
        )
        self._page_anim: QPropertyAnimation | None = None
        self.apply_visuals()
        # Re-apply styling when the OS switches dark/light (adaptive theme).
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                lambda _scheme: self.apply_visuals()
            )
        except Exception:
            pass
        # Seamlessly validate + configure the Vosk model path on startup so the
        # app recognizes it without manual intervention (no download here).
        self._verify_vosk_config()
        self._show_page(0)

    def _verify_vosk_config(self) -> None:
        try:
            from fps_coach import vosk_service

            path = vosk_service.verify_and_configure()
            if path is not None:
                QSettings().setValue("voice/vosk_model_path", str(path))
        except Exception:
            pass

    # --- Appearance / performance ----------------------------------------
    @staticmethod
    def _resolve_theme(setting: str) -> str:
        """Map a theme setting to a concrete 'dark'/'light'. 'system' follows the
        OS color scheme when available; defaults to dark."""
        if setting in ("dark", "light"):
            return setting
        app = QApplication.instance()
        try:
            scheme = app.styleHints().colorScheme()
            return "light" if scheme == Qt.ColorScheme.Light else "dark"
        except Exception:
            return "dark"

    def apply_visuals(self) -> None:
        """Apply the saved theme + visual mode + animation preference app-wide."""
        saved = QSettings()
        mode = str(saved.value("ui/mode", "luxurious"))
        theme = self._resolve_theme(str(saved.value("ui/theme", "system")))
        self._animations_on = saved.value("ui/animations", True, type=bool) and mode != "performance"
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_style(mode, theme))
        self._apply_card_shadows(enabled=(mode != "performance"))
        self._sync_coach_panel()
        self._sync_voice_commands()
        self._sync_tts()
        # Drive the dashboard's animated targets backdrop with the theme accent.
        accent = PALETTES.get(theme, PALETTES["dark"])["accent"]
        self.dashboard_page.set_animations(self._animations_on, accent)
        # Kill animations in the training canvas follow the same setting.
        self.training_page.canvas.set_animations(self._animations_on)

    # --- Text-to-speech for AI responses ---------------------------------
    def _sync_tts(self) -> None:
        enabled = QSettings().value("ui/tts_enabled", False, type=bool)
        running = self._tts_service is not None
        if enabled and not running:
            self._tts_service = _TtsService(self)
            self._tts_service.status.connect(self._on_tts_status)
            self._tts_service.failed.connect(self._on_tts_failed)
            self._tts_service.start()
        elif not enabled and running:
            self._tts_service.stop()
            self._tts_service = None

    def _speak_response(self, text: str) -> None:
        if self._tts_service is not None:
            self._tts_service.enqueue(text)

    def _on_tts_status(self, message: str) -> None:
        low = message.lower()
        if any(word in low for word in ("install", "setting up")):
            self._set_setup_indicator(True, message)
        elif "ready" in low:
            self._set_setup_indicator(False)

    def _on_tts_failed(self, message: str) -> None:
        self.chat_page._append("Text-to-speech", message)
        self._set_setup_indicator(False)
        QSettings().setValue("ui/tts_enabled", False)
        self.settings_page.tts_toggle.setChecked(False)
        if self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service = None

    def _sync_coach_panel(self) -> None:
        if not hasattr(self, "coach_panel"):
            return
        show = QSettings().value("ui/coach_panel", False, type=bool)
        if show:
            if not self.coach_panel.isVisible():
                self.coach_panel.place_top_right()
                self.coach_panel.show()
        else:
            self.coach_panel.hide()

    def _disable_coach_panel(self) -> None:
        QSettings().setValue("ui/coach_panel", False)
        if hasattr(self, "settings_page"):
            self.settings_page.coach_panel_toggle.setChecked(False)

    # --- Offline voice commands (Vosk) -----------------------------------
    def _sync_voice_commands(self) -> None:
        enabled = QSettings().value("ui/voice_commands", False, type=bool)
        running = self._voice_cmd_service is not None
        if enabled and not running:
            self._voice_cmd_service = _VoiceCommandService(self)
            self._voice_cmd_service.recognized.connect(self._on_voice_command)
            self._voice_cmd_service.status.connect(self._on_voice_status)
            self._voice_cmd_service.failed.connect(self._on_voice_cmd_failed)
            self._voice_cmd_service.start()
            self._set_listening(True, "\U0001f399 Starting\u2026")
        elif not enabled and running:
            self._voice_cmd_service.stop()
            self._voice_cmd_service = None
            self._set_listening(False)

    def _on_voice_status(self, message: str) -> None:
        self.chat_page._append("Voice", message)
        low = message.lower()
        if "listening" in low:
            self._set_setup_indicator(False)
            self._set_listening(True, "\U0001f399 Listening\u2026")
        elif any(word in low for word in ("install", "download", "extract", "setting up")):
            # Background setup in progress - show the download indicator.
            self._set_setup_indicator(True, message)
        elif "ready" in low:
            self._set_setup_indicator(False)
        else:
            self._set_listening(True, "\U0001f399 " + message[:18])

    def _set_setup_indicator(self, active: bool, text: str = "") -> None:
        self._setup_active = active
        collapsed = getattr(self, "_sidebar_collapsed", False)
        if active and text:
            self._setup_label.setText(text[:40])
        self._setup_label.setVisible(active and not collapsed)
        self._setup_bar.setVisible(active and not collapsed)

    def _on_voice_cmd_failed(self, message: str) -> None:
        self.chat_page._append("Voice", message)
        QSettings().setValue("ui/voice_commands", False)
        self.settings_page.voice_commands_toggle.setChecked(False)
        self._set_listening(False)
        self._set_setup_indicator(False)
        if self._voice_cmd_service is not None:
            self._voice_cmd_service.stop()
            self._voice_cmd_service = None

    def _set_listening(self, active: bool, text: str = "") -> None:
        self._listening_active = active
        if active and text:
            self._listening_label.setText(text)
        self._listening_label.setVisible(active and not self._sidebar_collapsed)

    def _on_voice_command(self, text: str) -> None:
        from fps_coach.voice_commands import parse

        wake = QSettings().value("ui/wake_word", "coach")
        command = parse(text, wake_word=str(wake))
        if command is None:
            return
        if command.kind == "navigate":
            index = self._key_to_index.get(command.value)
            if index is not None:
                self._show_page(index)
        elif command.kind == "calibrate":
            self._show_page(self._key_to_index["calibration"])
            if command.value == "start":
                self._calibration_page.canvas.start_session(12)
            else:
                self._calibration_page.canvas.cancel_session()
        elif command.kind == "preset":
            self._show_page(self._idx_chat)
            self.chat_page._apply_quick_action(command.value)
        elif command.kind == "panel":
            QSettings().setValue("ui/coach_panel", command.value == "show")
            self.settings_page.coach_panel_toggle.setChecked(command.value == "show")
            self._sync_coach_panel()
        elif command.kind == "dictate":
            self._show_page(self._idx_chat)
            self.chat_page.prompt.setPlainText(command.value)
            self.chat_page._send()

    # --- Retractable sidebar ---------------------------------------------
    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        collapsed = self._sidebar_collapsed
        target = 58 if collapsed else 218
        # Labels/brand hidden when collapsed; nav buttons keep tooltips.
        self._brand.setVisible(not collapsed)
        for label in self._status_labels:
            label.setVisible(not collapsed)
        for button, name in zip(self.nav_buttons, self._nav_names, strict=True):
            button.setText("" if collapsed else name)
            button.setToolTip(name)
        self._collapse_btn.setText("\u00bb" if collapsed else "\u00ab")
        if hasattr(self, "_listening_label"):
            self._listening_label.setVisible(self._listening_active and not collapsed)
        if hasattr(self, "_setup_bar"):
            show_setup = getattr(self, "_setup_active", False) and not collapsed
            self._setup_label.setVisible(show_setup)
            self._setup_bar.setVisible(show_setup)
        if getattr(self, "_animations_on", False):
            anim = QVariantAnimation(self)
            anim.setStartValue(self._sidebar.width())
            anim.setEndValue(target)
            anim.setDuration(160)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.valueChanged.connect(lambda v: self._sidebar.setFixedWidth(int(v)))
            anim.start()
            self._sidebar_anim = anim
        else:
            self._sidebar.setFixedWidth(target)

    def _apply_card_shadows(self, enabled: bool) -> None:
        for card in self.findChildren(QFrame):
            if card.objectName() not in ("card", "banner"):
                continue
            if enabled:
                effect = QGraphicsDropShadowEffect(card)
                effect.setBlurRadius(26)
                effect.setXOffset(0)
                effect.setYOffset(6)
                effect.setColor(QColor(0, 0, 0, 140))
                card.setGraphicsEffect(effect)
            else:
                card.setGraphicsEffect(None)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
        if getattr(self, "_animations_on", False):
            self._animate_current_page()

    def _animate_current_page(self) -> None:
        widget = self.stack.currentWidget()
        if widget is None:
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Drop the effect when done so it doesn't interfere with child effects.
        anim.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
        anim.start()
        self._page_anim = anim

    def _show_recommendation(self, summary: dict, recommendation: dict) -> None:
        self.recommendation.update_result(summary, recommendation)
        # Push the new results into the chat so the AI reacts proactively.
        self.chat_page.notify_new_calibration(summary, recommendation)
        if hasattr(self, "coach_panel"):
            self.coach_panel.update_feedback(summary, recommendation)
        self._show_page(self._idx_recommendation)

    def _on_curve_adjusted(self, previous, current, note: str) -> None:
        # A chat quick-action produced an adjusted curve: show it on the
        # Recommendation page as a before/after and switch to that page.
        self.recommendation.set_adjusted(previous, current, note)
        if hasattr(self, "coach_panel"):
            self.coach_panel.update_curve(current, note)
        self._show_page(self._idx_recommendation)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self.chat_page._request is not None:  # Keep the QThread alive until its network call returns.
            self._show_page(self._idx_chat)
            self.chat_page.provider_note.setText("Wait for the current model request to finish before closing.")
            event.ignore()
            return
        self.chat_page.shutdown()  # let any proactive background threads finish
        if self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service.wait(2000)
        if self._voice_cmd_service is not None:
            self._voice_cmd_service.stop()
            self._voice_cmd_service.wait(2000)
        if hasattr(self, "coach_panel"):
            self.coach_panel.close()
        self._backend.close()
        super().closeEvent(event)
