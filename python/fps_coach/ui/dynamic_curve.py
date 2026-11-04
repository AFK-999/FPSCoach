"""Opt-in, asynchronously updated local AI performance-curve previews."""
from __future__ import annotations

import threading
from copy import deepcopy

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fps_coach.curve_adjust import normalized_overlay
from fps_coach.curve_io import chart_axis_scale
from fps_coach.dynamic_curve_ai import analyze_performance, apply_factors

from .guide_book import COACH_BUTTON_SIZE, paint_guide_button_surface
from .widgets import CurveChart, _card, _label, _settings


class _Results(QObject):
    ready = Signal(object, object, str)


def _fingerprint(profile):
    return tuple(tuple((p.speed, p.multiplier, p.smoothness, p.corner) for p in axis)
                 for axis in (profile.horizontal, profile.vertical))


class DynamicCurveEngine(QObject):
    """One in-flight request, a coalesced latest snapshot, and explicit confirmation."""
    changed = Signal()
    enabled_changed = Signal(bool)
    confirmed = Signal(object, object, str)

    def __init__(self, base_provider, can_confirm, parent=None, *, mapping_provider=None):
        super().__init__(parent)
        self.base_provider = base_provider
        self.can_confirm = can_confirm
        self.mapping_provider = mapping_provider
        self.enabled = False
        self.goals = {"accuracy_pct": 90, "click_ms": 600}
        self.metrics = {}
        self.status = "Off. Enable to analyze in-app task summaries with local AI."
        self.reason = ""
        self.base = None
        self.proposed = None
        self._fresh = False
        self._running = False
        self._epoch = 0
        self._revision = 0
        self._result_revision = -1
        self._results = _Results(self)
        self._results.ready.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._dispatch)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self._epoch += 1
        self._revision += 1
        self._timer.stop()
        self.metrics = {}
        self.base = self.proposed = None
        self.reason = ""
        self._fresh = False
        self.status = ("On. Complete at least 3 click attempts, including a hit."
                       if enabled else "Off. An in-flight local request may finish; its result is discarded.")
        self.enabled_changed.emit(enabled)
        self.changed.emit()

    def set_goals(self, accuracy, click_ms):
        goals = {"accuracy_pct": int(accuracy), "click_ms": int(click_ms)}
        if goals == self.goals:
            return
        self.goals = goals
        self._epoch += 1
        self._revision += 1
        self._fresh = False
        if self.enabled:
            self.status = "Goals changed; updating the local AI preview."
            self._schedule()
        self.changed.emit()

    def session_started(self, source):
        if not self.enabled:
            return
        self.metrics.pop(source, None)
        self.base = self.proposed = None
        self.reason = ""
        self._epoch += 1
        self._revision += 1
        self._fresh = False
        self.status = f"New {source} session. Waiting for recorded attempts."
        self._schedule()
        self.changed.emit()

    def session_ended(self, source, completed):
        if not self.enabled:
            return
        # Calibration cancel discards its incomplete run; stopping training keeps its actual attempts.
        if source == "calibration" and not completed:
            self.metrics.pop(source, None)
            self.base = self.proposed = None
            self.reason = ""
            self._epoch += 1
            self._revision += 1
            self._fresh = False
        self._schedule()
        self.changed.emit()

    def update_metrics(self, snapshot):
        if not self.enabled or snapshot.get("source") not in ("calibration", "training"):
            return
        self.metrics[snapshot["source"]] = deepcopy(snapshot)
        self._revision += 1
        self._fresh = False
        self.status = "New task data recorded; updating local AI preview."
        self._schedule()
        self.changed.emit()

    def baseline_changed(self):
        if not self.enabled:
            return
        self.base = self.proposed = None
        self.reason = ""
        self._epoch += 1
        self._revision += 1
        self._fresh = False
        self.status = "Base curve changed; updating local AI preview."
        self._schedule()
        self.changed.emit()

    def _enough(self):
        return (sum(x.get("window_attempts", 0) for x in self.metrics.values()) >= 3 and
                sum(x.get("window_hits", 0) for x in self.metrics.values()) >= 1)

    def _schedule(self):
        if self.enabled and self._enough():
            self._timer.start()
        elif self.enabled:
            self.base = self.proposed = None
            self.reason = ""
            self.status = "Waiting for at least 3 click attempts, including a hit."

    def retry(self):
        if not self.enabled:
            return
        self._revision += 1
        self._fresh = False
        self._schedule()
        self.changed.emit()

    def _dispatch(self):
        if not self.enabled or not self._enough() or self._running:
            return
        try:
            base = self.base_provider()
            if base is None:
                self.base = self.proposed = None
                self.reason = ""
                self.status = "Waiting for a completed calibration target to form a base curve."
                self.changed.emit()
                return
            base = deepcopy(base)
        except Exception as exc:  # noqa: BLE001
            self.status = f"Base curve unavailable: {exc}"
            self.changed.emit()
            return
        settings = _settings()
        provider = str(settings.value("ai/provider", "ollama"))
        url = str(settings.value("ai/base_url", "http://127.0.0.1:11434"))
        model = str(settings.value("ai/model", "qwen3:1.7b"))
        goals, metrics = deepcopy(self.goals), deepcopy(self.metrics)
        if self.mapping_provider is not None:
            mapping = self.mapping_provider()
            if mapping is not None:
                metrics["_curve_mapping"] = deepcopy(mapping)
        job = {"epoch": self._epoch, "revision": self._revision, "base": base,
               "goals": goals, "metrics": metrics}
        self._running = True
        self.status = "Local AI is analyzing the latest snapshot; model latency varies."
        self.changed.emit()
        signals = self._results

        def work():
            try:
                proposal = analyze_performance(provider, url, model, goals, metrics, base)
                result = apply_factors(base, proposal)
                error = ""
            except Exception as exc:  # noqa: BLE001 - do not substitute a heuristic for AI
                proposal, result, error = None, None, str(exc) or "Local AI failed."
            try:
                signals.ready.emit(job, (proposal, result), error)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True, name="performance-local-ai").start()

    def _finished(self, job, result, error):
        self._running = False
        if not self.enabled:
            return
        if job["epoch"] != self._epoch:
            self._schedule()
            return
        if error:
            self._fresh = False
            self.base = self.proposed = None
            self.reason = ""
            self.status = f"Local AI unavailable: {error} Retry after fixing the model or service."
            if job["revision"] != self._revision:
                self._schedule()
            self.changed.emit()
            return
        proposal, adjusted = result
        self.base, self.proposed = job["base"], adjusted
        self.reason = proposal["reason"]
        self._result_revision = job["revision"]
        self._fresh = job["revision"] == self._revision
        counts = ", ".join(f"{name}: {data['window_attempts']} attempts"
                           for name, data in job["metrics"].items() if name in ("calibration", "training"))
        if not self._fresh:
            state = "Newer data pending."
        elif not self.can_confirm():
            state = "Finish or cancel calibration before confirming."
        else:
            state = "Ready to confirm."
        self.status = f"AI preview on {job['base'].name} from {counts}. {state}"
        if not self._fresh:
            self._schedule()
        self.changed.emit()

    def can_apply(self):
        return bool(self.enabled and self._fresh and self.base is not None and self.proposed is not None
                    and self.can_confirm())

    def confirm(self):
        if not self.can_apply():
            return
        current = self.base_provider()
        if current is None or _fingerprint(current) != _fingerprint(self.base):
            self.baseline_changed()
            return
        previous, adjusted, reason = deepcopy(self.base), deepcopy(self.proposed), self.reason
        self._fresh = False
        self._epoch += 1
        self._revision += 1
        self.status = "Confirmed in Calibration. New task data is needed for another proposal."
        self.confirmed.emit(previous, adjusted, "Local AI performance proposal · " + reason)
        self.changed.emit()


class SquarePerformanceChart(CurveChart):
    """Compact, read-only square widget with a genuinely square plotting area."""
    def __init__(self, parent=None, *, side=280):
        super().__init__(parent, editable=False)
        self.setFixedSize(side, side)

    def _plot_rect(self):
        bounds = super()._plot_rect()
        side = min(bounds.width(), bounds.height())
        return QRect(bounds.left() + (bounds.width() - side) // 2,
                     bounds.top() + (bounds.height() - side) // 2, side, side)


class DynamicCurvePanel(QWidget):
    """Recommendation and Training share one engine and the same live comparison."""
    def __init__(self, engine: DynamicCurveEngine, parent=None, *, show_title=True, square_chart=False,
                 title_text="Dynamic performance curve", chart_side=280):
        super().__init__(parent)
        self.engine = engine
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card, lay = (_card(title_text, "Optional local AI · preview and confirm before changing the in-app curve")
                     if show_title else _card())
        outer.addWidget(card)
        row = QHBoxLayout()
        self.enabled_box = QCheckBox("Enable AI Local Analysis")
        self.enabled_box.toggled.connect(engine.set_enabled)
        row.addWidget(self.enabled_box)
        row.addStretch(1)
        row.addWidget(QLabel("Accuracy goal"))
        self.accuracy = QSpinBox()
        self.accuracy.setRange(1, 100)
        self.accuracy.setSuffix(" %")
        self.accuracy.setAccessibleName("Target hit accuracy goal percentage")
        row.addWidget(self.accuracy)
        row.addWidget(QLabel("Hit click-time goal"))
        self.timing = QSpinBox()
        self.timing.setRange(50, 5000)
        self.timing.setSingleStep(25)
        self.timing.setSuffix(" ms")
        self.timing.setAccessibleName("Target to hit click-time goal milliseconds")
        row.addWidget(self.timing)
        lay.addLayout(row)
        self.accuracy.valueChanged.connect(self._goals_changed)
        self.timing.valueChanged.connect(self._goals_changed)
        lay.addWidget(_label(
            "Edit the example goals. Accuracy = clicks inside the visible target / attempts; "
            "hit time = target shown, trial restarted or collection enabled to hit. App-event timing, not hardware latency. "
            "Finish or cancel calibration before changing goals or confirming.",
            "cardSubtitle", wrap=True))
        self.metrics_label = _label("No recorded attempts.", "cardSubtitle", wrap=True)
        self.metrics_label.setFixedHeight(42)
        self.chart = SquarePerformanceChart(side=chart_side) if square_chart else CurveChart(editable=False)
        if not square_chart:
            self.chart.setFixedHeight(145)
        self.chart.points = []
        self.chart.xs = []
        self.chart.setAccessibleName("Before and proposed performance sensitivity curve")
        self.status_label = _label("", "cardSubtitle", wrap=True)
        self.status_label.setFixedHeight(64)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.reason_label = _label("", "cardSubtitle", wrap=True)
        self.reason_label.setFixedHeight(52)
        self.reason_label.setTextFormat(Qt.TextFormat.PlainText)
        actions = QHBoxLayout()
        self.retry_btn = QPushButton("Retry local AI")
        self.retry_btn.clicked.connect(engine.retry)
        self.confirm_btn = QPushButton("Confirm curve in Calibration")
        self.confirm_btn.setObjectName("primary")
        self.confirm_btn.clicked.connect(engine.confirm)
        actions.addWidget(self.retry_btn)
        actions.addWidget(self.confirm_btn)
        actions.addStretch(1)
        if square_chart:
            comparison = QHBoxLayout()
            comparison.setSpacing(20)
            comparison.addWidget(self.chart, 0, Qt.AlignmentFlag.AlignTop)
            detail = QVBoxLayout()
            detail.addWidget(self.metrics_label)
            detail.addWidget(self.status_label)
            detail.addWidget(self.reason_label)
            detail.addLayout(actions)
            detail.addStretch(1)
            comparison.addLayout(detail, 1)
            lay.addLayout(comparison)
        else:
            lay.addWidget(self.metrics_label)
            lay.addWidget(self.chart)
            lay.addWidget(self.status_label)
            lay.addWidget(self.reason_label)
            lay.addLayout(actions)
        lay.addWidget(_label(
            "Solid = AI proposal; dashed = base. Model suggestions are experiments, not a guarantee. "
            "Only numeric summaries and goals go to local Ollama when enabled; disable Ollama cloud "
            "for offline privacy. No game, device or OS settings are changed.", "cardSubtitle", wrap=True))
        engine.changed.connect(self.refresh)
        self.refresh()

    def _goals_changed(self, *_args):
        self.engine.set_goals(self.accuracy.value(), self.timing.value())

    def refresh(self):
        engine = self.engine
        with QSignalBlocker(self.enabled_box), QSignalBlocker(self.accuracy), QSignalBlocker(self.timing):
            self.enabled_box.setChecked(engine.enabled)
            self.accuracy.setValue(engine.goals["accuracy_pct"])
            self.timing.setValue(engine.goals["click_ms"])
        bits = []
        for name, data in engine.metrics.items():
            timing = data.get("mean_hit_click_ms")
            speed = data.get("mean_hit_speed_px_s")
            bits.append(f"{name.title()}: {data['window_accuracy_pct']:.0f}% / "
                        f"{data['window_attempts']} clicks · "
                        f"hit {timing:.0f} ms" if timing is not None else
                        f"{name.title()}: {data['window_accuracy_pct']:.0f}% / {data['window_attempts']} clicks · no hits")
            if speed is not None:
                bits[-1] += f" · {speed:.0f} {'virtual' if data.get('input_path') == 'raw' else 'desktop'} px/s"
        self.metrics_label.setText("  |  ".join(bits) or "No recorded attempts.")
        self.status_label.setText(engine.status)
        self.status_label.setToolTip(engine.status)
        self.reason_label.setText(engine.reason)
        self.reason_label.setToolTip(engine.reason)
        self.enabled_box.setEnabled(engine.can_confirm())
        self.accuracy.setEnabled(engine.can_confirm())
        self.timing.setEnabled(engine.can_confirm())
        self.retry_btn.setEnabled(engine.enabled and not engine._running)
        self.confirm_btn.setEnabled(engine.can_apply())
        if self.chart is not None:
            if engine.base is not None and engine.proposed is not None:
                prev, cur = normalized_overlay(engine.base, engine.proposed)
                self.chart.set_axis_scale(*chart_axis_scale(engine.base, engine.proposed))
                self.chart.set_previous([y for _, y in prev], xs=[x for x, _ in prev])
                self.chart.points = [y for _, y in cur]
                self.chart.xs = [x for x, _ in cur]
                self.chart.set_curve_weights([p.smoothness for p in engine.proposed.horizontal],
                                             [p.smoothness for p in engine.base.horizontal])
            else:
                self.chart.set_previous(None)
                self.chart.points = []
                self.chart.xs = []
                self.chart.set_axis_scale(None, None, "unavailable")
            self.chart.update()


class PerformanceDisclosure(QPushButton):
    """Keyboard-operable, metallic disclosure with a hand-drawn bot and state."""
    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setObjectName("performanceDisclosure")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(*COACH_BUTTON_SIZE)
        self.setAccessibleName("Dynamic Performance Curve")
        self._analysis_enabled = False
        self.toggled.connect(self._accessibility)
        self._accessibility(False)

    def set_analysis_enabled(self, enabled: bool) -> None:
        self._analysis_enabled = bool(enabled)
        self._accessibility(self.isChecked())
        self.update()

    def _accessibility(self, expanded: bool) -> None:
        state = "on" if self._analysis_enabled else "off"
        self.setAccessibleDescription(f"{'Expanded' if expanded else 'Collapsed'}; local AI analysis {state}. Activate to {'collapse' if expanded else 'expand'}.")
        self.setToolTip("Dynamic Performance Curve — " + self.accessibleDescription())
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_guide_button_surface(p, self)
        # A single centered bot icon; text is shown inside the expanded panel.
        cx, cy = self.width() / 2.0, self.height() / 2.0
        gold = QColor("#c4af7e")
        p.setPen(QPen(gold, 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 17), QPointF(cx, cy - 12))
        p.drawEllipse(QPointF(cx, cy - 18.5), 1.4, 1.4)
        p.drawRoundedRect(QRectF(cx - 13, cy - 12, 26, 20), 5, 5)
        p.drawLine(QPointF(cx - 17, cy - 5), QPointF(cx - 13, cy - 5))
        p.drawLine(QPointF(cx + 13, cy - 5), QPointF(cx + 17, cy - 5))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#b9cf87") if self._analysis_enabled else gold)
        p.drawEllipse(QPointF(cx - 5, cy - 4), 1.5, 1.5)
        p.drawEllipse(QPointF(cx + 5, cy - 4), 1.5, 1.5)
        p.setPen(QPen(gold, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - 4, cy + 3), QPointF(cx + 4, cy + 3))
        p.end()


class DynamicCurveSection(QWidget):
    """Collapsible Coach view of the same shared performance-curve engine."""
    def __init__(self, engine: DynamicCurveEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.toggle = PerformanceDisclosure()
        self.panel = DynamicCurvePanel(engine, square_chart=True, title_text="Dynamic Performance Curve")
        self.panel.hide()
        self.toggle.toggled.connect(self.panel.setVisible)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.panel)
        engine.enabled_changed.connect(self.toggle.set_analysis_enabled)
        self.toggle.set_analysis_enabled(engine.enabled)


class DynamicCurveMonitor(QWidget):
    """Compact live graph adjacent to a task; controls remain in the larger panel."""
    def __init__(self, engine: DynamicCurveEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        title = _label("LOCAL AI CURVE · LIVE PREVIEW", "formLabel")
        lay.addWidget(title)
        self.chart = SquarePerformanceChart(side=210)
        self.chart.setAccessibleName("Live AI performance-curve preview")
        lay.addWidget(self.chart, 0, Qt.AlignmentFlag.AlignHCenter)
        self.note = _label("", "cardSubtitle", wrap=True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setFixedHeight(36)
        lay.addWidget(self.note)
        engine.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        engine = self.engine
        self.setVisible(engine.enabled)
        if "unavailable" in engine.status.lower():
            state = "Local AI unavailable; see the panel below."
        elif engine._fresh:
            state = "Current."
        elif not engine._enough():
            state = "Waiting for at least three attempts, including a hit."
        else:
            state = "Waiting / updating."
        self.note.setText("Solid: proposal · dashed: base. " + state)
        self.note.setToolTip(engine.status)
        if engine.base is not None and engine.proposed is not None:
            prev, cur = normalized_overlay(engine.base, engine.proposed)
            self.chart.set_axis_scale(*chart_axis_scale(engine.base, engine.proposed))
            self.chart.set_previous([y for _, y in prev], xs=[x for x, _ in prev])
            self.chart.points = [y for _, y in cur]
            self.chart.xs = [x for x, _ in cur]
            self.chart.set_curve_weights([p.smoothness for p in engine.proposed.horizontal],
                                         [p.smoothness for p in engine.base.horizontal])
        else:
            self.chart.set_previous(None)
            self.chart.points = []
            self.chart.xs = []
            self.chart.set_axis_scale(None, None, "unavailable")
        self.chart.update()
