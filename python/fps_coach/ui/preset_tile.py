"""Gold-accented icon-and-label tiles shared by Coach and Crosshair."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from .guide_book import COACH_BUTTON_LABEL_SIZE, COACH_BUTTON_SIZE, paint_guide_button_surface


class PresetTile(QPushButton):
    """A compact manual-preset control with a vector icon above its label."""
    def __init__(self, key: str, title: str, parent=None):
        super().__init__(title, parent)
        self.key = key
        self.setObjectName("presetTile")
        self.setFixedSize(*COACH_BUTTON_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title.replace("\n", " ") + " manual preset")

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_guide_button_surface(p, self)
        p.setPen(QPen(QColor("#c4af7e"), 1.1, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx = self.width() / 2.0
        if self.key == "fast_flicks":
            # A speed trail and rising arrow above the label.
            p.drawLine(QPointF(cx - 13, 12), QPointF(cx - 5, 12))
            p.drawLine(QPointF(cx - 16, 16), QPointF(cx - 8, 16))
            arrow = QPainterPath(QPointF(cx - 5, 18))
            arrow.lineTo(cx + 11, 8)
            arrow.lineTo(cx + 10, 15)
            arrow.moveTo(cx + 11, 8)
            arrow.lineTo(cx + 4, 9)
            p.drawPath(arrow)
        elif self.key == "scope":
            # Scope lens and reticle, drawn with the same gold stroke as Coach.
            center = QPointF(cx, 14)
            p.drawEllipse(center, 7, 7)
            p.drawEllipse(center, 3.5, 3.5)
            p.drawLine(QPointF(cx - 11, 14), QPointF(cx - 4, 14))
            p.drawLine(QPointF(cx + 4, 14), QPointF(cx + 11, 14))
            p.drawLine(QPointF(cx, 3), QPointF(cx, 10))
            p.drawLine(QPointF(cx, 18), QPointF(cx, 22))
            p.drawPoint(center)
        else:
            center = QPointF(cx, 14)
            p.drawEllipse(center, 6, 6)
            p.drawEllipse(center, 3, 3)
            p.drawLine(QPointF(cx - 9, 14), QPointF(cx - 5, 14))
            p.drawLine(QPointF(cx + 5, 14), QPointF(cx + 9, 14))
            p.drawLine(QPointF(cx, 5), QPointF(cx, 9))
            p.drawLine(QPointF(cx, 19), QPointF(cx, 23))
        font = self.font()
        font.setBold(True)
        font.setPointSizeF(COACH_BUTTON_LABEL_SIZE)
        p.setFont(font)
        p.setPen(QColor("#e8ddc4"))
        p.drawText(QRectF(8, 23, self.width() - 16, 30), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()
