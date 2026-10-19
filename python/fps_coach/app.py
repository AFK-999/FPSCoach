from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from fps_coach.backend import CoreBackend
from fps_coach.env_repair import ensure_environment
from fps_coach.ollama_service import ensure_running_async
from fps_coach.ui.main_window import MainWindow
from fps_coach.ui.styles import APP_STYLE


def main() -> int:
    # Auto-repair the editable install if the project folder was moved/renamed.
    # Fast no-op when nothing changed; never raises.
    ensure_environment()

    # Launch the local Ollama service in the background so the AI is ready
    # immediately. Runs on a daemon thread - startup is never blocked, and it is
    # a no-op if Ollama is already running or not installed.
    ensure_running_async()

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
