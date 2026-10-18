from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from fps_coach.backend import CoreBackend
from fps_coach.env_repair import ensure_environment
from fps_coach.ui.main_window import MainWindow
from fps_coach.ui.styles import APP_STYLE


def main() -> int:
    # Auto-repair the editable install if the project folder was moved/renamed.
    # Fast no-op when nothing changed; never raises.
    ensure_environment()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("FPS Coach")
    app.setOrganizationName("FPS Coach")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_STYLE)

    backend = CoreBackend()
    window = MainWindow(backend)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
