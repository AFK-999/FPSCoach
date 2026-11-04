"""Shared building blocks for the Vantage pages.

Holds layout helpers (page header, card, scroll page), the segmented control,
the sensitivity-curve chart, and the crosshair drawing code that the preview
and PNG export share. Colors come from styles.py so the chrome follows the
active theme, while "stage" surfaces stay dark on purpose.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import replace

from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from fps_coach.crosshair import CrosshairConfig
from fps_coach.curve_sampling import KEY_TOLERANCE, key_indices
from fps_coach.curve_spline import cc3_spline, interpolating_segments, weighted_spline

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


class ComboField(QComboBox):
    """A combo box with a chevron drawn at its right edge.

    The stylesheet restyles ``QComboBox::drop-down`` so the field ends flush on the right,
    and on several styles that also removes Qt's own arrow: an editable box then reads as a
    plain text field with no hint that a list is behind it. The chevron is painted here, in
    the active theme's text colour, so every combo box shows the affordance in every theme.
    """

    ARROW_WIDTH = 22  # matches QComboBox::drop-down width in styles.py
    ARROW_SIZE = 16

    def arrow_colour(self) -> str:
        """The theme's text colour, muted while the field is disabled."""
        palette = active_palette()
        return palette["text3"] if not self.isEnabled() else palette["text"]

    def arrow_rect(self) -> QRectF:
        """Where the chevron sits: centred in the drop-down strip on the right."""
        size = float(self.ARROW_SIZE)
        left = float(self.width() - self.ARROW_WIDTH) + (self.ARROW_WIDTH - size) / 2.0
        top = (self.height() - size) / 2.0
        return QRectF(left, top, size, size)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        self._paint_arrow()

    def _paint_arrow(self) -> None:
        chevron = pixmap("chevron_down", self.arrow_colour(), self.ARROW_SIZE)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.arrow_rect().topLeft(), chevron)
        painter.end()


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


HANDLE_DOT_LIMIT = 64  # up to this many handles are drawn as dots; more are still grabbable
MIN_POINTS = 2  # a curve needs two points; the menu refuses to delete below this
MAX_POINTS = 200  # past this a hand-edited curve is unreadable, so adding stops
GRAB_DISTANCE = 14  # px: how close the pointer must be to grab a dot
_HANDLE_RADIUS_FEW = 4.5
_HANDLE_RADIUS_MANY = 3.0


def _positions(values, xs) -> list[float]:
    """x positions for ``values``: ``xs`` when it matches in length, else evenly spaced 0..1."""
    n = len(values)
    if xs is not None and len(xs) == n:
        return [float(x) for x in xs]
    if n < 2:
        return [0.0] * n
    return [i / (n - 1) for i in range(n)]


class CurveChart(QWidget):
    """Sensitivity curve with an optional previous curve and draggable handles.

    The default CC3-style B-spline is approximate, with controls not necessarily on the line.
    The Calibration chart instead interpolates every stored horizontal point and draws all dots
    on that line; neither preview is verified driver output. Coordinates use normalized profile
    speed and gain. Very long curves retain grouped mouse-drag behavior. Drag vertically to
    change gain; drag an interior dot
    horizontally to change speed (the endpoints stay at their original speed bounds). A right-click offers
    adding a point where you clicked and deleting the point under the pointer. Without ``xs`` the
    points are spaced evenly. The chart paints on the surrounding card, so it has no fill of its own.
    """

    curve_adjusted = Signal(list)  # compatibility signal: the edited y values
    point_selected = Signal(int)  # -1 means no selected point
    weight_edit_requested = Signal(int)
    curve_geometry_changed = Signal(list, list)  # y values and normalized x positions
    # Operation, index in the resulting/old curve, normalized x location.
    curve_structure_changed = Signal(str, int, float)

    def __init__(self, parent=None, editable: bool = True, interpolate_points: bool = False):
        super().__init__(parent)
        self._aligned_cc3 = bool(interpolate_points)
        self._interpolate_points = bool(interpolate_points)
        self._graph_style = "cc3"
        self.setMinimumHeight(200)
        self._points = [0.0, 0.3, 0.55, 0.78, 1.0]
        self._xs = None
        self._handles = None  # cached handle_indices(); cleared when the curve is replaced
        self.previous_points = None
        self.previous_xs: list[float] | None = None
        self.weights: list[float] = [0.5] * len(self._points)
        self.previous_weights: list[float] = []
        self._drag_index = None
        self._drag_base = None
        self._drag_x_base = None
        self._drag_handles = None
        self._drag_press = None
        self._hover_index = None
        self._selected_index = None
        self._editable = editable
        self.axis_x_max = None
        self.axis_y_max = None
        self.axis_x_units = "unavailable"
        self._view = (0.0, 1.0, 0.0, 1.0)  # normalized x-min/max, y-min/max
        self._pan_start = None
        self.setMouseTracking(editable)
        self.setToolTip("Hover a control point to inspect its values. Tick labels are rounded.")
        self.setAccessibleDescription(
            "Interpolated point preview, approximate and not verified CC3 driver output." if interpolate_points else
            "CC3-style unweighted spline preview is approximate, not verified driver output."
        )

    def set_graph_style(self, style: str) -> None:
        """Change preview rendering only, without modifying points or weights."""
        self._graph_style = style if style in {"cc3", "cc4"} else "cc3"
        self._interpolate_points = self._aligned_cc3 and self._graph_style == "cc3"
        if self._graph_style == "cc4":
            desc = "CC4-style weighted spline approximation; control dots may be off the curve, not verified driver output."
        elif self._interpolate_points:
            desc = "Interpolated CC3 point preview, approximate and not verified CC3 driver output."
        else:
            desc = "CC3-style unweighted spline preview is approximate, not verified driver output."
        self.setAccessibleDescription(desc)
        self.update()

    def set_editable(self, editable: bool) -> None:
        """Toggle pointer editing while keeping the current curve visible."""
        self._editable = bool(editable)
        self.setMouseTracking(self._editable)
        if not self._editable:
            self._drag_index = None
            self._drag_base = None
            self._drag_x_base = None
            self._drag_handles = None
            self._hover_index = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    @property
    def points(self):
        return self._points

    @points.setter
    def points(self, values):
        self._points = values
        if hasattr(self, "weights") and len(self.weights) != len(values):
            self.weights = [0.5] * len(values)
        self._handles = None

    @property
    def xs(self):
        return self._xs

    @xs.setter
    def xs(self, values):
        self._xs = values
        self._handles = None

    def set_previous(self, points, xs=None):
        self.previous_points = points
        self.previous_xs = list(xs) if xs is not None else None
        self.update()

    def set_curve_weights(self, current=None, previous=None):
        """Set profile weights, defaulting to neutral when unavailable."""
        self.weights = list(current) if current is not None and len(current) == len(self.points) else [0.5] * len(self.points)
        self.previous_weights = (list(previous) if previous is not None and self.previous_points
                                 and len(previous) == len(self.previous_points) else [])
        self.update()

    def x_positions(self) -> list[float]:
        """The x position of each current point (0..1)."""
        return _positions(self._points, self._xs)

    def handle_indices(self) -> list[int]:
        """Indices of the points a user can grab: the bends the curve needs, within KEY_TOLERANCE.

        Cached until the curve is replaced, so a drag keeps the same handles.
        """
        if self._handles is None:
            if len(self._points) < 3:
                self._handles = list(range(len(self._points)))
            else:
                self._handles = key_indices(self.x_positions(), list(self._points), KEY_TOLERANCE)
        return self._handles

    def handles_visible(self) -> bool:
        """True when every bend fits under the dot limit without sampling."""
        return 0 < len(self.handle_indices()) <= HANDLE_DOT_LIMIT

    def dot_indices(self) -> list[int]:
        """Visible handles; a numerically selected point is always included."""
        if self._aligned_cc3 or len(self._points) <= HANDLE_DOT_LIMIT:
            return list(range(len(self._points)))
        handles = self.handle_indices()
        if len(handles) <= HANDLE_DOT_LIMIT:
            shown = list(handles)
        else:
            last = len(handles) - 1
            shown = [handles[round(i * last / (HANDLE_DOT_LIMIT - 1))] for i in range(HANDLE_DOT_LIMIT)]
        selected = self._selected_index
        if selected is not None and 0 <= selected < len(self._points) and selected not in shown:
            if len(shown) >= HANDLE_DOT_LIMIT:
                nearest = min(range(len(shown)), key=lambda i: abs(shown[i] - selected))
                shown[nearest] = selected
            else:
                shown.append(selected)
        return sorted(set(shown))

    def set_axis_scale(self, x_max, y_max, x_units="normalized") -> None:
        """Map normalized widget coordinates to actual profile values for both curves."""
        self.axis_x_max = float(x_max) if x_max is not None and x_max > 0 else None
        self.axis_y_max = float(y_max) if y_max is not None and y_max > 0 else None
        self.axis_x_units = x_units
        self.update()

    @staticmethod
    def _tick_label(value: float, step: float) -> str:
        if not math.isfinite(value):
            return "?"
        if value == 0:
            return "0"
        # Six significant figures preserve common fractional grid values, with
        # compact scientific notation for very large/small profile values.
        return f"{value:.6g}"

    def _plot_rect(self):
        font = QFont(self.font())
        font.setPointSize(8)
        metrics = QFontMetrics(font)
        # Keep geometry stable while panning/zooming even as tick labels gain
        # digits; reserve room for a compact scientific-notation label.
        width = metrics.horizontalAdvance("1.23457e+308")
        left = max(55, min(100, width + 9))
        return self.rect().adjusted(left, 22, -16, -39)

    def paintEvent(self, event):
        pal = active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        grid = QColor(pal["line"])
        p.setPen(QPen(grid, 1))
        # Use as many numeric marks as fit, avoiding overlap in compact charts.
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        x0, x1 = self._view[:2]
        largest = max((p.fontMetrics().horizontalAdvance(
            self._tick_label(self.axis_x_max * (x0 + (x1 - x0) * i / 4), self.axis_x_max / 4))
            for i in range(5)), default=0) if self.axis_x_max else 0
        plot_width, plot_height = self._plot_span(rect)
        divisions = 2 if plot_width / 4 < largest + 20 else 4
        for i in range(divisions + 1):
            y = rect.bottom() - plot_height * i / divisions
            x = rect.left() + plot_width * i / divisions
            p.drawLine(rect.left(), int(y), rect.right(), int(y))
            p.drawLine(int(x), rect.top(), int(x), rect.bottom())

        p.setPen(QColor(pal["text2"]))
        if self.axis_x_max is not None and self.axis_y_max is not None:
            for i in range(divisions + 1):
                y = rect.bottom() - plot_height * i / divisions
                x = rect.left() + plot_width * i / divisions
                x0, x1, y0, y1 = self._view
                yl = self._tick_label(self.axis_y_max * (y0 + (y1 - y0) * i / divisions), self.axis_y_max / divisions)
                xl = self._tick_label(self.axis_x_max * (x0 + (x1 - x0) * i / divisions), self.axis_x_max / divisions)
                p.drawText(0, int(y) - 7, rect.left() - 6, 14, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, yl)
                # Place endpoint labels inside the widget and center intermediate labels.
                label_width = min(self.width(), max(60, largest + 6))
                left = max(0, min(self.width() - label_width, int(x) - label_width // 2))
                p.drawText(left, rect.bottom() + 3, label_width, 14, Qt.AlignmentFlag.AlignHCenter, xl)
        p.drawText(rect.left(), 2, max(0, rect.width()), 15, Qt.AlignmentFlag.AlignLeft, "Sensitivity (×)")
        if self._graph_style == "cc4":
            approximation = "CC4 weighted approx." if rect.width() >= 240 else "CC4 approx."
        else:
            approximation = ("Interpolated · not CC3" if self._interpolate_points else "CC3-style approx.") if rect.width() >= 240 else "approx."
        if self._points and rect.width() >= 125:
            p.drawText(rect.left(), 2, rect.width(), 15, Qt.AlignmentFlag.AlignRight, approximation)
        axis_title = {"normalized": "Input velocity (normalized)", "profile units": "Input velocity (profile)",
                      "unavailable": "Input velocity (no curve)"}.get(self.axis_x_units, "Input velocity (profile)")
        if p.fontMetrics().horizontalAdvance(axis_title) > rect.width():
            axis_title = {"normalized": "Input vel. (norm.)", "profile units": "Input vel. (profile)",
                          "unavailable": "Input vel. (no curve)"}.get(self.axis_x_units, "Input velocity")
        p.drawText(rect.left(), rect.bottom() + 19, max(0, rect.width()), 15,
                   Qt.AlignmentFlag.AlignHCenter, axis_title)

        p.save()
        # The selected handle is larger than a regular dot; allow its full
        # outline beyond the axes while still clipping to the chart widget.
        p.setClipRect(rect.adjusted(-7, -7, 7, 7))
        if self.previous_points:
            self._draw_curve(
                p, rect, self.previous_points, QColor(pal["amber"]),
                dashed=True, xs=_positions(self.previous_points, self.previous_xs), weights=self.previous_weights,
            )
        dots = self.dot_indices()
        self._draw_curve(
            p, rect, self._points, QColor(pal["accent"]),
            dots=bool(dots), fill=True, xs=self.x_positions(), handles=dots,
            hovered=self._hover_index, weights=self.weights,
        )
        p.restore()
        p.end()

    @staticmethod
    def _plot_span(rect):
        # QRect.right()/bottom() are inclusive: width/height are one pixel
        # larger than the distance between the corresponding drawn axes.
        return max(1, rect.right() - rect.left()), max(1, rect.bottom() - rect.top())

    def _pt(self, rect, x: float, v: float) -> QPointF:
        x0, x1, y0, y1 = self._view
        width, height = self._plot_span(rect)
        return QPointF(rect.left() + width * (x - x0) / (x1 - x0),
                       rect.bottom() - height * (v - y0) / (y1 - y0))

    def reset_view(self):
        self._view = (0.0, 1.0, 0.0, 1.0)
        self.update()

    @staticmethod
    def _view_bounds(start, width):
        # The viewport can extend beyond the current data; coordinates remain
        # nonnegative and navigation does not change the stored curve.
        width = min(10000.0, max(0.02, width))
        start = max(0.0, min(1000000.0 - width, start))
        return start, start + width

    def _navigation_zone(self, pos):
        rect = self._plot_rect()
        if rect.contains(pos):
            return "both"
        if rect.left() <= pos.x() <= rect.right() and rect.bottom() < pos.y() <= rect.bottom() + 18:
            return "x"
        if 0 <= pos.x() < rect.left() and rect.top() <= pos.y() <= rect.bottom():
            return "y"
        return None

    def select_point(self, index):
        index = index if index is not None and 0 <= index < len(self._points) else None
        if index != self._selected_index:
            self._selected_index = index
            self.update()

    def _fit_vertical_zoom(self, bottom, height):
        """Scale from the lower edge while keeping both plotted curves in view.

        Zoom is distinct from explicit vertical panning: after a pan has moved
        data outside the frame, the next vertical zoom brings it back into view.
        Points bound the interpolating and positive-weight spline previews.
        """
        values = [v for v in self._points if math.isfinite(v)]
        if self.previous_points:
            values.extend(v for v in self.previous_points if math.isfinite(v))
        if not values:
            return self._view_bounds(bottom, height)
        low, high = max(0.0, min(values)), max(0.0, max(values))
        bottom = min(max(0.0, bottom), low)
        height = max(height, high - bottom)
        return self._view_bounds(bottom, height)

    def wheelEvent(self, event):  # noqa: N802 - Qt API
        pos = event.position().toPoint()
        zone = self._navigation_zone(pos)
        # High-resolution touchpads can send pixel deltas without an angle.
        angle = event.angleDelta().y()
        pixels = event.pixelDelta().y()
        delta = angle if angle else pixels
        if not self._points or zone is None or delta == 0:
            event.ignore()
            return
        rect = self._plot_rect()
        x0, x1, y0, y1 = self._view
        width, _height = self._plot_span(rect)
        fx = min(1.0, max(0.0, (pos.x() - rect.left()) / width))
        # A 10% step is less abrupt than the previous 20%; fractional angle or
        # pixel deltas apply a proportional exponential step without snapping.
        factor = 0.9 ** max(-20.0, min(20.0, delta / 120.0))
        if zone in ("both", "x"):
            new_w = min(10000.0, max(0.02, (x1 - x0) * factor))
            x0, x1 = self._view_bounds(x0 + fx * ((x1 - x0) - new_w), new_w)
        if zone in ("both", "y"):
            new_h = min(10000.0, max(0.02, (y1 - y0) * factor))
            y0, y1 = self._fit_vertical_zoom(y0, new_h)
        self._view = (x0, x1, y0, y1)
        self.update()
        event.accept()

    def _curve_path(self, rect, points, xs, weights=None):
        """Build the actual line path using the same transform as dot centers."""
        path = QPainterPath(self._pt(rect, min(0.0, self._view[0]), points[0]))
        pairs = list(zip(xs, points, strict=True))
        if self._interpolate_points:
            path.lineTo(self._pt(rect, *pairs[0]))
            for segment in interpolating_segments(pairs):
                if len(segment) == 1:
                    path.lineTo(self._pt(rect, *segment[0]))
                else:
                    path.cubicTo(*(self._pt(rect, *point) for point in segment))
        else:
            if self._graph_style == "cc4":
                samples = weighted_spline(pairs, weights if weights is not None else self.weights)
            else:
                samples = cc3_spline(pairs)
            for x, value in samples:
                path.lineTo(self._pt(rect, x, value))
        # End at the last actual control point. Do not extrapolate a flat
        # upper-speed segment just because the viewport extends farther right.
        return path

    def _draw_curve(self, p, rect, points, color, dots=False, dashed=False, fill=False, xs=None,
                    handles=(), hovered=None, weights=None):
        n = len(points)
        if n < 2:
            return
        xs = _positions(points, xs)
        path = self._curve_path(rect, points, xs, weights)
        if fill:
            area = QPainterPath(path)
            area.lineTo(self._pt(rect, xs[-1], 0.0).x(), rect.bottom())
            area.lineTo(self._pt(rect, 0.0, 0.0).x(), rect.bottom())
            area.closeSubpath()
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            top = QColor(color)
            top.setAlpha(60)
            bottom = QColor(color)
            bottom.setAlpha(0)
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, bottom)
            p.fillPath(area, QBrush(grad))
        if dots and not self._interpolate_points and len(points) <= HANDLE_DOT_LIMIT:
            control = QPainterPath(self._pt(rect, xs[0], points[0]))
            for x, value in zip(xs[1:], points[1:], strict=True):
                control.lineTo(self._pt(rect, x, value))
            guide = QColor(color)
            guide.setAlpha(100)
            control_pen = QPen(guide, 1.0, Qt.PenStyle.DashLine)
            p.setPen(control_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(control)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if dots and handles:
            radius = _HANDLE_RADIUS_FEW if len(handles) <= 32 else _HANDLE_RADIUS_MANY
            base = QColor(active_palette()["surface"])
            p.setPen(QPen(color, 1.5))
            p.setBrush(QBrush(color if self._interpolate_points else base))
            for i in handles:
                if i == hovered or i == self._selected_index:
                    # The dot under the pointer fills in, so it is obvious what a drag will take.
                    p.setBrush(QBrush(color))
                    p.drawEllipse(self._pt(rect, xs[i], points[i]), radius + 1.5, radius + 1.5)
                    p.setBrush(QBrush(color if self._interpolate_points else base))
                    continue
                p.drawEllipse(self._pt(rect, xs[i], points[i]), radius, radius)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.axis_x_max and self.axis_y_max:
            index = self._point_at(event.pos())
            if self._graph_style == "cc4":
                description = "CC4-style weighted spline approximation; points may be off the line. Not verified driver output."
            elif self._interpolate_points:
                description = "Interpolated through stored points; not verified CC3 driver output."
            else:
                description = "CC3-style unweighted spline approximation; points may be off the line."
            if index is not None:
                velocity = self.x_positions()[index] * self.axis_x_max
                sensitivity = self.points[index] * self.axis_y_max
                weight = f"Weight: {self.weights[index]:.12g}\n" if self._graph_style == "cc4" and index < len(self.weights) else ""
                text = (f"Control point velocity: {velocity:.12g} ({self.axis_x_units})\n"
                        f"Control point sensitivity: {sensitivity:.12g}×\n" + weight + description)
            else:
                text = ((self.toolTip() + "\n" if self.toolTip() else "") + description +
                        " CC3 corner flags and driver settings are not simulated. "
                        "Wheel: zoom with vertical curve fit; drag empty space or axes to pan; middle click: reset.")
            QToolTip.showText(event.globalPos(), text, self)
            return True
        return super().event(event)

    def _point_at(self, pos) -> int | None:
        """The nearest draggable dot within GRAB_DISTANCE px of ``pos``, or None."""
        rect = self._plot_rect()
        if not rect.adjusted(-5, -5, 5, 5).contains(pos):
            return None
        xs = self.x_positions()
        best, best_distance = None, GRAB_DISTANCE
        for i in self.dot_indices():
            distance = (pos - self._pt(rect, xs[i], self._points[i]).toPoint()).manhattanLength()
            if distance < best_distance:
                best, best_distance = i, distance
        return best

    def _handle_at(self, pos) -> int | None:
        """Alias of _point_at, kept because older callers used the handle name."""
        return self._point_at(pos)

    def value_at(self, pos) -> tuple[float, float] | None:
        """Normalized profile coordinates under ``pos`` (possibly >1), or None outside the plot."""
        rect = self._plot_rect()
        # A px of slack: a click on the very edge of the plot lands one pixel outside the rect
        # after rounding, and "the top of the graph" is exactly where a point at 1.0 is drawn.
        if not rect.adjusted(-1, -1, 1, 1).contains(pos) or rect.width() <= 0 or rect.height() <= 0:
            return None
        width, height = self._plot_span(rect)
        x = (pos.x() - rect.left()) / width
        value = 1.0 - (pos.y() - rect.top()) / height
        x0, x1, y0, y1 = self._view
        return (max(0.0, x0 + x * (x1 - x0)),
                max(0.0, y0 + value * (y1 - y0)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.reset_view()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        zone = self._navigation_zone(pos)
        self._drag_index = self._point_at(pos) if self._editable and zone not in ("x", "y") else None
        if self._drag_index is None:
            self._drag_base = None
            self._drag_x_base = None
            self._drag_handles = None
            if self._points and zone is not None:
                self._pan_start = (event.position(), self._view, zone)
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        self.select_point(self._drag_index)
        self.point_selected.emit(self._drag_index)
        self._drag_base = list(self._points)
        self._drag_x_base = self.x_positions()
        self._drag_press = event.position()
        self._drag_handles = list(self.handle_indices())
        shape = (
            Qt.CursorShape.SizeVerCursor
            if self._drag_index in {0, len(self._points) - 1}
            else Qt.CursorShape.SizeAllCursor
        )
        self.setCursor(QCursor(shape))

    def mouseMoveEvent(self, event):
        if self._pan_start is not None:
            start, (x0, x1, y0, y1), zone = self._pan_start
            delta = event.position() - start
            rect = self._plot_rect()
            width, height = self._plot_span(rect)
            nx0, nx1 = (self._view_bounds(x0 - delta.x() / width * (x1 - x0), x1 - x0)
                        if zone in ("both", "x") else (x0, x1))
            ny0, ny1 = (self._view_bounds(y0 + delta.y() / height * (y1 - y0), y1 - y0)
                        if zone in ("both", "y") else (y0, y1))
            self._view = (nx0, nx1, ny0, ny1)
            self.update()
            return
        if not self._editable:
            return
        if self._drag_index is None or self._drag_base is None:
            self._update_hover(event.position().toPoint())
            return
        rect = self._plot_rect()
        x0, x1, y0, y1 = self._view
        width, height = self._plot_span(rect)
        v = y0 + (1.0 - (event.position().y() - rect.top()) / height) * (y1 - y0)
        v = min(max(v, 0.0), 1.0)
        base_xs = self._drag_x_base or self.x_positions()
        index = self._drag_index
        if index == 0 or index == len(base_xs) - 1:
            # Keep the ends pinned to their original speed bounds; interior points can move on x.
            x = base_xs[index]
        else:
            x = x0 + (event.position().x() - rect.left()) / width * (x1 - x0)
            x = min(max(x, base_xs[index - 1]), base_xs[index + 1])
            x = min(max(x, 0.0), 1.0)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._drag_press is not None:
            delta = event.position() - self._drag_press
            if abs(delta.x()) >= abs(delta.y()):
                v = self._drag_base[index]
            else:
                x = base_xs[index]
        self._move_handle(index, v)
        moved_xs = list(base_xs)
        moved_xs[index] = x
        self._xs = moved_xs  # explicit positions; leave the cached handles stable during a drag
        self.update()

    def _update_hover(self, pos) -> None:
        """Fill in the dot under the pointer and show a move cursor over it."""
        index = self._point_at(pos) if self._editable else None
        if index == self._hover_index:
            return
        self._hover_index = index
        if index is None:
            shape = Qt.CursorShape.ArrowCursor
        elif index in {0, len(self._points) - 1}:
            shape = Qt.CursorShape.SizeVerCursor
        else:
            shape = Qt.CursorShape.SizeAllCursor
        self.setCursor(QCursor(shape))
        self.update()

    def leaveEvent(self, event):  # noqa: N802 - Qt API
        if self._hover_index is not None:
            self._hover_index = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.update()

    def _move_handle(self, index: int, value: float, handles=None) -> None:
        """Set point ``index`` to ``value``. Points between bends follow it smoothly.

        Works from the curve as it was at the press, so repeated moves do not compound. A zero-gain
        dot uses a smooth additive offset (a ratio would leave zero stuck at zero). ``handles``
        defaults to the curve's bends plus the point being dragged, so a dot that is not a bend moves.
        """
        base = self._drag_base
        if base is None:
            return
        # CC3-style editing moves one control point; a very long legacy curve
        # still uses its displayed handle groups to remain practical to edit.
        if len(base) <= HANDLE_DOT_LIMIT:
            self._points[:] = base
            self._points[index] = min(1.0, max(0.0, value))
            return
        old = base[index]
        if handles is None:
            handles = self._drag_handles if self._drag_handles is not None else self.handle_indices()
        if index not in handles:
            handles = sorted({*handles, index})
        xs = self._drag_x_base or self.x_positions()
        handle_xs = [xs[i] for i in handles]
        if old <= 1e-6:
            handle_offsets = [0.0] * len(handles)
            handle_offsets[handles.index(index)] = value - old
            for i, (point, x) in enumerate(zip(base, xs, strict=False)):
                adjusted = point + _lerp_factor(handle_xs, handle_offsets, x)
                self._points[i] = min(1.0, max(0.0, adjusted))
            return
        handle_factors = [1.0] * len(handles)
        handle_factors[handles.index(index)] = value / old
        for i, (point, x) in enumerate(zip(base, xs, strict=False)):
            scaled = point * _lerp_factor(handle_xs, handle_factors, x)
            self._points[i] = min(1.0, max(0.0, scaled))

    def mouseReleaseEvent(self, event):
        if self._pan_start is not None:
            self._pan_start = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
        if self._drag_index is not None:
            self._drag_index = None
            self._drag_base = None
            self._drag_x_base = None
            self._drag_handles = None
            self._drag_press = None
            self._handles = None
            if self._hover_index is None:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            values = list(self._points)
            self.curve_geometry_changed.emit(values, self.x_positions())
            self.curve_adjusted.emit(values)

    # -- adding and removing points -------------------------------------------

    def _addable_value(self, pos):
        spot = self.value_at(pos)
        if spot is None:
            return None
        x, value = spot
        rect = self._plot_rect()
        width, height = self._plot_span(rect)
        tolerance_x = 2 * (self._view[1] - self._view[0]) / width
        tolerance_y = 2 * (self._view[3] - self._view[2]) / height
        if x > 1.0 + tolerance_x or value > 1.0 + tolerance_y:
            return None  # use numeric editing to expand the profile's domain
        return min(1.0, x), min(1.0, value)

    def add_point(self, pos) -> int | None:
        """Insert a point at ``pos``. Returns its index, or None when it cannot go there.

        The click sets both the speed (x) and the gain (value) of the new point, so a point can be
        put exactly where the curve needs one rather than only near where a dot happens to be.
        The x positions become explicit: an evenly spaced curve gains a point and keeps every
        other point where it was.
        """
        if not self._editable or len(self._points) >= MAX_POINTS:
            return None
        spot = self._addable_value(pos)
        if spot is None:
            return None
        x, value = spot
        xs = self.x_positions()
        points = list(self._points)
        index = bisect.bisect_right(xs, x)
        xs.insert(index, x)
        points.insert(index, value)
        weights = list(self.weights)
        weights.insert(index, 0.5)
        self._xs = xs
        self.points = points  # the setter drops the cached handles
        self.weights = weights
        self._hover_index = index
        self.update()
        self.curve_structure_changed.emit("add", index, x)
        values = list(self._points)
        self.curve_geometry_changed.emit(values, self.x_positions())
        self.curve_adjusted.emit(values)
        self.select_point(index)
        self.point_selected.emit(index)
        return index

    def remove_point(self, index: int | None = None, pos=None) -> bool:
        """Delete a point. ``index`` wins; otherwise the dot under ``pos`` is used.

        Refuses to go below MIN_POINTS: a curve needs two ends, and the page can still Undo.
        """
        if not self._editable:
            return False
        if index is None:
            index = self._point_at(pos) if pos is not None else None
        if index is None or not 0 <= index < len(self._points):
            return False
        if len(self._points) <= MIN_POINTS:
            return False
        xs = self.x_positions()
        deleted_x = xs[index]
        points = list(self._points)
        del xs[index]
        del points[index]
        weights = list(self.weights)
        del weights[index]
        self._xs = xs if len(xs) == len(points) else None
        self.points = points
        self.weights = weights
        self._hover_index = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()
        self.curve_structure_changed.emit("delete", index, deleted_x)
        values = list(self._points)
        self.curve_geometry_changed.emit(values, self.x_positions())
        self.curve_adjusted.emit(values)
        self.select_point(None)
        self.point_selected.emit(-1)
        return True

    def build_menu(self, pos) -> QMenu:
        """The right-click menu for ``pos``, with the actions this position allows."""
        menu = QMenu(self)
        palette = active_palette()
        menu.setStyleSheet(
            f"QMenu {{ background: {palette['surface2']}; color: {palette['text']}; "
            f"border: 1px solid {palette['line2']}; padding: 4px; }}"
            "QMenu::item { padding: 7px 16px; border-radius: 4px; }"
            f"QMenu::item:selected {{ background: {palette['raised']}; }}"
            f"QMenu::item:disabled {{ color: {palette['text3']}; }}"
            f"QMenu::separator {{ height: 1px; background: {palette['line']}; margin: 4px 8px; }}"
        )
        index = self._point_at(pos)
        inside = self._addable_value(pos) is not None

        add = menu.addAction("Add point here")
        add.setEnabled(self._editable and bool(inside) and len(self._points) < MAX_POINTS)
        add.setToolTip("Place a point at that speed and gain")
        menu.addSeparator()
        remove = menu.addAction("Delete point")
        remove.setEnabled(self._editable and index is not None and len(self._points) > MIN_POINTS)
        remove.setToolTip("Delete the point under the pointer")
        if self._graph_style == "cc4":
            edit_weight = menu.addAction("Edit point weight…")
            edit_weight.setEnabled(self._editable and index is not None)
            edit_weight.setToolTip("Select this control and edit its weight below the graph")
            edit_weight.triggered.connect(lambda: self.weight_edit_requested.emit(index) if index is not None else None)
        add.triggered.connect(lambda: self.add_point(pos))
        remove.triggered.connect(lambda: self.remove_point(index, pos=pos))
        return menu

    def contextMenuEvent(self, event):  # noqa: N802 - Qt API
        if not self._editable:
            return
        menu = self.build_menu(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()


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
            for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
                if x0 <= x <= x1:
                    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
                    y = y0 + t * (y1 - y0)
                    break
        samples.append(min(1.0, max(0.0, y)))
    return samples


def _curve_points(points) -> tuple[list[float], list[float]]:
    """Every point of a curve as (xs, ys), sorted by x and clamped to 0..1. Nothing is resampled.

    Fewer than two points give the same default shape as _curve_samples.
    """
    pts = sorted(((float(x), float(y)) for x, y in points), key=lambda pair: pair[0])
    if len(pts) < 2:
        fallback = [0.0, 0.25, 0.5, 0.75, 1.0]
        return list(fallback), list(fallback)
    xs = [min(1.0, max(0.0, x)) for x, _ in pts]
    ys = [min(1.0, max(0.0, y)) for _, y in pts]
    return xs, ys


def _lerp_factor(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear lookup of ys at x (xs ascending), clamped at the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys, strict=False), zip(xs[1:], ys[1:], strict=False), strict=False):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return ys[-1]


def _apply_chart_edit(profile, baseline: list[float], edited: list[float], xs=None, scale=None):
    """Scale a profile's gains by how far each chart point was dragged (edited / baseline).

    ``xs`` are the chart's x positions, as speed over ``scale`` (the curve's top speed when not
    given). Evenly spaced when ``xs`` is missing. Each profile point takes the factor at its own
    speed: a point that sits on a chart point gets exactly that point's factor, so dragging one
    point changes only that point.
    """
    xs = _positions(edited, xs)
    factors = [(e / b) if b > 1e-6 else 1.0 for e, b in zip(edited, baseline, strict=False)]
    zero_offsets = [(e - b) if b <= 1e-6 else 0.0 for e, b in zip(edited, baseline, strict=False)]
    chart_gain = max(baseline, default=0.0)

    def scaled(points):
        if not points:
            return points
        top = scale or (max((p.speed for p in points), default=1.0) or 1.0)
        gain_scale = (max((p.multiplier for p in points), default=1.0) / chart_gain
                      if chart_gain > 1e-6 else 1.0)
        updated = []
        for point in points:
            x = min(1.0, point.speed / top)
            multiplier = (
                point.multiplier * _lerp_factor(xs, factors, x)
                + _lerp_factor(xs, zero_offsets, x) * gain_scale
            )
            updated.append(replace(point, multiplier=max(0.05, min(20.0, multiplier))))
        return updated

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
