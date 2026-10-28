"""Vantage icon set, drawn with QPainter.

Every glyph is authored on a 24 x 24 grid with a 1.7-unit round-cap stroke, so
the set stays visually consistent at any size. Nothing is downloaded or taken
from a stock font. The API is unchanged: ``icon(name, color, size)`` returns a
QIcon and ``pixmap(name, color, size)`` returns a QPixmap.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_GRID = 24.0
_STROKE = 1.7


def _pen(color: QColor, width: float = _STROKE) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _fill(p: QPainter, color: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)


def _outline(p: QPainter, color: QColor, width: float = _STROKE) -> None:
    p.setPen(_pen(color, width))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _poly(points: list[tuple[float, float]], closed: bool = False) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for x, y in points[1:]:
        path.lineTo(x, y)
    if closed:
        path.closeSubpath()
    return path


def _line(p: QPainter, a: tuple[float, float], b: tuple[float, float]) -> None:
    p.drawLine(QPointF(*a), QPointF(*b))


# --- navigation -------------------------------------------------------------

def _draw_dashboard(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    for x, y in ((3.5, 3.5), (13.0, 3.5), (3.5, 13.0), (13.0, 13.0)):
        p.drawRoundedRect(QRectF(x, y, 7.5, 7.5), 2.2, 2.2)


def _draw_guide(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    left = QPainterPath(QPointF(3.0, 5.6))
    left.quadTo(QPointF(7.5, 3.8), QPointF(11.6, 6.0))
    left.lineTo(QPointF(11.6, 19.6))
    left.quadTo(QPointF(7.5, 17.6), QPointF(3.0, 19.2))
    left.closeSubpath()
    right = QPainterPath(QPointF(21.0, 5.6))
    right.quadTo(QPointF(16.5, 3.8), QPointF(12.4, 6.0))
    right.lineTo(QPointF(12.4, 19.6))
    right.quadTo(QPointF(16.5, 17.6), QPointF(21.0, 19.2))
    right.closeSubpath()
    p.drawPath(left)
    p.drawPath(right)


def _draw_capture(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    for pts in (
        [(3.0, 8.0), (3.0, 5.5), (5.5, 3.0), (8.0, 3.0)],
        [(16.0, 3.0), (18.5, 3.0), (21.0, 5.5), (21.0, 8.0)],
        [(21.0, 16.0), (21.0, 18.5), (18.5, 21.0), (16.0, 21.0)],
        [(8.0, 21.0), (5.5, 21.0), (3.0, 18.5), (3.0, 16.0)],
    ):
        p.drawPath(_poly(pts))
    p.drawEllipse(QPointF(12.0, 12.0), 3.4, 3.4)
    _fill(p, c)
    p.drawEllipse(QPointF(12.0, 12.0), 0.9, 0.9)


def _draw_calibration(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    p.drawPath(_poly([(3.5, 3.0), (3.5, 20.5), (21.0, 20.5)]))
    curve = QPainterPath(QPointF(5.5, 17.5))
    curve.cubicTo(QPointF(10.0, 17.0), QPointF(11.0, 9.0), QPointF(14.0, 8.5))
    curve.cubicTo(QPointF(16.5, 8.1), QPointF(18.0, 5.8), QPointF(20.0, 5.5))
    p.drawPath(curve)
    _fill(p, c)
    p.drawEllipse(QPointF(14.0, 8.5), 1.8, 1.8)


def _draw_recommendation(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    star = QPainterPath(QPointF(11.0, 3.5))
    star.cubicTo(QPointF(11.7, 9.2), QPointF(14.8, 12.3), QPointF(20.5, 13.0))
    star.cubicTo(QPointF(14.8, 13.7), QPointF(11.7, 16.8), QPointF(11.0, 22.5))
    star.cubicTo(QPointF(10.3, 16.8), QPointF(7.2, 13.7), QPointF(1.5, 13.0))
    star.cubicTo(QPointF(7.2, 12.3), QPointF(10.3, 9.2), QPointF(11.0, 3.5))
    p.drawPath(star)
    p.drawPath(_poly([(19.5, 2.5), (19.5, 6.0)]))
    p.drawPath(_poly([(17.75, 4.25), (21.25, 4.25)]))


def _draw_training(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    p.drawEllipse(QPointF(11.0, 13.0), 8.0, 8.0)
    p.drawEllipse(QPointF(11.0, 13.0), 4.0, 4.0)
    p.drawPath(_poly([(20.5, 3.5), (13.5, 10.5)]))
    p.drawPath(_poly([(20.5, 8.5), (20.5, 3.5), (15.5, 3.5)]))
    _fill(p, c)
    p.drawEllipse(QPointF(11.0, 13.0), 1.3, 1.3)


def _draw_crosshair(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    _line(p, (12.0, 2.5), (12.0, 8.5))
    _line(p, (12.0, 15.5), (12.0, 21.5))
    _line(p, (2.5, 12.0), (8.5, 12.0))
    _line(p, (15.5, 12.0), (21.5, 12.0))
    _fill(p, c)
    p.drawEllipse(QPointF(12.0, 12.0), 1.2, 1.2)


def _draw_chat(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    bubble = QPainterPath(QPointF(6.0, 3.5))
    bubble.lineTo(QPointF(18.0, 3.5))
    bubble.quadTo(QPointF(20.5, 3.5), QPointF(20.5, 6.0))
    bubble.lineTo(QPointF(20.5, 15.0))
    bubble.quadTo(QPointF(20.5, 17.5), QPointF(18.0, 17.5))
    bubble.lineTo(QPointF(11.0, 17.5))
    bubble.lineTo(QPointF(6.5, 21.0))
    bubble.lineTo(QPointF(6.5, 17.5))
    bubble.quadTo(QPointF(3.5, 17.5), QPointF(3.5, 15.0))
    bubble.lineTo(QPointF(3.5, 6.0))
    bubble.quadTo(QPointF(3.5, 3.5), QPointF(6.0, 3.5))
    bubble.closeSubpath()
    p.drawPath(bubble)
    _fill(p, c)
    for x in (8.0, 12.0, 16.0):
        p.drawEllipse(QPointF(x, 10.5), 1.15, 1.15)


def _draw_privacy(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    shield = QPainterPath(QPointF(12.0, 2.8))
    shield.lineTo(QPointF(19.5, 5.7))
    shield.lineTo(QPointF(19.5, 11.5))
    shield.cubicTo(QPointF(19.5, 16.2), QPointF(16.3, 19.6), QPointF(12.0, 21.2))
    shield.cubicTo(QPointF(7.7, 19.6), QPointF(4.5, 16.2), QPointF(4.5, 11.5))
    shield.lineTo(QPointF(4.5, 5.7))
    shield.closeSubpath()
    p.drawPath(shield)
    p.drawEllipse(QPointF(12.0, 10.4), 2.0, 2.0)
    _line(p, (12.0, 12.4), (12.0, 15.6))


def _draw_settings(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    for y in (6.0, 12.0, 18.0):
        _line(p, (3.0, y), (21.0, y))
    for x, y in ((8.5, 6.0), (15.5, 12.0), (10.0, 18.0)):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), 2.4, 2.4)


# --- controls ---------------------------------------------------------------

def _chevron(points: list[tuple[float, float]]) -> Callable[[QPainter, QColor], None]:
    def draw(p: QPainter, c: QColor) -> None:
        _outline(p, c)
        p.drawPath(_poly(points))

    return draw


_draw_chevron_left = _chevron([(15.0, 6.0), (9.0, 12.0), (15.0, 18.0)])
_draw_chevron_right = _chevron([(9.0, 6.0), (15.0, 12.0), (9.0, 18.0)])
_draw_chevron_up = _chevron([(6.0, 15.0), (12.0, 9.0), (18.0, 15.0)])
_draw_chevron_down = _chevron([(6.0, 9.0), (12.0, 15.0), (18.0, 9.0)])


def _draw_close(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    _line(p, (6.0, 6.0), (18.0, 18.0))
    _line(p, (18.0, 6.0), (6.0, 18.0))


def _draw_minimize(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    _line(p, (5.5, 12.0), (18.5, 12.0))


def _draw_refresh(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    arc = QPainterPath()
    arc.arcMoveTo(QRectF(4.0, 4.0, 16.0, 16.0), 40.0)
    arc.arcTo(QRectF(4.0, 4.0, 16.0, 16.0), 40.0, 280.0)
    p.drawPath(arc)
    p.drawPath(_poly([(20.0, 3.5), (20.0, 8.5), (15.0, 8.5)]))


def _draw_undo(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    curve = QPainterPath(QPointF(18.0, 18.0))
    curve.cubicTo(QPointF(21.0, 10.0), QPointF(14.0, 5.0), QPointF(6.5, 9.5))
    p.drawPath(curve)
    p.drawPath(_poly([(10.5, 7.0), (6.5, 9.5), (8.0, 13.5)]))


def _draw_redo(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    curve = QPainterPath(QPointF(6.0, 18.0))
    curve.cubicTo(QPointF(3.0, 10.0), QPointF(10.0, 5.0), QPointF(17.5, 9.5))
    p.drawPath(curve)
    p.drawPath(_poly([(13.5, 7.0), (17.5, 9.5), (16.0, 13.5)]))


def _draw_play(p: QPainter, c: QColor) -> None:
    _fill(p, c)
    p.drawPath(_poly([(8.0, 5.5), (19.0, 12.0), (8.0, 18.5)], closed=True))


def _draw_stop(p: QPainter, c: QColor) -> None:
    _fill(p, c)
    p.drawRoundedRect(QRectF(6.5, 6.5, 11.0, 11.0), 2.0, 2.0)


def _draw_speaker(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    p.drawPath(_poly([(4.0, 9.5), (8.0, 9.5), (13.0, 5.0), (13.0, 19.0), (8.0, 14.5), (4.0, 14.5)], closed=True))
    p.drawArc(QRectF(13.5, 8.5, 4.0, 7.0), -60 * 16, 120 * 16)
    p.drawArc(QRectF(15.5, 5.5, 7.5, 13.0), -55 * 16, 110 * 16)


def _tray(p: QPainter, c: QColor) -> None:
    p.drawPath(_poly([(4.0, 15.0), (4.0, 19.0), (5.5, 20.5), (18.5, 20.5), (20.0, 19.0), (20.0, 15.0)]))


def _draw_upload(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    _tray(p, c)
    _line(p, (12.0, 15.0), (12.0, 4.0))
    p.drawPath(_poly([(7.5, 8.5), (12.0, 4.0), (16.5, 8.5)]))


def _draw_download(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    _tray(p, c)
    _line(p, (12.0, 4.0), (12.0, 15.0))
    p.drawPath(_poly([(7.5, 10.5), (12.0, 15.0), (16.5, 10.5)]))


def _draw_mic(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    p.drawRoundedRect(QRectF(9.0, 3.0, 6.0, 11.0), 3.0, 3.0)
    cup = QPainterPath(QPointF(5.5, 11.0))
    cup.quadTo(QPointF(5.5, 17.5), QPointF(12.0, 17.5))
    cup.quadTo(QPointF(18.5, 17.5), QPointF(18.5, 11.0))
    p.drawPath(cup)
    _line(p, (12.0, 17.5), (12.0, 21.0))
    _line(p, (9.0, 21.0), (15.0, 21.0))


def _draw_send(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    p.drawPath(_poly([(3.5, 11.8), (20.5, 4.0), (13.5, 20.5), (11.0, 13.5)], closed=True))
    _line(p, (11.0, 13.5), (20.5, 4.0))


def _draw_check(p: QPainter, c: QColor) -> None:
    _outline(p, c, 2.0)
    p.drawPath(_poly([(4.5, 12.5), (9.5, 17.5), (19.5, 6.5)]))


def _draw_trash(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    _line(p, (4.0, 6.5), (20.0, 6.5))
    p.drawPath(_poly([(9.5, 6.5), (9.5, 4.5), (14.5, 4.5), (14.5, 6.5)]))
    p.drawPath(_poly([(6.0, 6.5), (7.0, 20.0), (17.0, 20.0), (18.0, 6.5)]))
    _line(p, (10.0, 10.5), (10.0, 16.5))
    _line(p, (14.0, 10.5), (14.0, 16.5))


def _draw_edit(p: QPainter, c: QColor) -> None:
    _outline(p, c)
    p.drawPath(_poly([(4.0, 20.0), (4.5, 15.5), (15.5, 4.5), (19.5, 8.5), (8.5, 19.5)], closed=True))
    _line(p, (13.0, 7.0), (17.0, 11.0))


def _draw_eye(p: QPainter, c: QColor) -> None:
    """Watch: an eye outline with a pupil. Used by Live vision."""
    _outline(p, c, 1.7)
    p.drawPath(
        _poly([(4.0, 12.0), (8.0, 8.0), (16.0, 8.0), (20.0, 12.0), (16.0, 16.0), (8.0, 16.0), (4.0, 12.0)])
    )
    p.drawEllipse(QPointF(12.0, 12.0), 2.4, 2.4)


def _draw_brand(p: QPainter, c: QColor) -> None:
    """The Vantage mark: a sighting ring with a V-shaped aim point."""
    _outline(p, c, 2.0)
    p.drawEllipse(QPointF(12.0, 12.0), 9.0, 9.0)
    p.drawPath(_poly([(7.5, 8.5), (12.0, 15.5), (16.5, 8.5)]))


_DRAWERS: dict[str, Callable[[QPainter, QColor], None]] = {
    "dashboard": _draw_dashboard,
    "guide": _draw_guide,
    "capture": _draw_capture,
    "calibration": _draw_calibration,
    "recommendation": _draw_recommendation,
    "training": _draw_training,
    "crosshair": _draw_crosshair,
    "chat": _draw_chat,
    "privacy": _draw_privacy,
    "settings": _draw_settings,
    "eye": _draw_eye,
    "chevron_left": _draw_chevron_left,
    "chevron_right": _draw_chevron_right,
    "chevron_up": _draw_chevron_up,
    "chevron_down": _draw_chevron_down,
    "close": _draw_close,
    "minimize": _draw_minimize,
    "refresh": _draw_refresh,
    "play": _draw_play,
    "stop": _draw_stop,
    "speaker": _draw_speaker,
    "upload": _draw_upload,
    "download": _draw_download,
    "undo": _draw_undo,
    "redo": _draw_redo,
    "mic": _draw_mic,
    "send": _draw_send,
    "check": _draw_check,
    "trash": _draw_trash,
    "edit": _draw_edit,
    "brand": _draw_brand,
}


def _color(value: str) -> QColor:
    return QColor(value)


def _resolve(color: str | None) -> str:
    """None means "the active theme's text color" so glyphs follow theme switches."""
    if color:
        return color
    from .styles import active_palette

    return active_palette()["text"]


def pixmap(name: str, color: str | None = None, size: int = 20) -> QPixmap:
    """Render a named icon to a transparent QPixmap of ``size`` pixels."""
    size = max(8, int(size))
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    drawer = _DRAWERS.get(name)
    if drawer is None:
        return out
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / _GRID, size / _GRID)
    drawer(painter, _color(_resolve(color)))
    painter.end()
    return out


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    """A QIcon wrapping :func:`pixmap` (crisp at the requested size)."""
    return QIcon(pixmap(name, color, size))


def available() -> list[str]:
    """Names of every drawn icon (used by tests)."""
    return sorted(_DRAWERS)
