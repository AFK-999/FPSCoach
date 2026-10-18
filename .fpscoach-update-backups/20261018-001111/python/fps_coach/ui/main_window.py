from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fps_coach.backend import CoreBackend, NativeCoreUnavailable
from fps_coach.ui.calibration import CalibrationCanvas


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

        steps = QGridLayout()
        steps.setSpacing(14)
        steps.addWidget(
            _card(
                "01  Verify capture",
                "Choose the display that owns your borderless game. The native core uses Windows Desktop Duplication and keeps only a bounded center region by default.",
            ),
            0,
            0,
        )
        steps.addWidget(
            _card(
                "02  Run calibration",
                "Click 12 targets inside this app. Move as you normally would—the local model measures efficiency, overshoot, corrections, and small oscillations.",
            ),
            0,
            1,
        )
        steps.addWidget(
            _card(
                "03  Review, don't automate",
                "Use the suggested curve as a starting point in your own approved mouse or game settings. Change one variable at a time and keep your original profile.",
            ),
            1,
            0,
        )
        steps.addWidget(
            _card(
                "Good baseline",
                "Disable pointer acceleration for the calibration, use the DPI and polling rate you normally play with, warm up first, and repeat after a hardware change.",
            ),
            1,
            1,
        )
        root.addLayout(steps)
        root.addSpacing(12)

        boundaries = _card(
            "Designed as a coach—not a game automation tool",
            "No process injection, memory reading, synthetic input, target detection, or automatic setting changes. Screen pixels remain local and the calibration runs only while you explicitly start it.",
        )
        root.addWidget(boundaries)
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
        config.addLayout(controls, 2, 0, 1, 3)
        root.addWidget(config_card)
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
        try:
            self._backend.start_capture(
                int(output["adapter_index"]),
                int(output["output_index"]),
                int(width),
                int(height),
                int(self.fps_combo.currentData()),
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
    def __init__(self) -> None:
        super().__init__()
        self._points: list[tuple[float, float]] = []
        self.setMinimumHeight(290)

    def set_points(self, points: list[tuple[float, float]]) -> None:
        self._points = [(float(x), float(y)) for x, y in points]
        self.update()

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
        screen_points = [
            QPointF(plot.left() + x * plot.width(), plot.bottom() - y * plot.height())
            for x, y in self._points
        ]
        painter.setPen(QPen(QColor("#70e1ba"), 3))
        for first, second in zip(screen_points, screen_points[1:]):
            painter.drawLine(first, second)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#b4ffe6"))
        for point in screen_points:
            painter.drawEllipse(point, 4, 4)


class RecommendationPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.addLayout(
            _page_header(
                "Explainable result",
                "Recommended response curve",
                "A local model turns movement features into a conservative, monotonic curve. Nothing is applied automatically.",
            )
        )

        row = QHBoxLayout()
        chart_card = QFrame()
        chart_card.setObjectName("card")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(16, 16, 16, 16)
        chart_layout.addWidget(_label("Normalized curve", "cardTitle"))
        self.chart = CurveChart()
        chart_layout.addWidget(self.chart)
        row.addWidget(chart_card, 3)

        summary_card = QFrame()
        summary_card.setObjectName("card")
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        self.title = _label("No calibration yet", "cardTitle")
        self.multiplier = _label("1.00×", "metric")
        self.confidence = _label("Confidence —", "muted")
        self.metrics = _label("Run calibration to measure your baseline.", "muted")
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
        root.addWidget(
            _label(
                "Apply manually in an approved settings tool, save it as a new profile, and test for several sessions. Revert if comfort or consistency gets worse.",
                "muted",
            )
        )

    def update_result(self, summary: dict[str, Any], recommendation: dict[str, Any]) -> None:
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
                "Local by design",
                "Frames and movement samples stay in memory on this PC. The foundation includes no account, analytics client, uploader, cloud endpoint, or recording feature.",
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
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 22, 16, 18)
        sidebar_layout.addWidget(_label("FPS COACH", "brand"))

        self.stack = QStackedWidget()
        pages: list[tuple[str, QWidget]] = []
        pages.append(("Beginner guide", GuidePage()))
        pages.append(("Capture", CapturePage(backend)))
        calibration = CalibrationPage(backend)
        pages.append(("Calibration", calibration))
        self.recommendation = RecommendationPage()
        pages.append(("Recommendation", self.recommendation))
        pages.append(("Privacy & limits", PrivacyPage()))

        self.nav_buttons: list[QPushButton] = []
        for index, (name, page) in enumerate(pages):
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._show_page(i))
            sidebar_layout.addWidget(button)
            self.nav_buttons.append(button)
            self.stack.addWidget(_scroll_page(page) if index in (0, 4) else page)
        sidebar_layout.addStretch()
        core_status = _label(backend.core_label, "statusGood" if backend.native_available else "statusWarn")
        sidebar_layout.addWidget(core_status)
        sidebar_layout.addWidget(
            _label("On-device · no input injection", "muted")
        )
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)

        calibration.recommendation_ready.connect(self._show_recommendation)
        self._show_page(0)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

    def _show_recommendation(self, summary: dict, recommendation: dict) -> None:
        self.recommendation.update_result(summary, recommendation)
        self._show_page(3)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._backend.close()
        super().closeEvent(event)
