"""Coach, privacy, and settings pages.

The AI coach's chat, suggestion, and curve-tuning logic is carried over from the
previous window. The layout is new: the transcript sits beside the tuning panel.
"""
from __future__ import annotations

import html
import math
import re
from copy import deepcopy

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach import scenarios as scenario_defs
from fps_coach.ai_chat import (
    AIChatClient,
    ChatSettings,
    curve_context,
    model_supports_images,
    suggested_text_models,
)
from fps_coach.ai_curve_actions import apply_adjustment, parse_adjustment
from fps_coach.analytics import AnalyticsStore
from fps_coach.chat_suggestions import suggest
from fps_coach.curve_adjust import adjust_from_recommendation, apply_preset, normalized_overlay
from fps_coach.curve_io import CurveFormatError, chart_points, recommendation_profile
from fps_coach.dashboard_config import (
    BOUNCE_SPEED_DEFAULT,
    BOUNCE_SPEED_MAX,
    BOUNCE_SPEED_MIN,
    clamp_bounce_speed,
)
from fps_coach.ingame import GAMES, InGameMode
from fps_coach.local_data import clear_calibration_history, count_calibration_records
from fps_coach.tts_service import SPEED_MAX, SPEED_MIN, VOLUME_MAX, VOLUME_MIN, SpeechOptions
from fps_coach.visual_coach import LiveVisionSession, VisionSettings, suggested_vision_models

from .styles import active_palette, fx_enabled
from .threads import (
    _ChatRequest,
    _LiveVisionThread,
    _ModelListThread,
    _ModelPullThread,
    _OllamaStatusCheck,
    _VoiceListenThread,
)
from .widgets import (
    CurveChart,
    ValueSlider,
    _card,
    _content_page,
    _divider,
    _label,
    _page_header,
    _page_root,
    _settings,
    set_glyph,
    ComboField,
)

# Speech volume starts at the top of the range, not at Qt's default of the lowest value.
VOLUME_DEFAULT = VOLUME_MAX
VOLUME_KEY = "tts/volume"


class ThinkingIndicator(QWidget):
    """A small "Coach is thinking" pill with three pulsing dots.

    It sits in a strip reserved at the bottom of the transcript, so it never covers the
    text. With effects off (performance mode) the dots are drawn still.
    """

    _STEPS = 10  # frames per pulse cycle
    _INTERVAL_MS = 120

    def __init__(self, host: QWidget, animate: bool = True):
        super().__init__(host)
        self._host = host
        self._animate = animate
        self._phase = 0
        self.setFixedSize(176, 28)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self.hide()

    def start(self) -> None:
        self._phase = 0
        self._place()
        self.show()
        self.raise_()
        if self._animate:
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % self._STEPS
        self.update()

    def _place(self) -> None:
        self.move(12, max(0, self._host.height() - self.height() - 8))

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        pal = active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(pal["line"]), 1))
        p.setBrush(QColor(pal["surface2"]))
        p.drawRoundedRect(rect, 14, 14)
        accent = QColor(pal["accent"])
        cy = self.height() / 2
        for i in range(3):
            if self._animate:
                wave = 0.5 + 0.5 * math.sin(2 * math.pi * self._phase / self._STEPS - i * 1.1)
            else:
                wave = 0.6
            radius = 2.6 + 1.6 * wave
            color = QColor(accent)
            color.setAlpha(int(110 + 145 * wave))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(16 + i * 11, cy), radius, radius)
        p.setPen(QColor(pal["text2"]))
        p.drawText(QRectF(52, 0, self.width() - 58, self.height()),
                   Qt.AlignmentFlag.AlignVCenter, "Coach is thinking")
        p.end()


# AI Coach Focus: the tabs the coach works with. Capture gives settings only (no screen, no gameplay).
FOCUS_AREAS = (("capture", "Capture"), ("calibration", "Calibration"), ("training", "Training"))

_CALIBRATION_INSTRUCTION = (
    "The user just completed a new calibration. Using the calibration summary "
    "and current curve already in your context, give a brief (2-3 sentence) "
    "proactive assessment and one concrete next step. Do not ask questions."
)


def _training_instruction(name: str, result) -> str:
    return (
        f"The user just finished a training drill: {name}, accuracy {result.accuracy * 100:.0f}%, "
        f"average reaction {result.avg_reaction_ms:.0f} ms, best {result.best_reaction_ms:.0f} ms. "
        "Using these results and your context, give a brief (2-3 sentence) assessment. "
        "If a curve change would help, name one concrete adjustment. Do not ask questions."
    )


def _scenario_label(key: str) -> str:
    try:
        return scenario_defs.get(key).name
    except KeyError:
        return key


def _visible_ai_reply(answer: str) -> str:
    """Hide the parser-only curve directive from chat text and read-aloud output."""
    visible = re.sub(r"\[ADJUST:\s*[^\]]+\]", "", answer, flags=re.IGNORECASE).strip()
    return visible or "I prepared a curve proposal; see the adjacent preview."


class AIChatPage(QWidget):
    """AI Coach chat. Sends turns through ai_chat.AIChatClient (Ollama by default),
    adds the latest calibration as context, and takes one spoken phrase as input.

    Also offers suggested actions and curve tuning on an imported curve, and posts a
    proactive note after each calibration.
    """

    assistant_said = Signal(str)  # emitted with each AI response (coach strip, TTS)
    curve_adjusted = Signal(object, object, str)  # (previous, current, note)
    read_aloud_changed = Signal(bool)  # the chat's "Read replies aloud" switch was flipped
    stop_speaking_requested = Signal()
    speech_options_changed = Signal(object)  # SpeechOptions, sent on every slider or voice change
    voice_test_requested = Signal()
    # Read aloud, Stop speaking and the reply text size live here; nothing else mirrors
    # them, so there is no change signal to emit.

    _THINKING_STRIP = 44  # px reserved under the transcript text while a request runs
    TEXT_MIN, TEXT_DEFAULT, TEXT_MAX = 12, 14, 24  # transcript font size range, in px

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = _settings()
        self._thread = None
        self._voice_thread = None
        self._messages: list[dict[str, str]] = []
        self._transcript_items: list[tuple[str, str]] = []
        self._summary_context = ""
        self._calibration_context = ""
        self._ingame_context = ""
        self._base_profile = None  # imported profile, when present
        self._calibrated_profile = None  # latest data-derived calibration curve
        self._last_recommendation = None
        self._preview_base_profile = None
        self._adjusted_profile = None
        self._proactive_pending = None
        self._proactive_check = None
        self._proactive_request = None
        self._capture_source = None      # CoreBackend; set by MainWindow
        self._live_thread = None
        self._live_session = None
        self._live_failures = 0

        scroll, outer = _content_page(1280)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "AI Coach",
            "Ask about sensitivity, drills, or your latest session.",
            eyebrow="Tools",
        ))

        body = QHBoxLayout()
        body.setSpacing(20)

        card, lay = _card()
        # Settings: a gear at the top opens the block with Replies (read aloud, stop, reply
        # text size) and Voice (speed, volume, voice, test, pitch note).
        settings_head = QHBoxLayout()
        settings_head.setSpacing(8)
        settings_head.addWidget(_label("Settings", "formLabel"))
        settings_head.addStretch(1)
        self.voice_settings_toggle = QToolButton()
        self.voice_settings_toggle.setObjectName("iconButton")
        self.voice_settings_toggle.setCheckable(True)
        self.voice_settings_toggle.setChecked(False)
        set_glyph(self.voice_settings_toggle, "settings")
        self.voice_settings_toggle.setAccessibleName("Settings")
        self.voice_settings_toggle.toggled.connect(self._on_voice_settings_toggled)
        settings_head.addWidget(self.voice_settings_toggle)
        lay.addLayout(settings_head)
        self.voice_settings = QWidget()
        voice_lay = QVBoxLayout(self.voice_settings)
        voice_lay.setContentsMargins(0, 0, 0, 0)
        voice_lay.setSpacing(8)
        voice_lay.addLayout(self._build_focus_section())
        voice_lay.addWidget(_label("Replies", "formLabel"))
        voice_lay.addLayout(self._build_speech_bar())
        voice_lay.addLayout(self._build_text_size_bar())
        voice_lay.addWidget(_label("Voice", "formLabel"))
        voice_lay.addLayout(self._build_voice_bar())
        voice_lay.addWidget(_label("Live vision", "formLabel"))
        voice_lay.addLayout(self._build_live_vision_setting())
        self.voice_settings.setVisible(False)  # collapsed until the gear is opened
        lay.addWidget(self.voice_settings)
        self.transcript = QTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setMinimumHeight(380)
        lay.addWidget(self.transcript, 1)
        # Visible while a request is running, so the transcript is never blank-looking.
        self._pending_requests = 0
        self._voice_name = ""  # the chosen voice; kept even before the engine's voice list arrives
        self._thinking = ThinkingIndicator(
            self.transcript,
            animate=fx_enabled(str(self._settings.value("ui/mode", "luxurious"))),
        )
        self.refresh_from_settings()
        self._update_voice_tooltip()

        self.suggestion_bar = QHBoxLayout()
        self.suggestion_bar.setSpacing(8)
        lay.addLayout(self.suggestion_bar)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask the coach\u2026")
        self.mic_btn = QToolButton()
        set_glyph(self.mic_btn, "mic")
        self.mic_btn.setObjectName("iconButton")
        self.mic_btn.setCheckable(True)
        self.mic_btn.setToolTip("Speak a question")
        self.send_btn = QPushButton(" Send")
        self.send_btn.setObjectName("primary")
        set_glyph(self.send_btn, "send")
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.mic_btn)
        input_row.addWidget(self.send_btn)
        lay.addLayout(input_row)
        body.addWidget(card, 3)

        live_card = self._build_live_vision_card()
        body.addWidget(live_card, 2)

        tune_card, tune_lay = _card(
            "Curve tuning",
            "Calibration and AI curve suggestions appear here as a visual before/after. Nothing is sent to Calibration until you choose.",
        )
        self.quick_hint = _label(
            "Run a calibration or import a curve to see its data-driven shape here.",
            "cardSubtitle", wrap=True,
        )
        tune_lay.addWidget(self.quick_hint)
        self.quick_preview = CurveChart(editable=False)
        self.quick_preview.setMinimumHeight(220)
        # Read-only preview: the chart is shown, not edited, from here.
        self.quick_preview.hide()
        tune_lay.addWidget(self.quick_preview, 1)
        self.apply_to_rec = QPushButton(" Send to calibration")
        self.apply_to_rec.setObjectName("secondary")
        set_glyph(self.apply_to_rec, "check")
        self.apply_to_rec.hide()
        tune_lay.addWidget(self.apply_to_rec, 0, Qt.AlignmentFlag.AlignLeft)
        tune_lay.addStretch(1)
        tune_card.setMinimumWidth(320)
        body.addWidget(tune_card, 2)
        outer.addLayout(body, 1)

        self.send_btn.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.input.textChanged.connect(self._refresh_suggestions)
        self.mic_btn.toggled.connect(self._start_voice)
        self.apply_to_rec.clicked.connect(self._send_adjusted_to_calibration)
        self._refresh_suggestions()

    def set_calibration_context(self, summary: dict, recommendation: dict) -> None:
        """Give the coach the latest data and display its curve beside the conversation."""
        self._summary_context = curve_context(summary, recommendation)
        self._last_recommendation = deepcopy(recommendation)
        try:
            self._calibrated_profile = recommendation_profile(recommendation)
        except CurveFormatError:
            self._calibrated_profile = None
        self._refresh_context()
        self._refresh_curve_preview()
        self._refresh_suggestions()

    def clear_calibration_context(self) -> None:
        """Forget a cleared calibration; retain and display an imported curve if one exists."""
        self._summary_context = ""
        self._last_recommendation = None
        self._calibrated_profile = None
        self._adjusted_profile = None
        self._preview_base_profile = None
        self._refresh_context()
        self._refresh_curve_preview()
        self._refresh_suggestions()

    def notify_new_calibration(self, summary: dict, recommendation: dict) -> None:
        """Called when a calibration completes. The coach's context is always refreshed. An
        automatic note and AI recommendation are posted only when Calibration focus is on."""
        self.set_calibration_context(summary, recommendation)
        if not self._focus_on("calibration"):
            return
        self._append("Coach (auto)", self._local_proactive_note(summary, recommendation))
        self._maybe_proactive_ai(_CALIBRATION_INSTRUCTION)

    def notify_new_training(self, result) -> None:
        """Called when a training drill finishes. Posts a note and an AI recommendation only
        when Training focus is on. Uses the app's own drill result; no screen is read."""
        if not self._focus_on("training"):
            return
        name = _scenario_label(result.scenario)
        self._append(
            "Coach (auto)",
            f"Training drill finished: {name}. Accuracy {result.accuracy * 100:.0f}%, "
            f"average reaction {result.avg_reaction_ms:.0f} ms, best {result.best_reaction_ms:.0f} ms. "
            "Ask for a curve adjustment, or use a suggested action.",
        )
        self._maybe_proactive_ai(_training_instruction(name, result))

    # -- AI Coach Focus ---------------------------------------------------------
    def _build_focus_section(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        box.addWidget(_label("AI Coach Focus", "formLabel"))
        box.addWidget(_label(
            "Choose which tabs the coach works with. Everything is off until you turn it on.",
            "cardSubtitle", wrap=True,
        ))
        row = QHBoxLayout()
        row.setSpacing(16)
        self.focus_boxes: dict[str, QCheckBox] = {}
        for key, label in FOCUS_AREAS:
            check = QCheckBox(label)
            check.setChecked(self._settings.value(f"coach/focus/{key}", False, type=bool))
            check.toggled.connect(self._on_focus_item_toggled)
            self.focus_boxes[key] = check
            row.addWidget(check)
        row.addStretch(1)
        box.addLayout(row)
        self.focus_note = _label("", "cardSubtitle", wrap=True)
        box.addWidget(self.focus_note)
        self._update_focus_note()
        return box

    def _focus_on(self, key: str) -> bool:
        boxes = getattr(self, "focus_boxes", None)
        return bool(boxes and boxes[key].isChecked())

    def _on_focus_item_toggled(self, _checked: bool = False) -> None:
        self._focus_changed()

    def _focus_changed(self) -> None:
        for key, box in self.focus_boxes.items():
            self._settings.setValue(f"coach/focus/{key}", box.isChecked())
        self._update_focus_note()
        self._refresh_context()

    def _update_focus_note(self) -> None:
        if not any(self._focus_on(key) for key, _ in FOCUS_AREAS):
            self.focus_note.setText(
                "Off. The coach posts no automatic notes after calibration or training."
            )
            return
        parts = []
        if self._focus_on("calibration"):
            parts.append("After each calibration: a note and a curve recommendation.")
        if self._focus_on("training"):
            parts.append("After each training drill: a note and a recommendation.")
        if self._focus_on("capture"):
            thread = getattr(self, "_live_thread", None)
            if thread is not None and thread.isRunning():
                parts.append(
                    "Capture: the coach sees the settings (preset, FPS) and still frames of the "
                    "preview while Live vision is on. It sees no game events."
                )
            else:
                parts.append(
                    "Capture: the coach sees capture settings (preset, FPS) only. "
                    "It cannot see the screen or gameplay."
                )
        self.focus_note.setText(" ".join(parts))

    def _with_capture_focus(self, context: str) -> str:
        if not self._focus_on("capture"):
            return context
        preset = str(self._settings.value("capture/preset", "balanced"))
        fps = self._settings.value("capture/fps", 60, type=int)
        thread = getattr(self, "_live_thread", None)
        watching = thread is not None and thread.isRunning()
        if watching:
            line = (
                f"Capture settings: preset '{preset}', {fps} FPS. Live vision is on, so the coach "
                "is receiving still frames of the capture preview; it sees no game events."
            )
        else:
            line = (
                f"Capture settings: preset '{preset}', {fps} FPS. The coach cannot see the "
                "screen or gameplay unless you turn Live vision on; this is configuration only."
            )
        return f"{context}\n\n{line}" if context else line

    def set_ingame_context(self, block: str) -> None:
        """Take the In-Game Mode block (game, DPI, cm/360°, suggestion) from Settings.

        The block is built by ``fps_coach.ingame.InGameMode.context_block`` and is
        empty while the mode is off, in which case the coach sees no in-game data.
        """
        self._ingame_context = str(block or "")
        self._refresh_context()

    def _with_ingame(self, context: str) -> str:
        if not self._ingame_context:
            return context
        return f"{context}\n\n{self._ingame_context}" if context else self._ingame_context

    def set_base_profile(self, profile) -> None:
        """Receive an imported curve and refresh the side-by-side tuning preview."""
        self._base_profile = deepcopy(profile) if profile is not None else None
        self._adjusted_profile = None
        self._preview_base_profile = None
        self._refresh_context()
        self._refresh_curve_preview()
        self._refresh_suggestions()

    def _active_curve_profile(self):
        """The curve a quick action or AI directive should build on, if any."""
        return self._adjusted_profile or self._base_profile or self._calibrated_profile

    def _refresh_curve_preview(self) -> None:
        """Show the latest calibration or imported curve without inventing a preset shape."""
        self._adjusted_profile = None
        self._preview_base_profile = None
        profile = self._base_profile or self._calibrated_profile
        if profile is None:
            self.quick_preview.hide()
            self.apply_to_rec.hide()
            self.quick_hint.setText(
                "Run a calibration or import a curve to see its data-driven shape here."
            )
            return

        recommendation = self._last_recommendation
        if self._base_profile is not None and recommendation and len(recommendation.get("points") or []) >= 2:
            try:
                adjusted, note = adjust_from_recommendation(self._base_profile, recommendation)
            except (CurveFormatError, ValueError):
                self._show_curve_profile(self._base_profile, "Imported curve; calibration preview unavailable.")
                return
            sample_count = int(recommendation.get("captured_sample_count", 0) or 0)
            self._show_preview(
                self._base_profile,
                adjusted,
                f"Calibration proposal · {sample_count:,} captured movement samples. {note}",
            )
            return

        if self._base_profile is not None:
            self._show_curve_profile(
                self._base_profile,
                f"Imported curve · {self._base_profile.name}. Ask for a small adjustment to preview it here.",
            )
        else:
            sample_count = int((recommendation or {}).get("captured_sample_count", 0) or 0)
            point_count = len(recommendation.get("points") or []) if recommendation else 0
            self._show_curve_profile(
                profile,
                f"Calibration curve · {sample_count:,} captured movement samples · "
                f"{point_count} data-derived control points.",
            )

    def _show_curve_profile(self, profile, note: str) -> None:
        """Draw a complete baseline using every measured/imported point, not five resamples."""
        points = chart_points(profile)
        if len(points) < 2:
            self.quick_preview.hide()
            self.apply_to_rec.hide()
            self.quick_hint.setText(note)
            return
        xs = [x for x, _ in points]
        self.quick_preview.set_previous(None)
        self.quick_preview.xs = xs
        self.quick_preview.points = [y for _, y in points]
        self.quick_preview.update()
        self.quick_preview.show()
        self.apply_to_rec.hide()
        self.quick_hint.setText(note)

    def _refresh_context(self) -> None:
        """Context = calibration summary + whichever source curve the coach should tune."""
        context = self._summary_context
        profile = self._base_profile or self._calibrated_profile
        if profile is not None:
            pts = ", ".join(
                f"({p.speed:.0f},{p.multiplier:.2f})" for p in profile.horizontal[:32]
            )
            kind = "Imported base" if self._base_profile is not None else "Current calibrated"
            context = (
                f"{context}\n\n{kind} curve '{profile.name}' "
                f"({profile.source_format}); horizontal speed,gain points: {pts}. "
                "When asked for adjustments, modify this curve rather than designing a new one."
            )
        self._calibration_context = self._with_ingame(self._with_capture_focus(context))

    def _local_proactive_note(self, summary: dict, recommendation: dict) -> str:
        title = str(recommendation.get("title", "Recommendation"))
        sens = float(recommendation.get("sensitivity_multiplier", 1.0) or 1.0)
        conf = float(recommendation.get("confidence", 0.0)) * 100
        path_eff = float(summary.get("path_efficiency", 0.0)) * 100
        over = float(summary.get("overshoot_score", 0.0)) * 100
        corr = float(summary.get("correction_score", 0.0)) * 100
        parts = [
            f"New calibration in. Direction: {title}.",
            f"Suggested starting multiplier {sens:.2f}x (confidence {conf:.0f}%).",
            f"Path efficiency {path_eff:.0f}%, overshoot {over:.0f}%, corrections {corr:.0f}%.",
        ]
        reasons = recommendation.get("reasons") or []
        if reasons:
            parts.append(f"Key point: {reasons[0]}")
        if self._base_profile is not None:
            parts.append(f"I've adjusted your imported curve '{self._base_profile.name}' to match.")
        parts.append("Ask me anything about these results, or use a suggested action.")
        return " ".join(parts)

    def _maybe_proactive_ai(self, instruction: str) -> None:
        # Never trigger a surprise download or a paid API call without user action.
        if str(self._settings.value("ai/provider", "ollama")) != "ollama":
            return
        if self._proactive_request is not None or self._proactive_check is not None:
            return
        settings = self._chat_settings()
        if not settings.base_url or not settings.model:
            return
        self._proactive_pending = instruction
        self._proactive_check = _OllamaStatusCheck(settings.base_url, settings.model, self)
        self._proactive_check.status_ready.connect(self._on_proactive_status)
        self._proactive_check.finished.connect(self._proactive_check_finished)
        self._proactive_check.start()

    def _proactive_check_finished(self) -> None:
        if self._proactive_check is not None:
            self._proactive_check.deleteLater()
        self._proactive_check = None

    def _on_proactive_status(self, reachable: bool, has_model: bool) -> None:
        if not (reachable and has_model) or self._proactive_pending is None:
            return
        if self._proactive_request is not None:
            return
        instruction = self._proactive_pending
        messages = list(self._messages) + [{"role": "user", "content": instruction}]
        self._proactive_request = _ChatRequest(self._chat_settings(), messages, self._calibration_context, self)
        self._proactive_request.response_ready.connect(self._proactive_received)
        self._proactive_request.request_failed.connect(self._proactive_quiet_fail)
        self._proactive_request.finished.connect(self._proactive_finished)
        self._proactive_request.finished.connect(self._request_finished)
        self._request_started()
        self._proactive_request.start()

    def _proactive_received(self, answer: str) -> None:
        self._messages.append({"role": "assistant", "content": answer})
        visible = _visible_ai_reply(answer)
        self._append("Coach", visible)
        self.assistant_said.emit(visible)
        # A recognized recommendation only refreshes the adjacent preview. Sending it to
        # Calibration still requires the user's explicit button click.
        self._apply_ai_adjustment(answer)

    def _proactive_quiet_fail(self, _message: str) -> None:
        # The instant local note already covered the user; stay silent on failure.
        pass

    def _proactive_finished(self) -> None:
        if self._proactive_request is not None:
            self._proactive_request.deleteLater()
        self._proactive_request = None

    def _refresh_suggestions(self, *_args) -> None:
        """Rebuild the suggestion chips to match what the user is typing."""
        while self.suggestion_bar.count():
            item = self.suggestion_bar.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for s in suggest(self.input.text(), self._active_curve_profile() is not None):
            chip = QPushButton(s["label"])
            chip.setObjectName("secondary")
            if s["kind"] == "preset":
                chip.clicked.connect(lambda checked=False, k=s["payload"]: self._apply_quick_action(k))
            else:
                chip.clicked.connect(lambda checked=False, p=s["payload"]: self._use_prompt(p))
            self.suggestion_bar.addWidget(chip)
        self.suggestion_bar.addStretch(1)

    def _use_prompt(self, prompt: str) -> None:
        """Drop a suggested question into the compose box for the user to send or edit."""
        self.input.setText(prompt)
        self.input.setFocus()

    def _show_preview(self, previous, adjusted, note: str) -> None:
        """Show every before/after control point; sending it remains an explicit user action."""
        self._preview_base_profile = deepcopy(previous)
        self._adjusted_profile = deepcopy(adjusted)
        prev_pts, cur_pts = normalized_overlay(previous, adjusted)
        prev_xs, prev_ys = zip(*prev_pts, strict=True)
        cur_xs, cur_ys = zip(*cur_pts, strict=True)
        self.quick_preview.set_previous(list(prev_ys), xs=list(prev_xs))
        self.quick_preview.xs = list(cur_xs)
        self.quick_preview.points = list(cur_ys)
        self.quick_preview.update()
        self.quick_preview.show()
        self.apply_to_rec.show()
        self.quick_hint.setText(
            f"{note}\nSolid: proposed · dashed: before. Nothing is sent until you choose Send to calibration."
        )
        self._refresh_suggestions()

    def _apply_quick_action(self, key: str) -> None:
        base = self._active_curve_profile()
        if base is None:
            self._append("Quick action", "Run a calibration or import a curve first.")
            return
        try:
            adjusted, note = apply_preset(base, key)
        except KeyError:
            return
        self._show_preview(base, adjusted, note)
        self._append(
            "Quick action",
            f"{note}\nThe adjacent before/after graph is ready for review. "
            f"First-point multiplier: {adjusted.horizontal[0].multiplier:.2f}x.",
        )

    def _apply_ai_adjustment(self, answer: str) -> None:
        """Convert an AI curve suggestion into a visual proposal, never an automatic apply."""
        plan = parse_adjustment(answer)
        base = self._active_curve_profile()
        if not plan or base is None:
            return
        try:
            adjusted, note = apply_adjustment(base, plan)
        except (ValueError, KeyError):
            return
        self._show_preview(base, adjusted, f"AI curve proposal · {note}")

    def _send_adjusted_to_calibration(self) -> None:
        if self._preview_base_profile is None or self._adjusted_profile is None:
            return
        note = self._adjusted_profile.description or "Adjusted via AI Coach curve preview."
        self.curve_adjusted.emit(
            deepcopy(self._preview_base_profile),
            deepcopy(self._adjusted_profile),
            note,
        )
        self._append(
            "Coach",
            "Sent the visual curve proposal to Calibration for review. It has not been applied to the game.",
        )

    # -- live vision (coach watches the capture preview) ----------------------
    def set_capture_source(self, backend) -> None:
        """Hand the page the object whose ``latest_frame()`` feeds the coach.

        MainWindow passes its CoreBackend. Without it the Live vision card
        explains that there is nothing to watch instead of failing.
        """
        self._capture_source = backend
        self._refresh_live_vision_note()

    def _build_live_vision_setting(self) -> QVBoxLayout:
        """The switch that decides whether the Live vision panel exists at all.

        Off by default, so the tab is just the chat until you ask for the panel.
        This is a layout preference and is remembered; the panel's own "Watch
        live" switch is not, and stays off at every launch.
        """
        box = QVBoxLayout()
        box.setSpacing(6)
        self.live_enable = QCheckBox("Enable live vision")
        self.live_enable.setToolTip(
            "Shows the Live vision panel beside the chat, where the coach can be given "
            "clips of the capture preview. Off: the panel is hidden and nothing is captured."
        )
        self.live_enable.setChecked(bool(_settings().value("ai/live_enabled", False, type=bool)))
        self.live_enable.toggled.connect(self._on_live_enable_toggled)
        box.addWidget(self.live_enable)
        hint = _label(
            "Adds a panel in the middle of this page that shows what the coach is shown. "
            "Watching still starts off every launch.",
            "cardSubtitle",
            wrap=True,
        )
        box.addWidget(hint)
        return box

    def _on_live_enable_toggled(self, enabled: bool) -> None:
        _settings().setValue("ai/live_enabled", bool(enabled))
        card = getattr(self, "live_card", None)
        if card is not None:
            card.setVisible(bool(enabled))
        if not enabled:
            self._stop_live_vision()  # hiding the panel must stop its capture too

    def _build_live_vision_card(self):
        card, lay = _card(
            "Live vision",
            "The coach watches the live capture preview as a moving clip. Off at every launch.",
        )
        # Filmstrip: the frames of the window the model was last given, oldest first.
        self.live_filmstrip = QWidget()
        strip_lay = QHBoxLayout(self.live_filmstrip)
        strip_lay.setContentsMargins(0, 0, 0, 0)
        strip_lay.setSpacing(4)
        self.live_slots: list[QLabel] = []
        for _ in range(4):
            slot = QLabel("")
            slot.setObjectName("stage")
            slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slot.setFixedHeight(74)
            slot.setMinimumWidth(40)
            slot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            slot.setScaledContents(False)
            slot.installEventFilter(self)  # re-fit when the card is resized
            strip_lay.addWidget(slot, 1)
            self.live_slots.append(slot)
        self._live_pixmaps: list = []
        lay.addWidget(self.live_filmstrip)

        self.live_motion = QProgressBar()
        self.live_motion.setRange(0, 100)
        self.live_motion.setValue(0)
        self.live_motion.setTextVisible(True)
        self.live_motion.setFormat("motion \u2014 no clip yet")
        self.live_motion.setToolTip(
            "How much the preview changed between the sampled frames. High motion means the "
            "coach's answer is about movement, not a static picture."
        )
        lay.addWidget(self.live_motion)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.live_toggle = QPushButton(" Watch live")
        self.live_toggle.setObjectName("secondary")
        self.live_toggle.setCheckable(True)
        self.live_toggle.setChecked(False)  # never on at launch
        set_glyph(self.live_toggle, "eye")
        self.live_now = QPushButton(" Look now")
        self.live_now.setObjectName("secondary")
        set_glyph(self.live_now, "refresh")
        self.live_now.setToolTip("Take a clip from the preview now, without starting the loop")
        row.addWidget(self.live_toggle)
        row.addWidget(self.live_now)
        row.addStretch(1)
        lay.addLayout(row)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        settings = _settings()

        form.addWidget(_label("Clip length", "formLabel"), 0, 0)
        self.live_frames = QSpinBox()
        self.live_frames.setRange(1, 8)
        self.live_frames.setSuffix(" frames")
        self.live_frames.setValue(int(settings.value("ai/live_frames", 4, type=int)))
        self.live_frames.setToolTip(
            "How many frames one answer is based on. One frame is a snapshot; four frames half "
            "a second apart is a two-second clip the model can compare."
        )
        self.live_frames.valueChanged.connect(
            lambda value: settings.setValue("ai/live_frames", int(value))
        )
        form.addWidget(self.live_frames, 0, 1)

        form.addWidget(_label("Sample every", "formLabel"), 1, 0)
        self.live_sample = QDoubleSpinBox()
        self.live_sample.setRange(0.1, 3.0)
        self.live_sample.setSingleStep(0.1)
        self.live_sample.setDecimals(1)
        self.live_sample.setSuffix(" s")
        self.live_sample.setValue(float(settings.value("ai/live_sample", 0.5, type=float)))
        self.live_sample.setToolTip(
            "Gap between the frames inside a clip. Smaller is closer to video and costs more per "
            "answer; most local vision models stall below about 0.3 s."
        )
        self.live_sample.valueChanged.connect(
            lambda value: settings.setValue("ai/live_sample", float(value))
        )
        form.addWidget(self.live_sample, 1, 1)

        form.addWidget(_label("Ask every", "formLabel"), 2, 0)
        self.live_interval = QDoubleSpinBox()
        self.live_interval.setRange(2.0, 120.0)
        self.live_interval.setSingleStep(1.0)
        self.live_interval.setDecimals(0)
        self.live_interval.setSuffix(" s")
        self.live_interval.setValue(float(settings.value("ai/live_interval", 4.0, type=float)))
        self.live_interval.setToolTip(
            "How long to wait after each reply before taking the next clip. A longer gap means "
            "fewer, less repetitive comments; the model's own speed is the real limit."
        )
        self.live_interval.valueChanged.connect(
            lambda value: settings.setValue("ai/live_interval", float(value))
        )
        form.addWidget(self.live_interval, 2, 1)
        lay.addLayout(form)

        self.live_change_only = QCheckBox("Only when the picture changes")
        self.live_change_only.setToolTip(
            "Skip requests while the preview is not moving, so a paused game costs nothing. "
            "Look now always sends."
        )
        self.live_change_only.setChecked(bool(settings.value("ai/live_change_only", True, type=bool)))
        self.live_change_only.toggled.connect(
            lambda checked: settings.setValue("ai/live_change_only", bool(checked))
        )
        lay.addWidget(self.live_change_only)

        self.live_status = _label("Off", "statusLabel", wrap=True)
        lay.addWidget(self.live_status)

        self.live_note = _label("", "cardSubtitle", wrap=True)
        lay.addWidget(self.live_note)
        self._refresh_live_vision_note()

        lay.addStretch(1)  # keep the controls together at the top of the tall card
        self.live_toggle.toggled.connect(self._toggle_live_vision)
        self.live_now.clicked.connect(self._ask_live_now)
        self.live_card = card
        card.setVisible(self.live_enable.isChecked())  # hidden unless the Setting is on
        return card

    def _live_frame_provider(self):
        """The newest captured frame, or None while the preview is not running."""
        source = self._capture_source
        latest = getattr(source, "latest_frame", None)
        if not callable(latest):
            return None
        try:
            frame = latest()
        except Exception:  # noqa: BLE001 - the loop reports a broken backend itself
            return None
        return frame if isinstance(frame, dict) and frame.get("bgra") else None

    def _refresh_live_vision_note(self) -> None:
        """Say whether the configured model can see, and where the frames go."""
        if not hasattr(self, "live_note"):
            return
        settings = self._chat_settings()
        notes: list[str] = []
        if not callable(getattr(self._capture_source, "latest_frame", None)):
            notes.append("Live vision needs the capture backend; it is not available here.")
        recommended = suggested_vision_models()[0]
        if not model_supports_images(settings.model, settings.provider):
            notes.append(
                f"{settings.model or 'This model'} cannot see images. Pick a vision model in "
                f"Settings - {recommended} is the best fit (ollama pull {recommended})."
            )
        elif not _is_timestamp_model(settings.model):
            notes.append(
                f"Yes, it can see. {recommended} is the better fit for a clip: it is trained on "
                "timestamped video frames, so it follows what changed and when."
            )
        if settings.provider == "openai":
            notes.append(
                "Frames are uploaded to your configured API endpoint while this runs."
            )
        elif model_supports_images(settings.model, settings.provider):
            notes.append("Local model: frames stay on this machine.")
        self.live_note.setText(" ".join(notes))
        self.live_note.setVisible(bool(notes))

    def _set_live_status(self, text: str, state: str = "") -> None:
        self.live_status.setText(text)
        self.live_status.setProperty("state", state)
        self.live_status.style().unpolish(self.live_status)
        self.live_status.style().polish(self.live_status)

    def _toggle_live_vision(self, checked: bool) -> None:
        if checked and not self.live_enable.isChecked():
            self._on_live_enable_toggled(True)  # never watch from a hidden panel
            self.live_enable.setChecked(True)
        if checked:
            self._start_live_vision()
        else:
            self._stop_live_vision()

    def _live_vision_settings(self) -> VisionSettings:
        return VisionSettings(
            frames_per_request=int(self.live_frames.value()),
            sample_interval=float(self.live_sample.value()),
            min_interval=float(self.live_interval.value()),
            adaptive=self.live_change_only.isChecked(),
        )

    def _start_live_vision(self) -> None:
        if self._live_thread is not None:
            return  # already starting or running
        if not callable(getattr(self._capture_source, "latest_frame", None)):
            self._stop_live_vision()
            self._set_live_status("No capture backend, so there is nothing to watch.", "bad")
            return
        settings = self._chat_settings()
        if not model_supports_images(settings.model, settings.provider):
            self._set_live_status(
                f"{settings.model} cannot see images — choose a vision model in Settings.",
                "warn",
            )
        else:
            self._set_live_status("Starting…", "good")
        self._live_failures = 0
        self._live_session = LiveVisionSession(
            frame_provider=self._live_frame_provider,
            chat=AIChatClient(settings),
            settings=self._live_vision_settings(),
            context_provider=lambda: self._calibration_context,
        )
        self._live_thread = _LiveVisionThread(self._live_session, self)
        self._live_thread.reply_ready.connect(self._live_vision_reply)
        self._live_thread.request_failed.connect(self._live_vision_failed)
        self._live_thread.status_changed.connect(self._live_vision_status)
        self._live_thread.window_sent.connect(self._live_vision_window)
        self._live_thread.finished.connect(self._live_vision_finished)
        blocked = self.live_toggle.blockSignals(True)
        self.live_toggle.setChecked(True)
        self.live_toggle.blockSignals(blocked)
        self.live_interval.setEnabled(False)
        self.live_frames.setEnabled(False)
        self.live_sample.setEnabled(False)
        self._live_thread.start()

    def _stop_live_vision(self) -> None:
        if self._live_thread is not None:
            self._live_thread.stop_session()
            if not self._live_thread.isRunning():
                self._live_thread.deleteLater()
                self._live_thread = None
        self._live_session = None
        signals_blocked = self.live_toggle.blockSignals(True)
        self.live_toggle.setChecked(False)
        self.live_toggle.blockSignals(signals_blocked)
        self.live_interval.setEnabled(True)
        self.live_frames.setEnabled(True)
        self.live_sample.setEnabled(True)
        self._live_motion_reset()
        self._set_live_status("Off")

    def _ask_live_now(self) -> None:
        """One frame, one answer — the loop runs once if it is not already."""
        if not callable(getattr(self._capture_source, "latest_frame", None)):
            self._set_live_status("No capture backend, so there is nothing to look at.", "bad")
            return
        if self._live_thread is not None and self._live_thread.isRunning():
            self._live_thread.ask_now()
            self._set_live_status("Looking at the newest frame…", "good")
            return
        self._start_live_vision()
        if self._live_thread is not None:
            self._live_thread.ask_now()

    def _live_vision_reply(self, text: str, meta) -> None:
        meta = meta or {}
        frames = int(meta.get("frames") or 1)
        stamp = f"{frames} frame{'s' if frames != 1 else ''}"
        if meta.get("span"):
            stamp += f" / {float(meta['span']):.1f} s"
        if meta.get("motion_word"):
            stamp += f" · {meta['motion_word']} motion"
        if meta.get("latency"):
            stamp += f" · {float(meta['latency']):.1f} s"
        if meta.get("changed") is not None and hasattr(self, "live_motion"):
            # The bar shows how much of the frame moved, which is the readable number.
            share = max(0.0, min(1.0, float(meta["changed"])))
            self.live_motion.setValue(int(round(share * 100)))
            where = meta.get("band") if meta.get("band") not in (None, "none") else "no area"
            self.live_motion.setFormat(
                f"motion {meta.get('motion_word', '')} · {share * 100:.0f}% ({where})"
            )
        self._append("Coach (live)", f"{text} <i>— {stamp}</i>")
        counters = self._live_session.counters if self._live_session is not None else None
        self._set_live_status(counters.line() if counters else "Watching", "good")

    def _live_vision_failed(self, message: str) -> None:
        self._live_failures += 1
        self._set_live_status(f"Live vision: {message}", "bad")

    def _live_vision_status(self, message: str) -> None:
        if message.startswith("Live vision stopped"):
            return  # the toggle and the counters already say so
        state = "warn" if message.startswith("No frame yet") else ""
        self._set_live_status(message, state)

    def _live_vision_window(self, frames, meta) -> None:
        """Show the clip the model was given — oldest frame first, newest last."""
        meta = meta or {}
        details = list(meta.get("frames") or [])
        jpegs = list(frames or [])
        visible = jpegs[-len(self.live_slots):]
        details = details[-len(self.live_slots):]
        self._live_pixmaps = []
        for index, slot in enumerate(self.live_slots):
            slot.clear()
            slot.setText("")
            if index >= len(visible):
                self._live_pixmaps.append(None)
                continue
            pixmap = QPixmap()
            if not pixmap.loadFromData(visible[index], "JPEG"):
                self._live_pixmaps.append(None)
                continue
            info = details[index] if index < len(details) else {}
            self._live_pixmaps.append(pixmap)
            slot.setToolTip(
                f"{info.get('width', '?')}\u00d7{info.get('height', '?')} \u00b7 "
                f"{info.get('bytes', 0) / 1024:.0f} KB \u00b7 "
                f"{info.get('age', 0):.1f} s before the newest frame"
            )
        self._fit_live_strip()

    def _fit_live_strip(self) -> None:
        for slot, pixmap in zip(self.live_slots, self._live_pixmaps, strict=False):
            if pixmap is None:
                continue
            slot.setPixmap(
                pixmap.scaled(
                    max(32, slot.width() - 4),
                    max(32, slot.height() - 4),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _live_motion_reset(self) -> None:
        if hasattr(self, "live_motion"):
            self.live_motion.setValue(0)
            self.live_motion.setFormat("motion \u2014 no clip yet")

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Resize and obj in getattr(self, "live_slots", []):
            self._fit_live_strip()
        return super().eventFilter(obj, event)

    def _live_vision_finished(self) -> None:
        self._live_session = None
        if self._live_thread is not None:
            self._live_thread.deleteLater()
            self._live_thread = None
        signals_blocked = self.live_toggle.blockSignals(True)
        self.live_toggle.setChecked(False)
        self.live_toggle.blockSignals(signals_blocked)
        self.live_interval.setEnabled(True)
        self.live_frames.setEnabled(True)
        self.live_sample.setEnabled(True)

    def shutdown(self) -> None:
        self._stop_live_vision()
        for thread in (
            self._thread,
            self._voice_thread,
            self._proactive_check,
            self._proactive_request,
            self._live_thread,
        ):
            if thread is not None and thread.isRunning():
                thread.wait(3000)

    # -- read aloud and text size ----------------------------------------------
    def _build_speech_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.read_aloud = QCheckBox("Read replies aloud")
        self.read_aloud.setToolTip(
            "Speaks each AI reply with the text-to-speech engine. "
            "Same saved value as Speak responses aloud in Settings > Voice."
        )
        self.read_aloud.toggled.connect(self._on_read_aloud_toggled)
        row.addWidget(self.read_aloud)
        self.stop_speaking_btn = QPushButton(" Stop speaking")
        self.stop_speaking_btn.setObjectName("secondary")
        set_glyph(self.stop_speaking_btn, "stop")
        self.stop_speaking_btn.setToolTip(
            "Clears replies waiting to be read. The sentence being read finishes."
        )
        self.stop_speaking_btn.clicked.connect(self.stop_speaking_requested.emit)
        row.addWidget(self.stop_speaking_btn)
        self.speech_status = _label("", wrap=True)
        row.addWidget(self.speech_status, 1)
        return row

    def _build_voice_bar(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(8)
        sliders = QHBoxLayout()
        sliders.setSpacing(10)
        sliders.addWidget(_label("Speed", "formLabel"))
        self.speed_slider = ValueSlider(
            round(SPEED_MIN * 100), round(SPEED_MAX * 100), fmt=lambda v: f"{v / 100:.2f}x"
        )
        self.speed_slider.setToolTip("Speaking speed. Changes apply to the next sentence spoken.")
        self.speed_slider.valueChanged.connect(lambda _v: self._emit_speech_options())
        sliders.addWidget(self.speed_slider, 1)
        sliders.addWidget(_label("Volume", "formLabel"))
        self.volume_slider = ValueSlider(VOLUME_MIN, VOLUME_MAX, fmt=lambda v: f"{v}%")
        # Explicit: a new slider would sit at the range minimum (0%, silent), so the
        # default is set here before refresh_from_settings restores anything saved.
        self.volume_slider.setValue(VOLUME_DEFAULT)
        self.volume_slider.setToolTip("Speech volume. Changes apply to the next sentence spoken.")
        self.volume_slider.valueChanged.connect(lambda _v: self._emit_speech_options())
        sliders.addWidget(self.volume_slider, 1)
        box.addLayout(sliders)

        voice_row = QHBoxLayout()
        voice_row.setSpacing(10)
        voice_row.addWidget(_label("Voice", "formLabel"))
        self.voice_combo = ComboField()
        self.voice_combo.setToolTip("Voices this system's speech engine offers.")
        self.voice_combo.addItem("Engine default", "")
        self.voice_combo.currentIndexChanged.connect(lambda _i: self._on_voice_chosen())
        voice_row.addWidget(self.voice_combo, 1)
        self.test_voice_btn = QPushButton(" Test voice")
        self.test_voice_btn.setObjectName("secondary")
        set_glyph(self.test_voice_btn, "speaker")
        self.test_voice_btn.clicked.connect(self._on_test_voice)
        voice_row.addWidget(self.test_voice_btn)
        box.addLayout(voice_row)

        box.addWidget(_label(
            "Pitch is not available: the speech engines this app uses do not expose a pitch setting."
        ))
        return box

    def speech_options(self) -> SpeechOptions:
        return SpeechOptions(
            speed=self.speed_slider.value() / 100,
            volume=self.volume_slider.value(),
            voice=self._voice_name,
        ).clamped()

    def set_voices(self, names: list[str]) -> None:
        """Fill the voice list from the speech engine, selecting the saved voice if present."""
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItem("Engine default", "")
        for name in names:
            self.voice_combo.addItem(name, name)
        index = self.voice_combo.findData(self._voice_name) if self._voice_name else -1
        self.voice_combo.setCurrentIndex(index if index >= 0 else 0)
        self.voice_combo.blockSignals(False)

    def _on_voice_settings_toggled(self, checked: bool) -> None:
        self.voice_settings.setVisible(bool(checked))

    def _update_voice_tooltip(self) -> None:
        self.voice_settings_toggle.setToolTip(
            f"Settings: read aloud {'on' if self.read_aloud.isChecked() else 'off'}, "
            f"speed {self.speed_slider.value() / 100:.2f}x, "
            f"volume {self.volume_slider.value()}%, "
            f"text {self.font_slider.value()} px"
        )

    def _emit_speech_options(self) -> None:
        options = self.speech_options()
        self._settings.setValue("tts/speed", options.speed)
        self._settings.setValue(VOLUME_KEY, options.volume)
        self._update_voice_tooltip()
        self.speech_options_changed.emit(options)

    def _on_voice_chosen(self) -> None:
        self._voice_name = str(self.voice_combo.currentData() or "")
        self._settings.setValue("tts/voice", self._voice_name)
        self._emit_speech_options()

    def _on_test_voice(self) -> None:
        if not self.read_aloud.isChecked():
            self.speech_status.setText("Turn on Read replies aloud to hear the voice test.")
            return
        self.voice_test_requested.emit()

    def _build_text_size_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(_label("Text size", "formLabel"))
        self.font_slider = ValueSlider(self.TEXT_MIN, self.TEXT_MAX, fmt=lambda v: f"{v} px")
        self.font_slider.setToolTip("Makes the chat transcript text larger or smaller.")
        self.font_slider.valueChanged.connect(self._on_font_size_changed)
        row.addWidget(self.font_slider, 1)
        self.font_reset_btn = QPushButton("Reset")
        self.font_reset_btn.setObjectName("secondary")
        self.font_reset_btn.setToolTip(f"Back to {self.TEXT_DEFAULT} px")
        self.font_reset_btn.clicked.connect(lambda: self.font_slider.setValue(self.TEXT_DEFAULT))
        row.addWidget(self.font_reset_btn)
        return row

    @staticmethod
    def ensure_volume_default(settings) -> int:
        """Store the 100% default once, so the volume is explicit from the first launch.

        Returns the value that will be used: the stored one when the user has ever set
        it (including 0, which means muted), otherwise 100.
        """
        stored = settings.value(VOLUME_KEY)
        if stored is None:
            settings.setValue(VOLUME_KEY, VOLUME_DEFAULT)
            return VOLUME_DEFAULT
        try:
            return SpeechOptions(volume=stored).clamped().volume
        except (TypeError, ValueError):
            # A corrupt value must not silence the coach: fall back to the default.
            settings.setValue(VOLUME_KEY, VOLUME_DEFAULT)
            return VOLUME_DEFAULT

    def refresh_from_settings(self) -> None:
        """Show the saved read-aloud and text-size values. Signals stay quiet."""
        if hasattr(self, "live_note"):
            self._refresh_live_vision_note()  # the model may have changed in Settings
        enabled = self._settings.value("ui/tts_enabled", False, type=bool)
        px = self._settings.value("chat/font_px", self.TEXT_DEFAULT, type=int)
        px = max(self.TEXT_MIN, min(self.TEXT_MAX, int(px)))
        self.read_aloud.blockSignals(True)
        self.read_aloud.setChecked(bool(enabled))
        self.read_aloud.blockSignals(False)
        self.font_slider.blockSignals(True)
        self.font_slider.setValue(px)
        self.font_slider.blockSignals(False)
        # Speech controls: saved values, shown without emitting.
        saved = SpeechOptions(
            speed=self._settings.value("tts/speed", 1.0, type=float),
            volume=self.ensure_volume_default(self._settings),
            voice=str(self._settings.value("tts/voice", "") or ""),
        ).clamped()
        self._voice_name = saved.voice
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(round(saved.speed * 100))
        self.speed_slider.blockSignals(False)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(saved.volume)
        self.volume_slider.blockSignals(False)
        self._apply_text_size(px)
        self._show_default_speech_text()
        if self._transcript_items:
            self._render_transcript()

    def _on_read_aloud_toggled(self, checked: bool) -> None:
        self._settings.setValue("ui/tts_enabled", bool(checked))
        self._show_default_speech_text()
        self._update_voice_tooltip()
        self.read_aloud_changed.emit(bool(checked))

    def _show_default_speech_text(self) -> None:
        if self.read_aloud.isChecked():
            self.speech_status.setText("Replies are read aloud.")
        else:
            self.speech_status.setText("Replies are shown as text only.")

    def set_speech_status(self, text: str) -> None:
        self.speech_status.setText(text)

    def set_speech_failed(self) -> None:
        self.speech_status.setText(
            "Read-aloud is unavailable on this system. See the message in the transcript."
        )

    def _on_font_size_changed(self, px: int) -> None:
        self._apply_text_size(px)
        self._settings.setValue("chat/font_px", int(px))
        self._update_voice_tooltip()

    def _apply_text_size(self, px: int) -> None:
        px = int(px)
        # The app stylesheet sets font-size on every QWidget, and a stylesheet overrides
        # setFont(). A widget-level font-size rule wins over it; the other transcript
        # styles (background, padding, colour) still come from the app stylesheet.
        self.transcript.setStyleSheet(f"font-size: {px}px;")
        font = self.transcript.font()
        font.setPixelSize(px)
        self.transcript.setFont(font)
        # The transcript's HTML sets no sizes of its own, so the document default decides.
        self.transcript.document().setDefaultFont(font)

    def _append(self, who, text):
        """Keep a theme-aware message record and insert a right/left-aligned bubble."""
        self._transcript_items.append((str(who), str(text)))
        self._insert_chat_bubble(who, text)

    def _render_transcript(self) -> None:
        self.transcript.clear()
        for who, text in self._transcript_items:
            self._insert_chat_bubble(who, text)

    def _insert_chat_bubble(self, who, text) -> None:
        """Insert an escaped chat bubble: user right, coach left."""
        is_user = str(who).strip().casefold() in {"you", "user"}
        palette = active_palette()
        if is_user:
            base = QColor(palette["surface"])
            accent = QColor(palette["accent"])
            mix = 0.14
            background = QColor(
                round(base.red() * (1.0 - mix) + accent.red() * mix),
                round(base.green() * (1.0 - mix) + accent.green() * mix),
                round(base.blue() * (1.0 - mix) + accent.blue() * mix),
            ).name()
            border = accent.name()
            alignment = "right"
        else:
            background = palette["surface2"]
            border = palette["line2"]
            alignment = "left"
        label = html.escape(str(who))
        body = html.escape(str(text)).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
        fragment = (
            f'<table align="{alignment}" width="78%" cellspacing="0" cellpadding="8" '
            f'border="1" bordercolor="{border}" bgcolor="{background}"><tr>'
            f'<td bgcolor="{background}"><span style="color:{palette["accent"]};">'
            f"<b>{label}:</b></span> <span style=\"color:{palette['text']}\">{body}</span>"
            "</td></tr></table>"
        )
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(fragment)
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _chat_settings(self) -> ChatSettings:
        s = self._settings
        return ChatSettings(
            provider=str(s.value("ai/provider", "ollama")),
            base_url=str(s.value("ai/base_url", "http://127.0.0.1:11434")),
            model=str(s.value("ai/model", "qwen3:1.7b")),
            temperature=float(s.value("ai/temperature", 0.35)),
        )

    def _send(self):
        text = self.input.text().strip()
        if not text or (self._thread is not None and self._thread.isRunning()):
            return
        self._append("You", text)
        self.input.clear()
        self._messages.append({"role": "user", "content": text})
        self._thread = _ChatRequest(self._chat_settings(), list(self._messages), self._calibration_context, self)
        self._thread.response_ready.connect(self._on_reply)
        self._thread.request_failed.connect(self._on_failed)
        self.send_btn.setEnabled(False)
        self._thread.finished.connect(lambda: self.send_btn.setEnabled(True))
        self._thread.finished.connect(self._request_finished)
        self._request_started()
        self._thread.start()

    def _request_started(self) -> None:
        """Show the thinking indicator. Requests can overlap, so this is counted."""
        self._pending_requests += 1
        if self._pending_requests == 1:
            self.transcript.setViewportMargins(0, 0, 0, self._THINKING_STRIP)
            self._thinking.start()

    def _request_finished(self) -> None:
        """Hide the indicator once every pending request has finished (reply or error)."""
        self._pending_requests = max(0, self._pending_requests - 1)
        if self._pending_requests == 0:
            self._thinking.stop()
            self.transcript.setViewportMargins(0, 0, 0, 0)

    def _on_reply(self, answer: str):
        self._messages.append({"role": "assistant", "content": answer})
        visible = _visible_ai_reply(answer)
        self._append("Coach", visible)
        self.assistant_said.emit(visible)
        self._apply_ai_adjustment(answer)

    def _on_failed(self, message: str):
        if self._messages and self._messages[-1]["role"] == "user":
            self._messages.pop()  # keep the history valid for the next attempt
        self._append("Coach", f"Error: {message}")

    def _start_voice(self, checked: bool):
        if not checked or (self._voice_thread is not None and self._voice_thread.isRunning()):
            return
        self._voice_thread = _VoiceListenThread(self)
        self._voice_thread.recognized.connect(self._on_voice_text)
        self._voice_thread.failed.connect(self._on_voice_failed)
        self._voice_thread.finished.connect(lambda: self.mic_btn.setChecked(False))
        self._voice_thread.start()

    def _on_voice_text(self, text: str):
        if text:
            self.input.setText(text)
            self._send()

    def _on_voice_failed(self, message: str):
        self._append("Voice", message)

    def clear_history(self) -> None:
        """Forget the on-screen transcript and the messages sent to the model (Privacy page)."""
        self._messages = []
        self._transcript_items.clear()
        self.transcript.clear()


def _is_timestamp_model(model: str) -> bool:
    """True for the families trained on interleaved timestamp + video-frame input."""
    name = (model or "").strip().lower()
    return any(hint in name for hint in ("qwen3-vl", "qwen3vl", "qwen2.5-vl", "qwen2.5vl"))


PRIVACY_POINTS: tuple[str, ...] = (
        "Calibration observes timestamped cursor positions only during the explicit in-app target task. "
        "Its movement-speed analysis and data-derived curve run locally; raw cursor samples are processed "
        "in memory and are not sent to the AI endpoint. Calibration does not use screen frames.",
        "Recording (Capture tab) is off unless you start it. It records the primary display. "
        "Without \"Keep the recording\" the file is deleted when you stop or close the app; with "
        "it, the video is saved to the folder you pick and stays there until you delete it. "
        "Recordings are never uploaded by the app. The analysis timer samples frame statistics "
        "only, never targets or gameplay events.",
        "In-Game Mode (Settings) is off at every launch and only turns on when you enable it "
        "for that session. It never reads or changes the game: you type your game, DPI, and "
        "in-game sensitivity, and the app converts those numbers. While it is on, those numbers "
        "are included in the context sent to your configured AI endpoint.",
        "Live vision (AI Coach) is off at every launch. While it is on, the capture preview is "
        "sampled into short clips of downscaled frames (4 frames half a second apart by default) and "
        "each clip is sent to the AI endpoint you configured, one clip at a time, with the reply in "
        "the chat. The frames are held in memory only and are never recorded by this feature. With a "
        "local Ollama model they stay on this machine; with an online provider they are uploaded to "
        "it. Turn it off and nothing leaves the app. The coach is asked to comment on the movement "
        "and the setup only - never on targets, enemies, or aim.",
        "AI requests are sent only to the endpoint you configure in Settings. Text-coaching requests can include "
        "the conversation, calibration summary, and up to 32 horizontal speed/gain points from the active curve; "
        "raw cursor samples are never sent. In-Game Mode values are included only while that session toggle is on.",
        "Voice recognition can run fully offline via a local model.",
        "The crosshair overlay is off by default. When it is on, or while you edit its "
        "position, it draws a crosshair on your primary screen only. The active crosshair "
        "overlay does not interact with or inject "
        "into any game processes. It does not read game frames, hook input, or read or "
        "write game memory. Locked, it ignores the mouse so clicks reach the game.",
        "The capture window list reads window titles and process names only, so you can "
        "choose a window. It does not read or write any process's memory.",
        "Preferences are stored in your system's settings store. The chat transcript is kept only for this session.",
)

class PrivacyPage(QWidget):
    """What stays on this machine, and a real way to delete the stored history."""

    # The main window clears the in-session calibration and recommendation state on this.
    calibration_cleared = Signal()

    def __init__(self, store: AnalyticsStore | None = None, chat_page=None, parent=None,
                 calibration_path=None):
        super().__init__(parent)
        self._store = store
        self._chat = chat_page
        self.calibration_path = calibration_path  # saved calibration history file (may be None)

        scroll, outer = _content_page(960)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Privacy",
            "Vantage runs its vision and coaching pipeline locally.",
            eyebrow="Help",
        ))

        points = PRIVACY_POINTS
        card, lay = _card("Data handling")
        for point in points:
            row = QHBoxLayout()
            mark = QLabel()
            set_glyph(mark, "check", 16, role="good")
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(_label(point, "cardSubtitle", wrap=True), 1)
            lay.addLayout(row)
        outer.addWidget(card)

        card2, lay2 = _card(
            "Local data",
            "Training history is saved to .fpscoach_analytics.json in the project folder.",
        )
        self.count_label = _label("", "cardSubtitle")
        lay2.addWidget(self.count_label)
        self.clear_btn = QPushButton(" Clear training history and chat")
        self.clear_btn.setObjectName("secondary")
        set_glyph(self.clear_btn, "trash")
        self.clear_btn.clicked.connect(self._clear)
        lay2.addWidget(self.clear_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.status = _label("", "statusLabel", wrap=True)
        lay2.addWidget(self.status)

        lay2.addWidget(_divider())
        lay2.addWidget(_label(
            "Calibration and recommendation history is saved to .fpscoach_calibration_history.json "
            "in the project folder. The app does not add new calibration results to it.",
            "cardSubtitle", wrap=True,
        ))
        self.calibration_count_label = _label("", "cardSubtitle")
        lay2.addWidget(self.calibration_count_label)
        self.clear_calibration_btn = QPushButton(" Clear calibration and recommendation history")
        self.clear_calibration_btn.setObjectName("secondary")
        set_glyph(self.clear_calibration_btn, "trash")
        self.clear_calibration_btn.clicked.connect(self._clear_calibration)
        lay2.addWidget(self.clear_calibration_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.calibration_status = _label("", "statusLabel", wrap=True)
        lay2.addWidget(self.calibration_status)
        outer.addWidget(card2)
        outer.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        count = len(self._store.sessions) if self._store is not None else 0
        self.count_label.setText(f"{count} saved training session{'s' if count != 1 else ''}.")
        saved = count_calibration_records(self.calibration_path)
        self.calibration_count_label.setText(
            f"{saved} saved calibration result{'s' if saved != 1 else ''}."
        )

    def _clear_calibration(self):
        answer = QMessageBox.question(
            self,
            "Vantage",
            "Delete the saved calibration and recommendation history, and clear the calibration "
            "and recommendation shown on screen? This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = clear_calibration_history(self.calibration_path)
        self.calibration_cleared.emit()
        self.refresh()
        self.calibration_status.setText(
            f"Cleared {removed} saved calibration result{'s' if removed != 1 else ''} "
            "and the recommendation on screen."
        )

    def _clear(self):
        answer = QMessageBox.question(
            self,
            "Vantage",
            "Delete all saved training sessions and clear the chat on screen? This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = len(self._store.sessions) if self._store is not None else 0
        if self._store is not None:
            self._store.clear()
        if self._chat is not None:
            self._chat.clear_history()
        self.refresh()
        self.status.setText(f"Cleared {removed} training session{'s' if removed != 1 else ''} and the chat.")


class SettingsPage(QWidget):
    settings_changed = Signal()
    bounce_speed_changed = Signal(int)  # serious-mode speed percentage, sent on every slider step
    ingame_changed = Signal()  # In-Game Mode toggle or any of its values changed
    ingame_question = Signal(str)  # "Ask the coach" with a ready-made question

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._pull_thread = None
        self._status_thread = None

        scroll, outer = _content_page(1240)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Settings",
            "Configure the AI backend, interface, and voice pipeline.",
            eyebrow="App",
        ))

        columns = QHBoxLayout()
        columns.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(20)
        left.addWidget(self._build_ai_card())
        left.addWidget(self._build_voice_card())
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(20)
        right.addWidget(self._build_ui_card())
        # No Replies card: read aloud, Stop speaking and the reply text size belong to the
        # AI Coach tab's Settings block, and duplicating them here meant two controls for
        # one saved value. The Voice card below keeps the "Speak responses aloud" switch.
        # In-Game Mode lives inside Advanced now: it is a session-only extra that most
        # people never open, so it should not take a card's worth of room by default.
        right.addWidget(self._build_advanced_card())
        right.addStretch(1)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        outer.addLayout(columns)

    def _build_ai_card(self):
        card, lay = _card("AI coach", "Point Vantage at a local or remote model endpoint.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Provider", "formLabel"), 0, 0)
        self.provider = ComboField()
        self.provider.addItems(["ollama", "openai-compatible"])
        self.provider.setCurrentText(self.s.value("ai/provider", "ollama"))
        self.provider.currentTextChanged.connect(self._on_ai_changed)
        form.addWidget(self.provider, 0, 1)

        form.addWidget(_label("Base URL", "formLabel"), 1, 0)
        self.base_url = QLineEdit(self.s.value("ai/base_url", "http://127.0.0.1:11434"))
        self.base_url.editingFinished.connect(self._on_ai_changed)
        form.addWidget(self.base_url, 1, 1)

        form.addWidget(_label("Model", "formLabel"), 2, 0)
        model_row = QHBoxLayout()
        # Editable, so a name can be picked from the list *or* typed (a model that is not
        # installed yet, a tag this endpoint names differently). Picking saves immediately;
        # typing is committed on focus-out, as before.
        self.model = ComboField()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setToolTip(
            "Pick a model from the list, or type a name. The list button asks the provider "
            "which models it has."
        )
        self.model.lineEdit().setPlaceholderText("Pick a model, or type one")
        self.model.textActivated.connect(lambda _text: self._on_ai_changed())
        self.model.lineEdit().editingFinished.connect(self._on_ai_changed)
        self.pull_btn = QToolButton()
        set_glyph(self.pull_btn, "download")
        self.pull_btn.setObjectName("iconButton")
        self.pull_btn.setToolTip("Download the model named above into the local Ollama.")
        self.pull_btn.clicked.connect(self._pull_model)
        self.model_list_btn = QToolButton()
        set_glyph(self.model_list_btn, "refresh")
        self.model_list_btn.setObjectName("iconButton")
        self.model_list_btn.setToolTip(
            "List the models this provider offers (Ollama: the ones installed on this machine)."
        )
        self.model_list_btn.clicked.connect(self._list_models)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.pull_btn)
        model_row.addWidget(self.model_list_btn)
        form.addLayout(model_row, 2, 1)

        self.model_hint = _label("", "cardSubtitle", wrap=True)
        form.addWidget(self.model_hint, 4, 0, 1, 2)

        form.addWidget(_label("Temperature", "formLabel"), 3, 0)
        # Stored as 0.00-1.50; the slider works in hundredths so the readout shows the stored value.
        self.temperature = ValueSlider(0, 150, fmt=lambda v: f"{v / 100:.2f}")
        self.temperature.setValue(int(float(self.s.value("ai/temperature", 0.35)) * 100))
        self.temperature.valueChanged.connect(self._on_ai_changed)
        form.addWidget(self.temperature, 3, 1)

        lay.addLayout(form)
        self._fill_model_choices()

        self.pull_progress = QProgressBar()
        self.pull_progress.setValue(0)
        self.pull_progress.setTextVisible(True)
        self.pull_progress.setVisible(False)
        lay.addWidget(self.pull_progress)

        status_row = QHBoxLayout()
        self.check_btn = QPushButton(" Check connection")
        self.check_btn.setObjectName("secondary")
        set_glyph(self.check_btn, "refresh")
        self.check_btn.clicked.connect(self._check_status)
        self.status_label = QLabel("Not checked")
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.check_btn)
        status_row.addWidget(self.status_label, 1)
        lay.addLayout(status_row)

        return card

    MODEL_CACHE_KEY = "ai/model_list"  # names the provider reported last time

    def _model_name(self) -> str:
        """The chosen name, without the "(suggested)" label, or whatever was typed."""
        typed = str(self.model.currentText() or "").strip()
        index = self.model.currentIndex()
        if index >= 0:
            data = self.model.itemData(index) or {}
            name = str(data.get("name", "") or "").strip()
            if name and typed.casefold() == self._model_label(name, str(data.get("source", ""))).casefold():
                return name
        return typed

    @staticmethod
    def _model_label(name: str, source: str) -> str:
        """How an entry reads in the list. Suggestions say so, so a pick is never a surprise."""
        if source == "suggested":
            return f"{name} (suggested, not installed)"
        return name

    def _model_index(self, name: str) -> int:
        """Index of ``name`` in the list, matched on the real name rather than the label."""
        target = str(name or "").strip().casefold()
        if not target:
            return -1
        for index in range(self.model.count()):
            data = self.model.itemData(index) or {}
            if str(data.get("name", "")).strip().casefold() == target:
                return index
        return -1

    def _saved_model(self) -> str:
        return str(self.s.value("ai/model", "qwen3:1.7b") or "").strip()

    def _cached_models(self) -> list[str]:
        """Names the provider listed last time. Survives a restart, so the list is never empty."""
        stored = self.s.value(self.MODEL_CACHE_KEY, [])
        if isinstance(stored, str):
            stored = [stored]
        return [str(name).strip() for name in (stored or []) if str(name).strip()]

    def _fill_model_choices(self, installed: list[str] | None = None) -> None:
        """Rebuild the model list: remembered names first, then suggestions.

        The current name is never dropped from the box even when the provider no longer
        lists it, and every item records where it came from so the hint can say whether
        picking it means "already installed" or "will be downloaded".
        """
        current = self._model_name() or self._saved_model()
        known = self._cached_models() if installed is None else list(installed)
        ordered: list[tuple[str, str]] = []
        if current:
            ordered.append((current, "current"))
        for name in known:
            ordered.append((name, "installed"))
        for name in suggested_text_models():
            ordered.append((name, "suggested"))

        seen: set[str] = set()
        items: list[tuple[str, str]] = []
        for name, source in ordered:
            key = str(name).strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append((str(name).strip(), source))

        blocked = self.model.blockSignals(True)
        self.model.clear()
        for name, source in items:
            self.model.addItem(self._model_label(name, source), {"name": name, "source": source})
        self.model.setCurrentIndex(self._model_index(current))
        self.model.blockSignals(blocked)
        self._update_model_hint(len(known))

    def _update_model_hint(self, known_count: int | None = None) -> None:
        known = self._cached_models() if known_count is None else known_count
        current = self._model_name()
        parts = [
            f"{known} model(s) from the provider" if known else "No list from the provider yet",
            f"{len(suggested_text_models())} suggested names",
        ]
        data = self.model.currentData()
        source = str(data.get("source", "")) if isinstance(data, dict) and current else None
        if source == "suggested":
            detail = (
                f"\"{current}\" is a suggested name, not a listed one: pull it with the "
                "download button."
            )
        elif source == "installed":
            detail = f"\"{current}\" is one the provider lists."
        elif current:
            detail = f"\"{current}\" is the saved name."
        else:
            detail = "Nothing is selected: pick a name from the list, or type one."
        self.model_hint.setText(" · ".join(parts) + ". " + detail)

    def _provider_key(self) -> str:
        """The provider name ai_chat understands ("openai-compatible" is "openai" there)."""
        text = str(self.provider.currentText() or "").strip().lower()
        return "openai" if text.startswith("openai") else "ollama"

    def _list_models(self) -> None:
        """Ask the provider which models it has, then refill the list."""
        running = getattr(self, "_model_thread", None)
        if running is not None and running.isRunning():
            return
        self.status_label.setText("Listing models…")
        self._model_thread = _ModelListThread(self.base_url.text(), self._provider_key(), parent=self)
        self._model_thread.models_ready.connect(self._on_models_ready)
        self._model_thread.failed.connect(self._on_models_failed)
        self._model_thread.finished.connect(self._model_list_finished)
        self._model_thread.start()

    def _model_list_finished(self) -> None:
        thread = getattr(self, "_model_thread", None)
        self._model_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_models_ready(self, models) -> None:
        names = [str(name).strip() for name in (models or []) if str(name).strip()]
        self.s.setValue(self.MODEL_CACHE_KEY, names)
        self._fill_model_choices(installed=names)
        if names:
            self._on_status(True, f"Found {len(names)} model(s) on this provider.")
        else:
            self._on_status(
                False, "The provider answered but lists no models. Pull one with the download button."
            )

    def _on_models_failed(self, message: str) -> None:
        self._update_model_hint()
        self._on_status(False, f"Could not list models: {message}. Known names are still selectable.")

    def showEvent(self, event) -> None:
        """First time the page is opened, fill the list from the provider by itself."""
        super().showEvent(event)
        if getattr(self, "_models_probed", False):
            return
        self._models_probed = True
        if not self._cached_models():
            self._list_models()

    def shutdown(self) -> None:
        """Let any background request finish before the page goes away."""
        for name in ("_model_thread", "_status_thread", "_pull_thread"):
            thread = getattr(self, name, None)
            if thread is None:
                continue
            try:
                if thread.isRunning():
                    thread.wait(3000)
            except RuntimeError:  # Qt already deleted it; nothing left to wait for
                continue

    def _on_ai_changed(self, *_args):
        self.s.setValue("ai/provider", self.provider.currentText())
        self.s.setValue("ai/base_url", self.base_url.text())
        self.s.setValue("ai/model", self._model_name())
        self.s.setValue("ai/temperature", self.temperature.value() / 100.0)
        self.settings_changed.emit()

    def _check_status(self):
        self.status_label.setText("Checking…")
        self._status_thread = _OllamaStatusCheck(self.base_url.text(), self._model_name())
        self._status_thread.status_ready.connect(self._on_status_ready)
        self._status_thread.start()

    def _on_status_ready(self, reachable, has_model):
        if not reachable:
            self._on_status(False, "Ollama is not reachable at this address.")
        elif not has_model:
            self._on_status(False, "Ollama is running, but this model is not installed. Use the download button to pull it.")
        else:
            self._on_status(True, "Connected. Model is installed.")

    def _on_status(self, ok, message):
        self.status_label.setText(message)
        self.status_label.setProperty("state", "good" if ok else "warn")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _pull_model(self):
        self.pull_progress.setVisible(True)
        self.pull_progress.setValue(0)
        self._pull_thread = _ModelPullThread(self.base_url.text(), self._model_name())
        self._pull_thread.progress.connect(lambda msg: self.pull_progress.setFormat(msg))
        self._pull_thread.finished_ok.connect(
            lambda: self._on_pull_finished(True, f"Model {self._model_name()} is installed.")
        )
        self._pull_thread.failed.connect(lambda msg: self._on_pull_finished(False, msg))
        self._pull_thread.start()

    def _on_pull_finished(self, ok, message):
        self.pull_progress.setVisible(False)
        self.status_label.setText(message)

    def _build_ui_card(self):
        card, lay = _card("Interface", "Choose the visual mode, theme, and motion preferences.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Mode", "formLabel"), 0, 0)
        self.mode = ComboField()
        self.mode.addItems(["luxurious", "performance"])
        self.mode.setCurrentText(self.s.value("ui/mode", "luxurious"))
        self.mode.currentTextChanged.connect(self._on_ui_changed)
        form.addWidget(self.mode, 0, 1)

        form.addWidget(_label("Theme", "formLabel"), 1, 0)
        self.theme = ComboField()
        self.theme.addItems(["system", "dark", "light"])
        self.theme.setCurrentText(self.s.value("ui/theme", "system"))
        self.theme.currentTextChanged.connect(self._on_ui_changed)
        form.addWidget(self.theme, 1, 1)

        self.animations = QCheckBox("Enable animations")
        self.animations.setChecked(self.s.value("ui/animations", True, type=bool))
        self.animations.toggled.connect(self._on_ui_changed)
        form.addWidget(self.animations, 2, 0, 1, 2)

        self.coach_panel = QCheckBox("Show floating coach panel")
        self.coach_panel.setChecked(self.s.value("ui/coach_panel", False, type=bool))
        self.coach_panel.toggled.connect(self._on_ui_changed)
        form.addWidget(self.coach_panel, 3, 0, 1, 2)

        # Serious mode (dashboard): how fast the tiles bounce. Its own signal, so a drag does not
        # rebuild the dashboard the way a theme change does.
        form.addWidget(_label("Serious mode speed", "formLabel"), 4, 0)
        self.bounce_speed = ValueSlider(
            BOUNCE_SPEED_MIN, BOUNCE_SPEED_MAX, fmt=lambda v: f"{v}%"
        )
        self.bounce_speed.setValue(clamp_bounce_speed(self.s.value("dashboard/bounce_speed", BOUNCE_SPEED_DEFAULT)))
        self.bounce_speed.setToolTip("How fast the dashboard tiles bounce in serious mode.")
        self.bounce_speed.valueChanged.connect(self._on_bounce_speed_changed)
        form.addWidget(self.bounce_speed, 4, 1)

        lay.addLayout(form)
        return card

    def _on_bounce_speed_changed(self, value: int) -> None:
        value = clamp_bounce_speed(value)
        self.s.setValue("dashboard/bounce_speed", value)
        self.bounce_speed_changed.emit(value)

    def _on_ui_changed(self, *_args):
        self.s.setValue("ui/mode", self.mode.currentText())
        self.s.setValue("ui/theme", self.theme.currentText())
        self.s.setValue("ui/animations", self.animations.isChecked())
        self.s.setValue("ui/coach_panel", self.coach_panel.isChecked())
        self.settings_changed.emit()

    def _build_voice_card(self):
        card, lay = _card("Voice", "Hands-free commands and spoken responses.")
        self.voice_commands = QCheckBox("Enable voice commands")
        self.voice_commands.setChecked(self.s.value("ui/voice_commands", False, type=bool))
        self.voice_commands.toggled.connect(self._on_voice_changed)
        lay.addWidget(self.voice_commands)

        wake_row = QHBoxLayout()
        wake_row.addWidget(_label("Wake word", "formLabel"))
        self.wake_word = QLineEdit(self.s.value("ui/wake_word", "coach"))
        self.wake_word.editingFinished.connect(self._on_voice_changed)
        wake_row.addWidget(self.wake_word, 1)
        lay.addLayout(wake_row)

        self.tts_enabled = QCheckBox("Speak responses aloud")
        self.tts_enabled.setChecked(self.s.value("ui/tts_enabled", False, type=bool))
        self.tts_enabled.toggled.connect(self._on_voice_changed)
        lay.addWidget(self.tts_enabled)

        # The app asks the engine for 100% volume, so anything quieter than that is the
        # system's own output level. Say so here, where the voice is switched on.
        self.volume_note = _label(
            "Voice too quiet? Vantage already asks the speech engine for 100% volume, so "
            "check the device before the app.\n"
            "In Windows: Settings > System > Sound > Volume, and Volume mixer to lift Vantage "
            "above the game. Also set Sound > More sound settings > Communications to "
            "\"Do nothing\", so Windows stops turning other audio down when it detects voice chat.",
            "cardSubtitle",
            wrap=True,
        )
        self.volume_note.setToolTip(
            "Windows controls the volume of the whole app. Vantage's own volume slider "
            "(AI Coach > Settings > Voice) is already at its maximum of 100% by default."
        )
        lay.addWidget(self.volume_note)

        model_row = QHBoxLayout()
        model_row.addWidget(_label("Vosk model path", "formLabel"))
        self.vosk_path = QLineEdit(self.s.value("voice/vosk_model_path", ""))
        self.vosk_path.editingFinished.connect(self._on_voice_changed)
        browse_btn = QToolButton()
        set_glyph(browse_btn, "upload")
        browse_btn.setObjectName("iconButton")
        browse_btn.clicked.connect(self._browse_model)
        model_row.addWidget(self.vosk_path, 1)
        model_row.addWidget(browse_btn)
        lay.addLayout(model_row)

        return card

    # ---- In-Game Mode -----------------------------------------------------
    def _build_ingame_card(self, embedded: bool = False):
        """Session-only mode: name the game, get the conversion, ask the coach.

        Off at every launch by design: ``InGameMode`` starts disabled and the
        checkbox state is never written to QSettings, so a restart always lands
        back in the off state. Only the game, DPI, and the user's own
        sensitivity are remembered.

        ``embedded=True`` renders it as a section inside Settings > Advanced: same
        widgets, no card frame, so it does not look like a card inside a card.
        """
        title = "In-Game Mode"
        subtitle = (
            "Tell Vantage which game you are practising and what sensitivity you use there, so "
            "the coach can talk in that game's numbers."
        )
        if embedded:
            card = QFrame()
            card.setObjectName("advancedSection")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(0, 14, 0, 0)
            lay.setSpacing(10)
            lay.addWidget(_label(title, "cardTitle"))
            lay.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        else:
            card, lay = _card(title, subtitle)

        self.ingame_enabled = QCheckBox("Enable In-Game Mode (this session only)")
        self.ingame_enabled.setToolTip(
            "Turns off automatically every time Vantage starts. Vantage does not read or change "
            "your game; you enter these values yourself."
        )
        self.ingame_enabled.setChecked(False)  # never restored from settings
        self.ingame_enabled.toggled.connect(self._on_ingame_toggled)
        lay.addWidget(self.ingame_enabled)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Game", "formLabel"), 0, 0)
        self.ingame_game = ComboField()
        for game in GAMES:
            self.ingame_game.addItem(game.name, game.key)
        saved_key = str(self.s.value("ingame/game", GAMES[0].key))
        index = self.ingame_game.findData(saved_key)
        self.ingame_game.setCurrentIndex(index if index >= 0 else 0)
        self.ingame_game.currentIndexChanged.connect(self._on_ingame_values_changed)
        form.addWidget(self.ingame_game, 0, 1)

        form.addWidget(_label("Mouse DPI", "formLabel"), 1, 0)
        self.ingame_dpi = QDoubleSpinBox()
        self.ingame_dpi.setRange(100.0, 32000.0)
        self.ingame_dpi.setSingleStep(50.0)
        self.ingame_dpi.setDecimals(0)
        self.ingame_dpi.setValue(float(self.s.value("ingame/dpi", 800.0, type=float)))
        self.ingame_dpi.valueChanged.connect(self._on_ingame_values_changed)
        form.addWidget(self.ingame_dpi, 1, 1)

        form.addWidget(_label("In-game sensitivity", "formLabel"), 2, 0)
        self.ingame_sens = QDoubleSpinBox()
        self.ingame_sens.setRange(0.001, 100.0)
        self.ingame_sens.setSingleStep(0.01)
        self.ingame_sens.setDecimals(3)
        self.ingame_sens.setValue(float(self.s.value("ingame/sensitivity", 1.0, type=float)))
        self.ingame_sens.valueChanged.connect(self._on_ingame_values_changed)
        form.addWidget(self.ingame_sens, 2, 1)

        form.addWidget(_label("Measured 360°", "formLabel"), 3, 0)
        self.ingame_measured = QDoubleSpinBox()
        self.ingame_measured.setRange(0.0, 200.0)
        self.ingame_measured.setSingleStep(0.5)
        self.ingame_measured.setDecimals(1)
        self.ingame_measured.setSuffix(" cm")
        self.ingame_measured.setSpecialValueText("Not measured")
        self.ingame_measured.setValue(float(self.s.value("ingame/measured_cm360", 0.0, type=float)))
        self.ingame_measured.setToolTip(
            "Optional: swipe a full turn in game, measure the distance, and enter it here. "
            "The conversion then uses your measurement instead of the published constant."
        )
        self.ingame_measured.valueChanged.connect(self._on_ingame_values_changed)
        form.addWidget(self.ingame_measured, 3, 1)

        lay.addLayout(form)

        self.ingame_result = _label("", "cardSubtitle", wrap=True)
        lay.addWidget(self.ingame_result)

        ask_row = QHBoxLayout()
        self.ingame_ask_btn = QPushButton(" Ask the coach")
        self.ingame_ask_btn.setObjectName("secondary")
        set_glyph(self.ingame_ask_btn, "chat")
        self.ingame_ask_btn.clicked.connect(self._ask_ingame)
        ask_row.addWidget(self.ingame_ask_btn)
        ask_row.addStretch(1)
        lay.addLayout(ask_row)

        self.ingame_note = _label(
            "Vantage never reads, hooks, or changes the game. If other software must stay "
            "closed while you play, keep this mode off.",
            "cardSubtitle",
            wrap=True,
        )
        lay.addWidget(self.ingame_note)

        self._ingame = InGameMode(
            game_key=saved_key,
            dpi=self.ingame_dpi.value(),
            sensitivity=self.ingame_sens.value(),
            measured_cm360=self.ingame_measured.value(),
        )
        self._ingame_multiplier: float | None = None
        self.ingame_stats = _label("", "cardSubtitle", wrap=True)
        lay.addWidget(self.ingame_stats)
        self._sync_ingame_controls()
        self._refresh_ingame_result()
        return card

    def ingame_model(self) -> InGameMode:
        """The session state, used by MainWindow to build the AI context."""
        return self._ingame

    def ingame_context(self) -> str:
        """AI context block for the current state (empty while the mode is off)."""
        return self._ingame.context_block(self._ingame_multiplier)

    def set_recommendation(self, recommendation: dict | None) -> None:
        """Show what the app's current recommendation means in this game's units."""
        multiplier = None
        if recommendation:
            try:
                value = float(recommendation.get("sensitivity_multiplier", 0.0) or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                multiplier = value
        self._ingame_multiplier = multiplier
        self._refresh_ingame_result()
        self.ingame_changed.emit()

    def _on_ingame_toggled(self, checked: bool) -> None:
        # Session only: the flag is intentionally not written to QSettings.
        if checked:
            self._ingame.enable()
        else:
            self._ingame.disable()
        self._sync_ingame_controls()
        self._refresh_ingame_result()
        self.ingame_changed.emit()

    def _on_ingame_values_changed(self, *_args) -> None:
        game_key = self.ingame_game.currentData()
        self._ingame.set_game(str(game_key) if game_key else GAMES[0].key)
        self._ingame.set_dpi(self.ingame_dpi.value())
        self._ingame.set_sensitivity(self.ingame_sens.value())
        self._ingame.set_measured_cm360(self.ingame_measured.value())
        self.s.setValue("ingame/game", self._ingame.game.key)
        self.s.setValue("ingame/dpi", float(self.ingame_dpi.value()))
        self.s.setValue("ingame/sensitivity", float(self.ingame_sens.value()))
        self.s.setValue("ingame/measured_cm360", float(self.ingame_measured.value()))
        self._refresh_ingame_result()
        self.ingame_changed.emit()

    def _sync_ingame_controls(self) -> None:
        active = self._ingame.enabled
        for widget in (self.ingame_game, self.ingame_dpi, self.ingame_sens,
                       self.ingame_measured, self.ingame_ask_btn):
            widget.setEnabled(active)
        self.ingame_result.setEnabled(active)

    def _refresh_ingame_result(self) -> None:
        if not self._ingame.enabled:
            self.ingame_result.setText("Off. Enable it to see your cm / 360° and the suggestion.")
            self.ingame_stats.setText("")
            return
        self.ingame_result.setText(self._ingame.feedback(self._ingame_multiplier))
        self.ingame_stats.setText(
            f"{self._ingame.counts_per_360:,.0f} counts per 360°. "
            + (
                "Using your measured distance."
                if self._ingame.measured_cm360 > 0
                else f"Using the {self._ingame.game.name} scale (measure your 360° to refine it)."
            )
        )

    def _ask_ingame(self) -> None:
        if not self._ingame.enabled:
            return
        self.ingame_question.emit(self._ingame.question(self._ingame_multiplier))

    def refresh_voice_state(self) -> None:
        """Show the saved read-aloud value. Signals stay quiet.

        The AI Coach tab owns the switch; this keeps the Voice card's copy in step when
        the chat's "Read replies aloud" changes the same saved value.
        """
        enabled = self.s.value("ui/tts_enabled", False, type=bool)
        self.tts_enabled.blockSignals(True)
        self.tts_enabled.setChecked(bool(enabled))
        self.tts_enabled.blockSignals(False)

    def _browse_model(self):
        path = QFileDialog.getExistingDirectory(self, "Select voice model folder")
        if path:
            self.vosk_path.setText(path)
            self._on_voice_changed()

    def _on_voice_changed(self, *_args):
        self.s.setValue("ui/voice_commands", self.voice_commands.isChecked())
        self.s.setValue("ui/wake_word", self.wake_word.text())
        self.s.setValue("ui/tts_enabled", self.tts_enabled.isChecked())
        self.s.setValue("voice/vosk_model_path", self.vosk_path.text())
        self.refresh_voice_state()
        self.settings_changed.emit()

    # ---- Advanced (disclosure) --------------------------------------------
    def _build_advanced_card(self):
        card, lay = _card()
        header = QHBoxLayout()
        header.addWidget(_label("Advanced", "cardTitle"))
        header.addStretch(1)
        self.advanced_toggle = QToolButton()
        set_glyph(self.advanced_toggle, "chevron_down")
        self.advanced_toggle.setObjectName("iconButton")
        self.advanced_toggle.setCheckable(True)
        header.addWidget(self.advanced_toggle)
        lay.addLayout(header)

        self.advanced_body = QWidget()
        body_lay = QVBoxLayout(self.advanced_body)
        body_lay.setContentsMargins(0, 8, 0, 0)
        body_lay.setSpacing(12)
        body_lay.addWidget(_label(
            "Session-only and setup options. Hardware acceleration is set on the Capture page; "
            "an API key field for hosted providers is not part of this build.",
            "cardSubtitle", wrap=True,
        ))
        # In-Game Mode: off at every launch, and only reaches the coach while it is on.
        self.ingame_card = self._build_ingame_card(embedded=True)
        body_lay.addWidget(self.ingame_card)
        self.advanced_body.setVisible(False)
        lay.addWidget(self.advanced_body)

        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        return card

    def _toggle_advanced(self, checked):
        self.advanced_body.setVisible(checked)
        set_glyph(self.advanced_toggle, "chevron_up" if checked else "chevron_down")
