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
from .crosshair_overlay import CrosshairOverlay
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

        topbar = self._build_topbar()
        root.addWidget(topbar)
        # The window cannot be narrower than the top bar needs: brand, every tab (so AI Coach
        # is never cut off), the core dot, and the utility buttons. Measured from the layout.
        self.setMinimumWidth(topbar.layout().totalMinimumSize().width())

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
            # Bold label width + icon, icon spacing, the 12 px side padding, and a margin so
            # the active (bold) label is never clipped.
            btn.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(label) + 60)
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
        # The tab strip never gets narrower than its tabs, so the window cannot shrink
        # to a width where the last tab (AI Coach) is cut off or needs scrolling.
        tab_buttons = list(self._nav_buttons.values())
        scroll.setMinimumWidth(sum(b.minimumWidth() for b in tab_buttons) + tabs.spacing() * (len(tab_buttons) - 1))
        lay.addWidget(scroll, 1)

        # Native core status (restored from the original): capture is either the
        # real native core or the Python preview mode.
        # Shown as a small status dot so it does not take room from the tabs.
        # The full text is in the tooltip and on the Capture page.
        core_text = "Native core" if self._backend.native_available else "Preview mode"
        self.core_status = QLabel()
        self.core_status.setObjectName("coreDot")
        self.core_status.setFixedSize(10, 10)
        self.core_status.setToolTip(f"{core_text}: {self._backend.core_label}")
        self.core_status.setAccessibleName(f"Capture core: {core_text}")
        _set_state(self.core_status, "good" if self._backend.native_available else "warn")
        lay.addWidget(self.core_status)
        lay.addSpacing(12)

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
        self.chat_page.read_aloud_changed.connect(self._on_read_aloud_changed)
        self.chat_page.stop_speaking_requested.connect(self._on_stop_speaking)
        self.chat_page.speech_options_changed.connect(self._on_speech_options_changed)
        self.chat_page.voice_test_requested.connect(self._on_voice_test)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.training_page.session_recorded.connect(self._on_session_recorded)
        self.crosshair_page.edited.connect(self._on_crosshair_edited)
        self.crosshair_page.activation_changed.connect(self._set_crosshair_overlay)
        self.crosshair_page.config_changed.connect(self._on_crosshair_config)
        self.crosshair_page.edit_mode_changed.connect(self._on_crosshair_edit_mode)
        self.crosshair_page.position_reset.connect(self._on_crosshair_position_reset)
        # The overlay is created only when Activation is on or the position is being
        # edited. Activation is saved (default off). Edit mode is never saved: it starts locked.
        self._overlay: CrosshairOverlay | None = None
        if self.crosshair_page.activation.isChecked():
            self._set_crosshair_overlay(True)

    def _ensure_overlay(self) -> CrosshairOverlay:
        if self._overlay is None:
            # No parent: the overlay must stay an independent window (see crosshair_overlay).
            self._overlay = CrosshairOverlay(
                self.crosshair_page._model, centre=self._saved_overlay_centre()
            )
            self._overlay.position_committed.connect(self._save_overlay_centre)
        return self._overlay

    def _saved_overlay_centre(self):
        from PySide6.QtCore import QPoint

        if not (self.s.contains("crosshair/overlay_x") and self.s.contains("crosshair/overlay_y")):
            return None
        return QPoint(int(self.s.value("crosshair/overlay_x")), int(self.s.value("crosshair/overlay_y")))

    def _save_overlay_centre(self, centre) -> None:
        self.s.setValue("crosshair/overlay_x", int(centre.x()))
        self.s.setValue("crosshair/overlay_y", int(centre.y()))

    def _set_crosshair_overlay(self, active: bool) -> None:
        if active:
            overlay = self._ensure_overlay()
            overlay.set_config(self.crosshair_page._model)
            overlay.show()
        elif self._overlay is not None and not self._overlay.editing:
            self._overlay.hide()
        self._refresh_dashboard()

    def _on_crosshair_edit_mode(self, editing: bool) -> None:
        overlay = self._ensure_overlay()
        overlay.set_config(self.crosshair_page._model)
        overlay.set_editing(editing)
        if editing or self.crosshair_page.activation.isChecked():
            overlay.show()
        else:
            overlay.hide()  # locked and not activated: nothing should be on screen

    def _on_crosshair_position_reset(self) -> None:
        for key in ("crosshair/overlay_x", "crosshair/overlay_y"):
            self.s.remove(key)
        overlay = self._ensure_overlay()
        overlay.reset_centre()

    def _on_crosshair_config(self, config) -> None:
        if self._overlay is not None:
            self._overlay.set_config(config)
        self._refresh_dashboard()

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
                f"Overlay {'on' if self.crosshair_page.activation.isChecked() else 'off'}"
                f" \u00b7 {model.color} \u00b7 opacity {model.opacity:.0%}",
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
            self._tts_service = _TtsService(self, options=self.chat_page.speech_options())
            self._tts_service.voices_ready.connect(self.chat_page.set_voices)
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
        self.chat_page.set_speech_failed()

    def _on_read_aloud_changed(self, enabled: bool):
        """The chat's switch and the Settings checkbox share one saved value."""
        self._sync_tts()
        self.settings_page.tts_enabled.blockSignals(True)
        self.settings_page.tts_enabled.setChecked(bool(enabled))
        self.settings_page.tts_enabled.blockSignals(False)

    def _on_speech_options_changed(self, options):
        """Speed, volume, or voice changed. A running speaker picks it up on the next sentence."""
        if self._tts_service is not None:
            self._tts_service.set_options(options)

    def _on_voice_test(self):
        if self._tts_service is not None:
            self._tts_service.enqueue("This is the coach voice. Aim a little higher on the second target.")

    def _on_stop_speaking(self):
        if self._tts_service is None:
            self.chat_page.set_speech_status("Read aloud is off, so nothing is queued.")
            return
        dropped = self._tts_service.clear()
        self.chat_page.set_speech_status(
            f"Stopped. {dropped} queued repl{'y' if dropped == 1 else 'ies'} cleared; "
            "the sentence being read finishes."
        )

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
            # Voice is off: free the Vosk model (it can be gigabytes). A running listener
            # keeps its own reference until it exits, so this is safe either way.
            from fps_coach import vosk_service

            vosk_service.release_model()

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
        self.chat_page.refresh_from_settings()
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
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            overlay.close()
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
