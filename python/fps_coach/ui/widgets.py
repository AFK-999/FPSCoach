"""Shared building blocks for the Vantage pages.

Holds layout helpers (page header, card, scroll page), the segmented control,
the sensitivity-curve chart, and the crosshair drawing code that the preview
and PNG export share. Colors come from styles.py so the chrome follows the
active theme, while "stage" surfaces stay dark on purpose.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from fps_coach.crosshair import CrosshairConfig

from .icons import icon, pixmap
from .styles import STAGE, active_palette


_ROLE_KEYS = {"text": "text", "text2": "text2", "accent": "accent", "ink": "accent_ink",
              "good": "good", "warn": "warn", "amber": "amber"}


def _glyph_color(widget: QWidget, role: str | None) -> str:
    if not role:
        role = "ink" if widget.objectName() == "primary" else "text"
    return active_palette()[_ROLE_KEYS.get(role, "text")]


def _paint_glyph(widget: QWidget) -> None:
    name = str(widget.property("glyph") or "")
    size = int(widget.property("glyph_size") or 18)
    role = str(widget.property("glyph_role") or "") or None
    color = _glyph_color(widget, role)
    if isinstance(widget, QAbstractButton):
        widget.setIcon(icon(name, color, size))
        widget.setIconSize(QSize(size, size))
    elif isinstance(widget, QLabel):
        widget.setPixmap(pixmap(name, color, size))


def set_glyph(widget: QWidget, name: str, size: int = 18, role: str | None = None) -> None:
    """Attach a drawn icon to a button or label. It is repainted on theme changes."""
    widget.setProperty("glyph", name)
    widget.setProperty("glyph_size", size)
    widget.setProperty("glyph_role", role or "")
    _paint_glyph(widget)


def refresh_glyphs(root: QWidget) -> None:
    """Repaint every registered glyph under ``root`` in the active theme."""
    for widget in root.findChildren(QWidget):
        if widget.property("glyph"):
            _paint_glyph(widget)


def _settings() -> QSettings:
    # Application-level store (names are set in app.py), so saved keys match.
    return QSettings()


def _label(text, object_name=None, wrap=False):
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    lbl.setWordWrap(wrap)
    return lbl


def _page_header(title, subtitle="", eyebrow=""):
    wrap = QWidget()
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    if eyebrow:
        lay.addWidget(_label(eyebrow.upper(), "eyebrow"))
    lay.addWidget(_label(title, "pageTitle"))
    if subtitle:
        lay.addWidget(_label(subtitle, "pageSubtitle", wrap=True))
    return wrap


def _card(title=None, subtitle=None):
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(12)
    if title:
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(_label(title, "cardTitle"))
        if subtitle:
            head.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        lay.addLayout(head)
    return card, lay


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


def _scroll_page(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(inner)
    return scroll


def _content_page(max_width: int = 1280) -> tuple[QScrollArea, QVBoxLayout]:
    """A scrollable page whose content is centered and capped at ``max_width``.

    Returns the scroll area (add it to the page) and the layout to fill.
    """
    inner = QWidget()
    inner.setObjectName("pageContent")
    row = QHBoxLayout(inner)
    row.setContentsMargins(32, 28, 32, 32)
    row.addStretch(1)
    column = QWidget()
    column.setMaximumWidth(max_width)
    column.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    lay = QVBoxLayout(column)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(20)
    row.addWidget(column, 100)
    row.addStretch(1)
    return _scroll_page(inner), lay


def _page_root(page: QWidget, scroll: QScrollArea) -> None:
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(scroll)


class ValueSlider(QWidget):
    """A horizontal slider with a live readout of its exact value.

    The readout updates on every change, including while the handle is dragged.
    It forwards the small QSlider API the pages use (value, setValue, valueChanged),
    so it can stand in for a plain QSlider. `fmt` turns the integer value into the
    text shown (for example "12 px", "35%", or "0.35").
    """

    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, fmt=str, parent=None):
        super().__init__(parent)
        self._fmt = fmt
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(lo, hi)

        self._readout = QLabel()
        self._readout.setObjectName("sliderValue")
        self._readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Fix the readout width to the widest value in range so the slider does not jump.
        metrics = QFontMetrics(self._readout.font())
        widest = max(metrics.horizontalAdvance(fmt(v)) for v in range(lo, hi + 1))
        self._readout.setMinimumWidth(widest + 4)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._readout)

        self._update_readout(self._slider.value())
        self._slider.valueChanged.connect(self._on_slider_changed)

    def _update_readout(self, value: int) -> None:
        self._readout.setText(self._fmt(value))

    def _on_slider_changed(self, value: int) -> None:
        self._update_readout(value)
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self._slider.value()

    def setValue(self, value: int) -> None:
        self._slider.setValue(value)

    def setToolTip(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setToolTip(text)
        self._slider.setToolTip(text)


class Segmented(QFrame):
    """A row of mutually exclusive text tabs, used for in-page view switching."""

    changed = Signal(int)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for index, text in enumerate(labels):
            btn = QPushButton(text)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, i=index: self.set_index(i))
            lay.addWidget(btn)
            self._buttons.append(btn)
        lay.addStretch(1)
        self.set_index(0, emit=False)

    def set_index(self, index: int, emit: bool = True) -> None:
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        if emit:
            self.changed.emit(index)


# ---------------------------------------------------------------------------
# Sensitivity curve chart
# ---------------------------------------------------------------------------

class CurveLegend(QWidget):
    """Key for the sensitivity chart: which line is the current curve and which is older.

    The current curve is a solid accent line with a dot. The older curve is a dashed
    amber line. When there is no older curve yet, its entry says so instead of hiding.
    """

    _CURRENT = "Current curve"
    _OLDER = "Older curve"
    _OLDER_EMPTY = "Older curve (none yet)"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._has_older = False
        self.setFixedHeight(22)
        self.setToolTip(
            "Current: the curve Export writes. Older: the curve from before the latest "
            "calibration, import, or edit."
        )

    def set_has_older(self, has_older: bool) -> None:
        self._has_older = bool(has_older)
        self.update()

    def paintEvent(self, event):
        pal = active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font_metrics = QFontMetrics(self.font())
        mid = self.height() / 2
        text_rect_h = self.height()
        x = 0.0

        # Current curve: solid accent line with a dot.
        accent = QColor(pal["accent"])
        p.setPen(QPen(accent, 2.5))
        p.drawLine(QPointF(x, mid), QPointF(x + 22, mid))
        p.setBrush(accent)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x + 11, mid), 3, 3)
        x += 28
        p.setPen(QColor(pal["text2"]))
        label = self._CURRENT
        p.drawText(QRectF(x, 0, font_metrics.horizontalAdvance(label) + 4, text_rect_h),
                   Qt.AlignmentFlag.AlignVCenter, label)
        x += font_metrics.horizontalAdvance(label) + 24

        # Older curve: dashed amber line (dimmed when there is none).
        amber = QColor(pal["amber"])
        if not self._has_older:
            amber.setAlpha(110)
        p.setPen(QPen(amber, 2, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x, mid), QPointF(x + 22, mid))
        x += 28
        p.setPen(QColor(pal["text2"]) if self._has_older else QColor(pal["text3"]))
        label = self._OLDER if self._has_older else self._OLDER_EMPTY
        p.drawText(QRectF(x, 0, font_metrics.horizontalAdvance(label) + 4, text_rect_h),
                   Qt.AlignmentFlag.AlignVCenter, label)
        p.end()


class CurveChart(QWidget):
    """Sensitivity curve with an optional previous curve and draggable points.

    Points are normalized gains (0..1) at evenly spaced speed positions. The
    chart paints on the surrounding card, so it has no fill of its own.
    """

    curve_adjusted = Signal(list)

    def __init__(self, parent=None, editable: bool = True):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.points = [0.0, 0.3, 0.55, 0.78, 1.0]
        self.previous_points = None
        self._drag_index = None
        self._editable = editable
        self.setMouseTracking(editable)

    def set_previous(self, points):
        self.previous_points = points
        self.update()

    def _plot_rect(self):
        return self.rect().adjusted(40, 16, -14, -30)

    def paintEvent(self, event):
        pal = active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        grid = QColor(pal["line"])
        p.setPen(QPen(grid, 1))
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(rect.left(), int(y), rect.right(), int(y))
        for i in range(5):
            x = rect.left() + rect.width() * i / 4
            p.drawLine(int(x), rect.top(), int(x), rect.bottom())

        label_color = QColor(pal["text3"])
        p.setPen(label_color)
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        p.drawText(0, rect.top() + 4, 34, 14, Qt.AlignmentFlag.AlignRight, "high")
        p.drawText(0, rect.bottom() - 6, 34, 14, Qt.AlignmentFlag.AlignRight, "low")
        p.drawText(rect.left(), rect.bottom() + 4, 60, 16, Qt.AlignmentFlag.AlignLeft, "slow")
        p.drawText(rect.right() - 60, rect.bottom() + 4, 60, 16, Qt.AlignmentFlag.AlignRight, "fast")

        if self.previous_points:
            self._draw_curve(p, rect, self.previous_points, QColor(pal["amber"]), dots=False, dashed=True)
        self._draw_curve(p, rect, self.points, QColor(pal["accent"]), dots=True, fill=True)
        p.end()

    def _xy(self, rect, i: int, n: int, v: float) -> QPointF:
        x = rect.left() + rect.width() * i / (n - 1)
        y = rect.bottom() - rect.height() * v
        return QPointF(x, y)

    def _draw_curve(self, p, rect, points, color, dots, dashed=False, fill=False):
        n = len(points)
        if n < 2:
            return
        path = QPainterPath()
        for i, v in enumerate(points):
            pt = self._xy(rect, i, n, v)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        if fill:
            area = QPainterPath(path)
            area.lineTo(rect.right(), rect.bottom())
            area.lineTo(rect.left(), rect.bottom())
            area.closeSubpath()
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            top = QColor(color)
            top.setAlpha(60)
            bottom = QColor(color)
            bottom.setAlpha(0)
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, bottom)
            p.fillPath(area, QBrush(grad))
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if dots:
            base = QColor(active_palette()["surface"])
            p.setPen(QPen(color, 1.5))
            p.setBrush(QBrush(base))
            for i, v in enumerate(points):
                p.drawEllipse(self._xy(rect, i, n, v), 4.5, 4.5)

    def mousePressEvent(self, event):
        if not self._editable:
            return
        rect = self._plot_rect()
        n = len(self.points)
        for i, v in enumerate(self.points):
            if (event.pos() - self._xy(rect, i, n, v).toPoint()).manhattanLength() < 14:
                self._drag_index = i
                break

    def mouseMoveEvent(self, event):
        if self._drag_index is None:
            return
        rect = self._plot_rect()
        v = 1.0 - (event.pos().y() - rect.top()) / max(rect.height(), 1)
        v = min(max(v, 0.0), 1.0)
        self.points[self._drag_index] = v
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_index is not None:
            self._drag_index = None
            self.curve_adjusted.emit(list(self.points))


def _curve_samples(points, count: int = 5) -> list[float]:
    """Sample a recommendation curve (x, y pairs) at evenly spaced x for CurveChart."""
    pts = sorted((float(x), float(y)) for x, y in points)
    if len(pts) < 2:
        return [0.0, 0.25, 0.5, 0.75, 1.0]
    samples = []
    for i in range(count):
        x = i / (count - 1)
        if x <= pts[0][0]:
            y = pts[0][1]
        elif x >= pts[-1][0]:
            y = pts[-1][1]
        else:
            y = pts[-1][1]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if x0 <= x <= x1:
                    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
                    y = y0 + t * (y1 - y0)
                    break
        samples.append(min(1.0, max(0.0, y)))
    return samples


def _lerp_factor(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear lookup of ys at x (xs ascending), clamped at the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return ys[-1]


def _apply_chart_edit(profile, baseline: list[float], edited: list[float]):
    """Scale a profile's gains by how far each chart point was dragged (edited / baseline).

    The chart samples the curve at evenly spaced x positions, so each sample gives a
    factor; every profile point gets the factor interpolated at its own speed.
    """
    n = len(edited)
    xs = [i / (n - 1) for i in range(n)]
    factors = [(e / b) if b > 1e-6 else 1.0 for e, b in zip(edited, baseline)]

    def scaled(points):
        if not points:
            return points
        top = max((p.speed for p in points), default=1.0) or 1.0
        return [
            replace(p, multiplier=max(0.05, min(20.0, p.multiplier * _lerp_factor(xs, factors, min(1.0, p.speed / top)))))
            for p in points
        ]

    return replace(profile, horizontal=scaled(profile.horizontal), vertical=scaled(profile.vertical))


# ---------------------------------------------------------------------------
# Crosshair drawing (shared by the preview and PNG export)
# ---------------------------------------------------------------------------

def _draw_crosshair(painter: QPainter, cx: float, cy: float, cfg: CrosshairConfig) -> None:
    """Paint the crosshair centered at (cx, cy)."""
    cfg = cfg.normalized()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(cfg.opacity)
    color = QColor(cfg.color)
    outline = QColor(cfg.outline_color)

    def seg(x1, y1, x2, y2):
        if cfg.outline:
            painter.setPen(QPen(outline, cfg.thickness + 2))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.setPen(QPen(color, cfg.thickness))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    if cfg.shape in ("cross", "cross_dot"):
        g, s = cfg.gap, cfg.size
        seg(cx - g - s, cy, cx - g, cy)
        seg(cx + g, cy, cx + g + s, cy)
        seg(cx, cy - g - s, cx, cy - g)
        seg(cx, cy + g, cx, cy + g + s)
    if cfg.shape == "circle":
        r = cfg.size
        if cfg.outline:
            painter.setPen(QPen(outline, cfg.thickness + 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.setPen(QPen(color, cfg.thickness))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r, r)
    if cfg.shape in ("dot", "cross_dot") and cfg.dot_size > 0:
        if cfg.outline:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(outline)
            painter.drawEllipse(QPointF(cx, cy), cfg.dot_size + 1.5, cfg.dot_size + 1.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(cx, cy), cfg.dot_size, cfg.dot_size)
    painter.setOpacity(1.0)


class CrosshairPreview(QWidget):
    """Crosshair on the always-dark stage surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self._cfg = CrosshairConfig()

    def set_config(self, cfg: CrosshairConfig):
        self._cfg = cfg
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(STAGE["stage_bg"]))
        p.setPen(QPen(QColor(STAGE["stage_grid"]), 1))
        # Faint center guides so the crosshair's alignment is visible.
        cx, cy = self.width() / 2, self.height() / 2
        p.drawLine(int(cx), 0, int(cx), self.height())
        p.drawLine(0, int(cy), self.width(), int(cy))
        _draw_crosshair(p, cx, cy, self._cfg)
        p.end()
