"""Overview and setup pages: Dashboard, Guide, Capture, Calibration, Recommendation.

Layout is rebuilt for the horizontal top navigation. Capture and calibration
logic is carried over unchanged from the previous window.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace  # noqa: F401  (kept for parity with curve helpers)
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach.ai_chat import AIChatClient  # noqa: F401  (re-exported for tests that patch this module)
from fps_coach.curve_adjust import adjust_from_recommendation, normalized_overlay
from fps_coach.curve_io import (
    CurveFormatError,
    chart_points,
    load_curve,
    recommendation_profile,
    save_curve,
)
from fps_coach.window_enum import get_window_rect, list_windows
from .styles import STAGE

from .calibration import CalibrationCanvas
from .widgets import (
    CurveChart,
    CurveLegend,
    _apply_chart_edit,
    set_glyph,
    _card,
    _content_page,
    _curve_points,
    _divider,
    _label,
    _page_header,
    _page_root,
    _settings,
)

if TYPE_CHECKING:
    from fps_coach.backend import CoreBackend


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class _Tile(QFrame):
    """A clickable dashboard tile. Buttons inside it consume their own clicks."""

    clicked = Signal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


class DashboardPage(QWidget):
    """Six module tiles. Click to open; reorder and hide them; values are live."""

    TILES = [
        ("capture", "Capture", "Live source status"),
        ("calibration", "Calibration", "Curve freshness"),
        ("training", "Training", "Recent sessions"),
        ("recommendation", "Recommendation", "Latest suggestion"),
        ("crosshair", "Crosshair", "Active preset"),
        ("chat", "AI Coach", "Conversation history"),
    ]
    COLUMNS = 3

    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Dashboard",
            "Every subsystem at a glance. Select a tile to open it.",
            eyebrow="Overview",
        ))

        self._order = self._load_order()
        self._enabled = self._load_enabled()
        self._value_labels: dict[str, QLabel] = {}
        self._caption_labels: dict[str, QLabel] = {}
        self._titles = {t[0]: t for t in self.TILES}

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        outer.addWidget(self.grid_host)

        self.restore_btn = QPushButton(" Show hidden tiles")
        self.restore_btn.setObjectName("secondary")
        set_glyph(self.restore_btn, "refresh")
        self.restore_btn.clicked.connect(self._show_all)
        outer.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

        self._rebuild_tiles()

    # --- state -------------------------------------------------------------
    def set_values(self, values: dict[str, tuple[str, str]]) -> None:
        """Update each tile's headline value and caption. ``values[key] = (value, caption)``."""
        for key, (value, caption) in values.items():
            if key in self._value_labels:
                self._value_labels[key].setText(value)
                self._caption_labels[key].setText(caption)

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

    # --- tiles -------------------------------------------------------------
    def _rebuild_tiles(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._value_labels.clear()
        self._caption_labels.clear()

        visible = [k for k in self._order if self._enabled.get(k, True)]
        for index, key in enumerate(visible):
            _kind, title, subtitle = self._titles[key]
            tile = self._build_tile(key, title, subtitle)
            self.grid.addWidget(tile, index // self.COLUMNS, index % self.COLUMNS)
        for col in range(self.COLUMNS):
            self.grid.setColumnStretch(col, 1)
        self.restore_btn.setVisible(len(visible) < len(self.TILES))

    def _build_tile(self, key, title, subtitle):
        tile = _Tile(key)
        tile.clicked.connect(self.navigate.emit)
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        icon_lbl = QLabel()
        set_glyph(icon_lbl, key, 20, role="accent")
        head.addWidget(icon_lbl)
        head.addWidget(_label(title, "cardTitle"))
        head.addStretch(1)
        for glyph, step in (("chevron_up", -1), ("chevron_down", 1)):
            btn = QToolButton()
            set_glyph(btn, glyph)
            btn.setObjectName("iconButton")
            btn.setToolTip("Move tile " + ("up" if step < 0 else "down"))
            btn.clicked.connect(lambda _c=False, k=key, d=step: self._move(k, d))
            head.addWidget(btn)
        hide_btn = QToolButton()
        set_glyph(hide_btn, "close")
        hide_btn.setObjectName("iconButton")
        hide_btn.setToolTip("Hide tile")
        hide_btn.clicked.connect(lambda _c=False, k=key: self._hide(k))
        head.addWidget(hide_btn)
        lay.addLayout(head)

        value = QLabel("\u2014")
        value.setObjectName("tileValue")
        lay.addWidget(value)
        caption = _label(subtitle, "cardSubtitle", wrap=True)
        lay.addWidget(caption)
        self._value_labels[key] = value
        self._caption_labels[key] = caption
        return tile

    def _move(self, key, direction):
        idx = self._order.index(key)
        new_idx = idx + direction
        if 0 <= new_idx < len(self._order):
            self._order[idx], self._order[new_idx] = self._order[new_idx], self._order[idx]
            self._save_state()
            self._rebuild_tiles()

    def _hide(self, key):
        self._enabled[key] = False
        self._save_state()
        self._rebuild_tiles()

    def _show_all(self):
        self._enabled = {t[0]: True for t in self.TILES}
        self._save_state()
        self._rebuild_tiles()


# ---------------------------------------------------------------------------
# Guide
# ---------------------------------------------------------------------------

class GuidePage(QWidget):
    """Onboarding: the five steps from first launch to a tuned setup."""

    STEPS = [
        ("capture", "Connect your capture source",
         "Point Vantage at your game window or display so it can watch your crosshair in real time."),
        ("calibration", "Run calibration",
         "Walk through the calibration task to map your mouse movement to in-game response."),
        ("recommendation", "Review your recommendation",
         "Vantage proposes a sensitivity curve tuned to your aim data, with the reasons behind it."),
        ("training", "Train with live feedback",
         "Use the training range to validate the new curve against real, moving targets."),
        ("chat", "Ask the AI coach anytime",
         "Open the coach for follow-up questions, or ask it to adjust an imported curve."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        scroll, outer = _content_page(960)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Guide",
            "A five-step path from first launch to a tuned, trained setup.",
            eyebrow="Help",
        ))
        for index, (key, title, body) in enumerate(self.STEPS, start=1):
            card, lay = _card()
            row = QHBoxLayout()
            row.setSpacing(16)
            badge = QLabel(f"{index:02d}")
            badge.setObjectName("stepBadge")
            badge.setFixedSize(52, 52)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

            col = QVBoxLayout()
            col.setSpacing(4)
            head = QHBoxLayout()
            head.setSpacing(8)
            glyph = QLabel()
            set_glyph(glyph, key, 16, role="text2")
            head.addWidget(glyph)
            head.addWidget(_label(title, "cardTitle"))
            head.addStretch(1)
            col.addLayout(head)
            col.addWidget(_label(body, "cardSubtitle", wrap=True))
            row.addLayout(col, 1)
            lay.addLayout(row)
            outer.addWidget(card)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

FRAME_INTERVAL_MS = 16  # about 60 Hz: the preview keeps up with 60 FPS capture


class FramePreview(QWidget):
    """Shows the newest captured frame, scaled to fit, painted straight from its pixels.

    The frame is wrapped as a QImage without a second copy, and painted on demand. There is no
    per-frame QPixmap conversion or smooth scaling: the preview is for watching a live feed, and
    that work is what costs the most on low-end machines. Only the newest frame is kept.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: QImage | None = None
        self._data: bytes | None = None  # keeps the bytes behind the QImage alive
        self.setMinimumSize(520, 292)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def has_frame(self) -> bool:
        return self._image is not None

    def set_frame(self, data, width: int, height: int, stride: int) -> bool:
        """Show a BGRA frame. Returns False (and keeps the old frame) if the buffer is too short."""
        if width <= 0 or height <= 0 or stride < width * 4:
            return False
        if not isinstance(data, bytes):
            data = bytes(data)
        if len(data) < stride * height:
            return False
        self._data = data
        self._image = QImage(data, width, height, stride, QImage.Format.Format_ARGB32)
        self.update()
        return True

    def clear_frame(self) -> None:
        self._image = None
        self._data = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._image is None or self._image.isNull():
            painter.setPen(QColor(STAGE["stage_text"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No frame yet")
            painter.end()
            return
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = max(1, int(iw * scale)), max(1, int(ih * scale))
        target = QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)
        painter.drawImage(target, self._image)
        painter.end()


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
        ("Custom", "custom", None, None),  # region chosen manually below; FPS from the Frame rate row
    ]
    FPS_CHOICES = (30, 60, 120)

    def __init__(self, backend: CoreBackend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._last_sequence = -1
        s = _settings()

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Capture",
            "Choose what Vantage watches and how often it samples it.",
            eyebrow="Input",
        ))

        body = QHBoxLayout()
        body.setSpacing(20)

        # Left: the newest frame on the always-dark stage surface.
        left = QVBoxLayout()
        left.setSpacing(10)
        stage = QFrame()
        stage.setObjectName("stage")
        stage_lay = QVBoxLayout(stage)
        stage_lay.setContentsMargins(12, 12, 12, 12)
        self.preview = FramePreview()
        stage_lay.addWidget(self.preview, 1)
        left.addWidget(stage, 1)
        self.stats_label = _label("", "cardSubtitle")
        left.addWidget(self.stats_label)
        body.addLayout(left, 3)

        # Right: source and control panel.
        card, lay = _card("Source", "Display output, window, preset, and acceleration.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Display output", "formLabel"), 0, 0)
        self.output_combo = QComboBox()
        form.addWidget(self.output_combo, 0, 1)

        form.addWidget(_label("Window", "formLabel"), 1, 0)
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        source_row.addWidget(self.source_combo, 1)
        self.window_refresh_btn = QToolButton()
        self.window_refresh_btn.setObjectName("iconButton")
        set_glyph(self.window_refresh_btn, "refresh")
        self.window_refresh_btn.setToolTip("Refresh the window list")
        source_row.addWidget(self.window_refresh_btn)
        form.addLayout(source_row, 1, 1)

        form.addWidget(_label("Preset", "formLabel"), 2, 0)
        self.preset = QComboBox()
        for label, value, _region, _fps in self.PRESETS:
            self.preset.addItem(label, value)
        index = self.preset.findData(str(s.value("capture/preset", "balanced")))
        self.preset.setCurrentIndex(index if index >= 0 else 1)
        self.preset.currentIndexChanged.connect(self._on_preset_changed)
        form.addWidget(self.preset, 2, 1)

        self.custom_label = _label("Custom region", "formLabel")
        form.addWidget(self.custom_label, 3, 0)
        self.custom_widget = QWidget()
        custom_row = QHBoxLayout(self.custom_widget)
        custom_row.setContentsMargins(0, 0, 0, 0)
        self.custom_region = QComboBox()
        self.custom_region.addItem("Center 640 × 360", (640, 360))
        self.custom_region.addItem("Center 960 × 540", (960, 540))
        custom_row.addWidget(self.custom_region, 1)
        form.addWidget(self.custom_widget, 3, 1)

        # Frame rate is always visible. A preset sets it when chosen; the user can change it.
        form.addWidget(_label("Frame rate", "formLabel"), 4, 0)
        self.fps_combo = QComboBox()
        for fps in self.FPS_CHOICES:
            self.fps_combo.addItem(f"{fps} FPS", fps)
        # Without a saved choice, start from the saved preset's own frame rate.
        preset_fps = self.PRESETS[max(0, self.preset.currentIndex())][3] or 60
        saved_fps = int(s.value("capture/fps", preset_fps, type=int))
        index = self.fps_combo.findData(saved_fps)
        self.fps_combo.setCurrentIndex(index if index >= 0 else self.FPS_CHOICES.index(60))
        self.fps_combo.currentIndexChanged.connect(self._on_fps_changed)
        form.addWidget(self.fps_combo, 4, 1)
        self._on_preset_changed(self.preset.currentIndex(), apply_fps=False)

        self.hwaccel = QCheckBox("GPU capture (D3D11)")
        self.hwaccel.setToolTip("Use the GPU (D3D11) for capture and scaling")
        self.hwaccel.setChecked(s.value("capture/hwaccel", True, type=bool))
        self.hwaccel.toggled.connect(lambda v: s.setValue("capture/hwaccel", bool(v)))
        form.addWidget(self.hwaccel, 5, 1)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton(" Start")
        self.start_btn.setObjectName("primary")
        set_glyph(self.start_btn, "play")
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("secondary")
        set_glyph(self.stop_btn, "stop")
        self.stop_btn.setEnabled(False)
        self.refresh_btn = QPushButton(" Displays")
        self.refresh_btn.setObjectName("secondary")
        set_glyph(self.refresh_btn, "refresh")
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
        lay.addStretch(1)

        card.setMinimumWidth(340)
        card.setMaximumWidth(420)
        body.addWidget(card, 2)
        outer.addLayout(body, 1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.refresh_btn.clicked.connect(self._load_outputs)
        self.window_refresh_btn.clicked.connect(self._load_windows)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start(250)

        # Frames are pulled at display rate, but only while this tab is visible and capture runs.
        # An older native build cannot report the frame number cheaply, so it keeps the 250 ms poll.
        self._fast_frames = bool(getattr(backend, "fast_frame_sequence", False))
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(FRAME_INTERVAL_MS if self._fast_frames else 250)
        self._frame_timer.timeout.connect(self._pump_frame)
        self._load_outputs()
        self._load_windows()

    # --- outputs and windows ----------------------------------------------
    def _load_outputs(self):
        self.output_combo.clear()
        outputs = self._backend.outputs()
        for out in outputs:
            label = f"{out['output_name']} · {out['adapter_name']} · {out['width']}×{out['height']}"
            self.output_combo.addItem(label, out)
        if not outputs:
            self.output_combo.addItem("No display output found", None)
        self._update_buttons()

    def _on_preset_changed(self, index: int, apply_fps: bool = True):
        value = self.preset.itemData(index)
        if value is not None:
            _settings().setValue("capture/preset", value)
        custom = value == "custom"
        self.custom_label.setVisible(custom)
        self.custom_widget.setVisible(custom)
        preset_fps = self.PRESETS[max(0, index)][3]
        if apply_fps and preset_fps is not None:
            found = self.fps_combo.findData(preset_fps)
            if found >= 0:
                self.fps_combo.setCurrentIndex(found)

    def _on_fps_changed(self, index: int):
        value = self.fps_combo.itemData(index)
        if value is not None:
            _settings().setValue("capture/fps", int(value))

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

    def _start(self):
        output = self.output_combo.currentData()
        if not isinstance(output, dict):
            self._show_error("No display output is available to capture.")
            return
        _label_text, value, region, _preset_fps = self.PRESETS[max(0, self.preset.currentIndex())]
        if value == "custom":
            region = self.custom_region.currentData()
        fps = self.fps_combo.currentData()
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
            self.preview.clear_frame()
            self._last_sequence = -1
        self._sync_frame_timer()
        self._update_buttons()

    def _sync_frame_timer(self) -> None:
        """Pull frames only while capture runs and this tab is on screen."""
        if self.stop_btn.isEnabled() and self.isVisible():
            if not self._frame_timer.isActive():
                self._frame_timer.start()
        else:
            self._frame_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_frame_timer()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_frame_timer()

    def _pump_frame(self) -> None:
        """Show the newest frame if it is new. Cheap when nothing changed (no pixels are copied)."""
        if self._fast_frames:
            sequence = self._backend.latest_sequence()
            if not sequence or sequence == self._last_sequence:
                return
        frame = self._backend.latest_frame()
        if not frame or int(frame["sequence"]) == self._last_sequence:
            return
        if self.preview.set_frame(frame["bgra"], int(frame["width"]), int(frame["height"]),
                                  int(frame["stride"])):
            self._last_sequence = int(frame["sequence"])

    def _set_status(self, text, state):
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

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

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

# recommendation_profile() builds gains over this top speed; the chart uses the same scale for them.
RECOMMENDATION_MAX_SPEED = 100.0


def _shared_speed(previous, current) -> float:
    """The speed normalized_overlay() scales both curves by (the larger of their top speeds)."""
    return max(
        max((p.speed for p in previous.horizontal), default=1.0) or 1.0,
        max((p.speed for p in current.horizontal), default=1.0) or 1.0,
    )


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
        # Undo/redo history for the curve. Each entry is a full snapshot taken before a change.
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        # Speed the chart's x positions are scaled by (None: the curve's own top speed).
        self._chart_scale: float | None = None

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Calibration",
            "Measure your movement with the 12-target task, then review the response curve.",
            eyebrow="Input",
        ))

        body = QHBoxLayout()
        body.setSpacing(20)

        # Left: the task field.
        task_card, task_lay = _card(
            "Calibration task",
            "Click each target at your normal sensitivity. Only this in-app task is measured.",
        )
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.recalibrate_btn = QPushButton(" Recalibrate")
        self.recalibrate_btn.setObjectName("primary")
        set_glyph(self.recalibrate_btn, "refresh")
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
        self.canvas.setMinimumHeight(380)
        task_lay.addWidget(self.canvas, 1)

        self.hint = _label("Press Recalibrate when you are ready.", "cardSubtitle", wrap=True)
        task_lay.addWidget(self.hint)
        body.addWidget(task_card, 3)

        # Right: the curve.
        curve_card, curve_lay = _card(
            "Sensitivity curve",
            "Solid line: current curve. Dashed amber: older curve. Drag points to fine-tune.",
        )
        self.legend = CurveLegend()
        curve_lay.addWidget(self.legend)
        self.chart = CurveChart()
        self.chart.setMinimumHeight(260)
        curve_lay.addWidget(self.chart, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.undo_btn = QPushButton(" Undo")
        self.undo_btn.setObjectName("secondary")
        set_glyph(self.undo_btn, "undo")
        self.undo_btn.setToolTip("Undo the last curve change (Ctrl+Z)")
        self.redo_btn = QPushButton(" Redo")
        self.redo_btn.setObjectName("secondary")
        set_glyph(self.redo_btn, "redo")
        self.redo_btn.setToolTip("Redo the change you undid (Ctrl+Y)")
        btn_row.addWidget(self.undo_btn)
        btn_row.addWidget(self.redo_btn)
        btn_row.addStretch(1)
        self.import_btn = QPushButton(" Import")
        self.import_btn.setObjectName("secondary")
        set_glyph(self.import_btn, "upload")
        self.save_btn = QPushButton(" Export")
        self.save_btn.setObjectName("secondary")
        set_glyph(self.save_btn, "download")
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        curve_lay.addLayout(btn_row)
        body.addWidget(curve_card, 2)
        outer.addLayout(body, 1)

        self.recalibrate_btn.clicked.connect(lambda: self.canvas.start_session(12))
        self.reset_btn.clicked.connect(self.canvas.cancel_session)
        self.canvas.progress_changed.connect(self._on_progress)
        self.canvas.hint_changed.connect(self.hint.setText)
        self.canvas.completed.connect(self._on_complete)
        self.chart.curve_adjusted.connect(self._on_curve_adjusted)
        self.save_btn.clicked.connect(self._save)
        self.import_btn.clicked.connect(self._import_curve)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        # Keyboard: Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z), active while this page has focus.
        for seq, handler in ((QKeySequence(QKeySequence.StandardKey.Undo), self.undo),
                             (QKeySequence(QKeySequence.StandardKey.Redo), self.redo)):
            shortcut = QShortcut(seq, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
        self._update_history_buttons()

    def clear_recommendation_state(self) -> None:
        """Forget the last recommendation and the Undo/Redo steps (Privacy > Local data).

        The curve on the chart and the curve Export writes are kept: they are the current curve,
        not history.
        """
        self._last_recommendation = None
        self._undo.clear()
        self._redo.clear()
        self._update_history_buttons()

    def _on_progress(self, completed: int, total: int):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(completed)

    def _set_chart(self, points, previous=None, xs=None, previous_xs=None, scale=None):
        """Show ``points`` at x positions ``xs`` (evenly spaced when None). See CurveChart."""
        self.chart.set_previous(previous, xs=previous_xs)
        self.legend.set_has_older(bool(previous))
        self.chart.points = list(points)
        self.chart.xs = list(xs) if xs is not None else None
        self._chart_scale = scale
        self._chart_baseline = list(points)
        self.chart.update()

    def _set_overlay(self, current_pts, previous_pts, scale: float) -> None:
        """Draw the current curve (solid) and the previous one (dashed), each at its real positions."""
        cur_xs, cur_ys = _curve_points(current_pts)
        prev_xs, prev_ys = _curve_points(previous_pts)
        self._set_chart(cur_ys, previous=prev_ys, xs=cur_xs, previous_xs=prev_xs, scale=scale)

    # -- undo / redo ----------------------------------------------------------
    _HISTORY_LIMIT = 50

    def _snapshot(self, points=None) -> dict:
        """Everything that shows or drives the curve, so it can be put back exactly."""
        return {
            "points": list(points if points is not None else self.chart.points),
            "previous": list(self.chart.previous_points) if self.chart.previous_points else None,
            "previous_xs": list(self.chart.previous_xs) if self.chart.previous_xs is not None else None,
            "xs": list(self.chart.xs) if self.chart.xs is not None else None,
            "scale": self._chart_scale,
            "profile": deepcopy(self._profile),
            "base": deepcopy(self._base_profile),
            "baseline": list(self._chart_baseline) if self._chart_baseline is not None else None,
            "hint": self.hint.text(),
        }

    def _checkpoint(self, points=None) -> None:
        """Record the state before a change. Call this before mutating the curve."""
        self._undo.append(self._snapshot(points))
        del self._undo[:-self._HISTORY_LIMIT]
        self._redo.clear()
        self._update_history_buttons()

    def _restore(self, snap: dict, message: str) -> None:
        self._profile = deepcopy(snap["profile"])
        self._base_profile = deepcopy(snap["base"])
        self._chart_baseline = list(snap["baseline"]) if snap["baseline"] is not None else None
        self.chart.previous_points = list(snap["previous"]) if snap["previous"] else None
        self.chart.previous_xs = list(snap["previous_xs"]) if snap.get("previous_xs") is not None else None
        self.legend.set_has_older(bool(self.chart.previous_points))
        self.chart.points = list(snap["points"])
        self.chart.xs = list(snap["xs"]) if snap.get("xs") is not None else None
        self._chart_scale = snap.get("scale")
        self.chart.update()
        self.hint.setText(message)
        self.profile_changed.emit({"restored": True})
        if self._base_profile is not None:
            self.base_profile_changed.emit(deepcopy(self._base_profile))
        self._update_history_buttons()

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop(), "Undid the last change.")

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop(), "Redid the change.")

    def _update_history_buttons(self) -> None:
        self.undo_btn.setEnabled(bool(self._undo))
        self.redo_btn.setEnabled(bool(self._redo))
        self.undo_btn.setToolTip(
            f"Undo the last curve change (Ctrl+Z){' - ' + str(len(self._undo)) + ' saved' if self._undo else ' - nothing to undo'}"
        )
        self.redo_btn.setToolTip(
            f"Redo the change you undid (Ctrl+Y){' - nothing to redo' if not self._redo else ''}"
        )

    def _on_complete(self, summary: dict, recommendation: dict):
        self._last_recommendation = recommendation
        self.apply_recommendation(recommendation)
        self.recommendation_ready.emit(summary, recommendation)

    def apply_recommendation(self, recommendation: dict) -> None:
        """Draw a recommendation's curve on the chart, before/after, and make it the curve Export writes.

        Used when a calibration finishes and when Generate recommendation is clicked. The
        change is recorded for Undo.
        """
        self._checkpoint()  # a recommendation is a change: the old curve can be undone
        try:
            if self._base_profile is not None:
                # An imported curve is adjusted by the result instead of replaced.
                adjusted, note = adjust_from_recommendation(self._base_profile, recommendation)
                prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
                self._profile = adjusted
                self._set_overlay(cur_pts, prev_pts, _shared_speed(self._base_profile, adjusted))
                self.hint.setText(note)
            else:
                self._profile = recommendation_profile(recommendation)
                xs, ys = _curve_points(recommendation.get("points") or [])
                self._set_chart(
                    ys,
                    previous=list(self.chart.points),
                    xs=xs,
                    previous_xs=self.chart.x_positions(),
                    scale=RECOMMENDATION_MAX_SPEED,
                )
        except CurveFormatError as exc:
            self._profile = None
            self.hint.setText(f"Could not build a curve from this run: {exc}")

    def _on_curve_adjusted(self, points):
        points = [float(v) for v in points]
        baseline = self._chart_baseline or points
        # The drag has already moved the chart points, so record the pre-drag baseline.
        # A click that moved nothing is not a change, so it gets no history entry.
        if baseline != points:
            self._checkpoint(points=baseline)
        xs = self.chart.x_positions()
        if self._profile is None:
            # No curve yet: treat the chart values as the curve's output-speed shape.
            try:
                self._profile = recommendation_profile(
                    {"points": list(zip(xs, points)), "sensitivity_multiplier": 1.0},
                    max_speed=RECOMMENDATION_MAX_SPEED,
                )
                self._chart_scale = RECOMMENDATION_MAX_SPEED
            except CurveFormatError:
                pass
        else:
            self._profile = _apply_chart_edit(self._profile, baseline, points, xs=xs, scale=self._chart_scale)
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
        self._checkpoint()
        self._base_profile = deepcopy(profile)
        self._profile = deepcopy(profile)
        self.base_profile_changed.emit(deepcopy(profile))
        xs, ys = _curve_points(chart_points(profile))
        self._set_chart(ys, xs=xs, scale=max((p.speed for p in profile.horizontal), default=1.0) or 1.0)
        self.hint.setText(
            f"Imported {Path(path).name}. Run calibration to adjust it, or drag points to edit it."
        )
        self.profile_changed.emit({"imported": path})

    def set_adjusted(self, previous, current, note: str) -> None:
        """Show an AI-adjusted curve as before/after and make it the curve Export writes."""
        self._checkpoint()
        self._profile = deepcopy(current)
        prev_pts, cur_pts = normalized_overlay(previous, current)
        self._set_overlay(cur_pts, prev_pts, _shared_speed(previous, current))
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
    """The analyzer's recommendation for the most recent calibration.

    The summary (left) and the suggested curve (right) always sit side by side.
    Neither column is stacked on resize or minimize.

    recommendation_ready(dict) is emitted for every new recommendation.
    apply_requested(dict) is emitted only by the Generate button. The main window
    uses it to show the curve on the Calibration chart.
    """

    recommendation_ready = Signal(dict)
    apply_requested = Signal(dict)

    def __init__(self, backend: CoreBackend, parent=None):
        super().__init__(parent)
        self._backend = backend

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Recommendation",
            "A suggested sensitivity from your latest calibration run.",
            eyebrow="Analysis",
        ))

        row = QHBoxLayout()
        row.setSpacing(20)

        # Left: summary.
        summary_card, summary_lay = _card()
        self.title_label = _label("Awaiting calibration", "cardTitle", wrap=True)
        summary_lay.addWidget(self.title_label)

        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.multiplier_value = QLabel("\u2014")
        self.confidence_value = QLabel("\u2014")
        self.trials_value = QLabel("\u2014")
        for caption, value in (
            ("Sensitivity", self.multiplier_value),
            ("Confidence", self.confidence_value),
            ("Targets", self.trials_value),
        ):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_label(caption, "formLabel"))
            value.setObjectName("metric")
            col.addWidget(value)
            metrics.addLayout(col, 1)
        summary_lay.addLayout(metrics)
        summary_lay.addWidget(_divider())

        self.summary = QLabel("No recommendation yet. Run the calibration task first.")
        self.summary.setObjectName("cardSubtitle")
        self.summary.setWordWrap(True)
        summary_lay.addWidget(self.summary)

        self.reasons_label = _label("", "cardSubtitle", wrap=True)
        summary_lay.addWidget(self.reasons_label)
        summary_lay.addStretch(1)

        self.generate_btn = QPushButton(" Generate recommendation")
        self.generate_btn.setObjectName("primary")
        set_glyph(self.generate_btn, "recommendation")
        summary_lay.addWidget(self.generate_btn, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(summary_card, 2)

        # Right: the suggested curve, read-only.
        chart_card, chart_lay = _card(
            "Suggested curve",
            "How gain rises from slow to fast movement with this sensitivity.",
        )
        self.empty_label = _label("Run a calibration to draw the suggested curve.", "stageText", wrap=True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(260)
        chart_lay.addWidget(self.empty_label, 1)
        self.chart = CurveChart(editable=False)
        self.chart.setMinimumHeight(260)
        self.chart.setVisible(False)
        chart_lay.addWidget(self.chart, 1)
        row.addWidget(chart_card, 3)

        outer.addLayout(row, 1)
        self.generate_btn.clicked.connect(self._generate)

    def update_result(self, summary: dict, recommendation: dict):
        """Show a finished calibration's recommendation and notify listeners."""
        mult = float(recommendation.get("sensitivity_multiplier", 1.0))
        confidence = float(recommendation.get("confidence", 0.0))
        title = str(recommendation.get("title", "Recommendation"))
        reasons = [str(r) for r in recommendation.get("reasons", [])]
        trials = int(summary.get("trial_count", 0))

        self.title_label.setText(title)
        self.multiplier_value.setText(f"\u00d7{mult:.2f}")
        self.confidence_value.setText(f"{confidence:.0%}")
        self.trials_value.setText(str(trials))
        self.summary.setText(
            f"{title}: sensitivity \u00d7{mult:.2f} (confidence {confidence:.0%}, {trials} targets)."
        )
        self.reasons_label.setText("\n".join(f"\u2022  {r}" for r in reasons))

        self.chart.previous_points = None
        xs, ys = _curve_points(recommendation.get("points") or [])
        self.chart.xs = xs
        self.chart.points = ys
        self.chart.setVisible(True)
        self.empty_label.setVisible(False)
        self.chart.update()
        self.recommendation_ready.emit(recommendation)

    def clear_result(self) -> None:
        """Show the empty "awaiting calibration" state again (Privacy > Local data)."""
        self.title_label.setText("Awaiting calibration")
        self.multiplier_value.setText("\u2014")
        self.confidence_value.setText("\u2014")
        self.trials_value.setText("\u2014")
        self.summary.setText("No recommendation yet. Run the calibration task first.")
        self.reasons_label.setText("")
        self.chart.previous_points = None
        self.chart.points = []
        self.chart.xs = None
        self.chart.setVisible(False)
        self.empty_label.setVisible(True)
        self.chart.update()

    def _generate(self):
        summary = dict(self._backend.analyzer.summary())
        if int(summary.get("trial_count", 0)) == 0:
            self.summary.setText("No calibration data yet. Run the calibration task first.")
            return
        recommendation = dict(self._backend.analyzer.recommend())
        self.update_result(summary, recommendation)
        self.apply_requested.emit(recommendation)
