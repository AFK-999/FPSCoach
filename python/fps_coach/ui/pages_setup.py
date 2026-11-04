"""Overview and setup pages: Dashboard, Guide, Capture, Calibration, Recommendation.

Layout is rebuilt for the horizontal top navigation. Capture and calibration
logic is carried over unchanged from the previous window.
"""
from __future__ import annotations

import bisect
import math
import random
import sys
from copy import deepcopy
from dataclasses import replace  # noqa: F401  (kept for parity with curve helpers)
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QRect,
    QSignalBlocker,
    QStandardPaths,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach.ai_chat import AIChatClient  # noqa: F401  (re-exported for tests that patch this module)
from fps_coach.backend import CURVE_DISTRIBUTION_VERSION
from fps_coach.calibration_wizard import clone_answers, wizard_context
from fps_coach.capture_recording import AnalysisTick, RecordingService, RecordingSummary
from fps_coach.curve_adjust import adjust_from_recommendation, normalized_overlay
from fps_coach.curve_io import (
    CurveFormatError,
    CurvePoint,
    CurveProfile,
    chart_axis_scale,
    chart_points,
    load_curve,
    recommendation_profile,
    save_curve,
)
from fps_coach.dashboard_config import (
    BOUNCE_BASE_SPEED,
    BOUNCE_SPEED_DEFAULT,
    clamp_bounce_speed,
)
from fps_coach.window_enum import get_window_rect, list_windows

from .calibration import (
    CALIBRATION_SAMPLE_RATES_HZ,
    DEFAULT_CALIBRATION_SAMPLE_RATE_HZ,
    CalibrationCanvas,
)
from .calibration_wizard import CalibrationWizard
from .dynamic_curve import DynamicCurvePanel
from .flip_card import FlipCard
from .guide_book import GuideBookButton, PerformanceStartButton
from .styles import STAGE, fx_enabled
from .widgets import (
    ComboField,
    CurveChart,
    CurveLegend,
    _apply_chart_edit,
    _card,
    _content_page,
    _curve_points,
    _divider,
    _label,
    _lerp_factor,
    _page_header,
    _page_root,
    _settings,
    set_glyph,
)

_UNSET = object()  # "argument not given", so a snapshot can record None on purpose

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
        self.edit_buttons: list[QWidget] = []  # reorder and hide buttons, hidden in serious mode
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_editing(self, on: bool) -> None:
        for button in self.edit_buttons:
            button.setVisible(on)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


class _Bouncer:
    """One tile while serious mode moves it: its position (floats) and unit direction."""

    __slots__ = ("tile", "x", "y", "ux", "uy")

    def __init__(self, tile: _Tile, x: float, y: float, ux: float, uy: float):
        self.tile = tile
        self.x = x
        self.y = y
        self.ux = ux
        self.uy = uy


BOUNCE_FRAME_MS = 16  # about 60 frames a second
BOUNCE_MAX_STEP_S = 0.05  # a long stall moves tiles at most this far, so they never jump
BOUNCE_MIN_TILE_W = 220
BOUNCE_MIN_STAGE_H = 420
_NO_LIMIT = 16777215  # Qt's QWIDGETSIZE_MAX


class DashboardPage(QWidget):
    """Six module tiles. Click to open; reorder and hide them; values are live.

    Serious mode: the tiles leave the grid and bounce around the dashboard, like the DVD screensaver.
    While they move, reordering and hiding are paused (their buttons are hidden). Turning the mode off
    puts the tiles back in the grid. Clicking a moving tile still opens it.
    """

    TILES = [
        ("capture", "Capture", "Live source status"),
        ("calibration", "Calibration", "Curve freshness"),
        ("training", "Training", "Recent sessions"),
        ("recommendation", "Recommendation", "Latest suggestion"),
        ("crosshair", "Crosshair", "Active preset"),
        ("chat", "AI Coach", "Conversation history"),
    ]
    COLUMNS = 3
    GRID_GAP = 14

    navigate = Signal(str)
    wizard_changed = Signal(dict)
    wizard_discuss = Signal()
    wizard_preview = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._wizard_dialog = None
        self._wizard_answers = clone_answers(None)
        self._wizard_completed = False
        self._wizard_available = True
        self._wizard_summary = {}
        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        # Keep the guide launcher beside the title; the dashboard grid below
        # contains only module tiles. The button does not change layout on click.
        self.dashboard_header = QWidget()
        header_lay = QVBoxLayout(self.dashboard_header)
        header_lay.setContentsMargins(0, 0, 0, 0)
        header_lay.setSpacing(4)
        header_lay.addWidget(_label("OVERVIEW", "eyebrow"))
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        self.dashboard_title = _label("Dashboard", "pageTitle")
        title_row.addWidget(self.dashboard_title)
        self.wizard_btn = GuideBookButton(self._guide_animations_enabled)
        book = Path(__file__).resolve().parents[1] / "assets" / "calibration_guide" / "gold_book.png"
        if book.is_file():
            self.wizard_btn.setIcon(QIcon(str(book)))
        self.wizard_btn.activate_requested.connect(self._open_calibration_wizard)
        title_row.addWidget(self.wizard_btn)
        title_row.addStretch(1)
        header_lay.addLayout(title_row)
        header_lay.addWidget(_label("Every subsystem at a glance. Select a tile to open it.", "pageSubtitle", wrap=True))
        outer.addWidget(self.dashboard_header)

        self._order = self._load_order()
        self._enabled = self._load_enabled()
        self._value_labels: dict[str, QLabel] = {}
        self._caption_labels: dict[str, QLabel] = {}
        self._titles = {t[0]: t for t in self.TILES}
        self._tiles: list[_Tile] = []  # visible tiles in display order, in the grid or bouncing

        self._serious = bool(self.s.value("dashboard/serious_mode", False, type=bool))
        self._bounce_speed = clamp_bounce_speed(self.s.value("dashboard/bounce_speed", BOUNCE_SPEED_DEFAULT))
        self._bouncers: list[_Bouncer] = []
        self._rng = random.Random()
        self._frame_clock = QElapsedTimer()
        self._bounce_timer = QTimer(self)
        self._bounce_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._bounce_timer.setInterval(BOUNCE_FRAME_MS)
        self._bounce_timer.timeout.connect(self._bounce_step)

        self.serious_check = QCheckBox("Animation flex")
        self.serious_check.setToolTip(
            "Tiles bounce around the dashboard like the DVD screensaver. Click a tile to open it. "
            "Speed is set in Settings > Interface."
        )
        self.serious_check.setChecked(self._serious)
        self.serious_check.toggled.connect(self.set_serious_mode)
        outer.addWidget(self.serious_check, 0, Qt.AlignmentFlag.AlignLeft)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(self.GRID_GAP)
        self.grid.setVerticalSpacing(self.GRID_GAP)
        outer.addWidget(self.grid_host)

        self.restore_btn = QPushButton(" Show hidden tiles")
        self.restore_btn.setObjectName("secondary")
        set_glyph(self.restore_btn, "refresh")
        self.restore_btn.clicked.connect(self._show_all)
        outer.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

        self._rebuild_tiles()

    def _guide_animations_enabled(self) -> bool:
        return bool(self.s.value("ui/animations", True, type=bool)) and fx_enabled(
            str(self.s.value("ui/mode", "luxurious"))
        )

    def set_wizard_available(self, available: bool) -> None:
        """A calibration run must finish or cancel before opening the guide."""
        self._wizard_available = bool(available)
        self.wizard_btn.setEnabled(self._wizard_available)

    def set_guide_summary(self, summary: dict | None) -> None:
        from fps_coach.guide_ai import safe_evidence
        self._wizard_summary = safe_evidence(summary)

    def closeEvent(self, event):
        if self._wizard_dialog is not None:
            self._wizard_dialog.reject()
        super().closeEvent(event)

    def _open_calibration_wizard(self) -> None:
        if not self._wizard_available:
            return
        if self._wizard_dialog is not None:
            self._wizard_dialog.raise_()
            self._wizard_dialog.activateWindow()
            return
        self._wizard_dialog = CalibrationWizard(
            clone_answers(self._wizard_answers), self, measured_summary=self._wizard_summary,
        )
        present = bool(wizard_context(self._wizard_answers) or self._wizard_answers["motion"])
        self._wizard_dialog.set_saved_actions(self._wizard_completed, present)
        self._wizard_dialog.discuss_saved.connect(self._discuss_saved_wizard)
        self._wizard_dialog.clear_saved.connect(self._clear_wizard)
        self._wizard_dialog.finished.connect(self._finish_calibration_wizard)
        self._wizard_dialog.open()

    def _finish_calibration_wizard(self, result: int) -> None:
        dialog, self._wizard_dialog = self._wizard_dialog, None
        if dialog is None:
            return
        if result == int(dialog.DialogCode.Accepted):
            self._wizard_answers = clone_answers(dialog.answers)
            self._wizard_completed = True
            self._refresh_wizard_status()
            self.wizard_changed.emit(clone_answers(self._wizard_answers))
            if dialog.preview_requested and dialog.ai_proposal and dialog.ai_proposal_answers == self._wizard_answers:
                self.wizard_preview.emit(deepcopy(dialog.ai_proposal))
            elif dialog.start_calibration_requested:
                self.navigate.emit("calibration")
        dialog.deleteLater()

    def _refresh_wizard_status(self) -> None:
        self.wizard_btn.setToolTip("Edit calibration guide" if self._wizard_completed else "Open calibration guide")

    def _discuss_saved_wizard(self) -> None:
        if self._wizard_dialog is not None:
            self._wizard_dialog.reject()  # Discard uncommitted edits, discuss saved answers only.
        self.wizard_discuss.emit()

    def _clear_wizard(self) -> None:
        if self._wizard_dialog is not None:
            self._wizard_dialog.reject()
        self._wizard_answers = clone_answers(None)
        self._wizard_completed = False
        self._refresh_wizard_status()
        self.wizard_changed.emit(clone_answers(None))

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

    # --- serious mode --------------------------------------------------------
    @property
    def serious_mode(self) -> bool:
        return self._serious

    @property
    def bounce_speed(self) -> int:
        """Current speed as a percentage (the Settings slider value)."""
        return self._bounce_speed

    def set_serious_mode(self, on: bool) -> None:
        on = bool(on)
        self._serious = on
        self.s.setValue("dashboard/serious_mode", on)
        with QSignalBlocker(self.serious_check):
            self.serious_check.setChecked(on)
        self._sync_bounce()

    def set_bounce_speed(self, percent: int) -> None:
        """Takes effect on the next frame. The Settings page saves the value."""
        self._bounce_speed = clamp_bounce_speed(percent)

    def _sync_bounce(self) -> None:
        """Bring the layout and the timer in line with the mode and with whether the page is on screen."""
        if not self._serious:
            self._leave_bounce()
        elif not self._bouncers and self.isVisible():
            self._enter_bounce()
        self._update_timer()

    def _enter_bounce(self) -> None:
        if self._bouncers or not self._tiles:
            return
        width = self.grid_host.width()
        if width <= 0:
            return  # not laid out yet; showEvent tries again
        gap = self.GRID_GAP
        tile_w = max(BOUNCE_MIN_TILE_W, (width - gap * (self.COLUMNS - 1)) // self.COLUMNS)
        tile_h = max(tile.sizeHint().height() for tile in self._tiles)
        rows = -(-len(self._tiles) // self.COLUMNS)
        stage_h = max(rows * tile_h + (rows - 1) * gap, BOUNCE_MIN_STAGE_H)
        self.grid_host.setFixedHeight(stage_h)
        for index, tile in enumerate(self._tiles):
            self.grid.removeWidget(tile)
            tile.setFixedSize(tile_w, tile_h)
            tile.set_editing(False)
            x = (index % self.COLUMNS) * (tile_w + gap)
            y = (index // self.COLUMNS) * (tile_h + gap)
            tile.move(x, y)
            tile.show()
            # Random starting direction between 20 and 70 degrees off the horizontal, in any quadrant.
            angle = self._rng.uniform(0.35, 1.2)
            ux = math.cos(angle) * self._rng.choice((-1.0, 1.0))
            uy = math.sin(angle) * self._rng.choice((-1.0, 1.0))
            self._bouncers.append(_Bouncer(tile, float(x), float(y), ux, uy))

    def _drop_bouncers(self) -> None:
        """Stop moving the tiles and give back the size limits and fixed stage height."""
        self._bounce_timer.stop()
        for bouncer in self._bouncers:
            bouncer.tile.setMinimumSize(0, 0)
            bouncer.tile.setMaximumSize(_NO_LIMIT, _NO_LIMIT)
        self._bouncers = []
        self.grid_host.setMinimumHeight(0)
        self.grid_host.setMaximumHeight(_NO_LIMIT)

    def _leave_bounce(self) -> None:
        if not self._bouncers:
            return
        self._drop_bouncers()
        for index, tile in enumerate(self._tiles):
            self.grid.addWidget(tile, index // self.COLUMNS, index % self.COLUMNS)
            tile.set_editing(True)
            tile.show()

    def _update_timer(self) -> None:
        if self._bouncers and self.isVisible():
            if not self._bounce_timer.isActive():
                self._frame_clock.restart()
                self._bounce_timer.start()
        else:
            self._bounce_timer.stop()

    def _bounce_step(self) -> None:
        """Move each tile by its speed times the frame time, and turn it at the edges."""
        dt = min(BOUNCE_MAX_STEP_S, self._frame_clock.restart() / 1000.0)
        step = BOUNCE_BASE_SPEED * (self._bounce_speed / 100.0) * dt
        width, height = self.grid_host.width(), self.grid_host.height()
        for bouncer in self._bouncers:
            tile = bouncer.tile
            max_x = max(0, width - tile.width())
            max_y = max(0, height - tile.height())
            bouncer.x += bouncer.ux * step
            bouncer.y += bouncer.uy * step
            if bouncer.x <= 0:
                bouncer.x, bouncer.ux = 0.0, abs(bouncer.ux)
            elif bouncer.x >= max_x:
                bouncer.x, bouncer.ux = float(max_x), -abs(bouncer.ux)
            if bouncer.y <= 0:
                bouncer.y, bouncer.uy = 0.0, abs(bouncer.uy)
            elif bouncer.y >= max_y:
                bouncer.y, bouncer.uy = float(max_y), -abs(bouncer.uy)
            tile.move(round(bouncer.x), round(bouncer.y))

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._sync_bounce()
        if self._serious and not self._bouncers:
            # The grid may not have its final width yet; try again once the event loop has run.
            QTimer.singleShot(0, self._sync_bounce)

    def hideEvent(self, event):  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self._update_timer()

    # --- tiles -------------------------------------------------------------
    def _rebuild_tiles(self):
        self._drop_bouncers()
        while self.grid.count():
            self.grid.takeAt(0)
        for tile in self._tiles:
            tile.hide()  # deleteLater runs later; a hidden old tile cannot show at its old position
            tile.deleteLater()
        self._tiles = []
        self._value_labels.clear()
        self._caption_labels.clear()

        visible = [k for k in self._order if self._enabled.get(k, True)]
        for index, key in enumerate(visible):
            _kind, title, subtitle = self._titles[key]
            tile = self._build_tile(key, title, subtitle)
            self._tiles.append(tile)
            self.grid.addWidget(tile, index // self.COLUMNS, index % self.COLUMNS)
        for col in range(self.COLUMNS):
            self.grid.setColumnStretch(col, 1)
        self.restore_btn.setVisible(len(visible) < len(self.TILES))
        self._sync_bounce()

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
            tile.edit_buttons.append(btn)
        hide_btn = QToolButton()
        set_glyph(hide_btn, "close")
        hide_btn.setObjectName("iconButton")
        hide_btn.setToolTip("Hide tile")
        hide_btn.clicked.connect(lambda _c=False, k=key: self._hide(k))
        head.addWidget(hide_btn)
        tile.edit_buttons.append(hide_btn)
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
    """Application onboarding plus explanatory guides to individual features."""

    open_crosshair = Signal()

    STEPS = [
        ("capture", "Connect your capture source",
         "Optionally start a display or window preview on Capture; this is not needed for calibration."),
        ("calibration", "Run calibration",
         "Use the in-app target task to build a starting curve from captured mouse movement."),
        ("recommendation", "Review your recommendation",
         "CurveCoach proposes a sensitivity curve tuned to your aim data, with the reasons behind it."),
        ("training", "Train with live feedback",
         "Use in-app training drills and analytics to compare and refine your setup."),
        ("chat", "Ask the AI coach anytime",
         "Open the coach for follow-up questions, or ask it to adjust an imported curve."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        scroll, outer = _content_page(960)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Guide",
            "Five introduction steps followed by a numbered Crosshair feature guide.",
            eyebrow="Help",
        ))
        for index, (key, title, body) in enumerate(self.STEPS, start=1):
            card, _ = self._numbered_card(f"{index:02d}", key, title, body)
            outer.addWidget(card)

        crosshair, content = self._numbered_card(
            "06", "crosshair", "Crosshair feature guide",
            "Design and preview a crosshair in the Crosshair tab. "
            "These instructions do not enable the overlay.",
        )
        crosshair.setAccessibleName("Crosshair feature guide")
        self.open_crosshair_btn = QPushButton("Open Crosshair")
        self.open_crosshair_btn.setObjectName("secondary")
        self.open_crosshair_btn.setAccessibleName("Open Crosshair from the application guide")
        self.open_crosshair_btn.clicked.connect(self.open_crosshair.emit)
        content.addWidget(self.open_crosshair_btn, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(crosshair)

        sections = (
            ("Design and preview",
             "Choose a preset or shape, then adjust thickness, arm length, center gap, dot size, opacity, "
             "outline and color. The preview updates as you change settings; preferences are saved."),
            ("Overlay and position",
             "Use the Crosshair overlay button to show or hide the overlay. Edit position lets you move it; "
             "lock it afterward or use Reset to centre. Some games or display modes may not show overlays."),
            ("Import and export",
             "Export your crosshair as a PNG or JSON file, or import a JSON configuration. "
             "Exporting does not change game settings."),
            ("Dynamic Magnification",
             "The Dynamic Magnification button opens its settings; it does not enable magnification. "
             "With the overlay active on supported Windows 10 version 2004 or later, enable it separately "
             "and adjust circle size, zoom and the optional toggle key. It uses live desktop capture; "
             "background polling of the chosen key is opt-in and protected content may not appear."),
            ("Check the rules",
             "Before using an overlay or magnification with a game, check the game and anti-cheat rules. "
             "No compatibility, approval or ban-safety guarantee is provided."),
        )
        for index, (heading, description) in enumerate(sections, start=1):
            card, _ = self._numbered_card(f"06.{index}", "crosshair", heading, description)
            card.setAccessibleName(f"Crosshair guide section 06.{index}: {heading}")
            outer.addWidget(card)

    @staticmethod
    def _numbered_card(number: str, key: str, title: str, body: str):
        """Use the same badge, glyph and card layout for steps and subsections."""
        card, lay = _card()
        row = QHBoxLayout()
        row.setSpacing(16)
        badge = QLabel(number)
        badge.setObjectName("stepBadge")
        badge.setFixedSize(52, 52)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        content = QVBoxLayout()
        content.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        glyph = QLabel()
        set_glyph(glyph, key, 16, role="text2")
        head.addWidget(glyph)
        head.addWidget(_label(title, "cardTitle"))
        head.addStretch(1)
        content.addLayout(head)
        content.addWidget(_label(body, "cardSubtitle", wrap=True))
        row.addLayout(content, 1)
        lay.addLayout(row)
        return card, content


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

FRAME_INTERVAL_MS = 4  # the check copies nothing, so a short poll shows each new frame almost at once
# In window mode, no new frame for this long is worth a note (a static or minimised window sends none).
WINDOW_STALL_MS = 1500


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

    The Recording card is independent of the native capture: it records the
    primary display to a scratch video through fps_coach.screen_recorder while
    an analysis timer samples the newest frame. The scratch file is deleted when
    recording stops or the window closes.
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
            "Choose what CurveCoach watches and how often it samples it.",
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
        self.output_combo = ComboField()
        form.addWidget(self.output_combo, 0, 1)

        form.addWidget(_label("Window", "formLabel"), 1, 0)
        source_row = QHBoxLayout()
        self.source_combo = ComboField()
        source_row.addWidget(self.source_combo, 1)
        self.window_refresh_btn = QToolButton()
        self.window_refresh_btn.setObjectName("iconButton")
        set_glyph(self.window_refresh_btn, "refresh")
        self.window_refresh_btn.setToolTip("Refresh the window list")
        source_row.addWidget(self.window_refresh_btn)
        form.addLayout(source_row, 1, 1)

        form.addWidget(_label("Preset", "formLabel"), 2, 0)
        self.preset = ComboField()
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
        self.custom_region = ComboField()
        self.custom_region.addItem("Center 640 × 360", (640, 360))
        self.custom_region.addItem("Center 960 × 540", (960, 540))
        custom_row.addWidget(self.custom_region, 1)
        form.addWidget(self.custom_widget, 3, 1)

        # Frame rate is always visible. A preset sets it when chosen; the user can change it.
        form.addWidget(_label("Frame rate", "formLabel"), 4, 0)
        self.fps_combo = ComboField()
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
        if not getattr(backend, "native_available", False):
            # mss reads the composited desktop on the CPU; a ticked GPU option that
            # does nothing is worse than no option at all.
            self.hwaccel.setChecked(False)
            self.hwaccel.setEnabled(False)
            self.hwaccel.setToolTip(
                "GPU capture needs the native C++ core. Live capture is running "
                "through mss on the CPU instead."
            )
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

        # Fallbacks and stall notes. Kept apart from errors so a fallback is never mistaken for a failure.
        self.notice_label = QLabel("")
        self.notice_label.setObjectName("statusLabel")
        self.notice_label.setWordWrap(True)
        self.notice_label.setVisible(False)
        lay.addWidget(self.notice_label)
        lay.addStretch(1)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(16)
        right_lay.addWidget(card)
        self.live_source_note = _label("", "cardSubtitle", wrap=True)
        self.live_source_note.setVisible(False)
        right_lay.addWidget(self.live_source_note)
        right_lay.addWidget(self._build_recording_card())
        right_lay.addStretch(1)
        right.setMinimumWidth(340)
        right.setMaximumWidth(420)
        body.addWidget(right, 2)
        outer.addLayout(body, 1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.refresh_btn.clicked.connect(self._load_outputs)
        self.window_refresh_btn.clicked.connect(self._load_windows)

        # Recording card (scratch video + analysis timer, deleted on stop).
        self.recording = RecordingService(self)
        self.recording.started.connect(self._on_recording_started)
        self.recording.stopped.connect(self._on_recording_stopped)
        self.recording.analysis.connect(self._on_analysis_tick)
        self.recording.failed.connect(self._on_recording_failed)
        self.record_start_btn.clicked.connect(self._start_recording)
        self.record_stop_btn.clicked.connect(self._stop_recording)
        self._apply_recording_availability()
        self._apply_capture_backend_note()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start(250)

        # Frames are pulled at display rate, but only while this tab is visible and capture runs.
        # An older native build cannot report the frame number cheaply, so it keeps the 250 ms poll.
        self._fast_frames = bool(getattr(backend, "fast_frame_sequence", False))
        self._start_note = ""  # set when the chosen mode fell back (for example, an older native build)
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(FRAME_INTERVAL_MS if self._fast_frames else 250)
        self._frame_timer.timeout.connect(self._pump_frame)
        self._load_outputs()
        self._load_windows()

    # --- live capture backend note ----------------------------------------
    def _apply_capture_backend_note(self) -> None:
        """Tell the user which engine feeds the preview, and what it cannot do.

        Without the native core this card used to look broken (no outputs, Start
        greyed out) because nothing said why. The Python path exists so the live
        preview still works; the note names its two limits.
        """
        if getattr(self._backend, "native_available", False):
            self.live_source_note.setVisible(False)
            return
        if getattr(self._backend, "uses_python_capture", False):
            self.live_source_note.setText(
                "Live preview is running through mss (Python) because the native core is "
                "not built. Up to 60 FPS, and a window is captured as the screen area "
                "behind it."
            )
        else:
            self.live_source_note.setText(
                "Live capture is unavailable: the native core is not built and mss is not "
                'installed. Install it with: pip install -e ".[recorder]"'
            )
        self.live_source_note.setVisible(True)

    # --- recording (scratch video + analysis timer) ------------------------
    RECORD_FPS_CHOICES = (15, 30, 60)

    def _build_recording_card(self):
        """Card under Source: scratch recording of the primary display."""
        card, lay = _card(
            "Recording",
            "Records the primary display while you record, and samples the newest frame on a "
            "timer. Keep the video or let it be discarded when you stop.",
        )
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Recording rate", "formLabel"), 0, 0)
        self.record_fps = ComboField()
        for fps in self.RECORD_FPS_CHOICES:
            self.record_fps.addItem(f"{fps} FPS", fps)
        saved_fps = int(_settings().value("capture/record_fps", 30, type=int))
        index = self.record_fps.findData(saved_fps)
        self.record_fps.setCurrentIndex(index if index >= 0 else 1)
        self.record_fps.currentIndexChanged.connect(self._on_record_fps_changed)
        form.addWidget(self.record_fps, 0, 1)

        form.addWidget(_label("Analysis every", "formLabel"), 1, 0)
        self.record_interval = QDoubleSpinBox()
        self.record_interval.setRange(0.5, 60.0)
        self.record_interval.setSingleStep(0.5)
        self.record_interval.setDecimals(1)
        self.record_interval.setSuffix(" s")
        self.record_interval.setValue(float(_settings().value("capture/record_interval", 2.0, type=float)))
        self.record_interval.setToolTip("How often the newest frame is handed to the analysis callback")
        self.record_interval.valueChanged.connect(
            lambda value: _settings().setValue("capture/record_interval", float(value))
        )
        form.addWidget(self.record_interval, 1, 1)
        lay.addLayout(form)

        self.record_keep = QCheckBox("Keep the recording")
        self.record_keep.setToolTip(
            "Save the video instead of discarding it. Saved files stay on this machine "
            "until you delete them."
        )
        self.record_keep.setChecked(bool(_settings().value("capture/record_keep", False, type=bool)))
        self.record_keep.toggled.connect(self._on_record_keep_toggled)
        lay.addWidget(self.record_keep)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.record_dir_edit = QLineEdit(self._record_dir())
        self.record_dir_edit.setReadOnly(True)
        self.record_dir_edit.setToolTip("Where kept recordings are written")
        self.record_dir_btn = QPushButton(" Folder…")
        self.record_dir_btn.setObjectName("secondary")
        set_glyph(self.record_dir_btn, "download")
        self.record_dir_btn.clicked.connect(self._choose_record_dir)
        dir_row.addWidget(self.record_dir_edit, 1)
        dir_row.addWidget(self.record_dir_btn)
        lay.addLayout(dir_row)
        self._sync_record_keep_controls()

        row = QHBoxLayout()
        row.setSpacing(8)
        self.record_start_btn = QPushButton(" Record")
        self.record_start_btn.setObjectName("secondary")
        set_glyph(self.record_start_btn, "play")
        self.record_stop_btn = QPushButton(" Stop")
        self.record_stop_btn.setObjectName("secondary")
        set_glyph(self.record_stop_btn, "stop")
        self.record_stop_btn.setEnabled(False)
        row.addWidget(self.record_start_btn)
        row.addWidget(self.record_stop_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.record_status = _label("Idle", "statusLabel")
        lay.addWidget(self.record_status)

        self.record_detail = _label("", "statusLabel", wrap=True)
        self.record_detail.setVisible(False)
        lay.addWidget(self.record_detail)

        self.record_analysis = _label("", "cardSubtitle", wrap=True)
        self.record_analysis.setVisible(False)
        lay.addWidget(self.record_analysis)

        self.record_note = _label(
            "Uses the primary display, independent of the Source selection above.",
            "cardSubtitle",
            wrap=True,
        )
        self.record_detail.setToolTip("")
        lay.addWidget(self.record_note)
        return card

    @staticmethod
    def default_record_dir() -> str:
        """Videos/FPSCoach (or Home/FPSCoach when the platform has no Movies folder)."""
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
        return str(Path(base or Path.home()) / "FPSCoach")

    def _record_dir(self) -> str:
        return str(_settings().value("capture/record_dir", self.default_record_dir()))

    def _sync_record_keep_controls(self) -> None:
        """The folder row only matters when the recording is kept."""
        keep = self.record_keep.isChecked()
        usable, _message = RecordingService.availability()
        # Called once while the card is being built, before the service exists.
        recording = bool(getattr(self, "recording", None) and self.recording.is_recording)
        self.record_dir_edit.setEnabled(keep and usable and not recording)
        self.record_dir_btn.setEnabled(keep and usable and not recording)

    def _on_record_keep_toggled(self, checked: bool) -> None:
        _settings().setValue("capture/record_keep", bool(checked))
        self._sync_record_keep_controls()

    def _choose_record_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Recordings folder", self.record_dir_edit.text())
        if chosen:
            self.record_dir_edit.setText(chosen)
            _settings().setValue("capture/record_dir", chosen)

    def _on_record_fps_changed(self, index: int) -> None:
        fps = self.record_fps.itemData(index)
        if fps is not None:
            _settings().setValue("capture/record_fps", int(fps))

    def _apply_recording_availability(self) -> None:
        usable, message = RecordingService.availability()
        self.record_start_btn.setEnabled(usable)
        self.record_fps.setEnabled(usable)
        self.record_interval.setEnabled(usable)
        self.record_keep.setEnabled(usable)
        self._sync_record_keep_controls()
        if not usable:
            self._set_record_status("Unavailable", "bad")
            self._show_record_detail(message)
            self.record_note.setText("Install the recorder extra to enable this card.")

    def _start_recording(self) -> None:
        keep = self.record_keep.isChecked()
        started = self.recording.start(
            fps=float(self.record_fps.currentData() or 30),
            analysis_interval=float(self.record_interval.value()),
            monitor_index=1,
            keep=keep,
            save_dir=self.record_dir_edit.text() if keep else None,
        )
        if started:
            self._set_record_buttons(recording=True)
            self._set_record_status("Starting…", "good")
            self._show_record_detail("")

    def _stop_recording(self) -> None:
        self.recording.stop()  # emits stopped() -> _on_recording_stopped

    def _on_recording_started(self, path: str) -> None:
        self._set_record_buttons(recording=True)
        self._set_record_status("Recording to a scratch file…", "good")
        self.record_detail.setToolTip(path)
        self._show_record_detail("")
        self.record_analysis.setText("")
        self.record_analysis.setVisible(False)

    def _on_recording_stopped(self, summary: RecordingSummary) -> None:
        self._set_record_buttons(recording=False)
        calls = f"{summary.analyses} analysis call{'s' if summary.analyses != 1 else ''}"
        if summary.kept:
            self._set_record_status(
                f"Saved · {summary.frames:,} frames · {summary.seconds:.1f} s", "good"
            )
            self._show_record_detail(f"Recording saved to {summary.path} ({calls}).")
        else:
            self._set_record_status(
                f"Stopped · {summary.frames:,} frames · {summary.seconds:.1f} s", ""
            )
            self._show_record_detail(f"Scratch video discarded after {calls}.")
        self.record_analysis.setVisible(False)

    def _on_recording_failed(self, message: str) -> None:
        self._set_record_buttons(recording=False)
        self._set_record_status("Recording stopped", "bad")
        self._show_record_detail(message)
        self.record_analysis.setVisible(False)

    def _on_analysis_tick(self, tick: AnalysisTick) -> None:
        self.record_analysis.setText(
            f"Analysis #{tick.count} · frame {tick.sequence:,} · "
            f"{tick.width}×{tick.height} · mean {tick.mean:.1f}"
        )
        self.record_analysis.setVisible(True)

    def _refresh_recording(self) -> None:
        status = self.recording.poll()
        if not status.running:
            return
        analyses = f"{status.analyses} analysis" if status.analyses == 1 else (
            f"{status.analyses} analyses"
        )
        self._set_record_status(
            f"Recording · {status.seconds:.1f} s · {status.frames:,} frames · {analyses}",
            "good",
        )

    def _set_record_buttons(self, *, recording: bool) -> None:
        usable, _message = RecordingService.availability()
        self.record_start_btn.setEnabled(usable and not recording)
        self.record_stop_btn.setEnabled(recording)
        self.record_fps.setEnabled(usable and not recording)
        self.record_interval.setEnabled(usable and not recording)
        self.record_keep.setEnabled(usable and not recording)
        self.record_dir_edit.setEnabled(
            usable and not recording and self.record_keep.isChecked()
        )
        self.record_dir_btn.setEnabled(
            usable and not recording and self.record_keep.isChecked()
        )

    def _set_record_status(self, text: str, state: str) -> None:
        self.record_status.setText(text)
        self.record_status.setProperty("state", state)
        self.record_status.style().unpolish(self.record_status)
        self.record_status.style().polish(self.record_status)

    def _show_record_detail(self, message: str) -> None:
        self.record_detail.setText(message)
        self.record_detail.setVisible(bool(message))

    def shutdown(self) -> None:
        """Stop the recording on window close; kept files stay on disk."""
        self.recording.shutdown()
        self.record_start_btn.setEnabled(False)
        self.record_stop_btn.setEnabled(False)

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
        window_note = ""
        start_note = ""
        hwnd = self.source_combo.currentData()
        window_rect = get_window_rect(int(hwnd)) if hwnd else None
        if hwnd and not window_rect:
            window_note = "Could not read that window's bounds; capturing the full output instead."
            hwnd = None
        try:
            if hwnd:
                # Window mode: only that window is recorded, even when other windows cover it.
                # The window's bounds are the screen-area fallback.
                left, top, width, height = window_rect
                start_note = self._backend.start_window(
                    int(hwnd),
                    int(output["adapter_index"]),
                    int(output["output_index"]),
                    int(width),
                    int(height),
                    int(fps),
                    int(left),
                    int(top),
                )
            else:
                self._backend.start_capture(
                    int(output["adapter_index"]),
                    int(output["output_index"]),
                    int(width),
                    int(height),
                    int(fps),
                    -1,
                    -1,
                )
        except RuntimeError as exc:  # NativeCoreUnavailable is a RuntimeError
            self._show_error(str(exc))
            return
        self._start_note = start_note
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
            self._show_notice("")
            self._start_note = ""
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

    def _show_notice(self, message):
        self.notice_label.setText(message)
        self.notice_label.setVisible(bool(message))

    @staticmethod
    def _status_text(mode: str) -> str:
        return {
            "window": "Capturing this window only",
            "region": "Capturing screen area",
            "output": "Capturing full display",
        }.get(mode, "Capturing…")

    def _refresh_stats(self):
        self._refresh_recording()
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
        mode = str(stats.get("capture_mode") or "")
        self._set_status(self._status_text(mode), "good")
        notices = [str(stats.get("notice") or ""), self._start_note]
        if mode == "window" and int(stats.get("last_frame_age_ms", 0)) > WINDOW_STALL_MS:
            notices.append("No new frames from this window for a while. It may be minimised, "
                           "or nothing in it is changing.")
        self._show_notice(" ".join(note for note in notices if note))
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
CALIBRATION_SAMPLE_RATE_KEY = "calibration/sample_hz"


def _shared_speed(previous, current) -> float:
    """The common speed scale used for profile overlays and axis metadata."""
    return chart_axis_scale(previous, current)[0]


class CalibrationPage(QWidget):
    """Calibration task (CalibrationCanvas on backend.analyzer) plus the curve view.

    When a run completes, emits recommendation_ready(summary, recommendation).
    """

    recommendation_ready = Signal(dict, dict)
    profile_changed = Signal(dict)
    graph_style_changed = Signal(str)  # preview preference; never a profile edit
    base_profile_changed = Signal(object)  # imported CurveProfile (AI Coach builds on it)
    def __init__(self, backend: CoreBackend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._last_recommendation = None
        self._profile = None         # the curve Export writes (imported, calibrated, or edited)
        self._base_profile = None    # imported curve that calibration adjusts
        self._chart_baseline = None  # chart values before the latest drag
        self._chart_baseline_xs = None  # their x positions, so an added/removed point can be compared
        self._pending_chart_structure = None  # ("add"/"delete", point index, x), set before the edit signal
        self._point_index = None  # currently selected horizontal control point
        # Undo/redo history for the curve. Each entry is a full snapshot taken before a change.
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        # Speed the chart's x positions are scaled by (None: the curve's own top speed).
        self._chart_scale: float | None = None
        self._live_snapshot: dict | None = None
        self._sample_settings = _settings()
        self._graph_style = str(self._sample_settings.value("calibration/graph_style", "cc3"))
        if self._graph_style not in {"cc3", "cc4"}:
            self._graph_style = "cc3"
        try:
            saved_sample_rate = int(
                self._sample_settings.value(
                    CALIBRATION_SAMPLE_RATE_KEY, DEFAULT_CALIBRATION_SAMPLE_RATE_HZ
                )
            )
        except (TypeError, ValueError):
            saved_sample_rate = DEFAULT_CALIBRATION_SAMPLE_RATE_HZ
        if saved_sample_rate not in CALIBRATION_SAMPLE_RATES_HZ:
            saved_sample_rate = DEFAULT_CALIBRATION_SAMPLE_RATE_HZ
        self._sample_rate_hz = saved_sample_rate

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        self._calibration_header = _page_header(
            "Calibration",
            "Capture movement across 12 targets. The curve is built from the speeds you actually use.",
            eyebrow="Input",
        )
        outer.addWidget(self._calibration_header)

        body = QHBoxLayout()
        body.setSpacing(20)

        # Left: the task field.
        task_card, task_lay = _card()
        heading_row = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(2)
        heading_text.addWidget(_label("Calibration task", "cardTitle"))
        heading_text.addWidget(_label("Click each target in this in-app task.", "cardSubtitle", wrap=True))
        heading_row.addLayout(heading_text, 1)
        self.task_settings_btn = QToolButton()
        self.task_settings_btn.setObjectName("iconButton")
        self.task_settings_btn.setCheckable(True)
        self.task_settings_btn.setChecked(False)
        self.task_settings_btn.setAccessibleName("Calibration input settings")
        self.task_settings_btn.setToolTip("Show calibration input settings")
        set_glyph(self.task_settings_btn, "settings")
        self.task_settings_btn.toggled.connect(self._toggle_input_settings)
        heading_row.addWidget(self.task_settings_btn, 0, Qt.AlignmentFlag.AlignTop)
        task_lay.addLayout(heading_row)
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

        # Inline, collapsed by default like the Training and Coach settings.
        # Keep the panel in the task card, never in a separate top-level dialog.
        self.input_settings_panel = QFrame()
        self.input_settings_panel.setObjectName("calibrationInputPanel")
        self.input_settings_panel.setAccessibleName("Calibration input settings panel")
        settings_lay = QVBoxLayout(self.input_settings_panel)
        settings_lay.setContentsMargins(12, 10, 12, 10)
        settings_lay.setSpacing(8)
        settings_lay.addWidget(_label("Input Path", "formLabel"))
        self.input_mode_combo = ComboField()
        self.input_mode_combo.addItem("Windows raw · neutral virtual cursor", "raw")
        self.input_mode_combo.addItem("Desktop pointer · natural OS pointer", "desktop")
        self.input_mode_combo.setCurrentIndex(self.input_mode_combo.findData("raw" if sys.platform == "win32" else "desktop"))
        self.input_mode_combo.setAccessibleName("Calibration input path")
        self.input_mode_combo.setToolTip("Raw mode requires foreground Windows relative mouse input and temporarily confines the OS cursor. It does not change game input.")
        settings_lay.addWidget(self.input_mode_combo)
        self.input_note = _label("", "cardSubtitle", wrap=True)
        settings_lay.addWidget(self.input_note)
        self.telemetry_note = _label(
            "DPI telemetry unavailable: configured DPI changes and physical drift are not detected or compensated. "
            "Keep your device DPI setting fixed during calibration.", "cardSubtitle", wrap=True,
        )
        settings_lay.addWidget(self.telemetry_note)
        rate_row = QHBoxLayout()
        rate_label = _label("Desktop sample-rate target", "formLabel")
        rate_label.setToolTip("Timer sampling target for desktop-pointer calibration; raw mode uses delivered packets.")
        self.sample_rate_combo = ComboField()
        for hz in CALIBRATION_SAMPLE_RATES_HZ:
            self.sample_rate_combo.addItem(f"{hz:,} Hz", hz)
        self.sample_rate_combo.setCurrentIndex(self.sample_rate_combo.findData(saved_sample_rate))
        self.sample_rate_combo.setAccessibleName("Desktop pointer sample-rate target")
        self.sample_rate_combo.setToolTip(
            "Target timer rate. At 1,000 Hz the timer samples every 1 ms. "
            "Qt/Windows scheduling or UI load may reduce the achieved rate; mouse-move events "
            "are recorded as well."
        )
        rate_row.addWidget(rate_label)
        rate_row.addWidget(self.sample_rate_combo)
        rate_row.addStretch(1)
        settings_lay.addLayout(rate_row)
        self.adaptive_targets = QCheckBox("Adaptive moving targets (optional)")
        supported = callable(getattr(backend.analyzer, "set_target", None))
        self.adaptive_targets.setEnabled(supported)
        self.adaptive_targets.setToolTip(
            "Off by default. Local rules adjust radius and motion speed using recent visible-target hit accuracy. "
            "Moving-target metrics are measured against its click-time center; not directly comparable with stationary calibration."
            if supported else "Requires an updated analyzer; rebuild the native extension.")
        settings_lay.addWidget(self.adaptive_targets)
        settings_lay.addWidget(_label(
            "Optional: local rules adjust moving target size and speed from recent visible-target accuracy. "
            "Metrics use the target center at click time, not its full motion history.", "cardSubtitle", wrap=True))
        self.input_settings_session_note = _label(
            "Input settings are locked during calibration.", "cardSubtitle", wrap=True
        )
        settings_lay.addWidget(self.input_settings_session_note)
        self.input_settings_panel.hide()
        task_lay.insertWidget(1, self.input_settings_panel)

        self.canvas = CalibrationCanvas(backend.analyzer)
        self.adaptive_targets.toggled.connect(self.canvas.set_adaptive_enabled)
        self.canvas.set_sample_rate(saved_sample_rate)
        self.canvas.set_input_mode(str(self.input_mode_combo.currentData()))
        self.canvas.setMinimumHeight(380)
        task_lay.addWidget(self.canvas, 1)

        self.hint = _label("Press Recalibrate when you are ready.", "cardSubtitle", wrap=True)
        task_lay.addWidget(self.hint)
        self.resume_raw_btn = QPushButton("Resume raw input")
        self.resume_raw_btn.setObjectName("secondary")
        self.resume_raw_btn.hide()
        self.resume_raw_btn.clicked.connect(self.canvas.resume_raw_capture)
        self.canvas.raw_resume_changed.connect(self.resume_raw_btn.setVisible)
        task_lay.addWidget(self.resume_raw_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._refresh_input_mode_note()
        body.addWidget(task_card, 3)

        # Calibration retains only its editable sensitivity curve.
        curve_card, curve_lay = _card()
        title_row = QHBoxLayout()
        self.curve_title = _label("Sensitivity curve", "cardTitle")
        title_row.addWidget(self.curve_title, 1)
        self.graph_style_combo = ComboField()
        self.graph_style_combo.addItem("CC3 · aligned points", "cc3")
        self.graph_style_combo.addItem("CC4 · weighted controls", "cc4")
        self.graph_style_combo.setCurrentIndex(self.graph_style_combo.findData(self._graph_style))
        self.graph_style_combo.setAccessibleName("Graph editing style")
        self.graph_style_combo.setToolTip(
            "Graph editing style for all previews. Custom Curve 3 keeps Calibration points aligned; "
            "Custom Curve 4 shows an approximate weighted B-spline with controls that may be off the line."
        )
        title_row.addWidget(self.graph_style_combo)
        self.point_settings_btn = QToolButton()
        self.point_settings_btn.setObjectName("iconButton")
        self.point_settings_btn.setCheckable(True)
        self.point_settings_btn.setAccessibleName("Control point settings")
        self.point_settings_btn.setAccessibleDescription("Collapsed; activate to expand")
        self.point_settings_btn.setToolTip("Show exact control point settings")
        set_glyph(self.point_settings_btn, "settings")
        title_row.addWidget(self.point_settings_btn)
        curve_lay.addLayout(title_row)
        curve_lay.addWidget(_label(
            "Drag points; right-click to add or delete. Scroll to zoom; "
            "drag empty space or an axis to pan; middle-click to reset.", "cardSubtitle", wrap=True,
        ))
        self.legend = CurveLegend()
        curve_lay.addWidget(self.legend)
        self.chart = CurveChart(interpolate_points=True)
        self.chart.set_graph_style(self._graph_style)
        self.graph_style_combo.currentIndexChanged.connect(self._on_graph_style_changed)
        self.chart.setMinimumHeight(260)
        self.chart.points = []
        self.chart.xs = []
        curve_lay.addWidget(self.chart, 1)
        self.curve_status = _label(
            "Waiting for captured movement. The live estimate appears after the first target.",
            "cardSubtitle", wrap=True,
        )
        curve_lay.addWidget(self.curve_status)

        # Exact values edit the current horizontal profile only; previews elsewhere
        # remain read-only until their existing confirmation flow is used.
        self.point_settings_panel = QWidget()
        self.point_settings_panel.setAccessibleName("Exact control point settings")
        precise = QGridLayout(self.point_settings_panel)
        precise.setContentsMargins(0, 0, 0, 0)
        precise.setHorizontalSpacing(8)
        precise.setVerticalSpacing(4)
        point_label = _label("Control point", "cardSubtitle")
        velocity_label = _label("Input velocity", "cardSubtitle")
        sensitivity_label = _label("Sensitivity ×", "cardSubtitle")
        self.point_combo = ComboField()
        self.point_combo.setAccessibleName("Select horizontal curve control point")
        self.point_velocity = QLineEdit()
        self.point_velocity.setAccessibleName("Control point input velocity")
        self.point_velocity.setPlaceholderText("Velocity")
        self.point_sensitivity = QLineEdit()
        self.point_sensitivity.setAccessibleName("Control point sensitivity multiplier")
        self.point_sensitivity.setPlaceholderText("Multiplier")
        self.point_weight_label = _label("Weight 0–1", "cardSubtitle")
        self.point_weight = QLineEdit()
        self.point_weight.setAccessibleName("CC4 control point weight")
        self.point_weight.setPlaceholderText("0 to 1")
        self.point_weight.setToolTip("Higher weight increases this point's influence in the approximate CC4 preview. Low speeds relate to micro aim; high speeds to flicks. This does not prove performance improvements.")
        self.weight_note = _label("Low speeds: micro aim · high speeds: flicks.", "cardSubtitle", wrap=True)
        self.weight_note.setToolTip("Higher weights pull the approximate CC4 preview toward the selected control. This is not a guaranteed performance improvement.")
        self.point_apply_btn = QPushButton("Apply")
        self.point_apply_btn.setObjectName("secondary")
        self.point_apply_btn.setAccessibleName("Apply exact control point values")
        self.point_apply_btn.setToolTip("Apply values to the in-app curve; export to save a file.")
        self.point_error = _label("", "statusLabel", wrap=True)
        self.point_error.setProperty("state", "warn")
        self.point_error.hide()
        precise.addWidget(point_label, 0, 0)
        precise.addWidget(velocity_label, 0, 1)
        precise.addWidget(sensitivity_label, 0, 2)
        precise.addWidget(self.point_combo, 1, 0)
        precise.addWidget(self.point_velocity, 1, 1)
        precise.addWidget(self.point_sensitivity, 1, 2)
        precise.addWidget(self.point_apply_btn, 1, 3)
        precise.addWidget(self.point_weight_label, 2, 0)
        precise.addWidget(self.point_weight, 2, 1)
        precise.addWidget(self.weight_note, 2, 2, 1, 2)
        precise.addWidget(self.point_error, 3, 0, 1, 4)
        precise.setColumnStretch(0, 1)
        precise.setColumnStretch(1, 1)
        precise.setColumnStretch(2, 1)
        curve_lay.addWidget(self.point_settings_panel)
        self.point_settings_panel.hide()
        self.point_settings_btn.toggled.connect(self._toggle_point_settings)
        self.point_combo.currentIndexChanged.connect(self._on_point_combo_changed)
        self.point_apply_btn.clicked.connect(self._apply_numeric_point)
        self.point_velocity.returnPressed.connect(self._apply_numeric_point)
        self.point_sensitivity.returnPressed.connect(self._apply_numeric_point)
        self.point_weight.returnPressed.connect(self._apply_numeric_point)
        self.chart.point_selected.connect(self._on_chart_point_selected)
        self.chart.weight_edit_requested.connect(self._on_weight_edit_requested)
        self._refresh_point_editor()

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
        set_glyph(self.import_btn, "download")
        self.save_btn = QPushButton(" Export")
        self.save_btn.setObjectName("secondary")
        set_glyph(self.save_btn, "upload")
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        curve_lay.addLayout(btn_row)
        # The task can grow when its settings open. Keep the graph column at
        # its own preferred height rather than stretching to match that growth.
        body.addWidget(curve_card, 2, Qt.AlignmentFlag.AlignTop)
        # Reserve scrollbar width before the panel expands, so the graph does
        # not shift horizontally when the content becomes taller than the view.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._curve_card = curve_card
        self._calibration_viewport = scroll.viewport()
        self._calibration_viewport.installEventFilter(self)
        outer.addLayout(body, 1)

        self.recalibrate_btn.clicked.connect(lambda: self.canvas.start_session(12))
        self.reset_btn.clicked.connect(self.canvas.cancel_session)
        self.canvas.progress_changed.connect(self._on_progress)
        self.canvas.hint_changed.connect(self.hint.setText)
        self.canvas.session_started.connect(self._on_session_started)
        self.canvas.session_cancelled.connect(self._on_session_cancelled)
        self.canvas.recommendation_updated.connect(self._on_live_recommendation)
        self.canvas.completed.connect(self._on_complete)
        self.chart.curve_structure_changed.connect(self._on_chart_structure_changed)
        self.chart.curve_geometry_changed.connect(self._on_curve_adjusted)
        self.sample_rate_combo.currentIndexChanged.connect(self._on_sample_rate_changed)
        self.input_mode_combo.currentIndexChanged.connect(self._on_input_mode_changed)
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

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if watched is self._calibration_viewport and event.type() == QEvent.Type.Resize:
            # Scale with the viewport, not with the independently expanding task
            # column. The inner page has 28+32 px margins and 20 px row spacing.
            available = event.size().height() - self._calibration_header.sizeHint().height() - 80
            self._curve_card.setMinimumHeight(max(0, available))
        return super().eventFilter(watched, event)

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

    def _toggle_input_settings(self, checked: bool) -> None:
        self.input_settings_panel.setVisible(checked)
        self.task_settings_btn.setToolTip(
            "Hide calibration input settings" if checked else "Show calibration input settings"
        )

    def _refresh_input_mode_note(self) -> None:
        raw = self.canvas.input_mode == "raw"
        self.input_note.setText(
            "Raw mode: 1 delivered relative count = 1 virtual logical pixel until the arena edge. "
            "Windows confines the OS cursor during the task; Esc releases it. OS delivery, device firmware, "
            "display scaling and games can differ. Packets, not the sample-rate timer, drive the curve."
            if raw else
            "Desktop mode observes the OS pointer, which may include Windows speed and acceleration. "
            "The requested timer cadence is not guaranteed."
        )
        self.sample_rate_combo.setEnabled(not raw and not self.canvas._active)

    def _on_input_mode_changed(self) -> None:
        self.canvas.set_input_mode(str(self.input_mode_combo.currentData()))
        self._refresh_input_mode_note()

    def _toggle_point_settings(self, expanded: bool) -> None:
        self.point_settings_panel.setVisible(expanded)
        self.point_settings_btn.setToolTip(
            "Hide exact control point settings" if expanded else "Show exact control point settings")
        self.point_settings_btn.setAccessibleDescription(
            "Expanded; activate to collapse" if expanded else "Collapsed; activate to expand")

    def _set_curve_controls_enabled(self, enabled: bool) -> None:
        """Keep the provisional graph read-only while the movement model is updating it."""
        self.chart.set_editable(enabled)
        self.graph_style_combo.setEnabled(enabled)
        self.point_settings_btn.setEnabled(enabled)
        self.recalibrate_btn.setEnabled(enabled)
        self.input_mode_combo.setEnabled(enabled)
        self.sample_rate_combo.setEnabled(enabled and self.canvas.input_mode == "desktop")
        self.adaptive_targets.setEnabled(enabled and callable(getattr(self._backend.analyzer, "set_target", None)))
        # Do not reflow/move the active arena by expanding or collapsing settings.
        self.task_settings_btn.setEnabled(enabled)
        if enabled:
            self.task_settings_btn.setToolTip(
                "Hide calibration input settings" if self.task_settings_btn.isChecked()
                else "Show calibration input settings"
            )
        else:
            self.task_settings_btn.setToolTip("Input settings are locked during calibration.")
        for control in (self.undo_btn, self.redo_btn, self.import_btn, self.save_btn):
            control.setEnabled(enabled)
        self._refresh_point_editor()
        if enabled:
            self._update_history_buttons()

    def _on_session_started(self) -> None:
        if self._live_snapshot is None:
            self._live_snapshot = self._snapshot()
        self._set_curve_controls_enabled(False)
        self.curve_status.setText(
            "Capturing cursor movement. A provisional curve will update after each completed target."
        )

    def _on_session_cancelled(self) -> None:
        if self._live_snapshot is not None:
            snapshot, self._live_snapshot = self._live_snapshot, None
            self._restore(snapshot, "Calibration cancelled; previous curve restored.")
        self._set_curve_controls_enabled(True)

    def _on_live_recommendation(self, summary: dict, recommendation: dict) -> None:
        """Draw the measured curve after each completed target without exporting it yet."""
        raw_points = list(recommendation.get("points") or [])
        if len(raw_points) < 2:
            return
        try:
            if self._base_profile is not None:
                adjusted, _note = adjust_from_recommendation(self._base_profile, recommendation)
                previous, current = normalized_overlay(self._base_profile, adjusted)
                self._set_overlay(current, previous, *chart_axis_scale(self._base_profile, adjusted),
                                  current_profile=adjusted, previous_profile=self._base_profile)
            else:
                profile = recommendation_profile(recommendation)
                self._set_profile_chart(profile)
        except CurveFormatError as exc:
            self.curve_status.setText(f"Live curve is not available yet: {exc}")
            return
        self._set_model_anchor_tooltip(recommendation)
        sample_count = int(recommendation.get("captured_sample_count", 0) or 0)
        trials = int(summary.get("trial_count", 0) or 0)
        window_count = summary.get("window_trial_count")
        window_label = (
            f" · rolling window {int(window_count)}/{int(summary.get('window_capacity', window_count))} targets"
            if window_count is not None else ""
        )
        point_label = (
            f"{len(raw_points)} model positions; imported curve keeps {len(self.chart.points)} points"
            if self._base_profile is not None else f"{len(raw_points)} model control points"
        )
        reference = float(recommendation.get("speed_reference_px_s", 0.0) or 0.0)
        units = "virtual px/s" if summary.get("input_path") == "raw" else "desktop px/s"
        baseline_label = f" · 95th-percentile speed reference {reference:,.0f} {units}" if reference > 0 else ""
        sparse_note = (" Sparse input cannot resolve intermediate corrections."
                       if window_count is not None and int(window_count) > 0 and sample_count < 2 * int(window_count) else "")
        self.curve_status.setText(
            f"Live estimate · {trials} completed target{'s' if trials != 1 else ''} · "
            f"{sample_count:,} captured movement samples{window_label}{baseline_label} · {point_label}. "
            "Updates as you work; the finished run determines the exportable curve." + sparse_note
        )

    def _set_model_anchor_tooltip(self, recommendation: dict) -> None:
        """Explain model anchors and the visual simplification's limits."""
        if self._base_profile is not None:
            self.chart.setToolTip(
                "This is an adjusted imported curve. Its own point positions are preserved; "
                "generated-model point selection does not simplify it."
            )
            return
        if recommendation.get("curve_distribution_version") != CURVE_DISTRIBUTION_VERSION:
            self.chart.setToolTip(
                "Model control points are not raw samples. This recommendation does not include "
                "the current point-selection information."
            )
            return
        reference = float(recommendation.get("speed_reference_px_s", 0.0) or 0.0)
        clipped = int(recommendation.get("clipped_speed_count", 0) or 0)
        detail = (
            f" Current 95th-percentile {'virtual' if self.canvas.input_mode == 'raw' else 'desktop'} "
            f"speed: {reference:,.1f} px/s; "
            f"{clipped:,} speed samples above it are clipped for candidate placement."
            if reference > 0 else ""
        )
        self.chart.setToolTip(
            "These are model control points, not raw samples. The fewest candidate points are "
            "selected to keep sampled output-speed model positions within a 0.005 normalized "
            "model-space distance. The plotted sensitivity is converted from those anchors, "
            "so that tolerance is not a bound on the gain plot. This is a visual approximation, "
            "not an exact or between-sample error guarantee. Generated exports use normalized "
            "0–100 speed, not measured px/s, and other applications may interpolate differently." + detail
        )

    def _on_sample_rate_changed(self, index: int) -> None:
        """Apply and persist the selected precise-timer sampling target."""
        rate = self.sample_rate_combo.itemData(index)
        if rate not in CALIBRATION_SAMPLE_RATES_HZ:
            return
        self._sample_rate_hz = int(rate)
        self.canvas.set_sample_rate(self._sample_rate_hz)
        self._sample_settings.setValue(CALIBRATION_SAMPLE_RATE_KEY, self._sample_rate_hz)

    def _set_chart(self, points, previous=None, xs=None, previous_xs=None, scale=None,
                   gain_scale=1.0, units="normalized", weights=None, previous_weights=None):
        """Draw normalized geometry with its actual profile axes recorded explicitly."""
        self.chart.set_previous(previous, xs=previous_xs)
        self.legend.set_has_older(bool(previous))
        self.chart.points = list(points)
        self.chart.xs = list(xs) if xs is not None else None
        self.chart.set_curve_weights(weights, previous_weights)
        self._chart_scale = scale
        self.chart.set_axis_scale(scale or 1.0, gain_scale, units)
        self._chart_baseline = list(points)
        self._chart_baseline_xs = self.chart.x_positions()
        self.chart.update()
        self._refresh_point_editor()

    def _set_profile_chart(self, profile):
        """Draw a profile in gain units, keeping the old line on one shared scale."""
        own_x, own_y, units = chart_axis_scale(profile)
        old_points = list(self.chart.points) if len(self.chart.points) >= 2 else None
        old_weights = list(self.chart.weights) if old_points else None
        old_xs = self.chart.x_positions() if old_points else None
        old_x = self.chart.axis_x_max if old_points else None
        old_y = self.chart.axis_y_max if old_points else None
        # An unscaled or differently sourced older plot cannot be compared honestly.
        if old_x is None or old_y is None or self.chart.axis_x_units != units:
            old_points, old_xs, old_x, old_y, old_weights = None, None, None, None, None
        shared_x, shared_y = max(own_x, old_x or own_x), max(own_y, old_y or own_y)
        raw = chart_points(profile)
        xs = [x * own_x / shared_x for x, _ in raw]
        ys = [y * own_y / shared_y for _, y in raw]
        if old_points:
            old_xs = [x * old_x / shared_x for x in old_xs]
            old_points = [y * old_y / shared_y for y in old_points]
        self._set_chart(ys, previous=old_points, xs=xs, previous_xs=old_xs,
                        scale=shared_x, gain_scale=shared_y, units=units,
                        weights=[p.smoothness for p in profile.horizontal], previous_weights=old_weights)

    def _set_overlay(self, current_pts, previous_pts, scale: float, gain_scale: float, units: str,
                     current_profile=None, previous_profile=None) -> None:
        """Draw current and previous profiles against their shared physical values."""
        cur_xs, cur_ys = _curve_points(current_pts)
        prev_xs, prev_ys = _curve_points(previous_pts)
        self._set_chart(cur_ys, previous=prev_ys, xs=cur_xs, previous_xs=prev_xs,
                        scale=scale, gain_scale=gain_scale, units=units,
                        weights=[p.smoothness for p in current_profile.horizontal] if current_profile else None,
                        previous_weights=[p.smoothness for p in previous_profile.horizontal] if previous_profile else None)

    # -- undo / redo ----------------------------------------------------------
    _HISTORY_LIMIT = 50

    def _snapshot(self, points=None, xs=_UNSET, baseline=_UNSET, baseline_xs=_UNSET) -> dict:
        """Everything that shows or drives the curve, so it can be put back exactly.

        The optional arguments describe the curve *before* the change. Adding or deleting a point
        moves both the values and their x positions, so a snapshot taken after the edit would
        restore the wrong dot count — the caller passes the curve as it was.
        """
        snap_xs = self.chart.xs if xs is _UNSET else xs
        snap_baseline = self._chart_baseline if baseline is _UNSET else baseline
        snap_baseline_xs = self._chart_baseline_xs if baseline_xs is _UNSET else baseline_xs
        return {
            "points": list(points if points is not None else self.chart.points),
            "previous": list(self.chart.previous_points) if self.chart.previous_points else None,
            "previous_xs": list(self.chart.previous_xs) if self.chart.previous_xs is not None else None,
            "weights": ([p.smoothness for p in self._profile.horizontal]
                        if points is not None and self._profile is not None
                        and len(self._profile.horizontal) == len(points) else list(self.chart.weights)),
            "previous_weights": list(self.chart.previous_weights),
            "xs": list(snap_xs) if snap_xs is not None else None,
            "scale": self._chart_scale,
            "axis_x_max": self.chart.axis_x_max,
            "axis_y_max": self.chart.axis_y_max,
            "axis_x_units": self.chart.axis_x_units,
            "point_index": self._point_index,
            "profile": deepcopy(self._profile),
            "base": deepcopy(self._base_profile),
            "baseline": list(snap_baseline) if snap_baseline else None,
            "baseline_xs": list(snap_baseline_xs) if snap_baseline_xs else None,
            "hint": self.hint.text(),
            "chart_tooltip": self.chart.toolTip(),
        }

    def _push_undo_snapshot(self, snapshot: dict) -> None:
        """Add a complete pre-change state to the bounded Undo stack."""
        self._undo.append(snapshot)
        del self._undo[:-self._HISTORY_LIMIT]
        self._redo.clear()
        self._update_history_buttons()

    def _checkpoint(self, points=None, xs=_UNSET, baseline=_UNSET, baseline_xs=_UNSET) -> None:
        """Record the state before a change. Call this before mutating the curve."""
        self._push_undo_snapshot(
            self._snapshot(points, xs=xs, baseline=baseline, baseline_xs=baseline_xs)
        )

    def _restore(self, snap: dict, message: str) -> None:
        self._profile = deepcopy(snap["profile"])
        self._base_profile = deepcopy(snap["base"])
        self._chart_baseline = list(snap["baseline"]) if snap["baseline"] is not None else None
        self._chart_baseline_xs = (
            list(snap["baseline_xs"]) if snap.get("baseline_xs") is not None else None
        )
        self.chart.previous_points = list(snap["previous"]) if snap["previous"] else None
        self.chart.previous_xs = list(snap["previous_xs"]) if snap.get("previous_xs") is not None else None
        self.legend.set_has_older(bool(self.chart.previous_points))
        self.chart.points = list(snap["points"])
        self.chart.xs = list(snap["xs"]) if snap.get("xs") is not None else None
        self.chart.set_curve_weights(snap.get("weights"), snap.get("previous_weights"))
        self._chart_scale = snap.get("scale")
        self.chart.set_axis_scale(snap.get("axis_x_max"), snap.get("axis_y_max"), snap.get("axis_x_units", "unavailable"))
        self.chart.setToolTip(snap.get("chart_tooltip", ""))
        self._point_index = snap.get("point_index")
        self._refresh_point_editor()
        self.chart.update()
        self.hint.setText(message)
        self.curve_status.setText(
            f"Curve restored · {len(self.chart.points)} control points."
            if self.chart.points else "Waiting for captured movement data."
        )
        self.profile_changed.emit({"restored": True})
        if self._base_profile is not None:
            self.base_profile_changed.emit(deepcopy(self._base_profile))
        self._update_history_buttons()

    def undo(self) -> None:
        if self.canvas._active or not self._undo:
            return
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop(), "Undid the last change.")

    def redo(self) -> None:
        if self.canvas._active or not self._redo:
            return
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop(), "Redid the change.")

    def _update_history_buttons(self) -> None:
        active = not self.canvas._active
        self.undo_btn.setEnabled(active and bool(self._undo))
        self.redo_btn.setEnabled(active and bool(self._redo))
        self.undo_btn.setToolTip(
            f"Undo the last curve change (Ctrl+Z){' - ' + str(len(self._undo)) + ' saved' if self._undo else ' - nothing to undo'}"
        )
        self.redo_btn.setToolTip(
            f"Redo the change you undid (Ctrl+Y){' - nothing to redo' if not self._redo else ''}"
        )

    def _on_complete(self, summary: dict, recommendation: dict):
        self._set_curve_controls_enabled(True)
        self._last_recommendation = recommendation
        self.apply_recommendation(recommendation)
        self.recommendation_ready.emit(summary, recommendation)

    def apply_recommendation(self, recommendation: dict) -> None:
        """Draw a recommendation's curve on the chart, before/after, and make it the curve Export writes.

        Used when a calibration finishes and when Generate recommendation is clicked. The
        change is recorded for Undo.
        """
        if self._live_snapshot is not None:
            # Treat all interim graph refreshes as one change: Undo returns to the
            # curve that existed before this calibration session began.
            snapshot, self._live_snapshot = self._live_snapshot, None
            self._push_undo_snapshot(snapshot)
        else:
            self._checkpoint()  # a recommendation is a change: the old curve can be undone
        self._set_model_anchor_tooltip(recommendation)
        try:
            if self._base_profile is not None:
                # An imported curve is adjusted by the result instead of replaced.
                adjusted, note = adjust_from_recommendation(self._base_profile, recommendation)
                prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
                self._profile = adjusted
                self._set_overlay(cur_pts, prev_pts, *chart_axis_scale(self._base_profile, adjusted),
                                  current_profile=adjusted, previous_profile=self._base_profile)
                self.hint.setText(note)
            else:
                self._profile = recommendation_profile(recommendation)
                self._set_profile_chart(self._profile)
            point_count = len(recommendation.get("points") or [])
            sample_count = int(recommendation.get("captured_sample_count", 0) or 0)
            window_count = recommendation.get("window_trial_count")
            window_label = (
                f" in the latest {int(window_count)} targets" if window_count is not None else ""
            )
            if sample_count and self._base_profile is not None:
                self.curve_status.setText(
                    f"Final estimate · {sample_count:,} captured movement samples{window_label} · "
                    f"{point_count} model recommendation positions. The imported curve "
                    f"keeps its {len(self.chart.points)} editable points."
                )
            elif sample_count:
                self.curve_status.setText(
                    f"Final curve · {sample_count:,} captured movement samples{window_label} · "
                    f"{len(self.chart.points)} adaptive control points. The graph is ready to edit or export."
                )
            else:
                self.curve_status.setText(f"Recommendation curve · {len(self.chart.points)} control points.")
            if window_count is not None and int(window_count) > 0 and sample_count < 2 * int(window_count):
                self.curve_status.setText(self.curve_status.text() + " Sparse input cannot resolve intermediate corrections.")
        except CurveFormatError as exc:
            self._profile = None
            self.hint.setText(f"Could not build a curve from this run: {exc}")

    def _on_graph_style_changed(self, _index: int) -> None:
        style = str(self.graph_style_combo.currentData())
        if self.canvas._active:
            with QSignalBlocker(self.graph_style_combo):
                self.graph_style_combo.setCurrentIndex(self.graph_style_combo.findData(self._graph_style))
            return
        if style not in {"cc3", "cc4"} or style == self._graph_style:
            return
        self._graph_style = style
        self.point_error.clear()
        self.point_error.hide()
        self._sample_settings.setValue("calibration/graph_style", style)
        self.chart.set_graph_style(style)
        self._update_weight_editor()
        self.graph_style_changed.emit(style)

    def _update_weight_editor(self) -> None:
        visible = self._graph_style == "cc4"
        for widget in (self.point_weight_label, self.point_weight, self.weight_note):
            widget.setVisible(visible)
        selected = (self._profile is not None and not self.canvas._active and
                    self._point_index is not None and 0 <= self._point_index < len(self._profile.horizontal))
        self.point_weight.setEnabled(visible and selected)
        if selected:
            self.point_weight.setText(repr(float(self._profile.horizontal[self._point_index].smoothness)))
        else:
            self.point_weight.clear()

    def _refresh_point_editor(self) -> None:
        """Reflect the selected profile point without rounding or emitting edits."""
        self.point_error.clear()
        self.point_error.hide()
        profile = self._profile
        usable = profile is not None and not self.canvas._active
        count = len(profile.horizontal) if usable else 0
        if self._point_index is not None and not 0 <= self._point_index < count:
            self._point_index = None
        with QSignalBlocker(self.point_combo):
            self.point_combo.clear()
            self.point_combo.addItem("Select point", None)
            for i in range(count):
                self.point_combo.addItem(f"Point {i + 1}", i)
            self.point_combo.setCurrentIndex(self._point_index + 1 if self._point_index is not None else 0)
        selected = usable and self._point_index is not None
        self.point_combo.setEnabled(usable and count > 0)
        self.point_velocity.setEnabled(selected)
        self.point_sensitivity.setEnabled(selected)
        self.point_apply_btn.setEnabled(selected)
        self.chart.select_point(self._point_index if selected else None)
        if selected:
            point = profile.horizontal[self._point_index]
            self.point_velocity.setText(repr(float(point.speed)))
            self.point_sensitivity.setText(repr(float(point.multiplier)))
            self.point_velocity.setToolTip(f"Velocity in {self.chart.axis_x_units}. New values: 0–1,000,000,000; larger imported values may be kept or reduced.")
            self.point_sensitivity.setToolTip("Sensitivity multiplier. New values: 0–20; larger imported values may be kept or reduced.")
        else:
            self.point_velocity.clear()
            self.point_sensitivity.clear()
        self._update_weight_editor()

    def _on_point_combo_changed(self, _index: int) -> None:
        self._point_index = self.point_combo.currentData()
        self._refresh_point_editor()

    def _on_chart_point_selected(self, index: int) -> None:
        self._point_index = index if index >= 0 else None
        self._refresh_point_editor()

    def _on_weight_edit_requested(self, index: int) -> None:
        if self.canvas._active or self._graph_style != "cc4":
            return
        self._on_chart_point_selected(index)
        self.point_settings_btn.setChecked(True)
        if self.point_weight.isEnabled():
            self.point_weight.setFocus()
            self.point_weight.selectAll()

    def _point_validation_error(self, message: str) -> None:
        self.point_settings_btn.setChecked(True)
        self.point_error.setText(message)
        self.point_error.show()
        self.hint.setText(message)

    def _apply_numeric_point(self) -> None:
        """Commit exact x/y values, preserve metadata and support undo/redo."""
        if self.canvas._active or self._profile is None or self._point_index is None:
            return
        index = self._point_index
        if not 0 <= index < len(self._profile.horizontal):
            return
        old = self._profile.horizontal[index]
        try:
            speed = float(self.point_velocity.text().strip())
            gain = float(self.point_sensitivity.text().strip())
            weight = float(self.point_weight.text().strip()) if self._graph_style == "cc4" else old.smoothness
        except ValueError:
            self._point_validation_error("Enter valid numbers for velocity, sensitivity and (in CC4) weight.")
            return
        # Existing imported values above these limits can be preserved or
        # reduced, but new edits cannot increase them further.
        if (not math.isfinite(speed) or not math.isfinite(gain)
                or not 0 <= speed <= max(1_000_000_000, old.speed)
                or not 0 <= gain <= max(20.0, old.multiplier)):
            self._point_validation_error("Velocity must be 0–1,000,000,000 and sensitivity 0–20; both must be finite. Existing larger values may be preserved or reduced.")
            return
        if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
            self._point_validation_error("Weight must be a finite number from 0 to 1.")
            return
        points = self._profile.horizontal
        if (index and speed < points[index - 1].speed) or (index + 1 < len(points) and speed > points[index + 1].speed):
            self._point_validation_error("Velocity must remain between the neighboring control points.")
            return
        if speed == old.speed and gain == old.multiplier and weight == old.smoothness:
            self.hint.setText("The selected point is unchanged.")
            return
        # If the two axes were identical they remain linked; independent
        # vertical points are intentionally untouched by this horizontal edit.
        linked = self._profile.vertical == self._profile.horizontal
        self._checkpoint()
        self._profile.horizontal[index] = replace(old, speed=speed, multiplier=gain, smoothness=weight)
        if linked:
            self._profile.vertical[index] = replace(self._profile.vertical[index], speed=speed, multiplier=gain, smoothness=weight)
        old_x = self.chart.axis_x_max or 1.0
        old_y = self.chart.axis_y_max or 1.0
        previous = list(self.chart.previous_points) if self.chart.previous_points else None
        previous_xs = (list(self.chart.previous_xs) if self.chart.previous_xs is not None else
                       [i / (len(previous) - 1) for i in range(len(previous))] if previous and len(previous) > 1 else None)
        previous_weights = list(self.chart.previous_weights)
        old_view = self.chart._view
        prev_x = [v * old_x for v in previous_xs] if previous_xs else []
        prev_y = [v * old_y for v in previous] if previous else []
        x_max = max([p.speed for p in self._profile.horizontal] + prev_x, default=0.0) or 1.0
        y_max = max([p.multiplier for p in self._profile.horizontal] + prev_y, default=0.0) or 1.0
        self._set_chart([p.multiplier / y_max for p in self._profile.horizontal],
                        xs=[p.speed / x_max for p in self._profile.horizontal],
                        previous=[v / y_max for v in prev_y] if previous else None,
                        previous_xs=[v / x_max for v in prev_x] if previous_xs else None,
                        scale=x_max, gain_scale=y_max, units=self.chart.axis_x_units,
                        weights=[p.smoothness for p in self._profile.horizontal],
                        previous_weights=previous_weights)
        if old_view == (0.0, 1.0, 0.0, 1.0):
            self.chart.reset_view()  # fit a changed endpoint or top sensitivity
        else:
            vx0, vx1 = self.chart._view_bounds(old_view[0] * old_x / x_max, (old_view[1] - old_view[0]) * old_x / x_max)
            vy0, vy1 = self.chart._view_bounds(old_view[2] * old_y / y_max, (old_view[3] - old_view[2]) * old_y / y_max)
            self.chart._view = (vx0, vx1, vy0, vy1)
            self.chart.update()
        self.hint.setText("Exact point values applied to the horizontal curve. Export to save the profile.")
        self.curve_status.setText(f"Manually edited curve · {len(self._profile.horizontal)} control points.")
        self.chart.setToolTip("Manually edited curve; preview is an approximation, not verified driver output.")
        self.profile_changed.emit({"point": index, "speed": speed, "multiplier": gain, "weight": weight})

    def _on_chart_structure_changed(self, operation: str, index: int, x: float) -> None:
        """Remember which point the widget is adding/deleting before it emits the new curve."""
        self._pending_chart_structure = (operation, index, x)

    @staticmethod
    def _axis_gain_at(points: list[CurvePoint], x: float, scale: float) -> float:
        """Interpolate an axis's multiplier at chart x, using the chart's speed scale."""
        if not points:
            return 1.0
        scale = scale or max((point.speed for point in points), default=1.0) or 1.0
        axis_xs = [point.speed / scale for point in points]
        axis_ys = [point.multiplier for point in points]
        return _lerp_factor(axis_xs, axis_ys, x)

    @staticmethod
    def _insert_axis_point(points: list[CurvePoint], x: float, multiplier: float, scale: float) -> list[CurvePoint]:
        """Insert a point at chart x into an axis, retaining the user's existing point objects."""
        result = deepcopy(points)
        speed = max(0.0, x * (scale or max((point.speed for point in result), default=1.0) or 1.0))
        candidate = CurvePoint(
            speed=speed,
            multiplier=max(0.05, min(20.0, multiplier)),
        )
        index = bisect.bisect_right([point.speed for point in result], speed)
        result.insert(index, candidate)
        return result

    @staticmethod
    def _remove_axis_point(
        points: list[CurvePoint], x: float, scale: float, index: int | None = None
    ) -> list[CurvePoint]:
        """Remove the corresponding axis control point, but never take an axis below two."""
        if len(points) <= 2:
            return points
        scale = scale or max((point.speed for point in points), default=1.0) or 1.0
        if index is None or index >= len(points) or abs(points[index].speed / scale - x) > 1e-5:
            index = min(range(len(points)), key=lambda i: abs(points[i].speed / scale - x))
        if abs(points[index].speed / scale - x) > 1e-5:
            return points  # a different-speed vertical curve has no matching point to remove
        result = list(points)
        del result[index]
        return result

    def _edit_profile_structure(
        self, updated_profile, source_profile, before_xs, before_values,
        after_xs, after_values, structure,
    ):
        """Keep the exported curve's control points in step with graph add/delete operations."""
        operation, index, x = structure
        if updated_profile is None or source_profile is None:
            return updated_profile
        scale = self._chart_scale or max((point.speed for point in source_profile.horizontal), default=1.0) or 1.0
        old_value = _lerp_factor(before_xs, before_values, x) if len(before_xs) >= 2 else 0.0
        new_value = (
            after_values[index] if operation == "add" and 0 <= index < len(after_values)
            else _lerp_factor(after_xs, after_values, x)
        )
        def added_gain(axis):
            previous_gain = self._axis_gain_at(axis, x, scale)
            if old_value > 1e-9:
                return previous_gain * new_value / old_value
            chart_gain = max(before_values, default=0.0)
            axis_gain = max((point.multiplier for point in axis), default=1.0)
            gain_scale = axis_gain / chart_gain if chart_gain > 1e-9 else 1.0
            return previous_gain + (new_value - old_value) * gain_scale

        updated = deepcopy(updated_profile)
        if operation == "add":
            updated.horizontal = self._insert_axis_point(
                updated.horizontal, x, added_gain(source_profile.horizontal), scale
            )
            updated.vertical = self._insert_axis_point(
                updated.vertical, x, added_gain(source_profile.vertical), scale
            )
        elif operation == "delete":
            updated.horizontal = self._remove_axis_point(updated.horizontal, x, scale, index)
            updated.vertical = self._remove_axis_point(updated.vertical, x, scale, index)
        return updated

    @staticmethod
    def _move_profile_speeds(profile, before_xs, after_xs, scale):
        """Move profile control points to the chart's new speed positions, without changing gain."""
        if profile is None or len(before_xs) != len(after_xs):
            return profile
        scale = scale or max((point.speed for point in profile.horizontal), default=1.0) or 1.0
        updated = deepcopy(profile)
        for axis_name in ("horizontal", "vertical"):
            axis = getattr(updated, axis_name)
            for index, (old_x, new_x) in enumerate(zip(before_xs, after_xs, strict=False)):
                if index >= len(axis):
                    continue
                # Only move a profile point if this chart dot represents it. This leaves a
                # differently sampled vertical axis alone instead of shifting the wrong point.
                if abs(axis[index].speed / scale - old_x) <= 1e-5:
                    axis[index] = replace(axis[index], speed=max(0.0, new_x * scale))
            axis.sort(key=lambda point: point.speed)
        return updated

    @staticmethod
    def _edit_hint(structural: bool, count: int) -> str:
        """What to say after a chart edit, so the user knows what just happened to their curve."""
        if structural:
            return (
                f"Point count changed: the curve now has {count} points. "
                "Export curve to save the edited version."
            )
        return "Curve edited. Export curve to save the edited version."

    def _on_curve_adjusted(self, points, xs=None):
        points = [float(v) for v in points]
        xs = list(self.chart.x_positions() if xs is None else xs)
        before = list(self._chart_baseline or points)  # the curve as it was
        before_xs = list(self._chart_baseline_xs or [])
        structure = self._pending_chart_structure
        self._pending_chart_structure = None
        structural = structure is not None or len(before) != len(points) or len(before_xs) != len(xs)
        positions_changed = len(before_xs) == len(xs) and any(
            old_x != new_x for old_x, new_x in zip(before_xs, xs, strict=False)
        )
        baseline = before
        if structural and len(before_xs) == len(before) and before:
            # A point was added or deleted: compare at the chart's new x positions, so the change
            # reaches the profile as "this speed region changed", not as a shifted curve.
            baseline = [_lerp_factor(before_xs, before, x) for x in xs]
        # A drag that moved nothing is not a change. Structural edits and horizontal motion need
        # history too, even when the y values are unchanged.
        if not structural and not positions_changed and baseline == points:
            return  # Selecting a dot alone must not mutate the profile or emit a change.
        if structural or positions_changed or baseline != points:
            self._checkpoint(
                points=before,
                xs=before_xs or None,
                baseline=before,
                baseline_xs=before_xs or None,
            )
        if self._profile is None:
            # The chart's vertical coordinates are sensitivity gains, not output speeds.
            x_max = self.chart.axis_x_max or RECOMMENDATION_MAX_SPEED
            y_max = self.chart.axis_y_max or 1.0
            values = [CurvePoint(max(0.0, x * x_max), max(0.05, min(20.0, y * y_max)))
                      for x, y in zip(xs, points, strict=False)]
            if len(values) >= 2:
                self._profile = CurveProfile("Edited curve", values, deepcopy(values))
                self._chart_scale = x_max
        else:
            old_profile = self._profile
            self._profile = _apply_chart_edit(
                old_profile, baseline, points, xs=xs, scale=self._chart_scale
            )
            # Each dot represents a stored horizontal profile point: commit exactly
            # the dragged speed and gain rather than interpolating a factor at
            # the old x position (which is wrong for simultaneous x/y drags).
            x_max = self.chart.axis_x_max or self._chart_scale or 1.0
            y_max = self.chart.axis_y_max or 1.0
            direct = (not structural and len(old_profile.horizontal) == len(points) == len(before_xs)
                      and all(abs(p.speed / x_max - x) < 1e-5
                              for p, x in zip(old_profile.horizontal, before_xs, strict=True)))
            if direct:
                def edited_point(p, x, y, old_x):
                    speed = p.speed if x == old_x else max(0.0, x * x_max)
                    gain = y * y_max
                    # Do not clamp unrelated imported points just because a
                    # different point was moved; CC profiles can contain zero.
                    if abs(gain - p.multiplier) <= 1e-10:
                        gain = p.multiplier
                    else:
                        gain = max(0.05, min(20.0, gain))
                    return replace(p, speed=speed, multiplier=gain)

                self._profile.horizontal = [edited_point(p, x, y, old_x)
                    for p, x, y, old_x in zip(old_profile.horizontal, xs, points, before_xs, strict=True)]
                # Matching vertical control points follow the same relative gain
                # change, while independently positioned axes keep the existing
                # relative-region adjustment above.
                if (len(old_profile.vertical) == len(old_profile.horizontal)
                        and all(abs(a.speed - b.speed) < 1e-5
                                for a, b in zip(old_profile.horizontal, old_profile.vertical, strict=True))):
                    vertical = []
                    for hp, vp, new in zip(old_profile.horizontal, old_profile.vertical,
                                           self._profile.horizontal, strict=True):
                        if new.multiplier == hp.multiplier:
                            gain = vp.multiplier
                        else:
                            gain = (vp.multiplier * new.multiplier / hp.multiplier
                                    if hp.multiplier > 1e-9 else vp.multiplier + new.multiplier - hp.multiplier)
                            gain = max(0.05, min(20.0, gain))
                        vertical.append(replace(vp, speed=new.speed, multiplier=gain))
                    self._profile.vertical = vertical
            if structure is not None:
                self._profile = self._edit_profile_structure(
                    self._profile, old_profile, before_xs, before, xs, points, structure
                )
            elif positions_changed and not direct:
                self._profile = self._move_profile_speeds(
                    self._profile, before_xs, xs, self._chart_scale
                )
        # The profile applies gain limits and may alter point geometry. Reflect
        # the actual saved horizontal values, not an unclamped cursor position.
        if self._profile is not None and len(self._profile.horizontal) == len(points):
            x_max = self.chart.axis_x_max or self._chart_scale or 1.0
            y_max = self.chart.axis_y_max or 1.0
            actual_max = max((p.multiplier for p in self._profile.horizontal), default=0.0)
            if actual_max > y_max:
                if self.chart.previous_points:
                    self.chart.previous_points = [v * y_max / actual_max for v in self.chart.previous_points]
                y_max = actual_max
                self.chart.set_axis_scale(x_max, y_max, self.chart.axis_x_units)
            points = [p.multiplier / y_max for p in self._profile.horizontal]
            xs = [p.speed / x_max for p in self._profile.horizontal]
            self.chart.points = list(points)
            self.chart.xs = list(xs)
            self.chart.set_curve_weights([p.smoothness for p in self._profile.horizontal],
                                         self.chart.previous_weights)
            self.chart.update()
        self._chart_baseline = list(points)
        self._chart_baseline_xs = list(xs)
        self.hint.setText(self._edit_hint(structural, len(points)))
        self.curve_status.setText(f"Manually edited curve · {len(points)} control points.")
        self.chart.setToolTip("Manually edited curve; the generated model selection no longer applies.")
        self._refresh_point_editor()
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
        x_max, y_max, units = chart_axis_scale(profile)
        self._set_chart(ys, xs=xs, scale=x_max, gain_scale=y_max, units=units,
                        weights=[p.smoothness for p in profile.horizontal])
        self.curve_status.setText(f"Imported curve · {len(profile.horizontal)} control points.")
        self.chart.setToolTip("Imported curve; generated model point selection does not alter its points.")
        self.hint.setText(
            f"Imported {Path(path).name}. Run calibration to adjust it, or drag points to edit it."
        )
        self.profile_changed.emit({"imported": path})

    def set_adjusted(self, previous, current, note: str) -> None:
        """Show an AI-adjusted curve as before/after and make it the curve Export writes."""
        self._checkpoint()
        self._profile = deepcopy(current)
        prev_pts, cur_pts = normalized_overlay(previous, current)
        self._set_overlay(cur_pts, prev_pts, *chart_axis_scale(previous, current),
                          current_profile=current, previous_profile=previous)
        self.curve_status.setText(
            f"Adjusted curve preview · {len(current.horizontal)} control points."
        )
        self.chart.setToolTip("Adjusted curve; generated model point selection does not simplify it.")
        self.hint.setText(f"{note} Export curve to save it.")

    def _save(self):
        if self._profile is None:
            self.hint.setText("Run a calibration or import a curve first, then export it.")
            return
        cc4_style = self._graph_style == "cc4"
        default_name = "fps_coach_curve.cc4" if cc4_style else "fps_coach_curve.ccurve"
        filters = ("Custom Curve 4 (*.cc4);;Custom Curve 3 (*.ccurve)" if cc4_style else
                   "Custom Curve 3 (*.ccurve);;Custom Curve 4 (*.cc4)")
        path, selected_filter = QFileDialog.getSaveFileName(self, "Export curve", default_name, filters)
        if not path:
            return
        if not Path(path).suffix:
            chosen_cc4 = "*.cc4" in selected_filter if selected_filter else cc4_style
            path += ".cc4" if chosen_cc4 else ".ccurve"
        extension = Path(path).suffix.lower()
        weights_present = any(p.smoothness != 0.5 for p in self._profile.horizontal + self._profile.vertical)
        conversion_warning = None
        if extension == ".ccurve" and (self._profile.source_format == "cc4" or weights_present):
            conversion_warning = ("Custom Curve 3 files cannot store CC4 point weights or CC4-only settings. "
                                  "Export anyway? Your in-app curve will not be changed.")
        elif extension == ".cc4" and self._profile.source_format == "ccurve":
            conversion_warning = ("CC3-only settings and corner behavior may not transfer to CC4. "
                                  "Export anyway? Your in-app curve will not be changed.")
        if conversion_warning and QMessageBox.question(
            self, "Export compatibility", conversion_warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            self.hint.setText("Export cancelled; the in-app curve and weights are unchanged.")
            return
        try:
            destination = save_curve(self._profile, path)
        except (CurveFormatError, OSError) as exc:
            QMessageBox.warning(self, "CurveCoach", f"Could not export curve: {exc}")
            return
        notes = ["The Calibration preview is not verified Custom Curve driver output; review the curve in Custom Curve."]
        if self._profile.source_format == "generated":
            notes.append("Velocity values are normalized, not measured counts/ms; review scaling in Custom Curve.")
        if destination.suffix.lower() == ".ccurve" and (self._profile.source_format == "cc4" or weights_present):
            notes.append("CC4 weights and settings cannot be represented in CC3; review the curve in Custom Curve 3.")
        if destination.suffix.lower() == ".cc4":
            notes.append("The weighted preview is approximate and does not verify CC4 rendering; review it in Custom Curve 4.")
            if self._profile.source_format == "ccurve":
                notes.append("CC3-only settings and corner behavior may not transfer to CC4.")
        if destination.suffix.lower() == ".cc4" and max(len(self._profile.horizontal), len(self._profile.vertical)) > 64:
            notes.append("This profile has over 64 points on an axis and may exceed Custom Curve 4's editor limit.")
        self.hint.setText(f"Exported the current curve to {destination}. " + " ".join(notes))
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
        self._calibration_in_progress = False

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

        self.dynamic_curve_btn = PerformanceStartButton()
        self.dynamic_curve_btn.clicked.connect(self._toggle_dynamic_curve)
        summary_lay.addWidget(self.dynamic_curve_btn, 0, Qt.AlignmentFlag.AlignLeft)
        summary_lay.addStretch(1)
        self.generate_btn = QPushButton(" Generate recommendation")
        self.generate_btn.setObjectName("primary")
        set_glyph(self.generate_btn, "recommendation")
        summary_lay.addWidget(self.generate_btn, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(summary_card, 2)

        # Right: the suggested curve, read-only.
        chart_card, chart_lay = _card(
            "Suggested curve",
            "Sensitivity multiplier versus mouse input velocity.",
        )
        self.empty_label = _label("Run a calibration to draw the suggested curve.", "stageText", wrap=True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(260)
        chart_lay.addWidget(self.empty_label, 1)
        self.chart = CurveChart(editable=False)
        self.chart.setMinimumHeight(260)
        self.chart.setVisible(False)
        chart_lay.addWidget(self.chart, 1)
        self.curve_deck = FlipCard(lambda: bool(_settings().value("ui/animations", True, type=bool))
                                  and fx_enabled(str(_settings().value("ui/mode", "luxurious"))))
        self.curve_deck.addWidget(chart_card)
        self.curve_deck.currentChanged.connect(self._update_dynamic_accessibility)
        self._update_dynamic_accessibility(0)
        row.addWidget(self.curve_deck, 3)

        outer.addLayout(row, 1)
        self.generate_btn.clicked.connect(self._generate)

    def install_dynamic_curve(self, engine):
        """Install the shared engine as the back face, without enabling analysis."""
        face = QWidget()
        layout = QVBoxLayout(face)
        layout.setContentsMargins(0, 0, 0, 0)
        back = QPushButton("Back to Suggested curve")
        back.setObjectName("secondary")
        back.clicked.connect(lambda: self.curve_deck.flip_to(0))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.dynamic_curve_panel = DynamicCurvePanel(engine, square_chart=True, chart_side=210, title_text="Dynamic Performance Curve")
        layout.addWidget(self.dynamic_curve_panel)
        self.curve_deck.addWidget(face)
        self.dynamic_back_btn = back

    def _update_dynamic_accessibility(self, index):
        self.dynamic_curve_btn.setAccessibleDescription(
            "Dynamic curve is shown; activate to return to Suggested curve." if index == 1
            else "Suggested curve is shown; activate to open Dynamic Performance Curve."
        )

    def _toggle_dynamic_curve(self):
        current = (self.curve_deck._target if self.curve_deck.animation.state() != self.curve_deck.animation.State.Stopped
                   else self.curve_deck.currentIndex())
        target = 1 - current
        self.curve_deck.flip_to(target)

    def update_result(self, summary: dict, recommendation: dict, display_profile=None):
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
        window_count = summary.get("window_trial_count")
        window_label = f", based on the latest {int(window_count)}" if window_count is not None else ""
        self.summary.setText(
            f"{title}: sensitivity \u00d7{mult:.2f} "
            f"(confidence {confidence:.0%}, {trials} targets{window_label})."
        )
        self.reasons_label.setText("\n".join(f"\u2022  {r}" for r in reasons))

        self.chart.previous_points = None
        try:
            profile = display_profile if display_profile is not None else recommendation_profile(recommendation)
        except CurveFormatError:
            self.chart.points = []
            self.chart.xs = []
            self.chart.set_axis_scale(None, None, "unavailable")
            self.chart.setVisible(False)
            self.empty_label.setText("A valid curve is not available for this recommendation.")
            self.empty_label.setVisible(True)
        else:
            self.show_curve_profile(profile)
        self.recommendation_ready.emit(recommendation)

    def show_curve_profile(self, profile) -> None:
        """Display the actual resulting profile, including an adjusted imported base."""
        if profile is None:
            return
        pairs = chart_points(profile)
        self.chart.xs = [x for x, _ in pairs]
        self.chart.points = [y for _, y in pairs]
        self.chart.set_curve_weights([p.smoothness for p in profile.horizontal])
        self.chart.set_axis_scale(*chart_axis_scale(profile))
        self.chart.setVisible(True)
        self.empty_label.setVisible(False)
        self.chart.update()

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
        self.chart.set_axis_scale(None, None, "unavailable")
        self.chart.setVisible(False)
        self.empty_label.setText("Run a calibration to draw the suggested curve.")
        self.empty_label.setVisible(True)
        self.chart.update()

    def set_calibration_in_progress(self, in_progress: bool) -> None:
        self._calibration_in_progress = bool(in_progress)
        self.generate_btn.setEnabled(not in_progress)

    def _generate(self):
        if self._calibration_in_progress:
            self.summary.setText("Finish or cancel the current calibration first.")
            return
        summary = dict(self._backend.analyzer.summary())
        if int(summary.get("trial_count", 0)) == 0:
            self.summary.setText("No calibration data yet. Run the calibration task first.")
            return
        recommendation = dict(self._backend.analyzer.recommend())
        self.update_result(summary, recommendation)
        self.apply_requested.emit(recommendation)
