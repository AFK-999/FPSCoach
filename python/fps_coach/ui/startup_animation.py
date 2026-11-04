"""A brief, decorative flowing light effect for the first application show."""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .styles import active_palette


class StartupGlow(QWidget):
    """Click-through, non-blocking aurora ribbons; never captures user input."""

    DURATION_MS = 2600

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("startupGlow")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._progress = 0.0
        self._seen = False
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._animation.valueChanged.connect(self._set_progress)
        self._animation.finished.connect(self.hide)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.hide()

    def play_once(self, enabled: bool) -> bool:
        """Play only on launch; do not replay after hiding, restoring or preference changes."""
        if self._seen:
            return False
        self._seen = True
        if not enabled:
            return False
        self.setGeometry(self.parentWidget().rect())
        self._progress = 0.0
        self.show()
        self.raise_()
        self._animation.start()
        return True

    def stop(self) -> None:
        self._animation.stop()
        self.hide()

    def _set_progress(self, value) -> None:
        self._progress = float(value)
        self.update()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def hideEvent(self, event):  # noqa: N802
        # Minimize, closure or normal completion must never leave a running timer.
        self._animation.stop()
        super().hideEvent(event)

    @staticmethod
    def _ribbon(left: float, width: float, baseline: float, amplitude: float,
                phase: float, offset: float) -> QPainterPath:
        path = QPainterPath()
        for i in range(121):
            u = i / 120.0
            taper = math.sin(math.pi * u) ** 1.25
            wave = math.sin(u * math.pi * 3.2 + phase + offset)
            wave += 0.24 * math.sin(u * math.pi * 6.0 - phase * 0.65 + offset)
            y = baseline + amplitude * taper * wave
            point = QPointF(left + u * width, y)
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        return path

    def paintEvent(self, event):  # noqa: N802
        t = self._progress
        fade = max(0.0, min(1.0, t / 0.15, (1.0 - t) / 0.24))
        if fade <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        light = QColor(active_palette()["bg"]).lightness() > 128
        colors = ("#528baf", "#5ab6a1", "#708d35", "#8265a5") if light else (
            "#83b7ff", "#7ae2d1", "#d7f249", "#b59cff")
        width = min(self.width() * 0.84, 1050.0)
        left = (self.width() - width) / 2.0
        baseline = self.height() * 0.80
        amplitude = min(42.0, self.height() * 0.055)
        phase = t * math.pi * 2.4
        for i in range(4):
            path = self._ribbon(left, width, baseline + (i - 1.5) * 3.5,
                                amplitude * (0.70 + i * 0.11), phase, i * 0.85)
            for pen_width, alpha in ((20.0, 19), (8.0, 43), (2.0, 185)):
                grad = QLinearGradient(left, baseline, left + width, baseline)
                for pos, index, scale in ((0.0, i, 0.0), (0.22, i, 0.75),
                                          (0.52, (i + 1) % 4, 1.0),
                                          (0.78, (i + 2) % 4, 0.75), (1.0, i, 0.0)):
                    color = QColor(colors[index])
                    color.setAlpha(round(alpha * fade * scale))
                    grad.setColorAt(pos, color)
                pen = QPen(grad, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                           Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.drawPath(path)
        p.end()
