"""Small, nonblocking horizontal card flip for two live widget faces."""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStackedWidget, QWidget

from .styles import active_palette


class _FlipOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.pixmap = None
        self.progress = 0.0
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(active_palette()["bg"]))
        if self.pixmap is not None and not self.pixmap.isNull():
            width = self.width() * abs(math.cos(math.pi * self.progress))
            target = QRectF((self.width() - width) / 2, 0, width, self.height())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        p.end()


class FlipCard(QStackedWidget):
    """Switch actual faces at the midpoint; animation never runs a nested event loop."""
    def __init__(self, animations_enabled, parent=None):
        super().__init__(parent)
        self._animations_enabled = animations_enabled
        self._target = 0
        self._switched = False
        self.overlay = _FlipOverlay(self)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(380)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self._frame)
        self.animation.finished.connect(self.finish_flip)

    def flip_to(self, index):
        if index not in range(self.count()):
            return
        if self.animation.state() != self.animation.State.Stopped:
            self.finish_flip()
        if index == self.currentIndex():
            return
        self._target, self._switched = index, False
        if not self.isVisible() or not self._animations_enabled():
            self.setCurrentIndex(index)
            return
        self.overlay.pixmap = self.currentWidget().grab()
        self.overlay.progress = 0.0
        self.overlay.setGeometry(self.rect())
        self.overlay.show()
        self.overlay.raise_()
        self.animation.start()

    def _frame(self, value):
        progress = float(value)
        if progress >= 0.5 and not self._switched:
            self.setCurrentIndex(self._target)
            self._switched = True
            self.overlay.pixmap = self.currentWidget().grab()
        self.overlay.progress = progress
        self.overlay.update()

    def finish_flip(self):
        self.animation.stop()
        self.setCurrentIndex(self._target)
        self.overlay.hide()
        self.overlay.pixmap = None

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())
