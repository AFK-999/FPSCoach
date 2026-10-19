"""On-screen crosshair overlay: a transparent, click-through, always-on-top window.

Off by default. When the user turns on Activation on the Crosshair page, this window
draws the crosshair at the centre of the primary display. It only draws; it does not
read the screen, the game, or any process, and it never takes focus or mouse input.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QWidget

from fps_coach.crosshair import CrosshairConfig

from .widgets import _draw_crosshair

_MIN_SIDE = 32


def overlay_side(cfg: CrosshairConfig) -> int:
    """Edge length of the square window that holds the crosshair with its outline."""
    reach = cfg.size + cfg.gap + max(cfg.thickness, cfg.dot_size) + 6
    return max(_MIN_SIDE, 2 * reach + 2)


class CrosshairOverlay(QWidget):
    def __init__(self, config: CrosshairConfig, parent: QWidget | None = None):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._cfg = config
        self._place()

    def set_config(self, config: CrosshairConfig) -> None:
        self._cfg = config
        self._place()
        self.update()

    def _place(self) -> None:
        """Centre the square on the primary display."""
        side = overlay_side(self._cfg)
        screen = QApplication.primaryScreen()
        centre = screen.availableGeometry().center() if screen else QPoint(640, 360)
        self.setGeometry(QRect(centre.x() - side // 2, centre.y() - side // 2, side, side))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        half = self.width() / 2
        _draw_crosshair(painter, half, half, self._cfg)
        painter.end()
