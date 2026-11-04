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
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QKeySequence, QPainterPath, QRegion, QShortcut
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
from fps_coach.local_data import CALIBRATION_HISTORY_NAME

from .brand_font import load_brand_font
from .crosshair_overlay import CrosshairOverlay
from .dynamic_curve import (
    DynamicCurveEngine,
    DynamicCurveMonitor,
    DynamicCurvePanel,
)
from .icons import icon
from .magnification import ToggleKeyPoller, windows_available
from .pages_coach import AIChatPage, PrivacyPage, SettingsPage
from .pages_practice import CrosshairPage, TrainingPage
from .pages_setup import CalibrationPage, CapturePage, DashboardPage, GuidePage, RecommendationPage
from .startup_animation import StartupGlow
from .styles import build_style, fx_enabled, set_active_theme
from .threads import _TtsService, _VoiceCommandService
from .widgets import _label, _settings, refresh_glyphs, set_glyph
from .window_chrome import AppTitleBar, ResizeShell

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
        load_brand_font()
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("CurveCoach")
        self.setWindowIcon(icon("brand", "#D7F249", 128))
        self._restore_maximized = False
        self.resize(1360, 860)
        self.setMinimumSize(1120, 680)
        self.s = _settings()

        self._nav_buttons: dict[str, QPushButton] = {}
        self._util_buttons: dict[str, QToolButton] = {}
        self._voice_service = None
        self._tts_service = None
        # Speech watchdog: (started at, sentence) while a sentence is being spoken.
        self._speech_watch: tuple[float, str] | None = None
        self._speech_retry_text: str | None = None
        self._speech_timer = QTimer(self)
        self._speech_timer.setInterval(500)
        self._speech_timer.timeout.connect(self._check_speech_watchdog)
        self._last_recommendation: dict | None = None
        self._last_summary: dict | None = None

        central = ResizeShell()
        self.setCentralWidget(central)
        # Overlay, not a layout row: voice feedback must not resize an active
        # calibration arena when a phrase arrives.
        self._voice_notice = QLabel(central)
        self._voice_notice.setObjectName("voiceCommandNotice")
        self._voice_notice.setAccessibleName("Voice command status")
        self._voice_notice.setTextFormat(Qt.TextFormat.PlainText)
        self._voice_notice.setWordWrap(True)
        self._voice_notice.setMaximumWidth(640)
        self._voice_notice.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._voice_notice.hide()
        self._voice_notice_timer = QTimer(self)
        self._voice_notice_timer.setSingleShot(True)
        self._voice_notice_timer.timeout.connect(self._voice_notice.hide)
        root = QVBoxLayout(central)
        root.setContentsMargins(ResizeShell.BORDER, ResizeShell.BORDER, ResizeShell.BORDER, ResizeShell.BORDER)
        root.setSpacing(0)
        self._shell_layout = root
        self.title_bar = AppTitleBar(self)
        root.addWidget(self.title_bar)
        root.addSpacing(3)

        topbar = self._build_topbar()
        root.addWidget(topbar)
        root.addSpacing(3)
        # The window cannot be narrower than the top bar needs: brand, every tab (so AI Coach
        # is never cut off), the core dot, and the utility buttons. Measured from the layout.
        self.setMinimumWidth(topbar.layout().totalMinimumSize().width() + 2 * ResizeShell.BORDER)

        self.coach_panel = CoachStrip(self)
        self.coach_panel.closed.connect(self._on_coach_closed)
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", False, type=bool))
        root.addWidget(self.coach_panel)

        self.stack = QStackedWidget()
        self._build_pages()
        root.addWidget(self.stack, 1)
        self.startup_glow = StartupGlow(central)

        self._wire_signals()
        self._install_shortcuts()
        self._window_shortcuts = []
        for key, action in (("F11", self.toggle_full_screen),
                            ("Alt+Space", self.title_bar.open_window_menu),
                            ("Alt+F4", self.close)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(action)
            self._window_shortcuts.append(shortcut)
        self._apply_theme()
        self._sync_tts()
        self._apply_voice_commands()
        hints = QApplication.instance().styleHints() if QApplication.instance() else None
        if hints is not None and hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_theme_changed)
        self._select_page("dashboard")
        self._nav_buttons["dashboard"].setFocus(Qt.FocusReason.OtherFocusReason)

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
        lay.setContentsMargins(20, 3, 14, 3)
        lay.setSpacing(6)

        self._brand = QLabel()
        set_glyph(self._brand, "brand", 28, role="accent")
        self._brand.setAccessibleName("CurveCoach logo: bot and curve chart")
        lay.addWidget(self._brand)
        lay.addWidget(_label("CURVECOACH", "brand"))
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
            btn.setFixedHeight(54)
            # Reserve room for the bold (checked) weight so the label never clips.
            bold = btn.font()
            bold.setWeight(bold.Weight.DemiBold)
            # Bold label width + icon, icon spacing, the 12 px side padding, and a margin so
            # the active (bold) label is never clipped.
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
        scroll.setFixedHeight(54)
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
        # Live vision (AI Coach tab) reads the newest frame from the same capture backend.
        self.chat_page.set_capture_source(self._backend)
        self.privacy_page = PrivacyPage(
            self._analytics_store,
            self.chat_page,
            calibration_path=Path(__file__).resolve().parents[3] / CALIBRATION_HISTORY_NAME,
        )
        self.privacy_page.calibration_cleared.connect(self._on_calibration_cleared)
        self.settings_page = SettingsPage()
        self._dynamic_live_recommendation = None
        self.dynamic_curve = DynamicCurveEngine(
            self._dynamic_base, lambda: not self.calibration_page.canvas._active, self,
            mapping_provider=self._dynamic_speed_mapping,
        )
        self.chat_page.set_aim_analysis_sources(self._analytics_store, self._dynamic_base,
                                               lambda: dict(self.dynamic_curve.goals))
        self.chat_page.aim_monitoring_changed.connect(self.training_page.canvas.set_aim_monitor_enabled)
        self.training_page.canvas.aim_monitor_updated.connect(self.chat_page.update_aim_monitor)
        self.privacy_page.training_cleared.connect(self.chat_page.clear_aim_history)
        self.training_page.dynamic_curve_panel = DynamicCurvePanel(self.dynamic_curve, square_chart=True)
        self.training_page._dynamic_curve_layout.addWidget(self.training_page.dynamic_curve_panel)
        self.recommendation_page.install_dynamic_curve(self.dynamic_curve)
        self.training_page.dynamic_curve_monitor = DynamicCurveMonitor(self.dynamic_curve)
        self.training_page.dynamic_curve_monitor.setMinimumWidth(245)
        self.training_page._dynamic_curve_monitor_layout.insertWidget(1, self.training_page.dynamic_curve_monitor)
        self.calibration_page.graph_style_changed.connect(self._set_graph_style)
        self._set_graph_style(self.calibration_page._graph_style)
        self.dynamic_curve.enabled_changed.connect(self.calibration_page.canvas.set_performance_enabled)
        self.dynamic_curve.enabled_changed.connect(self.training_page.canvas.set_performance_enabled)
        for source, canvas in (("calibration", self.calibration_page.canvas),
                               ("training", self.training_page.canvas)):
            canvas.performance_started.connect(lambda source=source: self.dynamic_curve.session_started(source))
            canvas.performance_updated.connect(self.dynamic_curve.update_metrics)
            canvas.performance_ended.connect(
                lambda complete, source=source: self.dynamic_curve.session_ended(source, complete)
            )
        self.dynamic_curve.confirmed.connect(self._on_dynamic_curve_confirmed)
        self.privacy_page.training_cleared.connect(lambda: self.dynamic_curve.set_enabled(False))

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
        self.guide_page.open_crosshair.connect(lambda: self._select_page("crosshair"))

    def _set_graph_style(self, style: str) -> None:
        """Update every sensitivity preview without changing profiles or AI proposals."""
        charts = (self.calibration_page.chart,
                  self.recommendation_page.chart, self.chat_page.quick_preview,
                  self.recommendation_page.dynamic_curve_panel.chart,
                  self.training_page.dynamic_curve_panel.chart,
                  self.training_page.dynamic_curve_monitor.chart)
        for chart in charts:
            chart.set_graph_style(style)

    def _dynamic_base(self):
        from copy import deepcopy

        from fps_coach.curve_adjust import adjust_from_recommendation
        from fps_coach.curve_io import CurvePoint, CurveProfile, recommendation_profile

        page = self.calibration_page
        if page.canvas._active:
            if self._dynamic_live_recommendation is None:
                return None
            if page._base_profile is not None:
                return adjust_from_recommendation(page._base_profile, self._dynamic_live_recommendation)[0]
            return recommendation_profile(self._dynamic_live_recommendation)
        if page._profile is not None:
            return deepcopy(page._profile)
        # A neutral base is explicitly disclosed; the shape is supplied by the model.
        points = [CurvePoint(0.0, 1.0), CurvePoint(100.0, 1.0)]
        return CurveProfile("Neutral 1x base", deepcopy(points), deepcopy(points),
                            "No imported or measured base curve exists.")

    def _dynamic_speed_mapping(self):
        """Reference the calibration-generated axis only while its provenance is known."""
        import math

        from fps_coach.curve_io import recommendation_profile

        from .dynamic_curve import _fingerprint

        page = self.calibration_page
        if page._base_profile is not None:  # imported speed units are not verified
            return None
        if page.canvas._active:
            recommendation = self._dynamic_live_recommendation
            input_path = page.canvas.input_mode
        else:
            recommendation = self._last_recommendation
            input_path = (self._last_summary or {}).get("input_path")
            if recommendation is None or page._profile is None:
                return None
            try:
                if _fingerprint(page._profile) != _fingerprint(recommendation_profile(recommendation)):
                    return None  # edited, confirmed or otherwise changed base
            except (ValueError, TypeError, KeyError):
                return None
        if not recommendation or input_path not in ("raw", "desktop"):
            return None
        ref = recommendation.get("speed_reference_px_s")
        if isinstance(ref, bool) or not isinstance(ref, (int, float)) or not math.isfinite(ref) or ref <= 0:
            return None
        return {"input_path": input_path, "reference_px_s": float(ref)}

    def _on_dynamic_curve_confirmed(self, previous, adjusted, note):
        if self.calibration_page.canvas._active:
            return
        self.calibration_page.set_adjusted(previous, adjusted, note)
        self.coach_panel.say("Performance curve confirmed in Calibration; export to save it.")
        self._select_page("calibration")

    def _wire_signals(self) -> None:
        self.dashboard_page.navigate.connect(self._select_page)
        self.dashboard_page.wizard_changed.connect(self._on_calibration_wizard_changed)
        self.dashboard_page.wizard_discuss.connect(self._discuss_calibration_wizard)
        self.dashboard_page.wizard_preview.connect(self._on_wizard_preview)
        self.calibration_page.recommendation_ready.connect(self._on_calibration_result)
        self.calibration_page.canvas.session_started.connect(self._on_calibration_started)
        self.calibration_page.canvas.session_cancelled.connect(self._on_calibration_cancelled)
        self.calibration_page.canvas.recommendation_updated.connect(self._on_live_calibration)
        self.calibration_page.profile_changed.connect(self._on_profile_changed)
        self.calibration_page.base_profile_changed.connect(self.chat_page.set_base_profile)
        self.chat_page.curve_adjusted.connect(self._on_curve_adjusted)
        self.recommendation_page.recommendation_ready.connect(self._on_recommendation)
        self.recommendation_page.apply_requested.connect(self._on_apply_recommendation)
        self.chat_page.assistant_said.connect(self._on_assistant_said)
        self.chat_page.assistant_said.connect(self._speak_response)
        self.chat_page.read_aloud_changed.connect(self._on_read_aloud_changed)
        self.chat_page.stop_speaking_requested.connect(self._on_stop_speaking)
        self.chat_page.speech_options_changed.connect(self._on_speech_options_changed)
        self.chat_page.voice_test_requested.connect(self._on_voice_test)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.settings_page.ingame_changed.connect(self._sync_ingame_context)
        self.settings_page.ingame_question.connect(self._on_ingame_question)
        self.settings_page.bounce_speed_changed.connect(self.dashboard_page.set_bounce_speed)
        self.training_page.session_recorded.connect(self._on_session_recorded)
        self.crosshair_page.edited.connect(self._on_crosshair_edited)
        self.crosshair_page.activation_changed.connect(self._set_crosshair_overlay)
        self.crosshair_page.config_changed.connect(self._on_crosshair_config)
        self.crosshair_page.edit_mode_changed.connect(self._on_crosshair_edit_mode)
        self.crosshair_page.position_reset.connect(self._on_crosshair_position_reset)
        self._magnification_keys = ToggleKeyPoller(self)
        self._magnification_keys.pressed.connect(self._on_magnification_hotkey)
        self._magnification_keys.failed.connect(self._on_magnification_key_failure)
        self.crosshair_page.magnification_toggled.connect(self._on_magnification_toggle)
        self.crosshair_page.magnification_options_changed.connect(self._on_magnification_options)
        self.crosshair_page.magnification_binding_changed.connect(self._sync_magnification_hotkey)
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
            self._overlay.magnification_stopped.connect(self.crosshair_page.set_magnification_error)
            self._overlay.overlay_visibility_changed.connect(self._on_overlay_visibility_changed)
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

    def _on_magnification_toggle(self, enabled: bool) -> None:
        page = self.crosshair_page
        if not enabled:
            if self._overlay is not None:
                self._overlay.stop_magnification()
            return
        if not page.activation.isChecked() or page.edit_position.isChecked() or not windows_available():
            page.set_magnification_error("Turn on the crosshair overlay and lock its position on supported Windows first.")
            return
        overlay = self._ensure_overlay()
        ok, error = overlay.start_magnification(page.mag_diameter.value(), page.mag_zoom.value() / 100.0)
        if not ok:
            page.set_magnification_error(error)

    def _on_magnification_options(self, diameter: int, zoom: float) -> None:
        if self._overlay is not None:
            self._overlay.set_magnification_options(diameter, zoom)

    def _sync_magnification_hotkey(self) -> None:
        page = self.crosshair_page
        key = int(page.mag_binding.currentData() or 0)
        if (windows_available() and page.activation.isChecked() and not page.edit_position.isChecked()
                and self._overlay is not None and self._overlay.isVisible()
                and page.mag_hotkey_arm.isChecked() and key):
            self._magnification_keys.start(key)
        else:
            self._magnification_keys.stop()

    def _on_overlay_visibility_changed(self, visible: bool) -> None:
        if visible:
            self._sync_magnification_hotkey()
        else:
            self._magnification_keys.stop()

    def _on_magnification_key_failure(self, message: str) -> None:
        self.crosshair_page.mag_hotkey_arm.setChecked(False)
        self.crosshair_page.mag_disclosure.setChecked(True)
        self.crosshair_page.mag_note.setText(message)

    def _on_magnification_hotkey(self) -> None:
        page = self.crosshair_page
        if (page.magnification_toggle.isEnabled() and page.mag_hotkey_arm.isChecked()
                and self._overlay is not None and self._overlay.isVisible()):
            page.magnification_toggle.toggle()

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
        if hasattr(self, "calibration_page"):
            if key == "calibration":
                self.calibration_page.canvas.resume_after_navigation()
            else:
                self.calibration_page.canvas.pause_for_navigation()
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

    def toggle_full_screen(self):
        """The green title control fills the screen and returns to the prior state."""
        if self.isFullScreen():
            if self._restore_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._restore_maximized = self.isMaximized()
            self.showFullScreen()

    def _update_window_shape(self):
        """Rounded window region outside full screen; full screen covers every pixel."""
        if self.isFullScreen():
            self.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14.0, 14.0)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_window_shape()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._update_window_shape()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.fullscreen_button.set_action_state(self.isFullScreen())
            margin = 0 if self.isMaximized() or self.isFullScreen() else ResizeShell.BORDER
            self._shell_layout.setContentsMargins(margin, margin, margin, margin)
            self._update_window_shape()

    def _system_theme_changed(self, *_args):
        if str(self.s.value("ui/theme", "system")) == "system":
            self._apply_theme()

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
        if not (bool(animations) and fx_enabled(mode)):
            self.recommendation_page.curve_deck.finish_flip()
        if hasattr(self, "startup_glow") and not (bool(animations) and fx_enabled(mode)):
            self.startup_glow.stop()
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
            self._tts_service.status.connect(self.chat_page.set_speech_status)
            self._tts_service.utterance_started.connect(self._on_utterance_started)
            self._tts_service.utterance_finished.connect(self._on_utterance_finished)
            self._tts_service.start()
        elif not enabled and self._tts_service is not None:
            self._tts_service.stop()
            self._tts_service.wait(3000)
            self._tts_service = None

    # -- speech watchdog ---------------------------------------------------------
    @staticmethod
    def _speech_timeout(text: str) -> float:
        """Seconds a sentence may take before the engine is treated as stalled. Generous:
        normal speech runs well under 0.1 s per character."""
        return 10.0 + 0.15 * len(text)

    def _on_utterance_started(self, text: str) -> None:
        self._speech_watch = (time.monotonic(), text)
        if not self._speech_timer.isActive():
            self._speech_timer.start()

    def _on_utterance_finished(self) -> None:
        self._speech_watch = None
        self._speech_timer.stop()

    def _check_speech_watchdog(self) -> None:
        if self._speech_watch is None:
            self._speech_timer.stop()
            return
        started, text = self._speech_watch
        if time.monotonic() - started > self._speech_timeout(text):
            self._restart_speech(text)

    def _restart_speech(self, text: str) -> None:
        """The engine has not finished a sentence in time. Drop that worker, start a new one,
        and read the sentence again once. If it stalls again, say so and move on."""
        old = self._tts_service
        self._speech_watch = None
        self._speech_timer.stop()
        self._tts_service = None
        if old is not None:
            old.abandon()
        if text == self._speech_retry_text:
            self._speech_retry_text = None
            self.chat_page.set_speech_status(
                "The speech engine stopped responding again, so this reply was skipped."
            )
            self._sync_tts()
            return
        self._speech_retry_text = text
        self._sync_tts()
        if self._tts_service is not None:
            self._tts_service.enqueue(text)
        self.chat_page.set_speech_status(
            "The speech engine stopped responding, so it was restarted and the reply is read again."
        )

    def _speak_response(self, text: str):
        if self._tts_service is not None:
            self._tts_service.enqueue(text)

    def _on_tts_failed(self, message: str):
        self.chat_page._append("Text-to-speech", message)
        self.chat_page.set_speech_failed()

    def _on_read_aloud_changed(self, enabled: bool):
        """The chat's switch and the Settings checkbox share one saved value."""
        self._sync_tts()
        self.settings_page.refresh_voice_state()

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
            self._voice_service.status.connect(self._on_voice_status)
            self.settings_page.set_voice_command_status("Starting voice command listener…")
            self._voice_service.start()
        elif not enabled and self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(1500)
            self._voice_service = None
            self.settings_page.set_voice_command_status("Voice commands are off.")
            # Voice is off: free the Vosk model (it can be gigabytes). A running listener
            # keeps its own reference until it exits, so this is safe either way.
            from fps_coach import vosk_service

            vosk_service.release_model()

    # ------------------------------------------------------------------
    def _discuss_calibration_wizard(self) -> None:
        self.chat_page.prepare_wizard_question()
        self._select_page("chat")

    def _on_wizard_preview(self, proposal: dict) -> None:
        self.chat_page.preview_guide_proposal(proposal, base_profile=self.calibration_page._profile)
        self._select_page("chat")

    def _on_calibration_wizard_changed(self, answers: dict) -> None:
        # Motion is part of the guide only; no separate Calibration-tab selector.
        self.chat_page.set_aiming_motion(answers.get("motion", ""))
        self.chat_page.set_calibration_wizard(answers)

    def _on_calibration_started(self) -> None:
        """Old session results must not be used as current in-progress evidence."""
        self._dynamic_live_recommendation = None
        self.dynamic_curve.baseline_changed()
        self.dynamic_curve.changed.emit()
        self.dashboard_page.set_wizard_available(False)
        self.dashboard_page.set_guide_summary(None)
        self._last_summary = None
        self._last_recommendation = None
        self.recommendation_page.clear_result()
        self.recommendation_page.set_calibration_in_progress(True)
        self.settings_page.set_recommendation(None)
        self.chat_page.begin_calibration_session()

    def _on_live_calibration(self, summary: dict, recommendation: dict) -> None:
        """Refresh coach context after each accepted target, without sending a network request."""
        self._dynamic_live_recommendation = dict(recommendation)
        self.dynamic_curve.baseline_changed()
        self.chat_page.set_calibration_context(dict(summary), dict(recommendation), provisional=True)

    def _on_calibration_cancelled(self) -> None:
        self._dynamic_live_recommendation = None
        self.dynamic_curve.baseline_changed()
        self.dynamic_curve.changed.emit()
        self.dashboard_page.set_wizard_available(True)
        self.recommendation_page.set_calibration_in_progress(False)
        self._last_summary = None
        self._last_recommendation = None
        self.recommendation_page.clear_result()
        self.settings_page.set_recommendation(None)
        self.chat_page.clear_calibration_context()

    def _on_calibration_result(self, summary: dict, recommendation: dict):
        self._dynamic_live_recommendation = None
        self.dynamic_curve.baseline_changed()
        self.dynamic_curve.changed.emit()
        self.dashboard_page.set_wizard_available(True)
        self.recommendation_page.set_calibration_in_progress(False)
        self._last_summary = dict(summary)
        self.dashboard_page.set_guide_summary(summary)
        self._last_recommendation = dict(recommendation)
        self.recommendation_page.update_result(summary, recommendation, self.calibration_page._profile)
        self.chat_page.notify_new_calibration(summary, recommendation)
        # In-Game Mode shows what the multiplier means in the selected game's units.
        self.settings_page.set_recommendation(recommendation)
        self._select_page("recommendation")

    # ------------------------------------------------------------------
    def _sync_ingame_context(self):
        """Hand the In-Game Mode block (or nothing) to the coach.

        The block is empty while the mode is off, so the model never receives
        in-game numbers the user did not enable for this session.
        """
        self.chat_page.set_ingame_context(self.settings_page.ingame_context())

    def _on_ingame_question(self, question: str):
        """Ask-the-coach: drop the prepared question in the chat and show it."""
        if not question:
            return
        self.chat_page.input.setText(question)
        self._select_page("chat")  # AI Coach lives under the "chat" key
        self.chat_page.input.setFocus()

    def _on_profile_changed(self, profile):
        self.dynamic_curve.baseline_changed()
        self.coach_panel.say("Calibration profile updated.")

    def _on_curve_adjusted(self, previous, current, note: str):
        if self.calibration_page.canvas._active:
            return
        # An AI quick action or directive produced an adjusted curve: show it on the
        # Calibration graph as before/after and switch there.
        self.calibration_page.set_adjusted(previous, current, note)
        self.dynamic_curve.baseline_changed()
        self.coach_panel.say(note)
        self._select_page("calibration")

    def _on_apply_recommendation(self, recommendation: dict):
        """Generate recommendation: draw the curve on the Calibration chart and show it there."""
        if self.calibration_page.canvas._active:
            return
        self.calibration_page.apply_recommendation(dict(recommendation))
        self.recommendation_page.show_curve_profile(self.calibration_page._profile)
        self.dynamic_curve.baseline_changed()
        mult = float(recommendation.get("sensitivity_multiplier", 1.0))
        self.coach_panel.say(f"Applied the recommended curve (\u00d7{mult:.2f}) to the Calibration chart.")
        self._select_page("calibration")

    def _on_calibration_cleared(self):
        """Privacy > Local data > Clear calibration and recommendation history."""
        self.dynamic_curve.set_enabled(False)
        if self.calibration_page.canvas._active:
            self.calibration_page.canvas.cancel_session()
        self._backend.analyzer.reset()  # the trials Generate recommendation would use
        self._last_summary = None
        self._last_recommendation = None
        self.chat_page.clear_calibration_context()
        self.calibration_page.clear_recommendation_state()
        self.dashboard_page._clear_wizard()
        self.dashboard_page.set_guide_summary(None)
        self.recommendation_page.clear_result()
        self._refresh_dashboard()
        self.coach_panel.say("Cleared the calibration and recommendation history.")

    def _on_recommendation(self, result):
        self._last_recommendation = dict(result)
        if self._last_summary is None and not self.calibration_page.canvas._active:
            current = dict(self._backend.analyzer.summary())
            if int(current.get("trial_count", 0) or 0) > 0:
                self._last_summary = current
        if self._last_summary is not None:
            self.dashboard_page.set_guide_summary(self._last_summary)
            self.chat_page.set_calibration_context(self._last_summary, self._last_recommendation)
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
        self.chat_page.notify_new_training(result)

    def _on_crosshair_edited(self, config):
        self._refresh_dashboard()

    def _on_voice_status(self, message: str) -> None:
        if self.s.value("ui/voice_commands", False, type=bool):
            self.settings_page.set_voice_command_status(message)

    def _on_voice_failed(self, message: str):
        if not self.s.value("ui/voice_commands", False, type=bool):
            return
        self.settings_page.set_voice_command_status(f"Voice commands unavailable: {message}")
        self.coach_panel.say(f"Voice commands unavailable: {message}")
        self._voice_feedback("Voice commands unavailable. Open Settings → Voice for details.")

    def _voice_feedback(self, message: str) -> None:
        # Never repeat recognized questions into the UI or back through TTS.
        dialog = self.dashboard_page._wizard_dialog
        if dialog is not None and dialog.isVisible():
            dialog.error.setTextFormat(Qt.TextFormat.PlainText)
            dialog.error.setText(message)
            return
        self._voice_notice.setText(message)
        self._voice_notice.adjustSize()
        parent = self.centralWidget()
        self._voice_notice.move(max(0, (parent.width() - self._voice_notice.width()) // 2),
                                max(0, parent.height() - self._voice_notice.height() - 12))
        self._voice_notice.show()
        self._voice_notice.raise_()
        self._voice_notice_timer.start(5000)

    def _voice_app_is_active(self) -> bool:
        active = QApplication.activeWindow()
        while active is not None:
            if active is self:
                return True
            active = active.parentWidget()
        return False

    def _voice_send_question(self, question: str) -> None:
        self._select_page("chat")
        chat = self.chat_page
        if chat.input.text().strip():
            self._voice_feedback("Voice: unsent chat draft preserved. Send or clear it first.")
            return
        if chat._thread is not None and chat._thread.isRunning():
            self._voice_feedback("Voice: the coach is busy. Try again after the reply.")
            return
        chat.input.setText(question)
        chat._send()
        self._voice_feedback("Voice: question sent to the configured coach provider.")

    def _on_voice_command(self, text: str) -> None:
        from fps_coach.voice_commands import parse

        # Ignore queued events after disable and never let nearby speech operate
        # the app while another application is in the foreground.
        if not self.s.value("ui/voice_commands", False, type=bool) or not self._voice_app_is_active():
            return
        # The chat microphone is a separate one-shot question flow. Avoid acting
        # on a simultaneously recognized copy while that flow is capturing.
        if self.chat_page._voice_thread is not None:
            return
        wake = self.s.value("ui/wake_word", "coach")
        command = parse(text, wake_word=str(wake))
        if command is None:
            return
        # Reduce feedback-loop activations from spoken coach replies. Allow the
        # explicit stop command, but do not treat other speech as a command or
        # send it to a provider while this app is reading a sentence aloud.
        if self._speech_watch is not None and not (command.kind == "coach" and command.value == "stop_speaking"):
            return
        stop_speech = command.kind == "coach" and command.value == "stop_speaking"
        wizard = self.dashboard_page._wizard_dialog
        modal = QApplication.activeModalWidget()
        if modal is not None and modal is not wizard and not stop_speech:
            self._voice_feedback("Voice: close the current dialog before using app commands.")
            return
        if wizard is not None and wizard.isVisible() and command.kind != "guide" and not stop_speech:
            self._voice_feedback("Voice: finish or cancel the guide before using another app command.")
            return
        if command.kind == "navigate":
            self._select_page(command.value)
            self._voice_feedback("Voice: page opened.")
        elif command.kind == "curve_view":
            self._select_page("calibration")
            self._voice_feedback("Voice: sensitivity curve shown.")
        elif command.kind == "settings":
            self._select_page("settings")
            self.settings_page.open_section(command.value)
            self._voice_feedback(f"Voice: {command.value} settings opened.")
        elif command.kind == "section":
            target, action = command.value.split(":", 1)
            page, toggle = {
                "calibration": ("calibration", self.calibration_page.task_settings_btn),
                "training": ("training", self.training_page.settings_toggle),
                "coach": ("chat", self.chat_page.voice_settings_toggle),
            }[target]
            self._select_page(page)
            if not toggle.isEnabled():
                self._voice_feedback("Voice: settings are locked during calibration; finish or reset the run first.")
                return
            toggle.setChecked(action == "open")
            def reveal_control():
                if self.stack.currentWidget() is self._pages[page]:
                    parent = toggle.parentWidget()
                    while parent is not None and not isinstance(parent, QScrollArea):
                        parent = parent.parentWidget()
                    if parent is not None:
                        parent.ensureWidgetVisible(toggle, 12, 12)
            QTimer.singleShot(0, reveal_control)
            self._voice_feedback(f"Voice: {target} settings {'opened' if action == 'open' else 'closed'}.")
        elif command.kind == "guide":
            if command.value == "open":
                if not self.dashboard_page.wizard_btn.isEnabled():
                    self._voice_feedback("Voice: finish or reset calibration before opening the guide.")
                    return
                self._select_page("dashboard")
                self.dashboard_page._open_calibration_wizard()
                return
            if wizard is None or not wizard.isVisible():
                self._voice_feedback("Voice: open the calibration guide first.")
                return
            if command.value == "back":
                wizard.go_back()
            elif command.value == "skip" and wizard.step < wizard.REVIEW_STEP:
                wizard.skip_step()  # Explicit Skip has the same clearing behavior as the button.
            elif command.value == "next" and wizard.step < wizard.REVIEW_STEP:
                wizard.go_next()  # Runs the existing per-step validation.
            elif wizard.step == wizard.REVIEW_STEP:
                self._voice_feedback("Voice: review your answers and use the on-screen Finish button.")
        elif command.kind == "calibrate":
            self._select_page("calibration")
            if command.value == "start":
                self.calibration_page.canvas.start_session(12)
            elif command.value == "resume":
                self.calibration_page.canvas.resume_raw_capture()
            else:
                self.calibration_page.canvas.cancel_session()
        elif command.kind == "preset":
            self._select_page("chat")
            self.chat_page._apply_quick_action(command.value)
            self._voice_feedback("Voice: curve preview requested; review before applying.")
        elif command.kind == "panel":
            show = command.value == "show"
            canvas = self.calibration_page.canvas
            if canvas._active and canvas.input_mode == "raw" and not canvas._paused_for_navigation:
                self._voice_feedback("Voice: finish or pause raw calibration before changing the coach panel.")
                return
            self.s.setValue("ui/coach_panel", show)
            self.coach_panel.setVisible(show)
            self._voice_feedback(f"Voice: coach panel {'shown' if show else 'hidden'}.")
        elif command.kind == "coach":
            if command.value == "explain_calibration":
                self._voice_send_question("Explain my latest calibration results and their limitations.")
            elif command.value == "stop_speaking":
                self._on_stop_speaking()
                self._voice_feedback("Voice: queued speech stopped; the current sentence may finish.")
            else:
                self._select_page("chat")
                if command.value == "focus":
                    self.chat_page.input.setFocus(Qt.FocusReason.OtherFocusReason)
                self._voice_feedback("Voice: AI Coach opened.")
        elif command.kind == "dictate":
            self._voice_send_question(command.value)

    def _on_coach_closed(self):
        self.s.setValue("ui/coach_panel", False)

    def play_startup_animation(self) -> bool:
        """Start the optional one-shot effect without delaying or blocking the UI."""
        enabled = self.s.value("ui/animations", True, type=bool)
        mode = str(self.s.value("ui/mode", "luxurious"))
        return self.startup_glow.play_once(bool(enabled) and fx_enabled(mode))

    def closeEvent(self, event):
        wizard = self.dashboard_page._wizard_dialog
        if wizard is not None:
            wizard.reject()  # return the borrowed Crosshair page before shutdown
        self.startup_glow.stop()
        self._magnification_keys.stop()
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
        self.settings_page.shutdown()  # lets a model list request finish
        self.capture_page.shutdown()  # stops the scratch recording and deletes its file
        self._backend.close()
        self.s.sync()
        super().closeEvent(event)


def main():
    from fps_coach.app import main as app_main

    sys.exit(app_main())


if __name__ == "__main__":
    main()
