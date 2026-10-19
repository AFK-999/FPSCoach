"""Procedural vector icon set for FPS Coach.

Every icon here is drawn with QPainter primitives on a 24x24 logical grid -
no bitmap assets, no icon fonts, no emoji glyphs. The visual language is a
single-weight "optics / HUD" line style: rounded caps, a consistent stroke
weight, and a restrained set of geometric motifs (reticles, brackets, a
single accent dot) that echo the card and sidebar detailing used throughout
the app. Icons are cached by (name, color, size) since they're re-requested
on every theme switch.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

_GRID = 24.0
_STROKE = 1.7


def _pen(color: str, width: float = _STROKE) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _no_fill(p: QPainter) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)


# --- Navigation glyphs ------------------------------------------------------

def _draw_dashboard(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawRoundedRect(QRectF(3.2, 3.2, 10.2, 10.2), 2.4, 2.4)
    p.drawRoundedRect(QRectF(14.6, 3.2, 6.2, 6.6), 2.0, 2.0)
    p.drawRoundedRect(QRectF(14.6, 11.0, 6.2, 9.8), 2.0, 2.0)
    p.drawRoundedRect(QRectF(3.2, 14.6, 10.2, 6.2), 2.0, 2.0)


def _draw_guide(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawEllipse(QRectF(3.0, 3.0, 18.0, 18.0))
    path = QPainterPath()
    path.moveTo(12, 7.2)
    path.lineTo(14.4, 12)
    path.lineTo(12, 16.8)
    path.lineTo(9.6, 12)
    path.closeSubpath()
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c))
    p.drawEllipse(QPointF(12, 12), 1.1, 1.1)


def _draw_capture(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawRoundedRect(QRectF(3.0, 5.4, 18.0, 13.4), 2.6, 2.6)
    p.drawEllipse(QPointF(12, 12.1), 4.1, 4.1)
    p.drawLine(QPointF(8.4, 5.4), QPointF(9.8, 2.8))
    p.drawLine(QPointF(15.6, 5.4), QPointF(14.2, 2.8))


def _draw_calibration(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawEllipse(QPointF(12, 12), 7.4, 7.4)
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    for x1, y1, x2, y2 in (
        (12, 1.6, 12, 4.0),
        (12, 20.0, 12, 22.4),
        (1.6, 12, 4.0, 12),
        (20.0, 12, 22.4, 12),
    ):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c))
    p.drawEllipse(QPointF(12, 12), 1.0, 1.0)


def _draw_training(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawLine(QPointF(4.0, 20.0), QPointF(20.0, 20.0))
    for x, top in ((6.4, 13.5), (11.2, 10.0), (16.0, 6.0)):
        p.drawLine(QPointF(x, 20.0), QPointF(x, top))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c))
    p.drawEllipse(QPointF(16.0, 6.0), 1.6, 1.6)


def _draw_recommendation(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(4.0, 18.0)
    path.cubicTo(9.0, 18.0, 8.0, 7.0, 14.0, 6.4)
    path.cubicTo(17.5, 6.0, 18.5, 9.0, 20.0, 6.0)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c))
    for pt in (QPointF(4.0, 18.0), QPointF(14.0, 6.4), QPointF(20.0, 6.0)):
        p.drawEllipse(pt, 1.3, 1.3)


def _draw_crosshair(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawLine(QPointF(12, 2.6), QPointF(12, 8.2))
    p.drawLine(QPointF(12, 15.8), QPointF(12, 21.4))
    p.drawLine(QPointF(2.6, 12), QPointF(8.2, 12))
    p.drawLine(QPointF(15.8, 12), QPointF(21.4, 12))
    p.drawEllipse(QPointF(12, 12), 2.0, 2.0)


def _draw_chat(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(3.2, 4.4, 17.6, 12.0), 4.0, 4.0)
    p.drawPath(bubble)
    tail = QPainterPath()
    tail.moveTo(7.6, 16.4)
    tail.lineTo(7.0, 20.2)
    tail.lineTo(10.8, 16.4)
    p.drawPath(tail)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c))
    for x in (8.4, 12.0, 15.6):
        p.drawEllipse(QPointF(x, 10.4), 1.0, 1.0)


def _draw_privacy(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    shield = QPainterPath()
    shield.moveTo(12, 2.8)
    shield.lineTo(19.6, 5.6)
    shield.lineTo(19.6, 11.6)
    shield.cubicTo(19.6, 17.0, 16.3, 20.2, 12, 21.4)
    shield.cubicTo(7.7, 20.2, 4.4, 17.0, 4.4, 11.6)
    shield.lineTo(4.4, 5.6)
    shield.closeSubpath()
    p.drawPath(shield)
    check = QPainterPath()
    check.moveTo(8.6, 12.0)
    check.lineTo(11.0, 14.6)
    check.lineTo(15.6, 9.2)
    p.drawPath(check)


def _draw_settings(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawEllipse(QPointF(12, 12), 3.6, 3.6)
    for i in range(8):
        angle = math.radians(i * 45)
        x1, y1 = 12 + math.cos(angle) * 6.6, 12 + math.sin(angle) * 6.6
        x2, y2 = 12 + math.cos(angle) * 9.4, 12 + math.sin(angle) * 9.4
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


# --- Small UI glyphs ---------------------------------------------------------

def _draw_chevron_left(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(14.5, 5)
    path.lineTo(9, 12)
    path.lineTo(14.5, 19)
    p.drawPath(path)


def _draw_chevron_right(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(9.5, 5)
    path.lineTo(15, 12)
    path.lineTo(9.5, 19)
    p.drawPath(path)


def _draw_chevron_up(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(5, 14.5)
    path.lineTo(12, 9)
    path.lineTo(19, 14.5)
    p.drawPath(path)


def _draw_chevron_down(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(5, 9.5)
    path.lineTo(12, 16)
    path.lineTo(19, 9.5)
    p.drawPath(path)


def _draw_close(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
    p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))


def _draw_minimize(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(6, 12), QPointF(18, 12))


def _draw_mic(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawRoundedRect(QRectF(9.4, 3.2, 5.2, 10.0), 2.6, 2.6)
    path = QPainterPath()
    path.moveTo(6.4, 11.4)
    path.cubicTo(6.4, 15.6, 8.9, 18.2, 12, 18.2)
    path.cubicTo(15.1, 18.2, 17.6, 15.6, 17.6, 11.4)
    p.drawPath(path)
    p.drawLine(QPointF(12, 18.2), QPointF(12, 21.2))
    p.drawLine(QPointF(8.8, 21.2), QPointF(15.2, 21.2))


def _draw_send(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(3.4, 12.6)
    path.lineTo(20.2, 4.0)
    path.lineTo(13.6, 20.6)
    path.lineTo(11.4, 13.6)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(11.4, 13.6), QPointF(20.2, 4.0))


def _draw_download(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawLine(QPointF(12, 3.6), QPointF(12, 14.4))
    path = QPainterPath()
    path.moveTo(7.6, 10.4)
    path.lineTo(12, 14.8)
    path.lineTo(16.4, 10.4)
    p.drawPath(path)
    p.drawLine(QPointF(5.2, 19.0), QPointF(18.8, 19.0))


def _draw_upload(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawLine(QPointF(12, 14.4), QPointF(12, 3.6))
    path = QPainterPath()
    path.moveTo(7.6, 7.6)
    path.lineTo(12, 3.2)
    path.lineTo(16.4, 7.6)
    p.drawPath(path)
    p.drawLine(QPointF(5.2, 19.0), QPointF(18.8, 19.0))


def _draw_check(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    path = QPainterPath()
    path.moveTo(5.5, 12.5)
    path.lineTo(10, 17)
    path.lineTo(18.5, 7)
    p.drawPath(path)


def _draw_refresh(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    rect = QRectF(4.5, 4.5, 15, 15)
    p.drawArc(rect, 40 * 16, 260 * 16)
    path = QPainterPath()
    path.moveTo(17.6, 5.6)
    path.lineTo(19.0, 9.4)
    path.lineTo(15.0, 9.0)
    p.drawPath(path)


def _draw_play(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    p.setBrush(QColor(c))
    path = QPainterPath()
    path.moveTo(8.5, 5.5)
    path.lineTo(18.5, 12)
    path.lineTo(8.5, 18.5)
    path.closeSubpath()
    p.drawPath(path)


def _draw_stop(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    p.setBrush(QColor(c))
    p.drawRoundedRect(QRectF(7, 7, 10, 10), 2.0, 2.0)


def _draw_edit(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawLine(QPointF(5, 19), QPointF(8, 19))
    path = QPainterPath()
    path.moveTo(7, 17)
    path.lineTo(16, 8)
    path.lineTo(19, 11)
    path.lineTo(10, 20)
    path.closeSubpath()
    p.drawPath(path)


def _draw_trash(p: QPainter, c: str) -> None:
    p.setPen(_pen(c))
    _no_fill(p)
    p.drawLine(QPointF(5, 7), QPointF(19, 7))
    p.drawLine(QPointF(9, 7), QPointF(9, 4.6))
    p.drawLine(QPointF(15, 7), QPointF(15, 4.6))
    p.drawRoundedRect(QRectF(9, 4.6, 6, 2.4), 1, 1)
    path = QPainterPath()
    path.moveTo(6.6, 7)
    path.lineTo(7.6, 20)
    path.lineTo(16.4, 20)
    path.lineTo(17.4, 7)
    p.drawPath(path)


_DRAWERS = {
    "dashboard": _draw_dashboard,
    "guide": _draw_guide,
    "capture": _draw_capture,
    "calibration": _draw_calibration,
    "training": _draw_training,
    "recommendation": _draw_recommendation,
    "crosshair": _draw_crosshair,
    "chat": _draw_chat,
    "privacy": _draw_privacy,
    "settings": _draw_settings,
    "chevron_left": _draw_chevron_left,
    "chevron_right": _draw_chevron_right,
    "chevron_up": _draw_chevron_up,
    "chevron_down": _draw_chevron_down,
    "close": _draw_close,
    "minimize": _draw_minimize,
    "mic": _draw_mic,
    "send": _draw_send,
    "download": _draw_download,
    "upload": _draw_upload,
    "check": _draw_check,
    "refresh": _draw_refresh,
    "play": _draw_play,
    "stop": _draw_stop,
    "edit": _draw_edit,
    "trash": _draw_trash,
}

_cache: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str = "#e7e5e0", size: int = 20) -> QIcon:
    """Render (and cache) a custom vector icon at the given size/color."""
    key = (name, color, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    drawer = _DRAWERS.get(name)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / _GRID, size / _GRID)
    if drawer is not None:
        drawer(painter, color)
    painter.end()
    result = QIcon(QPixmap.fromImage(image))
    _cache[key] = result
    return result


def pixmap(name: str, color: str = "#e7e5e0", size: int = 20) -> QPixmap:
    return icon(name, color, size).pixmap(size, size)