"""Optional self-reported flick motion selector and general trade-off guidance."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach.aiming_motion import BY_KEY, OPTIONS

from .styles import active_palette
from .widgets import _label


def _motion_icon(key: str) -> QIcon:
    """Small abstract motion diagrams; they are not anatomical measurements."""
    dark = QColor(active_palette()["input"]).lightness() < 128
    colors = {
        "wrist": ("#87d5df", "#317788"),
        "fingers": ("#d0b4f5", "#7b58a4"),
        "arm": ("#f0bd76", "#98621e"),
        "shoulder_hybrid": ("#bcd978", "#66851d"),
    }
    accent = QColor(colors[key][0 if dark else 1])
    muted = QColor("#84909b" if dark else "#66717d")
    image = QPixmap(112, 88)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(muted, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if key in ("wrist", "fingers"):
        p.drawLine(QPointF(6, 35), QPointF(22, 29))
        p.drawLine(QPointF(22, 29), QPointF(33, 25))
        p.drawLine(QPointF(33, 25), QPointF(43, 12))
        p.drawLine(QPointF(32, 26), QPointF(38, 12))
        p.drawLine(QPointF(31, 27), QPointF(33, 14))
        p.drawLine(QPointF(34, 28), QPointF(47, 21))
        p.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if key == "wrist":
            p.drawEllipse(QPointF(22, 29), 4, 4)
            path = QPainterPath(QPointF(17, 18))
            path.quadTo(24, 11, 32, 17)
            p.drawPath(path)
        else:
            for a, b in (((33, 25), (43, 12)), ((32, 26), (38, 12)), ((31, 27), (33, 14))):
                p.drawLine(QPointF(*a), QPointF(*b))
            p.drawEllipse(QPointF(40, 10), 2, 2)
    elif key == "arm":
        p.drawLine(QPointF(7, 32), QPointF(23, 32))
        p.drawLine(QPointF(23, 32), QPointF(37, 17))
        p.drawLine(QPointF(37, 17), QPointF(49, 13))
        p.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(QPointF(23, 32), 4, 4)
        p.drawLine(QPointF(23, 32), QPointF(37, 17))
        p.drawArc(29, 22, 17, 16, 20 * 16, 100 * 16)
    else:
        p.drawLine(QPointF(8, 36), QPointF(16, 15))
        p.drawLine(QPointF(16, 15), QPointF(29, 27))
        p.drawLine(QPointF(29, 27), QPointF(46, 17))
        p.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for point in ((16, 15), (29, 27), (46, 17)):
            p.drawEllipse(QPointF(*point), 3.4, 3.4)
        p.drawArc(4, 4, 24, 23, 30 * 16, 115 * 16)
    p.end()
    return QIcon(image)


class AimingMotionSelector(QFrame):
    """Four exclusive optional selections, with non-AI educational guidance."""

    selection_changed = Signal(str)  # empty string = no selection
    ask_ai = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._selection = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        head = QHBoxLayout()
        head.addWidget(_label("What moves when you flick?", "cardTitle"))
        head.addStretch(1)
        head.addWidget(_label("OPTIONAL · THIS SESSION", "eyebrow"))
        layout.addLayout(head)
        self.intro = _label(
            "Choose your primary motion; most people use a mix. This is self-reported and informs "
            "Coach context only—it does not change your measured curve.", "cardSubtitle", wrap=True,
        )
        layout.addWidget(self.intro)
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for option in OPTIONS:
            button = QToolButton()
            button.setObjectName("motionChoice")
            button.setText(f"{option.label}\n{option.caption}")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(56, 44))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.setAccessibleName(f"{option.label} primary flick motion")
            button.setToolTip(f"Select {option.label} as your self-reported primary motion.")
            button.clicked.connect(lambda _checked=False, key=option.key: self.set_selection(key))
            self.group.addButton(button)
            self.buttons[option.key] = button
            cards.addWidget(button, 1)
        layout.addLayout(cards)
        self.details_stack = QStackedWidget()
        placeholder = _label("Select an option to see general flick, tracking and fatigue trade-offs.", "cardSubtitle", wrap=True)
        self.details_stack.addWidget(placeholder)
        details = QWidget()
        detail_row = QHBoxLayout(details)
        detail_row.setContentsMargins(0, 0, 0, 0)
        detail_row.setSpacing(18)
        self.detail_labels: dict[str, QLabel] = {}
        for key, title in (("flick", "FLICK"), ("tracking", "TRACKING"), ("fatigue", "FATIGUE")):
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(_label(title, "eyebrow"))
            text = _label("", "cardSubtitle", wrap=True)
            text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            column.addWidget(text, 1)
            self.detail_labels[key] = text
            detail_row.addLayout(column, 1)
        self.details_stack.addWidget(details)
        layout.addWidget(self.details_stack)
        footer = QHBoxLayout()
        self.disclaimer = _label("General guidance (not AI or a diagnosis); results vary. Discuss opens a draft, not a request.", "cardSubtitle", wrap=True)
        footer.addWidget(self.disclaimer, 1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.clicked.connect(self.clear_selection)
        self.ask_btn = QPushButton("Discuss with AI Coach")
        self.ask_btn.setObjectName("secondary")
        self.ask_btn.setToolTip("Opens AI Coach with a draft question. Send it to your configured provider when ready.")
        self.ask_btn.clicked.connect(lambda: self.ask_ai.emit(self._selection))
        footer.addWidget(self.clear_btn)
        footer.addWidget(self.ask_btn)
        layout.addLayout(footer)
        self.refresh_icons()
        self._show_selection()

    @property
    def selection(self) -> str:
        return self._selection

    def refresh_icons(self) -> None:
        for key, button in self.buttons.items():
            button.setIcon(_motion_icon(key))

    def set_selection(self, key: str) -> None:
        if key not in BY_KEY:
            raise ValueError("Unknown aiming motion")
        if self._selection == key:
            self.buttons[key].setChecked(True)
            return
        self._selection = key
        self.buttons[key].setChecked(True)
        self._show_selection()
        self.selection_changed.emit(key)

    def clear_selection(self) -> None:
        if not self._selection:
            return
        self.group.setExclusive(False)
        for button in self.buttons.values():
            button.setChecked(False)
        self.group.setExclusive(True)
        self._selection = ""
        self._show_selection()
        self.selection_changed.emit("")

    def _show_selection(self) -> None:
        option = BY_KEY.get(self._selection)
        self.details_stack.setCurrentIndex(1 if option else 0)
        self.ask_btn.setEnabled(option is not None)
        self.clear_btn.setEnabled(option is not None)
        if option:
            for key, label in self.detail_labels.items():
                label.setText(getattr(option, key))
