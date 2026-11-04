"""Compact, accessible calibration-guide launcher with an optional click pulse."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QPushButton

GUIDE_BUTTON_SIZE = (140, 42)
COACH_BUTTON_SIZE = (150, 58)
COACH_BUTTON_LABEL_SIZE = 8.5


def paint_guide_button_surface(painter: QPainter, button: QPushButton, pulse: float = 0.0, padding: int = 4) -> QRectF:
    """Paint the Dashboard Start surface, shared by matching Coach controls."""
    inset = padding - pulse * 2.0
    bounds = QRectF(button.rect()).adjusted(inset + 0.5, inset + 0.5, -inset - 0.5, -inset - 0.5)
    fill = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
    fill.setColorAt(0, QColor("#665135" if button.underMouse() else "#55452e"))
    fill.setColorAt(0.48, QColor("#302a22"))
    fill.setColorAt(1, QColor("#1c1b1a"))
    border = QColor("#c4af7e" if button.hasFocus() else "#8f784c")
    if pulse:
        border = border.lighter(round(100 + pulse * 15))
    if not button.isEnabled():
        painter.setOpacity(0.48)
    painter.setPen(QPen(border, 1.4 if button.hasFocus() else 1.0))
    painter.setBrush(fill)
    painter.drawRoundedRect(bounds, 8, 8)
    glint = QLinearGradient(bounds.left() + 10, 0, bounds.right() - 10, 0)
    glint.setColorAt(0, QColor(230, 198, 132, 0))
    glint.setColorAt(0.5, QColor(230, 198, 132, 110))
    glint.setColorAt(1, QColor(230, 198, 132, 0))
    painter.setPen(QPen(QBrush(glint), 0.9))
    painter.drawLine(bounds.left() + 10, bounds.top() + 3, bounds.right() - 10, bounds.top() + 3)
    return bounds


class GuideBookButton(QPushButton):
    """Paint a small gold-accented button and pulse without changing layout geometry."""

    activate_requested = Signal()
    PADDING = 4

    def __init__(self, animations_enabled: Callable[[], bool], parent=None):
        super().__init__("Start", parent)
        self.setObjectName("guideStart")
        self._animations_enabled = animations_enabled
        self._pulse = 0.0
        self._waiting = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(260)
        self._animation.setStartValue(0.0)
        self._animation.setKeyValueAt(0.48, 1.0)
        self._animation.setEndValue(0.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._set_pulse)
        self._animation.finished.connect(self._finish_click)
        self.clicked.connect(self._clicked)
        self.setFixedSize(*GUIDE_BUTTON_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Start calibration guide")
        self.setToolTip("Open or edit calibration guide")

    def _set_pulse(self, value) -> None:
        self._pulse = float(value)
        self.update()

    def _clicked(self) -> None:
        if self._waiting:
            return
        if self.isVisible() and self._animations_enabled():
            self._waiting = True
            self._animation.start()
        else:
            self.activate_requested.emit()

    def _finish_click(self) -> None:
        self._pulse = 0.0
        pending, self._waiting = self._waiting, False
        self.update()
        if pending and self.isEnabled() and self.isVisible():
            self.activate_requested.emit()

    def cancel_animation(self) -> None:
        self._waiting = False
        self._animation.stop()
        self._pulse = 0.0
        self.update()

    def hideEvent(self, event):  # noqa: N802
        self.cancel_animation()
        super().hideEvent(event)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = paint_guide_button_surface(painter, self, self._pulse, self.PADDING)
        icon_side = 24.0 + 2.0 * self._pulse
        icon_left = bounds.center().x() - 40
        if not self.icon().isNull():
            pixmap = self.icon().pixmap(round(icon_side), round(icon_side))
            target = QRectF(icon_left, bounds.center().y() - icon_side / 2, icon_side, icon_side)
            painter.save()
            painter.setOpacity(painter.opacity() * 0.82)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            painter.restore()
        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e8ddc4"))
        if self.icon().isNull():
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, self.text())
        else:
            label_left = icon_left + 32
            painter.drawText(QRectF(label_left, bounds.top(), bounds.right() - label_left - 8, bounds.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        painter.end()


class PerformanceStartButton(QPushButton):
    """Compact bot-only control with the Dashboard Start metallic surface."""
    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setObjectName("performanceStart")
        self.setFixedSize(GUIDE_BUTTON_SIZE[1], GUIDE_BUTTON_SIZE[1])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Dynamic Performance Curve")
        self.setToolTip("Dynamic Performance Curve — switch between the suggested and dynamic curves")

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = paint_guide_button_surface(p, self)
        cx, cy = bounds.center().x(), bounds.center().y()
        gold = QColor("#c4af7e")
        p.setPen(QPen(gold, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 11), QPointF(cx, cy - 8))
        p.drawEllipse(QPointF(cx, cy - 12), 1.2, 1.2)
        p.drawRoundedRect(QRectF(cx - 9, cy - 8, 18, 15), 4, 4)
        p.drawLine(QPointF(cx - 3, cy + 3), QPointF(cx + 3, cy + 3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gold)
        p.drawEllipse(QPointF(cx - 3.75, cy - 2), 1.25, 1.25)
        p.drawEllipse(QPointF(cx + 3.75, cy - 2), 1.25, 1.25)
        p.end()
