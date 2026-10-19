"""Practice and tools pages: Training (drill range and analytics) and Crosshair.

The training range draws its enemy figures from the bundled, transparent
sprites in assets/enemies. A hit plays a short kill effect: a flash, a
shrinking fade, and a few shards. It uses only a handful of primitives, so it
stays light. Performance mode turns it off.
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fps_coach import scenarios as scenario_defs
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.crosshair import PRESETS as CROSSHAIR_PRESETS
from fps_coach.crosshair import CrosshairConfig, preset_names

from .styles import STAGE
from .widgets import (
    CrosshairPreview,
    ValueSlider,
    _draw_crosshair,
    Segmented,
    _card,
    _content_page,
    _label,
    _page_header,
    _page_root,
    _settings,
    set_glyph,
)


class TrainingCanvas(QWidget):
    """Runs a target-clicking scenario and records accuracy + reaction times."""

    finished = Signal(str, list, int, int)  # scenario_key, reaction_times_ms, hits, misses
    progress = Signal(int, int)

    KILL_MS = 460  # kill effect length: well under half a second
    SHARDS = 8

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
        self.setMinimumHeight(380)
        self.setMouseTracking(True)
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(16)
        self._move_timer.timeout.connect(self._advance)

        # Enemy figures (bundled transparent sprites) and their hit-flash tints.
        self._sprites = self._load_sprites()
        self._target_pix: QPixmap | None = None
        self._tints: dict[int, QPixmap] = {}
        # Kill effects.
        self._anim_enabled = True
        self._kills: list[dict] = []
        self._kill_timer = QTimer(self)
        self._kill_timer.setInterval(16)
        self._kill_timer.timeout.connect(self._kill_tick)

    @staticmethod
    def _load_sprites() -> list[QPixmap]:
        """Targets are drawn in code (ui/figures.py), transparent around the figure."""
        from .figures import target_pixmaps

        return [pix for pix in target_pixmaps() if not pix.isNull()]

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

    def _finish(self) -> None:
        self._active = False
        self._visible_target = False
        self._move_timer.stop()
        key = self._scenario.key if self._scenario else ""
        self.finished.emit(key, list(self._reaction_times), self._hits, self._misses)
        self.update()

    def _spawn_kill(self, cx: float, cy: float, radius: float, pix: QPixmap | None) -> None:
        if not self._anim_enabled:
            return
        shards = []
        for i in range(self.SHARDS):
            angle = (i / self.SHARDS) * 2 * math.pi + self._rng.uniform(-0.3, 0.3)
            speed = self._rng.uniform(1.6, 2.8)
            shards.append((math.cos(angle) * speed, math.sin(angle) * speed - 0.8))
        self._kills.append({
            "cx": cx, "cy": cy, "radius": radius, "pix": pix,
            "t0": time.perf_counter(), "shards": shards,
        })
        if not self._kill_timer.isActive():
            self._kill_timer.start()

    def _tinted(self, pix: QPixmap) -> QPixmap:
        """The sprite filled with the hit colour, keeping its alpha. Cached per sprite."""
        key = pix.cacheKey()
        cached = self._tints.get(key)
        if cached is not None:
            return cached
        out = QPixmap(pix.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.drawPixmap(0, 0, pix)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(out.rect(), QColor(STAGE["stage_hit"]))
        painter.end()
        self._tints[key] = out
        return out

    def _figure_rect(self, cx: float, cy: float, radius: float, pix: QPixmap | None, scale: float = 1.0) -> QRectF:
        height = 2.4 * radius * scale
        ratio = (pix.width() / max(1, pix.height())) if pix is not None and not pix.isNull() else 0.6
        width = height * ratio
        return QRectF(cx - width / 2, cy - height / 2, width, height)

    def _draw_enemy(self, painter: QPainter, cx: float, cy: float, radius: float,
                    pix: QPixmap | None, scale: float = 1.0) -> QRectF:
        rect = self._figure_rect(cx, cy, radius, pix, scale)
        if pix is not None and not pix.isNull():
            painter.drawPixmap(rect, pix, QRectF(0, 0, pix.width(), pix.height()))
            return rect
        # Procedural fallback figure (head and torso) if no sprite file is bundled.
        accent = QColor(STAGE["stage_accent"])
        painter.setPen(QPen(QColor(STAGE["stage_bg"]), 2))
        painter.setBrush(accent)
        r = radius * scale
        painter.drawEllipse(QPointF(cx, cy - r * 0.55), r * 0.38, r * 0.38)
        painter.drawRoundedRect(QRectF(cx - r * 0.5, cy - r * 0.1, r, r), r * 0.2, r * 0.2)
        return rect

    def _draw_brackets(self, painter: QPainter, rect: QRectF) -> None:
        """Four corner ticks marking the target. Cheaper and clearer than a ring."""
        color = QColor(STAGE["stage_accent"])
        color.setAlpha(210)
        painter.setPen(QPen(color, 2))
        pad = 6.0
        arm = max(6.0, rect.width() * 0.22)
        left, top = rect.left() - pad, rect.top() - pad
        right, bottom = rect.right() + pad, rect.bottom() + pad
        for x, y, dx, dy in (
            (left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1),
        ):
            painter.drawLine(QPointF(x, y), QPointF(x + dx * arm, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + dy * arm))

    def _kill_tick(self) -> None:
        now = time.perf_counter()
        self._kills = [k for k in self._kills if (now - k["t0"]) * 1000.0 < self.KILL_MS]
        if not self._kills:
            self._kill_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(STAGE["stage_bg"]))
        painter.setPen(QPen(QColor(STAGE["stage_line"]), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # Kill effects: a hit flash, the figure fading and growing slightly, and shards.
        now = time.perf_counter()
        for kill in self._kills:
            t = min(1.0, (now - kill["t0"]) * 1000.0 / self.KILL_MS)
            cx, cy, radius, pix = kill["cx"], kill["cy"], kill["radius"], kill["pix"]
            if pix is not None and not pix.isNull() and t < 0.22:
                painter.setOpacity(0.9 * (1.0 - t / 0.22))
                rect = self._figure_rect(cx, cy, radius, pix)
                painter.drawPixmap(rect, self._tinted(pix), QRectF(0, 0, pix.width(), pix.height()))
            fade = max(0.0, 1.0 - max(0.0, t - 0.2) / 0.8)
            if fade > 0.0:
                painter.setOpacity(fade)
                self._draw_enemy(painter, cx, cy, radius, pix, 1.0 + 0.08 * t)
            painter.setOpacity(max(0.0, 1.0 - t))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(STAGE["stage_hit"]))
            for vx, vy in kill["shards"]:
                px = cx + vx * radius * t
                py = cy + vy * radius * t + radius * 1.6 * t * t
                painter.drawRect(QRectF(px - 1.5, py - 1.5, 3.0, 3.0))
        painter.setOpacity(1.0)

        if self._active and self._visible_target and self._target_pix is not None:
            rect = self._draw_enemy(painter, self._target.x(), self._target.y(), self._radius, self._target_pix)
            self._draw_brackets(painter, rect)
        elif self._active and self._visible_target:
            rect = self._draw_enemy(painter, self._target.x(), self._target.y(), self._radius, None)
            self._draw_brackets(painter, rect)

        # Heads-up readout in the corner.
        if self._scenario is not None and (self._active or self._hits or self._misses):
            total = self._scenario.target_count
            attempts = self._hits + self._misses
            acc = (self._hits / attempts * 100.0) if attempts else 0.0
            painter.setPen(QColor(STAGE["stage_text"]))
            font = painter.font()
            font.setFamily("Cascadia Mono")
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(
                QRectF(16, 12, self.width() - 32, 20),
                Qt.AlignmentFlag.AlignLeft,
                f"HITS {self._hits}/{total}    MISS {self._misses}    ACC {acc:.0f}%",
            )

        if not self._active and not self._kills:
            painter.setPen(QColor(STAGE["stage_text"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Choose a scenario and press Start")
        elif self._active and not self._visible_target:
            painter.setPen(QColor(STAGE["stage_text"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Get ready\u2026")
        painter.end()


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

    Results are saved to the shared AnalyticsStore. The analytics view is one
    segment of the same page.
    """

    session_recorded = Signal(object)  # SessionResult

    def __init__(self, store: AnalyticsStore, parent=None):
        super().__init__(parent)
        self._store = store

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Training",
            "Scenario drills to validate your curve. No game is read.",
            eyebrow="Practice",
        ))

        self.view_switch = Segmented(["Drill", "Analytics"])
        self.view_switch.changed.connect(self._on_view_changed)
        outer.addWidget(self.view_switch, 0)

        self.stack = QWidget()
        stack_lay = QVBoxLayout(self.stack)
        stack_lay.setContentsMargins(0, 0, 0, 0)
        stack_lay.setSpacing(0)

        # Drill view.
        train_widget = QWidget()
        self.drill_view = train_widget
        drill = QVBoxLayout(train_widget)
        drill.setContentsMargins(0, 0, 0, 0)
        drill.setSpacing(14)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(12)
        self.scenario = QComboBox()
        for sc in scenario_defs.list_scenarios():
            self.scenario.addItem(sc.name, sc.key)
        self.scenario.currentIndexChanged.connect(self._describe)
        self.scenario.setMinimumWidth(220)
        pick_row.addWidget(self.scenario, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m targets")
        pick_row.addWidget(self.progress, 2)
        drill.addLayout(pick_row)

        self.desc = _label("", "cardSubtitle", wrap=True)
        drill.addWidget(self.desc)

        self.canvas = TrainingCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.progress.connect(self._on_progress)
        self.canvas.finished.connect(self._on_finished)
        drill.addWidget(self.canvas, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton(" Start drill")
        self.start_btn.setObjectName("primary")
        set_glyph(self.start_btn, "play")
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("secondary")
        set_glyph(self.stop_btn, "stop")
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        drill.addLayout(btn_row)

        self.result = _label("", "cardSubtitle", wrap=True)
        drill.addWidget(self.result)
        stack_lay.addWidget(train_widget, 1)

        # Analytics view.
        self.analytics_page = AnalyticsPage(store)
        self.analytics_page.setVisible(False)
        stack_lay.addWidget(self.analytics_page, 1)

        outer.addWidget(self.stack, 1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self._describe()

    def _on_view_changed(self, index: int) -> None:
        if index == 1:
            self._show_analytics()
        else:
            self._show_training()

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
        self.drill_view.setVisible(False)
        self.analytics_page.setVisible(True)

    def _show_training(self) -> None:
        self.analytics_page.setVisible(False)
        self.drill_view.setVisible(True)

class CrosshairPage(QWidget):
    """Crosshair designer: presets, live preview, JSON import/export and PNG export.

    Stores the original CrosshairConfig schema under "crosshair/config". The
    crosshair is shown in this app and written to files. Activation (off by
    default, stored under "crosshair/overlay_active") also draws it on the primary
    screen as a click-through overlay. The overlay does not read or attach to any game.
    """

    edited = Signal(dict)
    activation_changed = Signal(bool)
    config_changed = Signal(object)
    SHAPES = ("cross_dot", "cross", "dot", "circle")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._model = self._load_model()
        self._loading = False

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Crosshair",
            "Design a crosshair, then export it as a PNG or JSON for your own use.",
            eyebrow="Tools",
        ))

        body = QHBoxLayout()
        body.setSpacing(20)

        preview_card, preview_lay = _card("Preview", "Drawn on a fixed dark stage so contrast is accurate.")
        self.preview = CrosshairPreview()
        self.preview.setMinimumSize(360, 360)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_lay.addWidget(self.preview, 1)
        body.addWidget(preview_card, 3)

        controls_card, controls_lay = _card("Parameters")

        # Activation: one clear switch for the on-screen overlay. Off by default.
        self.activation = QPushButton()
        self.activation.setObjectName("activation")
        self.activation.setCheckable(True)
        self.activation.setCursor(Qt.CursorShape.PointingHandCursor)
        self.activation.setChecked(self.s.value("crosshair/overlay_active", False, type=bool))
        self.activation.toggled.connect(self._on_activation_toggled)
        self.activation_note = _label("", "cardSubtitle", wrap=True)
        controls_lay.addWidget(self.activation)
        controls_lay.addWidget(self.activation_note)
        self._update_activation_text(self.activation.isChecked())

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Preset", "formLabel"), 0, 0)
        self.preset = QComboBox()
        self.preset.addItem("Choose a preset\u2026", "")
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
        self.opacity = self._slider(10, 100, suffix="%")
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
        btn_row.setSpacing(8)
        export_btn = QPushButton(" JSON")
        export_btn.setObjectName("secondary")
        set_glyph(export_btn, "download")
        export_btn.clicked.connect(self._export_json_dialog)
        import_btn = QPushButton(" Import")
        import_btn.setObjectName("secondary")
        set_glyph(import_btn, "upload")
        import_btn.clicked.connect(self._import_dialog)
        png_btn = QPushButton(" PNG")
        png_btn.setObjectName("primary")
        set_glyph(png_btn, "download")
        png_btn.clicked.connect(self._export_png_dialog)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(import_btn)
        btn_row.addWidget(png_btn)
        btn_row.addStretch(1)
        controls_lay.addLayout(btn_row)

        self.status = _label("", "cardSubtitle", wrap=True)
        controls_lay.addWidget(self.status)
        controls_lay.addStretch(1)

        controls_card.setMinimumWidth(340)
        controls_card.setMaximumWidth(420)
        body.addWidget(controls_card, 2)
        outer.addLayout(body, 1)

        self._sync_controls()
        self._refresh()

    @staticmethod
    def _slider(lo: int, hi: int, suffix: str = " px") -> ValueSlider:
        return ValueSlider(lo, hi, fmt=lambda v: f"{v}{suffix}")

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

    def _update_activation_text(self, active: bool) -> None:
        self.activation.setText("Crosshair overlay: On" if active else "Crosshair overlay: Off")
        self.activation_note.setText(
            "Drawn on your primary screen, click-through, above other windows."
            if active else
            "Off. The crosshair is shown here and exported only; nothing is drawn on screen."
        )

    def _on_activation_toggled(self, checked: bool) -> None:
        self.s.setValue("crosshair/overlay_active", bool(checked))
        self._update_activation_text(bool(checked))
        self.activation_changed.emit(bool(checked))

    def _refresh(self):
        self.preview.set_config(self._model)
        self.config_changed.emit(self._model)
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
