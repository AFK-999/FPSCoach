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
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach import scenarios as scenario_defs
from fps_coach.analytics import AnalyticsStore, SessionResult
from fps_coach.crosshair import PRESETS as CROSSHAIR_PRESETS
from fps_coach.crosshair import CrosshairConfig, preset_names
from fps_coach.performance_telemetry import PerformanceTelemetry

from .magnification import ALLOWED_KEYS, BINDINGS, windows_available
from .preset_tile import PresetTile
from .styles import STAGE
from .widgets import (
    ComboField,
    CrosshairPreview,
    Segmented,
    ValueSlider,
    _card,
    _content_page,
    _draw_crosshair,
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
    performance_started = Signal()
    performance_updated = Signal(dict)
    aim_monitor_updated = Signal(dict)
    performance_ended = Signal(bool)

    KILL_MS = 460  # kill effect length: well under half a second
    SHARDS = 8

    def __init__(self) -> None:
        super().__init__()
        self._rng = random.Random()
        self._performance = PerformanceTelemetry("training")
        self._aim_monitor_enabled = False
        self._completed_for_summary = False
        self._aim_started_partial = False
        self._aim_last_emit = 0.0
        self._performance_enabled = False
        self._scenario = None
        self._active = False
        self._visible_target = False
        self._target = QPointF(0, 0)
        self._radius = 20
        self._vx = self._vy = 0.0
        self._spawn_time = 0.0
        self._target_generation = 0
        self._pending_reveal_generation = 0
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._reveal_delayed_target)
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

    def set_performance_enabled(self, enabled: bool) -> None:
        was_collecting = self._performance_enabled or self._aim_monitor_enabled
        self._performance_enabled = bool(enabled)
        self._reset_performance_collection(was_collecting)

    def set_aim_monitor_enabled(self, enabled: bool) -> None:
        # Consent changes start a new observation window, even if the separate
        # dynamic-curve feature was already collecting ephemeral telemetry.
        enabled = bool(enabled)
        if enabled == self._aim_monitor_enabled:
            return
        self._aim_monitor_enabled = enabled
        self._aim_started_partial = bool(enabled and self._active)
        self._performance.reset()
        if (self._performance_enabled or enabled) and self._active and self._visible_target:
            self._begin_performance_target(time.perf_counter())

    def _reset_performance_collection(self, was_collecting: bool) -> None:
        collecting = self._performance_enabled or self._aim_monitor_enabled
        if was_collecting == collecting:
            return
        self._performance.reset()
        if collecting and self._active and self._visible_target:
            self._begin_performance_target(time.perf_counter())

    def _collecting_performance(self) -> bool:
        return self._performance_enabled or self._aim_monitor_enabled

    def _aim_summary(self) -> dict:
        summary = self._performance.session_summary()
        summary["target_radius_px"] = self._radius
        summary["moving_target"] = bool(self._scenario and self._scenario.moving)
        summary["target_speed_px_s"] = self._scenario.speed if self._scenario else 0
        summary["observation_partial"] = self._aim_started_partial
        return summary

    def _emit_aim_monitor(self, timestamp: float, *, force: bool = False) -> None:
        if self._aim_monitor_enabled and (force or timestamp - self._aim_last_emit >= .25):
            self._aim_last_emit = timestamp
            self.aim_monitor_updated.emit(self._aim_summary())

    def _begin_performance_target(self, timestamp: float) -> None:
        local = self.mapFromGlobal(QCursor.pos())
        if self.isVisible() and self.rect().contains(local):
            self._performance.begin_target(timestamp, local.x(), local.y())
        else:
            self._performance.begin_target(timestamp)

    def set_animations(self, enabled: bool) -> None:
        self._anim_enabled = bool(enabled)
        if not enabled:
            self._kills.clear()
            self._kill_timer.stop()
            self.update()

    def start(self, scenario) -> None:
        self._scenario = scenario
        self._completed_for_summary = False
        self._aim_started_partial = False
        self._radius = scenario.target_radius
        self._active = True
        self._hits = 0
        self._misses = 0
        self._reaction_times = []
        self._performance.reset()
        self.performance_started.emit()
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
        self._reveal_timer.stop()
        if self._hits + self._misses > 0:
            key = self._scenario.key if self._scenario else ""
            self.finished.emit(key, list(self._reaction_times), self._hits, self._misses)
        self._performance.end_target()
        self.performance_ended.emit(False)
        self.update()

    def _next_target(self) -> None:
        if not self._scenario:
            return
        self._target_generation += 1
        generation = self._target_generation
        self._reveal_timer.stop()
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
            self._pending_reveal_generation = generation
            self._reveal_timer.start(int(self._rng.uniform(300, delay)))
        else:
            self._reveal_target(generation)

    def _reveal_delayed_target(self) -> None:
        self._reveal_target(self._pending_reveal_generation)

    def _reveal_target(self, generation: int | None = None) -> None:
        if not self._active or (generation is not None and generation != self._target_generation):
            return
        self._visible_target = True
        self._spawn_time = time.perf_counter()
        if self._collecting_performance():
            self._begin_performance_target(self._spawn_time)
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

    def _inside(self, point: QPointF) -> bool:
        return 0 <= point.x() < self.width() and 0 <= point.y() < self.height()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._collecting_performance():
            self._performance.break_path()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._collecting_performance() and self._active and self._visible_target:
            if self._inside(event.position()):
                now = time.perf_counter()
                self._performance.move(event.position().x(), event.position().y(), now)
                if self._scenario and self._scenario.moving:
                    self._performance.observe_tracking(event.position().x(), event.position().y(),
                                                       self._target.x(), self._target.y(), self._radius, now)
                    self._emit_aim_monitor(now)
            else:
                self._performance.break_path()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if not self._visible_target:
            return
        if not self._inside(event.position()):
            if self._collecting_performance():
                self._performance.break_path()
            return
        dist = math.hypot(event.position().x() - self._target.x(), event.position().y() - self._target.y())
        now = time.perf_counter()
        if self._collecting_performance():
            snapshot = self._performance.click(event.position().x(), event.position().y(),
                                               self._target.x(), self._target.y(), self._radius, now)
            if snapshot is not None:
                self._emit_aim_monitor(now, force=True)
                snapshot["input_path"] = "desktop"
                snapshot["target_radius_px"] = self._radius
                snapshot["scenario"] = self._scenario.key if self._scenario else ""
                snapshot["moving_target"] = bool(self._scenario and self._scenario.moving)
                self.performance_updated.emit(snapshot)
        if dist <= self._radius:
            self._hits += 1
            self._reaction_times.append((now - self._spawn_time) * 1000.0)
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
        self._reveal_timer.stop()
        self._completed_for_summary = True
        key = self._scenario.key if self._scenario else ""
        self.finished.emit(key, list(self._reaction_times), self._hits, self._misses)
        self._performance.end_target()
        self.performance_ended.emit(True)
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
        self.scenario = ComboField()
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

        # Side panel: target speed and difficulty. It sits beside the drill and the
        # analytics, so it stays in view in both. It is outside the drill canvas.
        panel, panel_lay = _card("Target settings", "Applies to the next drill you start.")
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(320)
        panel.setVisible(False)  # collapsed: only the gear icon shows until it is opened
        settings = _settings()
        panel_lay.addWidget(_label("Target speed", "formLabel"))
        self.speed_slider = ValueSlider(
            scenario_defs.SPEED_MIN_PCT,
            scenario_defs.SPEED_MAX_PCT,
            fmt=lambda v: f"{v / 100:.2f}x",
        )
        self.speed_slider.setValue(int(settings.value(
            "training/speed_pct", scenario_defs.SPEED_DEFAULT_PCT, type=int)))
        panel_lay.addWidget(self.speed_slider)
        panel_lay.addWidget(_label("Difficulty", "formLabel"))
        self.difficulty_slider = ValueSlider(
            scenario_defs.DIFFICULTY_MIN,
            scenario_defs.DIFFICULTY_MAX,
            fmt=lambda v: f"Level {v}",
        )
        self.difficulty_slider.setValue(int(settings.value(
            "training/difficulty", scenario_defs.DIFFICULTY_DEFAULT, type=int)))
        panel_lay.addWidget(self.difficulty_slider)
        self.tuning_note = _label("", "cardSubtitle", wrap=True)
        panel_lay.addWidget(self.tuning_note)
        panel_lay.addStretch(1)
        panel_lay.addWidget(_label("Locked while a drill is running.", "cardSubtitle", wrap=True))
        self.settings_panel = panel

        # A gear icon opens and closes the panel. The gear shows the current values in its tooltip.
        self.settings_toggle = QToolButton()
        self.settings_toggle.setObjectName("iconButton")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(False)
        set_glyph(self.settings_toggle, "settings")
        self.settings_toggle.setToolTip("Target settings")
        self.settings_toggle.setAccessibleName("Target settings")
        self.settings_toggle.toggled.connect(self.settings_panel.setVisible)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(10)
        side_lay.addWidget(self.settings_toggle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._dynamic_curve_monitor_layout = side_lay
        side_lay.addWidget(panel, 0, Qt.AlignmentFlag.AlignTop)
        side_lay.addStretch(1)

        # Left: the drill or the analytics. Right: the gear, and the panel when opened.
        body = QHBoxLayout()
        body.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self.view_switch, 0)
        left.addWidget(self.stack, 1)
        body.addLayout(left, 1)
        body.addWidget(side, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(body, 1)
        self._dynamic_curve_layout = outer

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.speed_slider.valueChanged.connect(self._on_tuning_changed)
        self.difficulty_slider.valueChanged.connect(self._on_tuning_changed)
        self._describe()

    def _on_view_changed(self, index: int) -> None:
        if index == 1:
            self._show_analytics()
        else:
            self._show_training()

    def _drill_scenario(self):
        """The selected drill with the speed and difficulty settings applied."""
        return scenario_defs.tuned(
            scenario_defs.get(self.scenario.currentData()),
            self.speed_slider.value(),
            self.difficulty_slider.value(),
        )

    def _on_tuning_changed(self, *_args) -> None:
        settings = _settings()
        settings.setValue("training/speed_pct", int(self.speed_slider.value()))
        settings.setValue("training/difficulty", int(self.difficulty_slider.value()))
        self._describe()

    def _describe(self, *_args) -> None:
        base = scenario_defs.get(self.scenario.currentData())
        sc = self._drill_scenario()
        self.desc.setText(base.description)
        parts = [f"Target radius {sc.target_radius} px"]
        if sc.moving:
            parts.append(f"moving at {sc.speed:.0f} px/s")
        if sc.spawn_delay_ms:
            parts.append(f"appears after up to {sc.spawn_delay_ms} ms")
        if not (sc.moving or sc.spawn_delay_ms):
            parts.append("speed setting applies to moving and reaction drills")
        self.tuning_note.setText(" · ".join(parts))
        if hasattr(self, "settings_toggle"):
            self.settings_toggle.setToolTip(
                f"Target settings: speed {self.speed_slider.value() / 100:.2f}x, "
                f"Level {self.difficulty_slider.value()}"
            )

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.scenario.setEnabled(not running)
        self.speed_slider.setEnabled(not running)
        self.difficulty_slider.setEnabled(not running)

    def _start(self) -> None:
        sc = self._drill_scenario()
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
        if self.canvas._aim_monitor_enabled and self.canvas._completed_for_summary:
            summary = self.canvas._aim_summary()
            if summary["attempts"]:
                result.aim_telemetry = summary
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

class _MagWrapLabel(QLabel):
    """Keep wrapped magnifier notices tall enough at narrow page widths."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setObjectName("cardSubtitle")
        self.setWordWrap(True)

    def _fit_wrapped_height(self) -> None:
        if self.width() > 0:
            height = self.heightForWidth(self.width())
            if height > 0 and height != self.minimumHeight():
                self.setMinimumHeight(height)
                self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_wrapped_height()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._fit_wrapped_height()


class CrosshairPage(QWidget):
    """Crosshair designer: presets, live preview, JSON import/export and PNG export.

    Stores the original CrosshairConfig schema under "crosshair/config". The
    crosshair is shown in this app and written to files. Activation (off by
    default, stored under "crosshair/overlay_active") also draws it on the primary
    screen as a click-through overlay. Optional magnification reads visible desktop
    pixels but does not attach to a game process.
    """

    edited = Signal(dict)
    activation_changed = Signal(bool)
    config_changed = Signal(object)
    edit_mode_changed = Signal(bool)  # True = edit (drag), False = locked
    position_reset = Signal()
    magnification_toggled = Signal(bool)
    magnification_options_changed = Signal(int, float)
    magnification_binding_changed = Signal()
    SHAPES = ("cross_dot", "cross", "dot", "circle")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._model = self._load_model()
        self._loading = False

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        self._crosshair_header = _page_header(
            "Crosshair",
            "Design a crosshair, then export it as a PNG or JSON for your own use.",
            eyebrow="Tools",
        )
        outer.addWidget(self._crosshair_header)

        body = QHBoxLayout()
        body.setSpacing(20)
        self._crosshair_body = body

        preview_card, preview_lay = _card("Preview", "Drawn on a fixed dark stage so contrast is accurate.")
        self._crosshair_preview_card = preview_card
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

        # Position: Edit (drag the overlay) then Lock. Always starts locked.
        self.edit_position = QPushButton("Edit position\u2026")
        self.edit_position.setObjectName("secondary")
        self.edit_position.setCheckable(True)
        self.edit_position.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_position.toggled.connect(self._on_edit_toggled)
        self.centre_position = QPushButton("Reset to centre")
        self.centre_position.setObjectName("secondary")
        self.centre_position.setCursor(Qt.CursorShape.PointingHandCursor)
        self.centre_position.clicked.connect(self._on_reset_position)
        position_row = QHBoxLayout()
        position_row.setSpacing(8)
        position_row.addWidget(self.edit_position)
        position_row.addWidget(self.centre_position)
        position_row.addStretch(1)
        controls_lay.addLayout(position_row)
        self.position_note = _label("", "cardSubtitle", wrap=True)
        controls_lay.addWidget(self.position_note)
        self._update_position_text(False)

        # Match Coach's compact icon-over-label preset tiles. This button
        # discloses settings only; it never starts capture or key monitoring.
        self.mag_disclosure = PresetTile("scope", "Dynamic\nMagnification")
        self.mag_disclosure.setAccessibleName("Dynamic Magnification settings")
        self.mag_disclosure.setAccessibleDescription("Collapsed; activate to expand settings")
        self.mag_disclosure.setToolTip("Open Dynamic Magnification settings")
        self.mag_disclosure.setCheckable(True)
        controls_lay.addWidget(self.mag_disclosure, 0, Qt.AlignmentFlag.AlignLeft)
        self.mag_panel = QWidget()
        self.mag_panel.setAccessibleName("Dynamic Magnification controls")
        mag_lay = QVBoxLayout(self.mag_panel)
        mag_lay.setContentsMargins(0, 0, 0, 0)
        mag_lay.setSpacing(8)
        self.mag_panel.setVisible(False)
        self.mag_disclosure.toggled.connect(self._set_magnification_panel_expanded)
        controls_lay.addWidget(self.mag_panel)
        self.magnification_toggle = QPushButton("Dynamic magnification: Off")
        self.magnification_toggle.setObjectName("secondary")
        self.magnification_toggle.setCheckable(True)
        self.magnification_toggle.setAccessibleName("Toggle dynamic magnification")
        self.magnification_toggle.toggled.connect(self._on_magnification_toggled)
        mag_lay.addWidget(self.magnification_toggle)
        mag_form = QGridLayout()
        mag_form.setHorizontalSpacing(12)
        mag_form.setColumnMinimumWidth(0, 112)
        mag_form.setColumnStretch(1, 1)
        self.mag_diameter = ValueSlider(80, 600, fmt=lambda value: f"{value} px")
        self.mag_zoom = ValueSlider(100, 500, fmt=lambda value: f"{value / 100:.2f}×")
        try:
            diameter = int(self.s.value("crosshair/magnification_diameter", 240))
            zoom = int(self.s.value("crosshair/magnification_zoom_pct", 200))
            binding = int(self.s.value("crosshair/magnification_binding", 0))
        except (ValueError, TypeError):
            diameter, zoom, binding = 240, 200, 0
        self.mag_diameter.setValue(diameter)
        self.mag_zoom.setValue(zoom)
        self.mag_diameter.setAccessibleName("Magnification circle diameter in logical pixels")
        self.mag_zoom.setAccessibleName("Magnification zoom level")
        mag_form.addWidget(_label("Circle diameter", "formLabel"), 0, 0)
        mag_form.addWidget(self.mag_diameter, 0, 1)
        mag_form.addWidget(_label("Zoom", "formLabel"), 1, 0)
        mag_form.addWidget(self.mag_zoom, 1, 1)
        self.mag_binding = ComboField()
        self.mag_binding.setAccessibleName("Magnification toggle key or mouse button")
        for title, vk in BINDINGS:
            self.mag_binding.addItem(title, vk)
        self.mag_binding.setCurrentIndex(self.mag_binding.findData(binding if binding in ALLOWED_KEYS else 0))
        mag_form.addWidget(_label("Toggle key", "formLabel"), 2, 0)
        mag_form.addWidget(self.mag_binding, 2, 1)
        mag_lay.addLayout(mag_form)
        self.mag_hotkey_arm = QCheckBox("Enable toggle key for this session")
        self.mag_hotkey_arm.setChecked(False)
        self.mag_hotkey_arm.setToolTip("Monitors only the selected key while the overlay is on, even when CurveCoach is not focused. The input is not consumed.")
        mag_lay.addWidget(self.mag_hotkey_arm)
        self.mag_note = _MagWrapLabel("")
        mag_lay.addWidget(self.mag_note)
        self.mag_warning = _MagWrapLabel(
            "The lens targets 60+ updates per second; actual speed and key response depend on capture, "
            "rendering and system load. Live capture is local and may include game pixels. Background "
            "key polling is opt-in; keys still reach other apps and may conflict or miss fast taps. Mouse buttons are physical. "
            "Protected/fullscreen content may fail; recordings may omit the overlay. If you see recursive "
            "feedback, turn it off. Check the game and anti-cheat rules before use.")
        mag_lay.addWidget(self.mag_warning)
        self.mag_diameter.valueChanged.connect(self._on_mag_options_changed)
        self.mag_zoom.valueChanged.connect(self._on_mag_options_changed)
        self.mag_binding.currentIndexChanged.connect(self._on_mag_binding_changed)
        self.mag_hotkey_arm.toggled.connect(lambda _checked: self.magnification_binding_changed.emit())
        self._update_mag_availability()

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.setColumnMinimumWidth(0, 112)
        form.setColumnStretch(1, 1)

        form.addWidget(_label("Preset", "formLabel"), 0, 0)
        self.preset = ComboField()
        self.preset.addItem("Choose a preset\u2026", "")
        for name in preset_names():
            self.preset.addItem(name.replace("_", " ").title(), name)
        self.preset.currentIndexChanged.connect(self._apply_preset)
        form.addWidget(self.preset, 0, 1)

        form.addWidget(_label("Shape", "formLabel"), 1, 0)
        self.shape = ComboField()
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
            "Off. The crosshair is shown here and exported. It appears on screen only while you edit its position."
        )

    def _on_activation_toggled(self, checked: bool) -> None:
        self.s.setValue("crosshair/overlay_active", bool(checked))
        self._update_activation_text(bool(checked))
        if not checked and self.magnification_toggle.isChecked():
            self.magnification_toggle.setChecked(False)
        self._update_mag_availability()
        self.activation_changed.emit(bool(checked))
        self.magnification_binding_changed.emit()

    def _update_position_text(self, editing: bool) -> None:
        self.edit_position.setText("Lock position" if editing else "Edit position\u2026")
        self.position_note.setText(
            "Drag the crosshair to where you want it, then click Lock position. "
            "While editing, the overlay takes the mouse."
            if editing else
            "Locked. The overlay ignores the mouse, so clicks go to the game. "
            "The position is saved."
        )

    def _on_edit_toggled(self, checked: bool) -> None:
        self._update_position_text(bool(checked))
        if checked and self.magnification_toggle.isChecked():
            self.magnification_toggle.setChecked(False)
        self._update_mag_availability()
        self.edit_mode_changed.emit(bool(checked))
        self.magnification_binding_changed.emit()

    def _set_magnification_panel_expanded(self, expanded: bool) -> None:
        self.mag_panel.setVisible(expanded)
        self.mag_disclosure.setToolTip(
            "Close Dynamic Magnification settings" if expanded else "Open Dynamic Magnification settings")
        self.mag_disclosure.setAccessibleDescription(
            "Expanded; activate to collapse settings" if expanded else "Collapsed; activate to expand settings")

    def _update_mag_availability(self) -> None:
        supported = windows_available()
        ready = supported and self.activation.isChecked() and not self.edit_position.isChecked()
        self.magnification_toggle.setEnabled(ready)
        self.mag_binding.setEnabled(supported)
        self.mag_hotkey_arm.setEnabled(ready and bool(self.mag_binding.currentData()))
        if not supported:
            note = "Requires Windows 10 version 2004 or newer; unavailable on this system."
        elif self.edit_position.isChecked():
            note = "Lock the crosshair position to use magnification."
        elif not self.activation.isChecked():
            note = "Turn on the crosshair overlay first. Magnification and key monitoring start off each launch."
        elif self.magnification_toggle.isChecked():
            note = "Live primary-screen pixels are enlarged locally. Some games or capture paths may be black or stale."
        else:
            note = "Off. Enable only where permitted. An armed key is monitored in the background and still reaches other apps."
        self.mag_note.setText(note)

    def _on_magnification_toggled(self, enabled: bool) -> None:
        self.magnification_toggle.setText("Dynamic magnification: On" if enabled else "Dynamic magnification: Off")
        self._update_mag_availability()
        self.magnification_toggled.emit(enabled)

    def _on_mag_options_changed(self, _value: int) -> None:
        self.s.setValue("crosshair/magnification_diameter", self.mag_diameter.value())
        self.s.setValue("crosshair/magnification_zoom_pct", self.mag_zoom.value())
        self.magnification_options_changed.emit(self.mag_diameter.value(), self.mag_zoom.value() / 100.0)

    def _on_mag_binding_changed(self, _index: int) -> None:
        self.s.setValue("crosshair/magnification_binding", int(self.mag_binding.currentData() or 0))
        self._update_mag_availability()
        self.magnification_binding_changed.emit()

    def set_magnification_error(self, message: str) -> None:
        self.mag_disclosure.setChecked(True)
        if self.magnification_toggle.isChecked():
            self.magnification_toggle.setChecked(False)
        self.mag_note.setText(message)

    def _on_reset_position(self) -> None:
        self.position_reset.emit()

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
            QMessageBox.warning(self, "CurveCoach", f"Could not import crosshair: {exc}")
