"""Theme-aware window chrome with compact accessible traffic-light controls.

Movement and resizing delegate to the window manager when supported, retaining
native snapping where the platform provides it. A pointer-driven fallback keeps
basic movement and resizing available on platforms without those operations.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QWidget

from .styles import active_palette


class WindowControl(QPushButton):
    """Circular control with an always-visible high-contrast action symbol."""

    def __init__(self, action: str, parent=None):
        super().__init__(parent)
        self.action = action
        self.setObjectName("windowControl")
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._full = False
        self.set_action_state(False)

    def set_action_state(self, full: bool) -> None:
        self._full = full
        label = {"close": "Close window", "minimize": "Minimize window",
                 "fullscreen": "Exit full screen" if full else "Enter full screen"}[self.action]
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor({"close": "#f16c65", "minimize": "#eab85a", "fullscreen": "#65c887"}[self.action])
        if self.underMouse() or self.isDown():
            color = color.lighter(112)
        p.setBrush(color)
        focus_color = QColor("#252b2b" if QColor(active_palette()["surface2"]).lightness() > 128 else "#ffffff")
        p.setPen(QPen(focus_color if self.hasFocus() else color.darker(135), 1.5 if self.hasFocus() else 0.8))
        p.drawEllipse(QRectF(5, 5, 16, 16))
        p.setPen(QPen(QColor("#253026"), 1.25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.action == "close":
            p.drawLine(10, 10, 16, 16)
            p.drawLine(16, 10, 10, 16)
        elif self.action == "minimize":
            p.drawLine(10, 13, 16, 13)
        elif self._full:
            p.drawLine(10, 13, 13, 13)
            p.drawLine(13, 10, 13, 13)
            p.drawLine(13, 13, 16, 16)
        else:
            p.drawLine(10, 16, 16, 10)
            p.drawLine(12, 10, 16, 10)
            p.drawLine(16, 10, 16, 14)
        p.end()


class AppTitleBar(QFrame):
    """Draggable application title strip; buttons do not initiate a drag."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("appTitleBar")
        self.setFixedHeight(36)
        self._drag_offset = None
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 14, 0)
        row.setSpacing(2)
        self.minimize_button = WindowControl("minimize", self)
        self.fullscreen_button = WindowControl("fullscreen", self)
        self.close_button = WindowControl("close", self)
        self.title = QLabel("CurveCoach", self)
        self.title.setObjectName("windowTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.title)
        row.addStretch(1)
        row.addWidget(self.minimize_button)
        row.addWidget(self.fullscreen_button)
        row.addWidget(self.close_button)
        self.close_button.clicked.connect(window.close)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.fullscreen_button.clicked.connect(window.toggle_full_screen)

    def open_window_menu(self, position=None):
        """Nonblocking, keyboard-accessible alternatives to the round controls."""
        w = self.window()
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        restore = menu.addAction("Restore")
        restore.setEnabled(w.isMaximized() or w.isFullScreen() or w.isMinimized())
        restore.triggered.connect(w.showNormal)
        maximize = menu.addAction("Maximize")
        maximize.setEnabled(not w.isMaximized() and not w.isFullScreen())
        maximize.triggered.connect(w.showMaximized)
        minimize = menu.addAction("Minimize")
        minimize.triggered.connect(w.showMinimized)
        full = menu.addAction("Exit full screen" if w.isFullScreen() else "Enter full screen")
        full.triggered.connect(w.toggle_full_screen)
        menu.addSeparator()
        menu.addAction("Close").triggered.connect(w.close)
        self._window_menu = menu
        menu.popup(position if position is not None else self.mapToGlobal(QPoint(0, self.height())))

    def contextMenuEvent(self, event):  # noqa: N802
        self.open_window_menu(event.globalPos())
        event.accept()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            w = self.window()
            if w.isFullScreen():
                event.accept()
                return
            handle = w.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._drag_offset = None
            else:
                self._drag_offset = event.globalPosition().toPoint() - w.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.window().isFullScreen():
            w = self.window()
            w.showNormal() if w.isMaximized() else w.showMaximized()
            self._drag_offset = None
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ResizeShell(QWidget):
    """Six-pixel resizable border around the application content."""

    BORDER = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shell")
        self.setMouseTracking(True)
        self._resize_edges = Qt.Edge(0)
        self._resize_origin = QPoint()
        self._resize_geometry = QRect()

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        if self.window().isFullScreen() or self.window().isMaximized():
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if pos.x() < self.BORDER:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - self.BORDER:
            edges |= Qt.Edge.RightEdge
        if pos.y() < self.BORDER:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - self.BORDER:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _cursor_for(self, edges: Qt.Edge):
        left, right = bool(edges & Qt.Edge.LeftEdge), bool(edges & Qt.Edge.RightEdge)
        top, bottom = bool(edges & Qt.Edge.TopEdge), bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event):  # noqa: N802
        edges = self._edges_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges:
            window = self.window()
            handle = window.windowHandle()
            if handle is None or not handle.startSystemResize(edges):
                self._resize_edges = edges
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geometry = window.geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            window, rect = self.window(), QRect(self._resize_geometry)
            delta = event.globalPosition().toPoint() - self._resize_origin
            if self._resize_edges & Qt.Edge.LeftEdge:
                rect.setLeft(min(rect.left() + delta.x(), rect.right() - window.minimumWidth() + 1))
            if self._resize_edges & Qt.Edge.RightEdge:
                rect.setRight(max(rect.right() + delta.x(), rect.left() + window.minimumWidth() - 1))
            if self._resize_edges & Qt.Edge.TopEdge:
                rect.setTop(min(rect.top() + delta.y(), rect.bottom() - window.minimumHeight() + 1))
            if self._resize_edges & Qt.Edge.BottomEdge:
                rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + window.minimumHeight() - 1))
            window.setGeometry(rect)
            event.accept()
            return
        self.setCursor(self._cursor_for(self._edges_at(event.position().toPoint())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._resize_edges = Qt.Edge(0)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QEvent):  # noqa: N802
        if not self._resize_edges:
            self.unsetCursor()
        super().leaveEvent(event)


class GuideTitleBar(QFrame):
    """Compact draggable guide title with a single reject/close action."""
    def __init__(self, dialog):
        super().__init__(dialog)
        self.setObjectName("appTitleBar")
        self.setFixedHeight(36)
        self._drag_offset = None
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 14, 0)
        row.setSpacing(2)
        self.title = QLabel("Calibration guide", self)
        self.title.setObjectName("windowTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.close_button = WindowControl("close", self)
        self.close_button.setAccessibleName("Close calibration guide")
        self.close_button.setToolTip("Close calibration guide without saving")
        row.addWidget(self.title)
        row.addStretch(1)
        row.addWidget(self.close_button)
        self.close_button.clicked.connect(dialog.reject)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                self._drag_offset = None
            else:
                self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)
