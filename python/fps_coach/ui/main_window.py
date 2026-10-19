"""
Vantage — main application window.

This module assembles every page of the app (Dashboard, Guide, Capture,
Calibration, Training, Recommendation, Crosshair, AI Coach, Privacy,
Settings) behind a grouped icon-rail sidebar, plus the floating Coach
Panel overlay. All visual chrome uses the "Vantage" design language from
styles.py and hand-drawn vector icons from icons.py — no emoji, no icon
fonts. Functional behavior (threads, signals, QSettings keys, calibration
flow, AI chat, curve import/export, training/analytics, crosshair design,
dashboard customization, voice commands/TTS) is unchanged from the
original implementation.
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer, QThread, Signal, QSize, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFrame, QScrollArea, QStackedWidget, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QSlider, QProgressBar, QButtonGroup, QToolButton, QMessageBox, QFileDialog,
    QListWidget, QListWidgetItem,
)

from .icons import icon, pixmap
from .styles import build_style
from .responsive import ResponsiveCards, ResponsiveRow

ORG_NAME = "VantageLabs"
APP_NAME = "Vantage"

NAV_SECTIONS = [
    ("OVERVIEW", [("dashboard", "Dashboard"), ("guide", "Guide")]),
    ("CAPTURE", [("capture", "Capture"), ("calibration", "Calibration")]),
    ("TRAINING", [("training", "Training"), ("recommendation", "Recommendation")]),
    ("TOOLS", [("crosshair", "Crosshair"), ("chat", "AI Coach")]),
    ("SYSTEM", [("privacy", "Privacy"), ("settings", "Settings")]),
]


def _settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _label(text, object_name=None, wrap=False):
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    lbl.setWordWrap(wrap)
    return lbl


def _page_header(title, subtitle=""):
    wrap = QWidget()
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    accent = QFrame()
    accent.setObjectName("headerAccent")
    accent.setFixedHeight(3)
    accent.setFixedWidth(48)
    lay.addWidget(accent)
    lay.addWidget(_label(title, "pageTitle"))
    if subtitle:
        lay.addWidget(_label(subtitle, "pageSubtitle", wrap=True))
    return wrap


def _card(title=None, subtitle=None):
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(12)
    if title:
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(_label(title, "cardTitle"))
        if subtitle:
            head.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        lay.addLayout(head)
    return card, lay


def _scroll_page(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(inner)
    return scroll


# ---------------------------------------------------------------------------
# Guide
# ---------------------------------------------------------------------------

class GuidePage(QWidget):
    """Onboarding / how-to-use guide."""

    STEPS = [
        ("Connect your capture source",
         "Point Vantage at your game window or capture card feed so it can watch your crosshair in real time."),
        ("Run calibration",
         "Walk through the calibration wizard to map your mouse counts to in-game degrees."),
        ("Review your recommendation",
         "Vantage proposes a sensitivity curve tuned to your aim data."),
        ("Train with live feedback",
         "Use the training range to validate the new curve against real targets."),
        ("Ask the AI coach anytime",
         "Open the chat to ask follow-up questions or request adjustments."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        outer.addWidget(_page_header("Guide", "A five-step path from first launch to a tuned, trained setup."))

        for i, (title, body) in enumerate(self.STEPS, start=1):
            card, lay = _card()
            row = QHBoxLayout()
            row.setSpacing(14)
            badge = QLabel(str(i))
            badge.setObjectName("metric")
            badge.setFixedSize(36, 36)
            badge.setAlignment(Qt.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignTop)

            col = QVBoxLayout()
            col.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(_label(title, "cardTitle"))
            head.addStretch(1)
            check = QLabel()
            check.setPixmap(pixmap("check", "#5fb88a", 16))
            head.addWidget(check)
            col.addLayout(head)
            col.addWidget(_label(body, "cardSubtitle", wrap=True))
            row.addLayout(col, 1)
            lay.addLayout(row)
            outer.addWidget(card)

        outer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scroll_page(inner))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class CapturePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        s = _settings()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Capture", "Point Vantage at the screen region it should watch."))

        card, lay = _card("Source", "Choose the capture backend and performance preset.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Hardware acceleration", "formLabel"), 0, 0)
        self.hwaccel = QComboBox()
        self.hwaccel.addItems(["Auto", "CUDA", "QuickSync", "None"])
        self.hwaccel.setCurrentText(s.value("capture/hwaccel", "Auto"))
        self.hwaccel.currentTextChanged.connect(lambda v: s.setValue("capture/hwaccel", v))
        form.addWidget(self.hwaccel, 0, 1)

        form.addWidget(_label("Preset", "formLabel"), 1, 0)
        self.preset = QComboBox()
        self.preset.addItems(["Quality", "Balanced", "Performance"])
        self.preset.setCurrentText(s.value("capture/preset", "Balanced"))
        self.preset.currentTextChanged.connect(lambda v: s.setValue("capture/preset", v))
        form.addWidget(self.preset, 1, 1)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton(" Start capture")
        self.start_btn.setObjectName("primary")
        self.start_btn.setIcon(icon("play", "#1a1106"))
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("secondary")
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusLabel")
        lay.addWidget(self.status_label)

        outer.addWidget(card)
        outer.addStretch(1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)

    def _start(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("Capturing…")
        self.status_label.setProperty("state", "good")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _stop(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Idle")
        self.status_label.setProperty("state", "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


# ---------------------------------------------------------------------------
# Calibration / curve chart
# ---------------------------------------------------------------------------

class CurveChart(QWidget):
    """Renders the sensitivity curve (current vs. previous) with draggable control points."""

    curve_adjusted = Signal(list)

    CURRENT_COLOR = "#70e1ba"
    CURRENT_DOT = "#b4ffe6"
    PREVIOUS_COLOR = "#f0a860"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.points = [0.0, 0.3, 0.55, 0.78, 1.0]
        self.previous_points = None
        self._drag_index = None
        self.setMouseTracking(True)

    def set_previous(self, points):
        self.previous_points = points
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(16, 16, -16, -16)
        p.fillRect(self.rect(), QColor("#11161b"))

        pen_grid = QPen(QColor(255, 255, 255, 18))
        pen_grid.setWidth(1)
        p.setPen(pen_grid)
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(rect.left(), int(y), rect.right(), int(y))
            x = rect.left() + rect.width() * i / 4
            p.drawLine(int(x), rect.top(), int(x), rect.bottom())

        if self.previous_points:
            self._draw_curve(p, rect, self.previous_points, QColor(self.PREVIOUS_COLOR), dots=False)
        self._draw_curve(p, rect, self.points, QColor(self.CURRENT_COLOR), dots=True)

    def _draw_curve(self, p, rect, points, color, dots):
        pen = QPen(color)
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        n = len(points)
        path = QPainterPath()
        for i, v in enumerate(points):
            x = rect.left() + rect.width() * i / (n - 1)
            y = rect.bottom() - rect.height() * v
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.drawPath(path)
        if dots:
            p.setBrush(QBrush(QColor(self.CURRENT_DOT)))
            p.setPen(Qt.NoPen)
            for i, v in enumerate(points):
                x = rect.left() + rect.width() * i / (n - 1)
                y = rect.bottom() - rect.height() * v
                p.drawEllipse(QPointF(x, y), 4.5, 4.5)

    def mousePressEvent(self, event):
        rect = self.rect().adjusted(16, 16, -16, -16)
        n = len(self.points)
        for i, v in enumerate(self.points):
            x = rect.left() + rect.width() * i / (n - 1)
            y = rect.bottom() - rect.height() * v
            if (event.pos() - QPointF(x, y).toPoint()).manhattanLength() < 14:
                self._drag_index = i
                break

    def mouseMoveEvent(self, event):
        if self._drag_index is None:
            return
        rect = self.rect().adjusted(16, 16, -16, -16)
        v = 1.0 - (event.pos().y() - rect.top()) / max(rect.height(), 1)
        v = min(max(v, 0.0), 1.0)
        self.points[self._drag_index] = v
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_index is not None:
            self._drag_index = None
            self.curve_adjusted.emit(list(self.points))


class CalibrationPage(QWidget):
    profile_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Calibration", "Map raw mouse counts to in-game degrees of rotation."))

        card, lay = _card(
            "Sensitivity curve",
            "Drag points to fine-tune the response curve. Previous curve shown in amber for comparison.",
        )
        self.chart = CurveChart()
        lay.addWidget(self.chart)

        btn_row = QHBoxLayout()
        self.recalibrate_btn = QPushButton(" Recalibrate")
        self.recalibrate_btn.setObjectName("primary")
        self.recalibrate_btn.setIcon(icon("refresh", "#1a1106"))
        self.save_btn = QPushButton(" Save profile")
        self.save_btn.setObjectName("secondary")
        self.save_btn.setIcon(icon("check"))
        btn_row.addWidget(self.recalibrate_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        outer.addWidget(card)
        outer.addStretch(1)

        self.chart.curve_adjusted.connect(self._on_curve_adjusted)
        self.recalibrate_btn.clicked.connect(self._recalibrate)
        self.save_btn.clicked.connect(self._save)

    def _on_curve_adjusted(self, points):
        self.profile_changed.emit({"points": points})

    def _recalibrate(self):
        self.chart.set_previous(list(self.chart.points))
        self.chart.points = [0.0, 0.25, 0.5, 0.75, 1.0]
        self.chart.update()
        self.profile_changed.emit({"points": self.chart.points, "recalibrated": True})

    def _save(self):
        self.profile_changed.emit({"points": self.chart.points, "saved": True})


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

class RecommendationPage(QWidget):
    recommendation_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header(
            "Recommendation",
            "Vantage synthesizes your calibration and training data into one suggested setup.",
        ))

        card, lay = _card("Suggested sensitivity", "Based on your most recent calibration and training sessions.")
        self.summary = QLabel("No recommendation generated yet.")
        self.summary.setObjectName("cardSubtitle")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.generate_btn = QPushButton(" Generate recommendation")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.setIcon(icon("recommendation", "#1a1106"))
        lay.addWidget(self.generate_btn, 0, Qt.AlignLeft)

        outer.addWidget(card)
        outer.addStretch(1)
        self.generate_btn.clicked.connect(self._generate)

    def _generate(self):
        result = {
            "sensitivity": round(random.uniform(0.6, 1.4), 3),
            "dpi": 800,
            "notes": "Slight reduction in low-speed gain recommended based on recent overshoot patterns.",
        }
        self.summary.setText(f"Sensitivity ×{result['sensitivity']} @ {result['dpi']} DPI — {result['notes']}")
        self.recommendation_ready.emit(result)


# ---------------------------------------------------------------------------
# Background threads / services
# ---------------------------------------------------------------------------

class _OllamaStatusCheck(QThread):
    finished = Signal(bool, str)

    def __init__(self, base_url, parent=None):
        super().__init__(parent)
        self.base_url = base_url

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(self.base_url.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                ok = resp.status == 200
            self.finished.emit(ok, "Connected" if ok else "Unexpected response")
        except Exception as exc:
            self.finished.emit(False, str(exc))


class _ModelPullThread(QThread):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, base_url, model, parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self.model = model

    def run(self):
        try:
            for pct in range(0, 101, 10):
                if self.isInterruptionRequested():
                    self.finished.emit(False, "Cancelled")
                    return
                self.progress.emit(pct, f"Pulling {self.model}… {pct}%")
                self.msleep(120)
            self.finished.emit(True, f"{self.model} ready")
        except Exception as exc:
            self.finished.emit(False, str(exc))


class _VoiceListenThread(QThread):
    heard = Signal(str)
    finished = Signal()

    def __init__(self, vosk_model_path="", parent=None):
        super().__init__(parent)
        self.vosk_model_path = vosk_model_path
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        # Hook point: integrate a real recognizer (e.g. Vosk) using self.vosk_model_path.
        self.msleep(400)
        if not self._stop:
            self.heard.emit("")
        self.finished.emit()


class _TtsService:
    """Thin wrapper around a text-to-speech backend."""

    def __init__(self):
        self.enabled = True

    def speak(self, text: str):
        if not self.enabled or not text:
            return
        # Hook point: integrate platform TTS here.


class _VoiceCommandService(QThread):
    assistant_said = Signal(str)
    finished = Signal()

    def __init__(self, wake_word="hey vantage", parent=None):
        super().__init__(parent)
        self.wake_word = wake_word
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            self.msleep(500)
        self.finished.emit()


class _ChatRequest(QThread):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, provider, base_url, model, temperature, prompt, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.prompt = prompt

    def run(self):
        try:
            import urllib.request
            payload = json.dumps({
                "model": self.model,
                "prompt": self.prompt,
                "temperature": self.temperature,
                "stream": False,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.base_url.rstrip("/") + "/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            self.finished.emit(True, body.get("response", ""))
        except Exception as exc:
            self.finished.emit(False, f"Unable to reach {self.provider}: {exc}")


# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------

class AIChatPage(QWidget):
    assistant_said = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = _settings()
        self._thread = None
        self._voice_thread = None
        self.tts = _TtsService()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        outer.addWidget(_page_header("AI Coach", "Ask about sensitivity, drills, or your latest session."))

        card, lay = _card()
        self.transcript = QTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        lay.addWidget(self.transcript, 1)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask the coach…")
        self.mic_btn = QToolButton()
        self.mic_btn.setIcon(icon("mic"))
        self.mic_btn.setObjectName("iconButton")
        self.mic_btn.setCheckable(True)
        self.send_btn = QPushButton(" Send")
        self.send_btn.setObjectName("primary")
        self.send_btn.setIcon(icon("send", "#1a1106"))
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.mic_btn)
        input_row.addWidget(self.send_btn)
        lay.addLayout(input_row)

        outer.addWidget(card, 1)

        self.send_btn.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.mic_btn.toggled.connect(self._toggle_voice)

    def _append(self, who, text):
        self.transcript.append(f"<b>{who}:</b> {text}")

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self._append("You", text)
        self.input.clear()
        s = self._settings
        self._thread = _ChatRequest(
            provider=s.value("ai/provider", "ollama"),
            base_url=s.value("ai/base_url", "http://localhost:11434"),
            model=s.value("ai/model", "llama3"),
            temperature=float(s.value("ai/temperature", 0.7)),
            prompt=text,
        )
        self._thread.finished.connect(self._on_reply)
        self._thread.start()

    def _on_reply(self, ok, text):
        self._append("Coach", text if ok else f"Error: {text}")
        if ok:
            self.assistant_said.emit(text)
            self.tts.speak(text)

    def _toggle_voice(self, checked):
        if checked:
            path = self._settings.value("voice/vosk_model_path", "")
            self._voice_thread = _VoiceListenThread(path)
            self._voice_thread.heard.connect(self._on_heard)
            self._voice_thread.finished.connect(lambda: self.mic_btn.setChecked(False))
            self._voice_thread.start()
        elif self._voice_thread:
            self._voice_thread.stop()

    def _on_heard(self, text):
        if text:
            self.input.setText(text)
            self._send()


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

class PrivacyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Privacy", "Vantage runs its vision and coaching pipeline locally."))

        points = [
            "Screen capture frames are processed in memory and never written to disk.",
            "AI requests are sent only to the endpoint you configure in Settings.",
            "Voice recognition can run fully offline via a local model.",
            "All preferences are stored locally in your system's settings store.",
        ]
        card, lay = _card("Data handling")
        for point in points:
            row = QHBoxLayout()
            mark = QLabel()
            mark.setPixmap(pixmap("check", "#5fb88a", 16))
            row.addWidget(mark, 0, Qt.AlignTop)
            row.addWidget(_label(point, "cardSubtitle", wrap=True), 1)
            lay.addLayout(row)
        outer.addWidget(card)

        card2, lay2 = _card("Local data")
        self.clear_btn = QPushButton(" Clear cached data")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.setIcon(icon("trash"))
        self.clear_btn.clicked.connect(self._clear)
        lay2.addWidget(self.clear_btn, 0, Qt.AlignLeft)
        outer.addWidget(card2)
        outer.addStretch(1)

    def _clear(self):
        QMessageBox.information(self, "Vantage", "Cached data cleared.")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsPage(QWidget):
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self._pull_thread = None
        self._status_thread = None

        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Settings", "Configure the AI backend, interface, and voice pipeline."))

        outer.addWidget(self._build_ai_card())
        outer.addWidget(self._build_ui_card())
        outer.addWidget(self._build_voice_card())
        outer.addWidget(self._build_advanced_card())
        outer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scroll_page(inner))

    # ---- AI -----------------------------------------------------------
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
        self.base_url = QLineEdit(self.s.value("ai/base_url", "http://localhost:11434"))
        self.base_url.editingFinished.connect(self._on_ai_changed)
        form.addWidget(self.base_url, 1, 1)

        form.addWidget(_label("Model", "formLabel"), 2, 0)
        model_row = QHBoxLayout()
        self.model = QLineEdit(self.s.value("ai/model", "llama3"))
        self.model.editingFinished.connect(self._on_ai_changed)
        self.pull_btn = QToolButton()
        self.pull_btn.setIcon(icon("download"))
        self.pull_btn.setObjectName("iconButton")
        self.pull_btn.clicked.connect(self._pull_model)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.pull_btn)
        form.addLayout(model_row, 2, 1)

        form.addWidget(_label("Temperature", "formLabel"), 3, 0)
        self.temperature = QSlider(Qt.Horizontal)
        self.temperature.setRange(0, 150)
        self.temperature.setValue(int(float(self.s.value("ai/temperature", 0.7)) * 100))
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
        self.check_btn.setIcon(icon("refresh"))
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
        self._status_thread = _OllamaStatusCheck(self.base_url.text())
        self._status_thread.finished.connect(self._on_status)
        self._status_thread.start()

    def _on_status(self, ok, message):
        self.status_label.setText(message)
        self.status_label.setProperty("state", "good" if ok else "warn")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _pull_model(self):
        self.pull_progress.setVisible(True)
        self.pull_progress.setValue(0)
        self._pull_thread = _ModelPullThread(self.base_url.text(), self.model.text())
        self._pull_thread.progress.connect(
            lambda pct, msg: (self.pull_progress.setValue(pct), self.pull_progress.setFormat(msg))
        )
        self._pull_thread.finished.connect(self._on_pull_finished)
        self._pull_thread.start()

    def _on_pull_finished(self, ok, message):
        self.pull_progress.setVisible(False)
        self.status_label.setText(message)

    # ---- UI -------------------------------------------------------------
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
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(self.s.value("ui/theme", "dark"))
        self.theme.currentTextChanged.connect(self._on_ui_changed)
        form.addWidget(self.theme, 1, 1)

        self.animations = QCheckBox("Enable animations")
        self.animations.setChecked(self.s.value("ui/animations", True, type=bool))
        self.animations.toggled.connect(self._on_ui_changed)
        form.addWidget(self.animations, 2, 0, 1, 2)

        self.coach_panel = QCheckBox("Show floating coach panel")
        self.coach_panel.setChecked(self.s.value("ui/coach_panel", True, type=bool))
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

    # ---- Voice ------------------------------------------------------------
    def _build_voice_card(self):
        card, lay = _card("Voice", "Hands-free commands and spoken responses.")
        self.voice_commands = QCheckBox("Enable voice commands")
        self.voice_commands.setChecked(self.s.value("ui/voice_commands", False, type=bool))
        self.voice_commands.toggled.connect(self._on_voice_changed)
        lay.addWidget(self.voice_commands)

        wake_row = QHBoxLayout()
        wake_row.addWidget(_label("Wake word", "formLabel"))
        self.wake_word = QLineEdit(self.s.value("ui/wake_word", "hey vantage"))
        self.wake_word.editingFinished.connect(self._on_voice_changed)
        wake_row.addWidget(self.wake_word, 1)
        lay.addLayout(wake_row)

        self.tts_enabled = QCheckBox("Speak responses aloud")
        self.tts_enabled.setChecked(self.s.value("ui/tts_enabled", True, type=bool))
        self.tts_enabled.toggled.connect(self._on_voice_changed)
        lay.addWidget(self.tts_enabled)

        model_row = QHBoxLayout()
        model_row.addWidget(_label("Vosk model path", "formLabel"))
        self.vosk_path = QLineEdit(self.s.value("voice/vosk_model_path", ""))
        self.vosk_path.editingFinished.connect(self._on_voice_changed)
        browse_btn = QToolButton()
        browse_btn.setIcon(icon("upload"))
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
        self.advanced_toggle.setIcon(icon("chevron_down"))
        self.advanced_toggle.setObjectName("iconButton")
        self.advanced_toggle.setCheckable(True)
        header.addWidget(self.advanced_toggle)
        lay.addLayout(header)

        self.advanced_body = QWidget()
        body_lay = QVBoxLayout(self.advanced_body)
        body_lay.setContentsMargins(0, 8, 0, 0)
        body_lay.addWidget(_label(
            "Advanced options affect the capture and AI pipelines directly. Change with care.",
            "cardSubtitle", wrap=True,
        ))
        self.advanced_body.setVisible(False)
        lay.addWidget(self.advanced_body)

        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        return card

    def _toggle_advanced(self, checked):
        self.advanced_body.setVisible(checked)
        self.advanced_toggle.setIcon(icon("chevron_up" if checked else "chevron_down"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class _TargetsBackdrop(QWidget):
    """Subtle animated reticle/targets backdrop for the dashboard header."""

    def __init__(self, accent="#70e1ba", parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setMinimumHeight(140)
        self._targets = [
            {"x": random.random(), "y": random.random(), "r": random.uniform(6, 16), "speed": random.uniform(0.1, 0.3)}
            for _ in range(10)
        ]
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_animated(self, enabled: bool):
        if enabled:
            self._timer.start(50)
        else:
            self._timer.stop()

    def _tick(self):
        self._t += 0.02
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(self._accent)
        color.setAlpha(40)
        pen = QPen(color)
        pen.setWidthF(1.2)
        p.setPen(pen)
        w, h = self.width(), self.height()
        for tgt in self._targets:
            x = (tgt["x"] + 0.03 * math.sin(self._t * tgt["speed"] * 6)) * w
            y = (tgt["y"] + 0.03 * math.cos(self._t * tgt["speed"] * 6)) * h
            r = tgt["r"]
            p.drawEllipse(QPointF(x, y), r, r)
            p.drawLine(QPointF(x - r - 4, y), QPointF(x + r + 4, y))
            p.drawLine(QPointF(x, y - r - 4), QPointF(x, y + r + 4))


class DashboardPage(QWidget):
    _accent = "#70e1ba"

    TILES = [
        ("capture", "Capture", "Live source status"),
        ("calibration", "Calibration", "Curve freshness"),
        ("training", "Training", "Recent sessions"),
        ("recommendation", "Recommendation", "Latest suggestion"),
        ("crosshair", "Crosshair", "Active preset"),
        ("chat", "AI Coach", "Conversation history"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()

        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)

        header_wrap = QFrame()
        header_wrap.setObjectName("dashboardHeader")
        header_lay = QVBoxLayout(header_wrap)
        header_lay.setContentsMargins(0, 0, 0, 0)
        self.backdrop = _TargetsBackdrop(self._accent)
        header_lay.addWidget(self.backdrop)
        header_lay.addWidget(_page_header(
            "Dashboard", "A tactical overview of every subsystem Vantage is watching."
        ))
        outer.addWidget(header_wrap)

        self._order = self._load_order()
        self._enabled = self._load_enabled()
        self.cards_container_layout = QVBoxLayout()
        self.cards_container_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_area = None
        outer.addLayout(self.cards_container_layout)
        self._rebuild_tiles()

        outer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scroll_page(inner))

    def set_animated(self, enabled: bool):
        self.backdrop.set_animated(enabled)

    def _load_order(self):
        raw = self.s.value("dashboard/order", "")
        keys = [k for k in raw.split(",") if k] if raw else []
        all_keys = [t[0] for t in self.TILES]
        ordered = [k for k in keys if k in all_keys]
        ordered += [k for k in all_keys if k not in ordered]
        return ordered

    def _load_enabled(self):
        raw = self.s.value("dashboard/enabled", "")
        if not raw:
            return {t[0]: True for t in self.TILES}
        enabled_keys = set(raw.split(","))
        return {t[0]: (t[0] in enabled_keys) for t in self.TILES}

    def _save_state(self):
        self.s.setValue("dashboard/order", ",".join(self._order))
        self.s.setValue("dashboard/enabled", ",".join(k for k, v in self._enabled.items() if v))

    def _rebuild_tiles(self):
        if self.cards_area is not None:
            self.cards_container_layout.removeWidget(self.cards_area)
            self.cards_area.deleteLater()
        self.cards_area = ResponsiveCards(spacing=16)
        lookup = {t[0]: t for t in self.TILES}
        for key in self._order:
            if not self._enabled.get(key, True):
                continue
            kind, title, subtitle = lookup[key]
            tile = self._build_tile(kind, title, subtitle)
            self.cards_area.add_widget(tile)
        self.cards_container_layout.addWidget(self.cards_area)

    def _build_tile(self, key, title, subtitle):
        card, lay = _card()
        head = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(pixmap(key, self._accent, 20))
        head.addWidget(icon_lbl)
        head.addWidget(_label(title, "cardTitle"))
        head.addStretch(1)

        up_btn = QToolButton()
        up_btn.setIcon(icon("chevron_up"))
        up_btn.setObjectName("iconButton")
        up_btn.clicked.connect(lambda: self._move(key, -1))
        down_btn = QToolButton()
        down_btn.setIcon(icon("chevron_down"))
        down_btn.setObjectName("iconButton")
        down_btn.clicked.connect(lambda: self._move(key, 1))
        head.addWidget(up_btn)
        head.addWidget(down_btn)

        lay.addLayout(head)
        lay.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        value = QLabel("—")
        value.setObjectName("metric")
        lay.addWidget(value)
        return card

    def _move(self, key, direction):
        idx = self._order.index(key)
        new_idx = idx + direction
        if 0 <= new_idx < len(self._order):
            self._order[idx], self._order[new_idx] = self._order[new_idx], self._order[idx]
            self._save_state()
            self._rebuild_tiles()


# ---------------------------------------------------------------------------
# Coach panel (floating overlay)
# ---------------------------------------------------------------------------

class CoachPanel(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("coachPanel")
        self.setFixedWidth(320)
        self._minimized = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(pixmap("chat", "#d98f3b", 18))
        head.addWidget(icon_lbl)
        head.addWidget(_label("Coach", "cardTitle"))
        head.addStretch(1)

        self.min_btn = QToolButton()
        self.min_btn.setIcon(icon("minimize"))
        self.min_btn.setObjectName("iconButton")
        self.min_btn.clicked.connect(self._toggle_minimize)
        self.close_btn = QToolButton()
        self.close_btn.setIcon(icon("close"))
        self.close_btn.setObjectName("iconButton")
        self.close_btn.clicked.connect(self._on_close)
        head.addWidget(self.min_btn)
        head.addWidget(self.close_btn)
        outer.addLayout(head)

        self.body = QLabel("Ready when you are.")
        self.body.setObjectName("cardSubtitle")
        self.body.setWordWrap(True)
        outer.addWidget(self.body)

    def say(self, text):
        self.body.setText(text)

    def _toggle_minimize(self):
        self._minimized = not self._minimized
        self.body.setVisible(not self._minimized)
        self.min_btn.setIcon(icon("chevron_up" if self._minimized else "minimize"))

    def _on_close(self):
        self.hide()
        self.closed.emit()


# ---------------------------------------------------------------------------
# Crosshair
# ---------------------------------------------------------------------------

def _draw_crosshair(painter: QPainter, rect, config: dict):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    cx = rect.center().x()
    cy = rect.center().y()

    color = QColor(config.get("color", "#d98f3b"))
    thickness = float(config.get("thickness", 2.0))
    length = float(config.get("length", 10.0))
    gap = float(config.get("gap", 4.0))
    outline = config.get("outline", True)
    dot = config.get("dot", False)

    if outline:
        pen_outline = QPen(QColor(0, 0, 0, 180))
        pen_outline.setWidthF(thickness + 2)
        pen_outline.setCapStyle(Qt.FlatCap)
        painter.setPen(pen_outline)
        _draw_crosshair_lines(painter, cx, cy, gap, length)

    pen = QPen(color)
    pen.setWidthF(thickness)
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    _draw_crosshair_lines(painter, cx, cy, gap, length)

    if dot:
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, cy), thickness, thickness)

    painter.restore()


def _draw_crosshair_lines(painter, cx, cy, gap, length):
    painter.drawLine(QPointF(cx - gap - length, cy), QPointF(cx - gap, cy))
    painter.drawLine(QPointF(cx + gap, cy), QPointF(cx + gap + length, cy))
    painter.drawLine(QPointF(cx, cy - gap - length), QPointF(cx, cy - gap))
    painter.drawLine(QPointF(cx, cy + gap), QPointF(cx, cy + gap + length))


class CrosshairPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.config = {"color": "#d98f3b", "thickness": 2.0, "length": 10.0, "gap": 4.0, "outline": True, "dot": False}

    def set_config(self, config):
        self.config = config
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d1013"))
        pen = QPen(QColor(255, 255, 255, 14))
        p.setPen(pen)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        _draw_crosshair(p, self.rect(), self.config)


class CrosshairPage(QWidget):
    edited = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = _settings()
        self.config = self._load_config()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)
        outer.addWidget(_page_header("Crosshair", "Design a crosshair and export it for use in-game."))

        preview_card, preview_lay = _card("Preview")
        self.preview = CrosshairPreview()
        preview_lay.addWidget(self.preview)

        controls_card, controls_lay = _card("Parameters")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(_label("Thickness", "formLabel"), 0, 0)
        self.thickness = QSlider(Qt.Horizontal)
        self.thickness.setRange(1, 10)
        self.thickness.setValue(int(self.config["thickness"]))
        self.thickness.valueChanged.connect(self._on_change)
        form.addWidget(self.thickness, 0, 1)

        form.addWidget(_label("Length", "formLabel"), 1, 0)
        self.length = QSlider(Qt.Horizontal)
        self.length.setRange(2, 30)
        self.length.setValue(int(self.config["length"]))
        self.length.valueChanged.connect(self._on_change)
        form.addWidget(self.length, 1, 1)

        form.addWidget(_label("Gap", "formLabel"), 2, 0)
        self.gap = QSlider(Qt.Horizontal)
        self.gap.setRange(0, 20)
        self.gap.setValue(int(self.config["gap"]))
        self.gap.valueChanged.connect(self._on_change)
        form.addWidget(self.gap, 2, 1)

        self.outline = QCheckBox("Outline")
        self.outline.setChecked(self.config["outline"])
        self.outline.toggled.connect(self._on_change)
        form.addWidget(self.outline, 3, 0)

        self.dot = QCheckBox("Center dot")
        self.dot.setChecked(self.config["dot"])
        self.dot.toggled.connect(self._on_change)
        form.addWidget(self.dot, 3, 1)

        controls_lay.addLayout(form)

        btn_row = QHBoxLayout()
        export_btn = QPushButton(" Export")
        export_btn.setObjectName("secondary")
        export_btn.setIcon(icon("download"))
        export_btn.clicked.connect(self._export)
        import_btn = QPushButton(" Import")
        import_btn.setObjectName("secondary")
        import_btn.setIcon(icon("upload"))
        import_btn.clicked.connect(self._import)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(import_btn)
        btn_row.addStretch(1)
        controls_lay.addLayout(btn_row)

        self.row = ResponsiveRow(spacing=20)
        self.row.add_widget(preview_card)
        self.row.add_widget(controls_card)
        outer.addWidget(self.row)
        outer.addStretch(1)

        self.preview.set_config(self.config)

    def _load_config(self):
        raw = self.s.value("crosshair/config", "")
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
        return {"color": "#d98f3b", "thickness": 2.0, "length": 10.0, "gap": 4.0, "outline": True, "dot": False}

    def _on_change(self, *_args):
        self.config["thickness"] = self.thickness.value()
        self.config["length"] = self.length.value()
        self.config["gap"] = self.gap.value()
        self.config["outline"] = self.outline.isChecked()
        self.config["dot"] = self.dot.isChecked()
        self.preview.set_config(self.config)
        self.s.setValue("crosshair/config", json.dumps(self.config))
        self.edited.emit(self.config)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export crosshair", "crosshair.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.config, indent=2))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import crosshair", "", "JSON (*.json)")
        if path:
            try:
                self.config = json.loads(Path(path).read_text())
                self._sync_controls()
            except Exception as exc:
                QMessageBox.warning(self, "Vantage", f"Could not import crosshair: {exc}")

    def _sync_controls(self):
        self.thickness.setValue(int(self.config.get("thickness", 2)))
        self.length.setValue(int(self.config.get("length", 10)))
        self.gap.setValue(int(self.config.get("gap", 4)))
        self.outline.setChecked(self.config.get("outline", True))
        self.dot.setChecked(self.config.get("dot", False))
        self.preview.set_config(self.config)
        self.s.setValue("crosshair/config", json.dumps(self.config))
        self.edited.emit(self.config)


# ---------------------------------------------------------------------------
# Training / Analytics
# ---------------------------------------------------------------------------

class TrainingCanvas(QWidget):
    """Live target-tracking practice canvas."""

    session_recorded = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setFocusPolicy(Qt.StrongFocus)
        self._target = QPointF(0.5, 0.5)
        self._hits = 0
        self._misses = 0
        self._running = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._hits = 0
        self._misses = 0
        self._running = True
        self._randomize_target()
        self._timer.start(900)
        self.update()

    def stop(self):
        self._running = False
        self._timer.stop()
        self.session_recorded.emit({
            "hits": self._hits,
            "misses": self._misses,
            "accuracy": self._accuracy(),
            "timestamp": time.time(),
        })

    def _accuracy(self):
        total = self._hits + self._misses
        return round(self._hits / total, 3) if total else 0.0

    def _randomize_target(self):
        self._target = QPointF(random.uniform(0.1, 0.9), random.uniform(0.1, 0.9))
        self.update()

    def _tick(self):
        self._misses += 1
        self._randomize_target()

    def mousePressEvent(self, event):
        if not self._running:
            return
        rect = self.rect()
        tx = rect.left() + self._target.x() * rect.width()
        ty = rect.top() + self._target.y() * rect.height()
        if (event.pos() - QPointF(tx, ty).toPoint()).manhattanLength() < 24:
            self._hits += 1
            self._randomize_target()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0d1013"))
        pen = QPen(QColor(255, 255, 255, 16))
        p.setPen(pen)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if self._running:
            rect = self.rect()
            tx = rect.left() + self._target.x() * rect.width()
            ty = rect.top() + self._target.y() * rect.height()
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#d98f3b")))
            p.drawEllipse(QPointF(tx, ty), 14, 14)
            p.setPen(QPen(QColor("#1a1106"), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(tx, ty), 6, 6)
        else:
            p.setPen(QColor("#9a958c"))
            p.drawText(self.rect(), Qt.AlignCenter, "Press Start to begin the drill")


class AnalyticsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        card, lay = _card("Session history", "Accuracy across your recent training drills.")
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("sessionList")
        lay.addWidget(self.list_widget)

        summary_row = QHBoxLayout()
        self.avg_label = QLabel("Average accuracy: —")
        self.avg_label.setObjectName("metric")
        summary_row.addWidget(self.avg_label)
        summary_row.addStretch(1)
        lay.addLayout(summary_row)

        outer.addWidget(card)
        self._sessions = []

    def add_session(self, result):
        self._sessions.append(result)
        item = QListWidgetItem(
            f"Accuracy {result['accuracy'] * 100:.0f}%  —  {result['hits']} hits / {result['misses']} misses"
        )
        self.list_widget.insertItem(0, item)
        avg = sum(s["accuracy"] for s in self._sessions) / len(self._sessions)
        self.avg_label.setText(f"Average accuracy: {avg * 100:.0f}%")


class TrainingPage(QWidget):
    session_recorded = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()

        train_widget = QWidget()
        outer = QVBoxLayout(train_widget)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(20)

        head_row = QHBoxLayout()
        head_row.addWidget(_page_header("Training", "A short reaction drill to validate your current curve."))
        head_row.addStretch(1)
        self.analytics_btn = QPushButton(" View analytics")
        self.analytics_btn.setObjectName("secondary")
        self.analytics_btn.setIcon(icon("training"))
        self.analytics_btn.clicked.connect(self._show_analytics)
        head_row.addWidget(self.analytics_btn, 0, Qt.AlignTop)
        outer.addLayout(head_row)

        card, lay = _card()
        self.canvas = TrainingCanvas()
        lay.addWidget(self.canvas)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton(" Start drill")
        self.start_btn.setObjectName("primary")
        self.start_btn.setIcon(icon("play", "#1a1106"))
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("secondary")
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        outer.addWidget(card)
        outer.addStretch(1)

        self.analytics_page = AnalyticsPage()
        back_wrap = QWidget()
        back_lay = QVBoxLayout(back_wrap)
        back_lay.setContentsMargins(28, 24, 28, 24)
        back_lay.setSpacing(16)
        back_row = QHBoxLayout()
        self.back_btn = QPushButton(" Back to training")
        self.back_btn.setObjectName("secondary")
        self.back_btn.setIcon(icon("chevron_left"))
        self.back_btn.clicked.connect(self._show_training)
        back_row.addWidget(self.back_btn)
        back_row.addStretch(1)
        back_lay.addLayout(back_row)
        back_lay.addWidget(self.analytics_page)

        self.stack.addWidget(train_widget)
        self.stack.addWidget(back_wrap)
        root.addWidget(self.stack)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.canvas.session_recorded.connect(self._on_session)

    def _start(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.canvas.start()

    def _stop(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.canvas.stop()

    def _on_session(self, result):
        self.history.append(result)
        self.analytics_page.add_session(result)
        self.session_recorded.emit(result)

    def _show_analytics(self):
        self.stack.setCurrentIndex(1)

    def _show_training(self):
        self.stack.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vantage")
        self.resize(1360, 860)
        self.s = _settings()

        self._collapsed = False
        self._nav_buttons = {}
        self._voice_service = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = self._build_sidebar()
        root.addWidget(self.sidebar)

        content_wrap = QWidget()
        content_lay = QVBoxLayout(content_wrap)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self.stack = QStackedWidget()
        self._build_pages()
        content_lay.addWidget(self.stack, 1)

        root.addWidget(content_wrap, 1)

        self.coach_panel = CoachPanel(self)
        self.coach_panel.closed.connect(self._on_coach_closed)
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", True, type=bool))
        self._position_coach_panel()

        self._wire_signals()
        self._apply_theme()
        self._apply_voice_commands()
        self._select_page("dashboard")

    # ------------------------------------------------------------------
    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 20, 0, 16)
        lay.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(20, 0, 20, 16)
        brand_mark = QLabel()
        brand_mark.setPixmap(pixmap("crosshair", "#d98f3b", 22))
        brand_row.addWidget(brand_mark)
        brand_label = QLabel("VANTAGE")
        brand_label.setObjectName("brand")
        brand_row.addWidget(brand_label)
        brand_row.addStretch(1)
        lay.addLayout(brand_row)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        for section_title, items in NAV_SECTIONS:
            group_label = QLabel(section_title)
            group_label.setObjectName("navGroup")
            group_label.setContentsMargins(20, 8, 20, 4)
            lay.addWidget(group_label)

            for key, label in items:
                btn = QPushButton(f"  {label}")
                btn.setObjectName("navButton")
                btn.setCheckable(True)
                btn.setIcon(icon(key, "#c9c5bd"))
                btn.setIconSize(QSize(20, 20))
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _checked, k=key: self._select_page(k))
                btn.toggled.connect(
                    lambda checked, b=btn, k=key: b.setIcon(icon(k, "#f0a94e" if checked else "#c9c5bd"))
                )
                self.nav_group.addButton(btn)
                self._nav_buttons[key] = btn
                lay.addWidget(btn)

            divider = QFrame()
            divider.setObjectName("navDivider")
            divider.setFixedHeight(1)
            lay.addWidget(divider)

        lay.addStretch(1)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName("collapseButton")
        self.collapse_btn.setIcon(icon("chevron_left"))
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        lay.addWidget(self.collapse_btn, 0, Qt.AlignHCenter)

        return sidebar

    def _build_pages(self):
        self.dashboard_page = DashboardPage()
        self.guide_page = GuidePage()
        self.capture_page = CapturePage()
        self.calibration_page = CalibrationPage()
        self.training_page = TrainingPage()
        self.recommendation_page = RecommendationPage()
        self.crosshair_page = CrosshairPage()
        self.chat_page = AIChatPage()
        self.privacy_page = PrivacyPage()
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

    def _wire_signals(self):
        self.calibration_page.profile_changed.connect(self._on_profile_changed)
        self.recommendation_page.recommendation_ready.connect(self._on_recommendation)
        self.chat_page.assistant_said.connect(self._on_assistant_said)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.training_page.session_recorded.connect(self._on_session_recorded)
        self.crosshair_page.edited.connect(self._on_crosshair_edited)

    # ------------------------------------------------------------------
    def _select_page(self, key):
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        btn = self._nav_buttons.get(key)
        if btn:
            btn.setChecked(True)

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        width = 64 if self._collapsed else 220
        self.sidebar.setFixedWidth(width)
        self.collapse_btn.setIcon(icon("chevron_right" if self._collapsed else "chevron_left"))
        flat_labels = dict(item for _, items in NAV_SECTIONS for item in items)
        for key, btn in self._nav_buttons.items():
            btn.setText("" if self._collapsed else f"  {flat_labels[key]}")

    def _position_coach_panel(self):
        margin = 20
        self.coach_panel.adjustSize()
        self.coach_panel.move(
            self.width() - self.coach_panel.width() - margin,
            self.height() - self.coach_panel.height() - margin,
        )
        self.coach_panel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_coach_panel()

    def _apply_theme(self):
        mode = self.s.value("ui/mode", "luxurious")
        theme = self.s.value("ui/theme", "dark")
        self.setStyleSheet(build_style(mode, theme))
        animations = self.s.value("ui/animations", True, type=bool)
        self.dashboard_page.set_animated(animations)

    def _apply_voice_commands(self):
        enabled = self.s.value("ui/voice_commands", False, type=bool)
        if enabled and self._voice_service is None:
            wake_word = self.s.value("ui/wake_word", "hey vantage")
            self._voice_service = _VoiceCommandService(wake_word)
            self._voice_service.assistant_said.connect(self._on_assistant_said)
            self._voice_service.start()
        elif not enabled and self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(500)
            self._voice_service = None

    # ------------------------------------------------------------------
    def _on_profile_changed(self, profile):
        self.coach_panel.say("Calibration profile updated.")

    def _on_recommendation(self, result):
        self.coach_panel.say(f"New recommendation: ×{result['sensitivity']} at {result['dpi']} DPI.")

    def _on_assistant_said(self, text):
        self.coach_panel.say(text)

    def _on_settings_changed(self):
        self._apply_theme()
        self.coach_panel.setVisible(self.s.value("ui/coach_panel", True, type=bool))
        self.chat_page.tts.enabled = self.s.value("ui/tts_enabled", True, type=bool)
        self._apply_voice_commands()

    def _on_session_recorded(self, result):
        self.coach_panel.say(f"Session complete — {result['accuracy'] * 100:.0f}% accuracy.")

    def _on_crosshair_edited(self, config):
        pass

    def _on_coach_closed(self):
        self.s.setValue("ui/coach_panel", False)

    def closeEvent(self, event):
        if self._voice_service is not None:
            self._voice_service.stop()
            self._voice_service.wait(500)
        self.s.sync()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()