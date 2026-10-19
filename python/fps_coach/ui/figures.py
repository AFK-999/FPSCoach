"""Training targets drawn in code with QPainter (no image files).

Two figures share the Vantage visual language: a helmeted sentry robot and a hover
drone. Each is authored in its own design space and painted onto a transparent
QPixmap, so the training range can scale them and tint them on a hit the same way
it did with the bundled PNG sprites.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap

# Fixed palette for the figures. They sit on the always-dark stage, so they are not theme-dependent.
BODY = "#2B3139"
PLATE = "#3E4650"
SHADOW = "#16191E"
EDGE = "#6E7987"
ACCENT = "#D7F249"
AMBER = "#F0A94E"


def _c(value: str, alpha: int = 255) -> QColor:
    color = QColor(value)
    color.setAlpha(alpha)
    return color


def _rounded(p: QPainter, x: float, y: float, w: float, h: float, r: float,
             fill: str, edge: str | None = EDGE, edge_w: float = 1.2) -> None:
    if edge:
        p.setPen(QPen(_c(edge), edge_w))
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(fill)))
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _round_pen(color: str, width: float) -> QPen:
    pen = QPen(_c(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def _glow(p: QPainter, cx: float, cy: float, radius: float, color: str, alpha: int) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(color, alpha)))
    p.drawEllipse(QPointF(cx, cy), radius, radius)


def _paint_robot(p: QPainter) -> None:
    """Design space 100 x 170. Head, visor, shoulder plates, glowing core, legs."""
    # Legs, hips and feet first so the torso overlaps them.
    _rounded(p, 31, 112, 17, 42, 6, PLATE)
    _rounded(p, 52, 112, 17, 42, 6, PLATE)
    _rounded(p, 32, 103, 36, 14, 5, BODY)
    _rounded(p, 26, 150, 24, 12, 5, BODY)
    _rounded(p, 50, 150, 24, 12, 5, BODY)
    # Arms.
    _rounded(p, 9, 68, 15, 40, 7, PLATE)
    _rounded(p, 76, 68, 15, 40, 7, PLATE)
    _glow(p, 16.5, 111, 7, BODY, 255)
    _glow(p, 83.5, 111, 7, BODY, 255)
    # Torso and core.
    _rounded(p, 30, 64, 40, 44, 9, BODY)
    p.setPen(QPen(_c(EDGE, 150), 1.0))
    p.drawLine(QPointF(36, 74), QPointF(64, 74))
    p.drawLine(QPointF(36, 98), QPointF(64, 98))
    _glow(p, 50, 84, 12, ACCENT, 50)
    _glow(p, 50, 84, 7, ACCENT, 255)
    # Shoulder plates.
    _rounded(p, 17, 46, 66, 22, 11, PLATE)
    # Neck and helmet.
    _rounded(p, 43, 38, 14, 12, 3, SHADOW, edge=None)
    _rounded(p, 32, 6, 36, 36, 12, PLATE)
    # Visor: a bright band with a soft glow behind it.
    _glow(p, 50, 23, 17, ACCENT, 40)
    _rounded(p, 38, 17, 24, 9, 3.5, ACCENT, edge=None)
    p.setPen(QPen(_c(SHADOW), 1.2))
    p.drawLine(QPointF(44, 13), QPointF(44, 21))
    p.drawLine(QPointF(56, 13), QPointF(56, 21))


def _paint_drone(p: QPainter) -> None:
    """Design space 200 x 100. Central pod with an eye, angled arms, rotors, thrusters."""
    # Arms.
    p.setPen(_round_pen(EDGE, 7.5))
    p.drawLine(QPointF(70, 50), QPointF(24, 30))
    p.drawLine(QPointF(130, 50), QPointF(176, 30))
    p.setPen(_round_pen(BODY, 4.5))
    p.drawLine(QPointF(70, 50), QPointF(24, 30))
    p.drawLine(QPointF(130, 50), QPointF(176, 30))
    # Rotor pods.
    for cx in (22, 178):
        _rounded(p, cx - 22, 24, 44, 11, 5.5, PLATE)
        _rounded(p, cx - 6, 30, 12, 10, 3, BODY)
    # Thrusters glowing underneath.
    for x in (84, 106):
        _glow(p, x + 6, 82, 9, AMBER, 60)
        _rounded(p, x, 72, 12, 12, 3, AMBER, edge=None)
    # Central pod.
    p.setPen(QPen(_c(EDGE), 1.4))
    p.setBrush(QBrush(_c(BODY)))
    p.drawEllipse(QPointF(100, 50), 36, 26)
    p.setPen(QPen(_c(EDGE, 120), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(70, 32, 60, 36), 30 * 16, 120 * 16)
    # Eye.
    _glow(p, 100, 48, 17, ACCENT, 55)
    _glow(p, 100, 48, 10, ACCENT, 255)
    _glow(p, 100, 48, 4, SHADOW, 255)


def _render(paint, width: int, height: int, design_w: float, design_h: float) -> QPixmap:
    out = QPixmap(width, height)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(width / design_w, height / design_h)
    paint(painter)
    painter.end()
    return out


def robot_pixmap(height: int = 256) -> QPixmap:
    """A sentry robot on a transparent background."""
    width = max(8, round(height * 100 / 170))
    return _render(_paint_robot, width, height, 100.0, 170.0)


def drone_pixmap(height: int = 128) -> QPixmap:
    """A hover drone on a transparent background."""
    width = max(8, round(height * 200 / 100))
    return _render(_paint_drone, width, height, 200.0, 100.0)


def target_pixmaps() -> list[QPixmap]:
    """The figures used by the training range, in the order they are picked from."""
    return [robot_pixmap(), drone_pixmap()]
