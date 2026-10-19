"""Coach, privacy, and settings pages.

The AI coach's chat, suggestion, and curve-tuning logic is carried over from the
previous window. The layout is new: the transcript sits beside the tuning panel.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fps_coach.ai_chat import ChatSettings, curve_context
from fps_coach.ai_curve_actions import apply_adjustment, parse_adjustment
from fps_coach.analytics import AnalyticsStore
from fps_coach.chat_suggestions import suggest
from fps_coach.curve_adjust import apply_preset, normalized_overlay

from .threads import _ChatRequest, _ModelPullThread, _OllamaStatusCheck, _VoiceListenThread
from .widgets import (
    CurveChart,
    ValueSlider,
    _card,
    _content_page,
    _curve_samples,
    _label,
    _page_header,
    _page_root,
    _settings,
    set_glyph,
)


class AIChatPage(QWidget):
    """AI Coach chat. Sends turns through ai_chat.AIChatClient (Ollama by default),
    adds the latest calibration as context, and takes one spoken phrase as input.

    Also offers suggested actions and curve tuning on an imported curve, and posts a
    proactive note after each calibration.
    """

    assistant_said = Signal(str)  # emitted with each AI response (coach strip, TTS)
    curve_adjusted = Signal(object, object, str)  # (previous, current, note)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = _settings()
        self._thread = None
        self._voice_thread = None
        self._messages: list[dict[str, str]] = []
        self._summary_context = ""
        self._calibration_context = ""
        self._base_profile = None
        self._adjusted_profile = None
        self._proactive_pending = None
        self._proactive_check = None
        self._proactive_request = None

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
        self.transcript = QTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setMinimumHeight(380)
        lay.addWidget(self.transcript, 1)

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

        tune_card, tune_lay = _card(
            "Curve tuning",
            "Preview a quick action on the imported curve, then send it to Calibration.",
        )
        self.quick_hint = _label(
            "Import a curve in Calibration to enable one-click tuning.", "cardSubtitle", wrap=True
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
        """Give the coach the latest calibration so answers can refer to it."""
        self._summary_context = curve_context(summary, recommendation)
        self._refresh_context()

    def notify_new_calibration(self, summary: dict, recommendation: dict) -> None:
        """Called when a calibration completes: refresh the coach's context and post
        a proactive note without waiting for the user."""
        self.set_calibration_context(summary, recommendation)
        self._append("Coach (auto)", self._local_proactive_note(summary, recommendation))
        self._maybe_proactive_ai(summary, recommendation)

    def set_base_profile(self, profile) -> None:
        """Receive the imported curve so suggestions and AI adjustments can build on it."""
        self._base_profile = deepcopy(profile) if profile is not None else None
        self._adjusted_profile = None
        if self._base_profile is not None:
            self.quick_hint.setText(
                f"Base curve: '{self._base_profile.name}'. Suggestions update as you type; "
                "tap a tuning action to preview it below."
            )
        else:
            self.quick_hint.setText("Import a curve in Calibration to enable one-click tuning.")
            self.quick_preview.hide()
            self.apply_to_rec.hide()
        self._refresh_context()
        self._refresh_suggestions()

    def _refresh_context(self) -> None:
        """Context = latest calibration summary + a description of the imported base curve."""
        context = self._summary_context
        if self._base_profile is not None:
            pts = ", ".join(
                f"({p.speed:.0f},{p.multiplier:.2f})" for p in self._base_profile.horizontal[:12]
            )
            context = (
                f"{context}\n\nImported base curve '{self._base_profile.name}' "
                f"({self._base_profile.source_format}); horizontal speed,gain points: {pts}. "
                "When asked for adjustments, modify THIS curve rather than designing a new one."
            )
        self._calibration_context = context

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

    def _maybe_proactive_ai(self, summary: dict, recommendation: dict) -> None:
        # Never trigger a surprise download or a paid API call without user action.
        if str(self._settings.value("ai/provider", "ollama")) != "ollama":
            return
        if self._proactive_request is not None or self._proactive_check is not None:
            return
        settings = self._chat_settings()
        if not settings.base_url or not settings.model:
            return
        self._proactive_pending = (summary, recommendation)
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
        instruction = (
            "The user just completed a new calibration. Using the calibration summary "
            "and current curve already in your context, give a brief (2-3 sentence) "
            "proactive assessment and one concrete next step. Do not ask questions."
        )
        messages = list(self._messages) + [{"role": "user", "content": instruction}]
        self._proactive_request = _ChatRequest(self._chat_settings(), messages, self._calibration_context, self)
        self._proactive_request.response_ready.connect(self._proactive_received)
        self._proactive_request.request_failed.connect(self._proactive_quiet_fail)
        self._proactive_request.finished.connect(self._proactive_finished)
        self._proactive_request.start()

    def _proactive_received(self, answer: str) -> None:
        self._messages.append({"role": "assistant", "content": answer})
        self._append("Coach", answer)
        self.assistant_said.emit(answer)
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
        for s in suggest(self.input.text(), self._base_profile is not None):
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

    def _show_preview(self, adjusted, note: str) -> None:
        self._adjusted_profile = adjusted
        prev_pts, cur_pts = normalized_overlay(self._base_profile, adjusted)
        self.quick_preview.set_previous(_curve_samples(prev_pts))
        self.quick_preview.points = _curve_samples(cur_pts)
        self.quick_preview.update()
        self.quick_preview.show()
        self.apply_to_rec.show()
        self.quick_hint.setText(note)

    def _apply_quick_action(self, key: str) -> None:
        if self._base_profile is None:
            self._append("Quick action", "Import a curve in Calibration first.")
            return
        try:
            adjusted, note = apply_preset(self._base_profile, key)
        except KeyError:
            return
        self._show_preview(adjusted, note)
        self._append(
            "Quick action",
            f"{note}\nPreview updated. Multiplier now {adjusted.horizontal[0].multiplier:.2f}x at the first point.",
        )

    def _apply_ai_adjustment(self, answer: str) -> None:
        """If the assistant recommended a curve change, apply it to the imported curve
        and show it in the preview and on the Calibration graph."""
        if self._base_profile is None:
            return
        plan = parse_adjustment(answer)
        if not plan:
            return
        try:
            adjusted, note = apply_adjustment(self._base_profile, plan)
        except (ValueError, KeyError):
            return
        self._show_preview(adjusted, note)
        self.curve_adjusted.emit(deepcopy(self._base_profile), deepcopy(adjusted), note)
        self._append("Applied", f"{note} Updated the Calibration graph automatically.")

    def _send_adjusted_to_calibration(self) -> None:
        if self._base_profile is None or self._adjusted_profile is None:
            return
        self.curve_adjusted.emit(
            deepcopy(self._base_profile),
            deepcopy(self._adjusted_profile),
            self._adjusted_profile.description or "Adjusted via AI quick action.",
        )
        self._append("Quick action", "Sent the adjusted curve to Calibration (open it to review and export).")

    def shutdown(self) -> None:
        for thread in (self._thread, self._voice_thread, self._proactive_check, self._proactive_request):
            if thread is not None and thread.isRunning():
                thread.wait(2000)

    def _append(self, who, text):
        self.transcript.append(f"<b>{who}:</b> {text}")

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
        self._thread.start()

    def _on_reply(self, answer: str):
        self._messages.append({"role": "assistant", "content": answer})
        self._append("Coach", answer)
        self.assistant_said.emit(answer)
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
        self.transcript.clear()


class PrivacyPage(QWidget):
    """What stays on this machine, and a real way to delete the stored history."""

    def __init__(self, store: AnalyticsStore | None = None, chat_page=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._chat = chat_page

        scroll, outer = _content_page(960)
        _page_root(self, scroll)
        outer.addWidget(_page_header(
            "Privacy",
            "Vantage runs its vision and coaching pipeline locally.",
            eyebrow="Help",
        ))

        points = [
            "Screen capture frames are processed in memory and never written to disk.",
            "AI requests are sent only to the endpoint you configure in Settings.",
            "Voice recognition can run fully offline via a local model.",
            "Preferences are stored in your system's settings store. The chat transcript is kept only for this session.",
        ]
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
        outer.addWidget(card2)
        outer.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        count = len(self._store.sessions) if self._store is not None else 0
        self.count_label.setText(f"{count} saved training session{'s' if count != 1 else ''}.")

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
        self.provider = QComboBox()
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
        self.model = QLineEdit(self.s.value("ai/model", "qwen3:1.7b"))
        self.model.editingFinished.connect(self._on_ai_changed)
        self.pull_btn = QToolButton()
        set_glyph(self.pull_btn, "download")
        self.pull_btn.setObjectName("iconButton")
        self.pull_btn.clicked.connect(self._pull_model)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.pull_btn)
        form.addLayout(model_row, 2, 1)

        form.addWidget(_label("Temperature", "formLabel"), 3, 0)
        # Stored as 0.00-1.50; the slider works in hundredths so the readout shows the stored value.
        self.temperature = ValueSlider(0, 150, fmt=lambda v: f"{v / 100:.2f}")
        self.temperature.setValue(int(float(self.s.value("ai/temperature", 0.35)) * 100))
        self.temperature.valueChanged.connect(self._on_ai_changed)
        form.addWidget(self.temperature, 3, 1)

        lay.addLayout(form)

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

    def _on_ai_changed(self, *_args):
        self.s.setValue("ai/provider", self.provider.currentText())
        self.s.setValue("ai/base_url", self.base_url.text())
        self.s.setValue("ai/model", self.model.text())
        self.s.setValue("ai/temperature", self.temperature.value() / 100.0)
        self.settings_changed.emit()

    def _check_status(self):
        self.status_label.setText("Checking…")
        self._status_thread = _OllamaStatusCheck(self.base_url.text(), self.model.text())
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
        self._pull_thread = _ModelPullThread(self.base_url.text(), self.model.text())
        self._pull_thread.progress.connect(lambda msg: self.pull_progress.setFormat(msg))
        self._pull_thread.finished_ok.connect(
            lambda: self._on_pull_finished(True, f"Model {self.model.text()} is installed.")
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
        self.mode = QComboBox()
        self.mode.addItems(["luxurious", "performance"])
        self.mode.setCurrentText(self.s.value("ui/mode", "luxurious"))
        self.mode.currentTextChanged.connect(self._on_ui_changed)
        form.addWidget(self.mode, 0, 1)

        form.addWidget(_label("Theme", "formLabel"), 1, 0)
        self.theme = QComboBox()
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

        lay.addLayout(form)
        return card

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
        body_lay.addWidget(_label(
            "No advanced options are exposed yet. Hardware acceleration is set on the Capture page. "
            "An API key field for hosted providers is not part of this build.",
            "cardSubtitle", wrap=True,
        ))
        self.advanced_body.setVisible(False)
        lay.addWidget(self.advanced_body)

        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        return card

    def _toggle_advanced(self, checked):
        self.advanced_body.setVisible(checked)
        set_glyph(self.advanced_toggle, "chevron_up" if checked else "chevron_down")
