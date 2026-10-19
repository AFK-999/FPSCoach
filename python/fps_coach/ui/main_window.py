"""
Vantage — main application window.

Layout: a horizontal top bar holds the seven working tabs (Dashboard, Capture,
Calibration, Recommendation, Training, Crosshair, AI Coach) and three utility
buttons (Guide, Privacy, Settings). A slim coach strip sits under the bar and
can be collapsed or hidden. Pages fill the rest of the window.

Functional wiring (restored to the original backend behavior):
  * MainWindow(backend) receives the CoreBackend created in app.py and passes
    it to Capture, Calibration and Recommendation.
  * Capture: backend.outputs(), start_capture(), stop_capture(),
    capture_stats(), latest_frame().
  * Calibration: CalibrationCanvas(backend.analyzer) -> completed(summary, rec).
  * Recommendation: backend.analyzer.summary() / recommend().
  * AI Coach: ai_chat (AIChatClient, check_ollama, pull_model, curve_context).
  * Voice and TTS: vosk_service, voice_control, voice_commands, tts_service.
  * Crosshair: CrosshairConfig schema under the "crosshair/config" key.
  * QSettings() uses the application names set in app.py, so saved keys match.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach.analytics import AnalyticsStore
from fps_coach.backend import CoreBackend

from .pages_coach import AIChatPage, PrivacyPage, SettingsPage
from .pages_practice import CrosshairPage, TrainingPage
from .pages_setup import CalibrationPage, CapturePage, DashboardPage, GuidePage, RecommendationPage
from .styles import build_style, fx_enabled, set_active_theme
from .threads import _TtsService, _VoiceCommandService
from .widgets import _label, _settings, refresh_glyphs, set_glyph

ORG_NAME = "VantageLabs"
APP_NAME = "Vantage"

# Working tabs, in order. Ctrl+1 .. Ctrl+7 switch to them.
NAV_TABS = [
    ("dashboard", "Dashboard"),
    ("capture", "Capture"),
    ("calibration", "Calibration"),
    ("recommendation", "Recommendation"),
    ("training", "Training"),
    ("crosshair", "Crosshair"),
    ("chat", "AI Coach"),
]

# Utility buttons on the right of the bar (icon-only, with tooltips).
UTILITY = [
    ("guide", "Guide"),
    ("privacy", "Privacy"),
    ("settings", "Settings"),
]


def _set_state(widget: QWidget, state: str) -> None:
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class CoachStrip(QFrame):
    """The coach as a slim strip under the top bar: latest message, collapse, hide."""

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("coachStrip")
        self._minimized = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 12, 8)
        lay.setSpacing(10)

        glyph = QLabel()
        set_glyph(glyph, "chat", 16, role="accent")
        lay.addWidget(glyph)
        lay.addWidget(_label("Coach", "cardTitle"))

        self.body = QLabel("Ready when you are.")
        self.body.setObjectName("coachText")
        self.body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.body, 1)

        self.min_btn = QToolButton()
        self.min_btn.setObjectName("iconButton")
        self.min_btn.setToolTip("Collapse the coach strip")
        set_glyph(self.min_btn, "chevron_down", 16)
        self.min_btn.clicked.connect(self._toggle_minimize)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("iconButton")
        self.close_btn.setToolTip("Hide the coach strip")
        set_glyph(self.close_btn, "close", 16)
        self.close_btn.clicked.connect(self._on_close)
        lay.addWidget(self.min_btn)
        lay.addWidget(self.close_btn)

    def say(self, text):
        self.body.setText(text)
        self.body.setToolTip(text)

    def _toggle_minimize(self):
        self._minimized = not self._minimized
        self.body.setVisible(not self._minimized)
        set_glyph(self.min_btn, "chevron_up" if self._minimized else "chevron_down", 16)
        self.min_btn.setToolTip("Expand the coach strip" if self._minimized else "Collapse the coach strip")

    def _on_close(self):
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    def __init__(self, backend: CoreBackend | None = None):
        super().__init__()
        self._backend = backend if backend is not None else CoreBackend()
        self.setWindowTitle("Vantage")
        self.resize(1360, 860)
        self.setMinimumSize(1120, 680)
        self.s = _settings()

        self._nav_buttons: dict[str, QPushButton] = {}
        self._util_buttons: dict[str, QToolButton] = {}
        self._voice_service = None
        self._tts_service = None
        self._last_recommendation: dict | None = None
        self._last_summary: dict | None = None

        central = QWidget()
        central.setObjectName("shell")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        self.coach_panel = CoachStrip(self)
        self.coach_panel.closed.connect(self._on_coach_closed)
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", False, type=bool))
        root.addWidget(self.coach_panel)

        self.stack = QStackedWidget()
        self._build_pages()
        root.addWidget(self.stack, 1)

        self._wire_signals()
        self._install_shortcuts()
        self._apply_theme()
        self._sync_tts()
        self._apply_voice_commands()
        self._select_page("dashboard")

    @property
    def backend(self) -> CoreBackend:
        """The CoreBackend this window was built with (same object as _backend)."""
        return self._backend

    # ------------------------------------------------------------------
    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 14, 0)
        lay.setSpacing(6)

        self._brand = QLabel()
        set_glyph(self._brand, "brand", 22, role="accent")
        lay.addWidget(self._brand)
        lay.addWidget(_label("VANTAGE", "brand"))
        lay.addSpacing(22)

        tabs_host = QWidget()
        tabs = QHBoxLayout(tabs_host)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(2)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for key, label in NAV_TABS:
            btn = QPushButton(label)
            btn.setObjectName("navTab")
            btn.setCheckable(True)
            btn.setFixedHeight(60)
            # Reserve room for the bold (checked) weight so the label never clips.
            bold = btn.font()
            bold.setWeight(bold.Weight.DemiBold)
            btn.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(label) + 56)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _c=False, k=key: self._select_page(k))
            self.nav_group.addButton(btn)
            self._nav_buttons[key] = btn
            tabs.addWidget(btn)
        tabs.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("navScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(60)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tabs_host)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(scroll, 1)

        # Native core status (restored from the original): capture is either the
        # real native core or the Python preview mode.
        self.core_status = QLabel("Native core" if self._backend.native_available else "Preview mode")
        self.core_status.setObjectName("pill")
        self.core_status.setFixedHeight(24)
        self.core_status.setToolTip(self._backend.core_label)
        _set_state(self.core_status, "good" if self._backend.native_available else "warn")
        lay.addWidget(self.core_status)
        lay.addSpacing(8)

        for key, label in UTILITY:
            btn = QToolButton()
            btn.setObjectName("utility")
            btn.setCheckable(True)
            btn.setToolTip(label)
            btn.setAccessibleName(label)
            btn.setFixedSize(34, 34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, k=key: self._select_page(k))
            self._util_buttons[key] = btn
            lay.addWidget(btn)
        return bar

    def _build_pages(self) -> None:
        self.dashboard_page = DashboardPage()
        self.guide_page = GuidePage()
        self.capture_page = CapturePage(self._backend)
        self.calibration_page = CalibrationPage(self._backend)
        self._analytics_store = AnalyticsStore.load(
            Path(__file__).resolve().parents[3] / ".fpscoach_analytics.json"
        )
        self.training_page = TrainingPage(self._analytics_store)
        self.recommendation_page = RecommendationPage(self._backend)
        self.crosshair_page = CrosshairPage()
        self.chat_page = AIChatPage()
        self.privacy_page = PrivacyPage(self._analytics_store, self.chat_page)
        self.settings_page = SettingsPage()

        self._pages = {
            "dashboard": self.dashboard_page,
            "guide": self.guide_page,
            "capture": self.capture_page,
            "calibration": self.calibration_page,
            "training": self.training_page,
            "recommendation": self.recommendation_page,
            "crosshair": self.crosshair_page,
            "chat": self.chat_page,
            "privacy": self.privacy_page,
            "settings": self.settings_page,
        }
        for page in self._pages.values():
            self.stack.addWidget(page)

    def _wire_signals(self) -> None:
        self.dashboard_page.navigate.connect(self._select_page)
        self.calibration_page.recommendation_ready.connect(self._on_calibration_result)
        self.calibration_page.profile_changed.connect(self._on_profile_changed)
        self.calibration_page.base_profile_changed.connect(self.chat_page.set_base_profile)
        self.chat_page.curve_adjusted.connect(self._on_curve_adjusted)
        self.recommendation_page.recommendation_ready.connect(self._on_recommendation)
        self.chat_page.assistant_said.connect(self._on_assistant_said)
        self.chat_page.assistant_said.connect(self._speak_response)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.training_page.session_recorded.connect(self._on_session_recorded)
        self.crosshair_page.edited.connect(self._on_crosshair_edited)

    def _install_shortcuts(self) -> None:
        for index, (key, _label_text) in enumerate(NAV_TABS, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda k=key: self._select_page(k))

    # ------------------------------------------------------------------
    def _select_page(self, key):
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        for tab_key, btn in self._nav_buttons.items():
            btn.setChecked(tab_key == key)
        for util_key, btn in self._util_buttons.items():
            btn.setChecked(util_key == key)
        self._refresh_nav_icons()
        if key == "dashboard":
            self._refresh_dashboard()
        elif key == "privacy":
            self.privacy_page.refresh()

    def _refresh_nav_icons(self) -> None:
        """Checked tabs use the strong text color; the rest use the secondary one."""
        for key, btn in self._nav_buttons.items():
            set_glyph(btn, key, 18, role="text" if btn.isChecked() else "text2")
        for key, btn in self._util_buttons.items():
            set_glyph(btn, key, 18, role="text" if btn.isChecked() else "text2")

    def _refresh_dashboard(self) -> None:
        """Fill every dashboard tile from the live state of the other pages."""
        if self._last_summary is not None:
            analyzer_trials = int(self._last_summary.get("trial_count", 0))
        else:
            try:
                analyzer_trials = int(self._backend.analyzer.summary().get("trial_count", 0))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                analyzer_trials = 0
        running = self.capture_page.stop_btn.isEnabled()
        aggregate = self._analytics_store.aggregate()
        model = self.crosshair_page._model
        user_turns = sum(1 for m in self.chat_page._messages if m.get("role") == "user")
        rec = self._last_recommendation

        values = {
            "capture": (
                "Running" if running else "Idle",
                self.capture_page.stats_label.text() or "Start a stream on the Capture page",
            ),
            "calibration": (
                f"{analyzer_trials} targets" if analyzer_trials else "Not run",
                "Curve from the latest run" if analyzer_trials else "Run the 12-target task",
            ),
            "training": (
                f"{aggregate['avg_accuracy'] * 100:.0f}%" if aggregate["total"] else "\u2014",
                (f"{aggregate['total']} sessions \u00b7 avg reaction {aggregate['avg_reaction_ms']:.0f} ms"
                 if aggregate["total"] else "No drills recorded yet"),
            ),
            "recommendation": (
                f"\u00d7{float(rec.get('sensitivity_multiplier', 1.0)):.2f}" if rec else "\u2014",
                (f"Confidence {float(rec.get('confidence', 0.0)):.0%}" if rec
                 else "Generate one after calibration"),
            ),
            "crosshair": (
                model.shape.replace("_", " ").title(),
                f"Color {model.color} \u00b7 opacity {model.opacity:.0%}",
            ),
            "chat": (
                str(user_turns),
                "Messages this session" if user_turns else "Ask the coach a question",
            ),
        }
        self.dashboard_page.set_values(values)

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_theme(theme: str) -> str:
        """Map 'system' to the OS dark/light scheme; 'dark' and 'light' pass through."""
        if theme in ("dark", "light"):
            return theme
        app = QApplication.instance()
        scheme = app.styleHints().colorScheme() if app is not None else None
        return "light" if scheme == Qt.ColorScheme.Light else "dark"

    def _apply_theme(self):
        mode = str(self.s.value("ui/mode", "luxurious"))
        theme = self._resolve_theme(str(self.s.value("ui/theme", "system")))
        set_active_theme(theme)
        self.setStyleSheet(build_style(mode, theme))
        animations = self.s.value("ui/animations", True, type=bool)
        self.training_page.canvas.set_animations(bool(animations) and fx_enabled(mode))
        refresh_glyphs(self)
        self._refresh_nav_icons()
        self.dashboard_page._rebuild_tiles()
        self._refresh_dashboard()

    def _sync_tts(self):
        enabled = self.s.value("ui/tts_enabled", False, type=bool)
        if enabled and self._tts_service is None:
            self._tts_service = _TtsService(self)
            self._tts_service.failed.connect(self._on_tts_failed)
            self._tts_service.start()
        elif not enabled and self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service.wait(3000)
            self._tts_service = None

    def _speak_response(self, text: str):
        if self._tts_service is not None:
            self._tts_service.enqueue(text)

    def _on_tts_failed(self, message: str):
        self.chat_page._append("Text-to-speech", message)

    def _apply_voice_commands(self):
        enabled = self.s.value("ui/voice_commands", False, type=bool)
        if enabled and self._voice_service is None:
            self._voice_service = _VoiceCommandService(self)
            self._voice_service.recognized.connect(self._on_voice_command)
            self._voice_service.failed.connect(self._on_voice_failed)
            self._voice_service.start()
        elif not enabled and self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(1500)
            self._voice_service = None

    # ------------------------------------------------------------------
    def _on_calibration_result(self, summary: dict, recommendation: dict):
        self._last_summary = dict(summary)
        self.recommendation_page.update_result(summary, recommendation)
        self.chat_page.notify_new_calibration(summary, recommendation)
        self._select_page("recommendation")

    def _on_profile_changed(self, profile):
        self.coach_panel.say("Calibration profile updated.")

    def _on_curve_adjusted(self, previous, current, note: str):
        # An AI quick action or directive produced an adjusted curve: show it on the
        # Calibration graph as before/after and switch there.
        self.calibration_page.set_adjusted(previous, current, note)
        self.coach_panel.say(note)
        self._select_page("calibration")

    def _on_recommendation(self, result):
        self._last_recommendation = dict(result)
        mult = float(result.get("sensitivity_multiplier", 1.0))
        confidence = float(result.get("confidence", 0.0))
        self.coach_panel.say(f"New recommendation: \u00d7{mult:.2f} (confidence {confidence:.0%}).")
        self._refresh_dashboard()

    def _on_assistant_said(self, text):
        self.coach_panel.say(text)
        self._refresh_dashboard()

    def _on_settings_changed(self):
        self._apply_theme()
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", False, type=bool))
        self._sync_tts()
        self._apply_voice_commands()

    def _on_session_recorded(self, result):
        self.coach_panel.say(f"Session complete \u2014 {result.accuracy * 100:.0f}% accuracy.")
        self._refresh_dashboard()

    def _on_crosshair_edited(self, config):
        self._refresh_dashboard()

    def _on_voice_failed(self, message: str):
        self.coach_panel.say(f"Voice commands unavailable: {message}")

    def _on_voice_command(self, text: str) -> None:
        from fps_coach.voice_commands import parse

        wake = self.s.value("ui/wake_word", "coach")
        command = parse(text, wake_word=str(wake))
        if command is None:
            return
        if command.kind == "navigate":
            self._select_page(command.value)
        elif command.kind == "calibrate":
            self._select_page("calibration")
            if command.value == "start":
                self.calibration_page.canvas.start_session(12)
            else:
                self.calibration_page.canvas.cancel_session()
        elif command.kind == "preset":
            self._select_page("chat")
            self.chat_page._apply_quick_action(command.value)
        elif command.kind == "panel":
            show = command.value == "show"
            self.s.setValue("ui/coach_panel", show)
            self.coach_panel.setVisible(show)
        elif command.kind == "dictate":
            self._select_page("chat")
            self.chat_page.input.setText(command.value)
            self.chat_page._send()

    def _on_coach_closed(self):
        self.s.setValue("ui/coach_panel", False)

    def closeEvent(self, event):
        if self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(1500)
        if self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service.wait(3000)
        self.chat_page.shutdown()
        self._backend.close()
        self.s.sync()
        super().closeEvent(event)


def main():
    from fps_coach.app import main as app_main

    sys.exit(app_main())


if __name__ == "__main__":
    main()
