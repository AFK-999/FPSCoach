"""Five optional questions and a setup review."""

from __future__ import annotations

import math
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fps_coach.aiming_motion import OPTIONS as MOTIONS
from fps_coach.calibration_wizard import GRIPS, PADS, PROBLEM_GUIDANCE, PROBLEMS, clone_answers
from fps_coach.guide_ai import analyze_guide, safe_evidence

from .setup_preview import SetupPreview
from .styles import active_palette, fx_enabled
from .widgets import _label, _settings
from .window_chrome import GuideTitleBar, ResizeShell


class Diagram(QWidget):
    """Bundled example illustrations or simple pad/unknown art, never measurements."""

    def __init__(self, kind: str, key: str, parent=None):
        super().__init__(parent)
        self.kind, self.key = kind, key
        asset = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "calibration_guide"
            / f"{kind}_{key}.jpg"
        )
        self.image = (
            QPixmap(str(asset)) if kind in ("grip", "motion") and key != "unsure" else QPixmap()
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumHeight(130 if kind in ("grip", "motion") else 95)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.kind in ("grip", "motion") and self.key != "unsure":
            bounds = QRectF(0, 0, self.width(), self.height())
            clip = QPainterPath()
            clip.addRoundedRect(bounds, 8, 8)
            painter.setClipPath(clip)
            if not self.image.isNull():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                # Fill the card's picture area, cropping its edges rather than stretching the hand.
                # Grip arrows must remain visible; letterbox rather than cropping them.
                resize = min if self.kind == "grip" else max
                if self.kind == "grip":
                    painter.fillRect(bounds, QColor("#1c2023"))
                factor = resize(bounds.width() / self.image.width(), bounds.height() / self.image.height())
                width, height = self.image.width() * factor, self.image.height() * factor
                destination = QRectF(
                    (bounds.width() - width) / 2, (bounds.height() - height) / 2, width, height
                )
                painter.drawPixmap(destination, self.image, QRectF(self.image.rect()))
            else:
                painter.fillRect(bounds, QColor("#282e30"))
                painter.setPen(QColor("#c2c9c6"))
                painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "Illustration unavailable")
            painter.end()
            return
        scale = min(self.width() / 180, self.height() / 105)
        painter.translate((self.width() - 180 * scale) / 2, (self.height() - 105 * scale) / 2)
        painter.scale(scale, scale)
        palette = active_palette()
        dark = QColor(palette["input"]).lightness() < 128
        base = QColor("#60727a" if dark else "#8c9b9e")
        accent = QColor(palette["accent"])
        accent.setAlpha(210)
        painter.setPen(QPen(base.lighter(125), 1.6))
        painter.setBrush(base.darker(145) if dark else base.lighter(135))
        if self.key == "unsure":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(base, 2))
            painter.drawEllipse(QPointF(90, 52), 35, 35)
            font = painter.font()
            font.setPixelSize(35)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(accent)
            painter.drawText(QRectF(55, 14, 70, 76), Qt.AlignmentFlag.AlignCenter, "?")
        elif self.kind == "pad":
            sizes = {"small": (62, 66), "medium": (106, 72), "large": (156, 78)}
            width, height = sizes[self.key]
            painter.drawRoundedRect(QRectF(90 - width / 2, 50 - height / 2, width, height), 8, 8)
            if self.key == "large":
                painter.setBrush(QColor("#202a2e" if dark else "#c3cdd0"))
                painter.drawRoundedRect(QRectF(23, 42, 62, 25), 3, 3)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(104 if self.key == "large" else 84, 40, 13, 23), 6, 6)
        painter.end()


class ChoiceCard(QPushButton):
    """A real checkable button with readable child labels and an accessible name."""

    def __init__(self, title: str, subtitle: str, details: str, kind="", key="", parent=None):
        super().__init__(parent)
        self.setObjectName("wizardChoice")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{title}. {subtitle}. {details}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)
        if kind:
            self.diagram = Diagram(kind, key)
            layout.addWidget(self.diagram, 1)
        title_label = _label(title, "wizardOptionTitle", wrap=True)
        caption = _label(subtitle, "wizardOptionCaption", wrap=True)
        body = _label(details, "wizardOptionBody", wrap=True)
        for label in (title_label, caption, body):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        if not kind:
            layout.addStretch(1)


class _GuideSignals(QObject):
    finished = Signal(int, object, str)


class CalibrationWizard(QDialog):
    """Five optional questionnaire pages followed by a setup review."""

    REVIEW_STEP = 5

    discuss_saved = Signal()
    clear_saved = Signal()

    _TITLES = (
        "What’s going wrong?",
        "How do you hold it?",
        "What moves when you flick?",
        "How big is your mousepad?",
        "Your current setup",
        "Your setup at a glance",
    )
    _SUBTITLES = (
        "Select any that sound familiar, or choose Not sure. These are your impressions, not measured scores.",
        "Choose the closest fit. Your grip may vary; no single grip is proven best for everyone.",
        "Choose your main motion, or Not sure. Most people use a combination of joints.",
        "Choose a relative size, and optionally enter the width you can actually use.",
        "Enter only what you know. Values are self-reported and do not change your game settings.",
        "Review your answers. Grip and flick motion are highlighted exactly as reported; use Back to change anything.",
    )
    _EVIDENCE = (
        'A first-person targeting study found a task-dependent speed–precision trade-off. <a href="https://ieee-cog.org/2022/assets/papers/paper_64.pdf">Read study</a>',
        'A 22-player preferred-grip study found no significant overall flick/tracking score difference. <a href="https://doi.org/10.3390/app14167112">Read study</a>',
        'Joint contributions are not measured here. Comfort and movement vary. <a href="https://www.osha.gov/etools/computer-workstations/components/pointer-mouse">Ergonomics guidance</a>',
        'Available travel depends on usable space and starting position; no size category is universal. <a href="https://ieee-cog.org/2022/assets/papers/paper_64.pdf">Sensitivity study</a>',
        'Game sensitivity values differ between games; DPI and sensitivity alone do not establish cm/360. <a href="https://ieee-cog.org/2022/assets/papers/paper_64.pdf">Sensitivity study</a>',
    )

    def __init__(self, initial=None, parent=None, measured_summary=None):
        super().__init__(parent)
        self.setWindowTitle("Calibration guide")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("calibrationWizard")
        self.resize(1200, 780)
        self.setMinimumSize(920, 610)
        self.answers = clone_answers(initial)
        self.measured_summary = safe_evidence(measured_summary)
        self.ai_proposal = None
        self.ai_proposal_answers = None
        self.preview_requested = False
        self.start_calibration_requested = False
        self._ai_revision = 0
        self._ai_running = False
        self._ai_alive = True
        self._ai_signals = _GuideSignals(self)
        self._ai_signals.finished.connect(self._on_ai_finished)
        self._ai_timer = QTimer(self)
        self._ai_timer.setSingleShot(True)
        self._ai_timer.setInterval(450)
        self._ai_timer.timeout.connect(self._dispatch_ai)
        self.finished.connect(self._stop_ai)
        self.step = 0
        self.cards: list[dict[str, ChoiceCard]] = []
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        shell = ResizeShell(self)
        window_layout.addWidget(shell)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(ResizeShell.BORDER, ResizeShell.BORDER, ResizeShell.BORDER, ResizeShell.BORDER)
        shell_layout.setSpacing(0)
        self.title_bar = GuideTitleBar(self)
        shell_layout.addWidget(self.title_bar)
        shell_layout.addSpacing(3)
        body = QWidget()
        shell_layout.addWidget(body, 1)
        root = QHBoxLayout(body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("wizardSidebar")
        sidebar.setFixedWidth(195)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20, 25, 20, 25)
        nav.setSpacing(10)
        nav.addWidget(_label("CURVECOACH", "wizardBrand"))
        nav.addWidget(_label("CALIBRATION GUIDE", "eyebrow"))
        nav.addSpacing(20)
        self.nav_labels = []
        for number, label in enumerate(
            ("The problem", "Your grip", "Aim motion", "Mousepad", "Current setup", "Review setup"), 1
        ):
            item = _label(f"{number:02}   {label}", "wizardNav")
            self.nav_labels.append(item)
            nav.addWidget(item)
        nav.addStretch(1)
        self.saved_discuss_btn = QPushButton("Discuss saved answers")
        self.saved_discuss_btn.setObjectName("secondary")
        self.saved_discuss_btn.clicked.connect(self.discuss_saved.emit)
        self.saved_discuss_btn.hide()
        nav.addWidget(self.saved_discuss_btn)
        self.saved_clear_btn = QPushButton("Clear saved answers")
        self.saved_clear_btn.setObjectName("secondary")
        self.saved_clear_btn.clicked.connect(self.clear_saved.emit)
        self.saved_clear_btn.hide()
        nav.addWidget(self.saved_clear_btn)
        nav.addWidget(_label("Answers · session only", "cardSubtitle", wrap=True))
        root.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(30, 26, 30, 24)
        main.setSpacing(11)
        self.counter = _label("", "eyebrow")
        main.addWidget(self.counter)
        self.title = _label("", "pageTitle", wrap=True)
        main.addWidget(self.title)
        self.subtitle = _label("", "cardSubtitle", wrap=True)
        main.addWidget(self.subtitle)
        main.addWidget(
            _label(
                "When local analysis is on, changing answers sends current selections and setup to the selected "
                "loopback Ollama service. Disable Ollama cloud features to prevent service-side forwarding; "
                "no model is downloaded automatically. Finished answers may accompany later text Coach requests, "
                "including to an online provider if selected.",
                "cardSubtitle",
                wrap=True,
            )
        )
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        self.evidence = QLabel()
        self.evidence.setObjectName("wizardEvidence")
        self.evidence.setTextFormat(Qt.TextFormat.RichText)
        self.evidence.setWordWrap(True)
        self.evidence.setOpenExternalLinks(True)
        self.evidence.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        main.addWidget(self.evidence)
        ai_row = QHBoxLayout()
        self.ai_enabled = QCheckBox("Analyze selections with local AI")
        self.ai_enabled.setChecked(True)
        self.ai_enabled.setToolTip("Uses only the configured loopback Ollama service and an installed model; no automatic downloads.")
        self.ai_enabled.toggled.connect(self._ai_toggled)
        ai_row.addWidget(self.ai_enabled)
        ai_row.addStretch(1)
        self.preview_btn = QPushButton("Preview in AI Coach")
        self.preview_btn.setObjectName("secondary")
        self.preview_btn.setToolTip(
            "Available when the current local AI analysis is ready. Opens its guidance in AI Coach; "
            "spoken replies follow your Read replies aloud setting."
        )
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self._open_ai_preview)
        ai_row.addWidget(self.preview_btn)
        main.addLayout(ai_row)
        self.ai_status = _label("Local AI is ready to analyze your next selection; an installed Ollama model is required.", "cardSubtitle", wrap=True)
        self.ai_status.setTextFormat(Qt.TextFormat.PlainText)
        main.addWidget(self.ai_status)
        self.error = _label("", "wizardError", wrap=True)
        main.addWidget(self.error)
        footer = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.clicked.connect(self.reject)
        self.back_btn = QPushButton("Back")
        self.back_btn.setObjectName("secondary")
        self.back_btn.clicked.connect(self.go_back)
        self.next_btn = QPushButton("Continue")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self.go_next)
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.back_btn)
        self.skip_btn = QPushButton("Skip question")
        self.skip_btn.setObjectName("secondary")
        self.skip_btn.setToolTip("Skip and clear any answers on this question.")
        self.skip_btn.clicked.connect(self.skip_step)
        footer.addWidget(self.skip_btn)
        footer.addStretch(1)
        self.start_calibration_btn = QPushButton("Finish and open Calibration")
        self.start_calibration_btn.setObjectName("secondary")
        self.start_calibration_btn.clicked.connect(self._finish_and_open_calibration)
        self.start_calibration_btn.hide()
        footer.addWidget(self.start_calibration_btn)
        footer.addWidget(self.next_btn)
        main.addLayout(footer)
        root.addLayout(main, 1)
        self._build_choices()
        self._build_setup()
        self._build_review()
        for field in (self.pad_width, self.mouse_model, self.game, self.dpi, self.sensitivity):
            field.textChanged.connect(self._schedule_ai)
        self._refresh()
        next(iter(self.cards[0].values())).setFocus(Qt.FocusReason.OtherFocusReason)

    def _page(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.stack.addWidget(scroll)
        return layout

    def _update_window_shape(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14.0, 14.0)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_window_shape()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._update_window_shape()

    def _build_choices(self):
        layout = self._page()
        grid = QGridLayout()
        grid.setSpacing(10)
        choices = {}
        for i, (key, (title, caption, _detail)) in enumerate(PROBLEMS.items()):
            card = ChoiceCard(title, caption, "")
            card.setMinimumHeight(90 if key != "unsure" else 64)
            card.setChecked(key in self.answers["problems"])
            card.clicked.connect(lambda checked, k=key: self._problem_clicked(k, checked))
            grid.addWidget(
                card, i // 2 if i < 4 else 2, i % 2 if i < 4 else 0, 1, 1 if i < 4 else 2
            )
            choices[key] = card
        layout.addLayout(grid)
        self.problem_guidance = _label("", "cardSubtitle", wrap=True)
        layout.addWidget(self.problem_guidance)
        layout.addStretch(1)
        self.cards.append(choices)
        self._update_problem_guidance()

        for kind, collection in (
            ("grip", GRIPS),
            (
                "motion",
                {
                    m.key: (
                        m.label,
                        m.caption,
                        f"Flick: {m.flick}\nTracking: {m.tracking}\nFatigue: {m.fatigue}",
                    )
                    for m in MOTIONS
                },
            ),
            ("pad", PADS),
        ):
            layout = self._page()
            row = QHBoxLayout()
            row.setSpacing(10)
            choices = {}
            for key, (title, caption, details) in collection.items():
                card = ChoiceCard(title, caption, details, kind, key)
                card.setMinimumHeight(330 if kind == "motion" else (280 if kind == "grip" else 230))
                card.setChecked(self.answers[kind] == key)
                card.clicked.connect(
                    lambda checked, k=key, typ=kind: self._single_clicked(typ, k, checked)
                )
                row.addWidget(card, 1)
                choices[key] = card
            layout.addLayout(row)
            if kind == "motion":
                unknown = ChoiceCard(
                    "Not sure", "Skip this question; no body-joint inference is made.", ""
                )
                unknown.setMinimumHeight(62)
                unknown.setChecked(self.answers["motion"] == "unsure")
                unknown.clicked.connect(
                    lambda checked: self._single_clicked("motion", "unsure", checked)
                )
                layout.addWidget(unknown)
                choices["unsure"] = unknown
            if kind == "pad":
                width = QHBoxLayout()
                width.addWidget(_label("Usable mouse travel (mm), optional", "formLabel"))
                self.pad_width = QLineEdit()
                self.pad_width.setPlaceholderText("e.g. 400")
                self.pad_width.setAccessibleName("Usable mouse travel in millimeters, optional")
                if self.answers["pad_width_mm"]:
                    self.pad_width.setText(str(self.answers["pad_width_mm"]))
                self.pad_width.setMaximumWidth(150)
                width.addWidget(self.pad_width)
                width.addStretch(1)
                layout.addLayout(width)
            layout.addStretch(1)
            self.cards.append(choices)

    def set_saved_actions(self, completed: bool, has_answers: bool) -> None:
        """Saved-answer actions live in the guide, not next to the Dashboard logo."""
        self.saved_clear_btn.setVisible(bool(completed))
        self.saved_discuss_btn.setVisible(bool(completed and has_answers))

    @staticmethod
    def _setup_card(title: str, subtitle: str):
        card = QFrame()
        card.setObjectName("wizardSetup")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)
        layout.addWidget(_label(title, "wizardOptionTitle", wrap=True))
        layout.addWidget(_label(subtitle, "cardSubtitle", wrap=True))
        return card, layout

    def _build_setup(self):
        layout = self._page()
        row = QHBoxLayout()
        row.setSpacing(10)
        mouse_card, mouse_layout = self._setup_card("Your mouse", "Enter the mouse model")
        self.mouse_model = QLineEdit(self.answers["mouse_model"])
        self.mouse_model.setMaxLength(80)
        self.mouse_model.setPlaceholderText("Optional · type a model")
        self.mouse_model.setAccessibleName("Mouse model")
        mouse_layout.addWidget(self.mouse_model)
        mouse_layout.addWidget(_label("Game", "wizardOptionCaption"))
        self.game = QLineEdit(self.answers["game"])
        self.game.setMaxLength(80)
        self.game.setPlaceholderText("Optional · type a game")
        self.game.setAccessibleName("Game")
        mouse_layout.addWidget(self.game)
        mouse_layout.addStretch(1)
        row.addWidget(mouse_card, 3)

        sens_card, sens_layout = self._setup_card("In-game sensitivity", "Enter your current setting")
        self.sensitivity = QLineEdit(str(self.answers["sensitivity"]) if self.answers["sensitivity"] else "")
        self.sensitivity.setPlaceholderText("Optional · e.g. 0.35")
        self.sensitivity.setAccessibleName("In-game sensitivity")
        sens_layout.addWidget(self.sensitivity)
        self.sensitivity_unknown = QCheckBox("I don't know my sensitivity")
        self.sensitivity_unknown.toggled.connect(lambda checked: self._set_unknown(self.sensitivity, checked))
        sens_layout.addWidget(self.sensitivity_unknown)
        sens_layout.addStretch(1)
        row.addWidget(sens_card, 2)

        dpi_card, dpi_layout = self._setup_card("DPI", "Check your mouse's configured setting")
        dpi_row = QHBoxLayout()
        self.dpi_down = QPushButton("−")
        self.dpi_down.setObjectName("secondary")
        self.dpi_down.setAccessibleName("Decrease entered DPI by 100")
        self.dpi_down.clicked.connect(lambda: self._step_dpi(-100))
        self.dpi = QLineEdit(str(self.answers["dpi"]) if self.answers["dpi"] else "")
        self.dpi.setPlaceholderText("Optional · e.g. 800")
        self.dpi.setAccessibleName("Mouse DPI")
        self.dpi.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dpi_up = QPushButton("+")
        self.dpi_up.setObjectName("secondary")
        self.dpi_up.setAccessibleName("Increase entered DPI by 100")
        self.dpi_up.clicked.connect(lambda: self._step_dpi(100))
        dpi_row.addWidget(self.dpi_down)
        dpi_row.addWidget(self.dpi, 1)
        dpi_row.addWidget(self.dpi_up)
        dpi_layout.addLayout(dpi_row)
        self.dpi_unknown = QCheckBox("I don't know my DPI")
        self.dpi_unknown.toggled.connect(self._toggle_dpi_unknown)
        dpi_layout.addWidget(self.dpi_unknown)
        dpi_layout.addStretch(1)
        row.addWidget(dpi_card, 2)
        layout.addLayout(row)
        layout.addWidget(_label(
            "All fields are entered by you. CurveCoach does not detect mouse DPI, read game settings or "
            "automatically calculate cm/360 from DPI and sensitivity alone.", "cardSubtitle", wrap=True,
        ))
        layout.addStretch(1)

    @staticmethod
    def _set_unknown(field: QLineEdit, checked: bool) -> None:
        if checked:
            field.clear()
        field.setEnabled(not checked)

    def _toggle_dpi_unknown(self, checked: bool) -> None:
        self._set_unknown(self.dpi, checked)
        self.dpi_down.setEnabled(not checked)
        self.dpi_up.setEnabled(not checked)

    def _step_dpi(self, delta: int) -> None:
        try:
            current = int(self.dpi.text()) if self.dpi.text().strip() else 800 - delta
        except ValueError:
            current = 800 - delta
        self.dpi.setText(str(min(64000, max(1, current + delta))))

    def _build_review(self):
        layout = self._page()
        self.setup_preview = SetupPreview()
        layout.addWidget(self.setup_preview)
        layout.addStretch(1)

    def _finish_and_open_calibration(self):
        if self.step != self.REVIEW_STEP:
            return
        self.start_calibration_requested = True
        self.go_next()

    def _problem_clicked(self, key: str, checked: bool):
        selected = set(self.answers["problems"])
        if checked:
            if key == "unsure":
                selected = {"unsure"}
            else:
                selected.discard("unsure")
                selected.add(key)
        else:
            selected.discard(key)
        self.answers["problems"] = [k for k in PROBLEMS if k in selected]
        for k, button in self.cards[0].items():
            button.setChecked(k in selected)
        self._update_problem_guidance()
        self._schedule_ai()

    def _update_problem_guidance(self):
        notes = [PROBLEM_GUIDANCE[k] for k in self.answers["problems"]]
        self.problem_guidance.setText(
            "General guidance · " + " ".join(notes)
            if notes
            else "Select an option for general guidance, or skip."
        )

    def _single_clicked(self, kind: str, key: str, checked: bool):
        # Single selection, but a second click can clear it and leave the step unanswered.
        self.answers[kind] = key if checked else ""
        idx = {"grip": 1, "motion": 2, "pad": 3}[kind]
        for k, button in self.cards[idx].items():
            button.setChecked(bool(checked and k == key))
        self._schedule_ai()

    def _snapshot_answers(self):
        current = dict(self.answers)
        current.update(pad_width_mm=self.pad_width.text(), mouse_model=self.mouse_model.text(),
                       game=self.game.text(), dpi=self.dpi.text(), sensitivity=self.sensitivity.text())
        return clone_answers(current)

    def _ai_toggled(self, checked):
        if checked:
            self._schedule_ai()
        else:
            self._ai_revision += 1
            self._ai_timer.stop()
            self.ai_proposal = self.ai_proposal_answers = None
            self.preview_btn.setEnabled(False)
            self.ai_status.setText("Local AI is off. An in-flight request may still finish locally; its result will be discarded.")

    def _schedule_ai(self, *_args):
        self._ai_revision += 1
        self.ai_proposal = self.ai_proposal_answers = None
        self.preview_btn.setEnabled(False)
        if not self.ai_enabled.isChecked() or not self._ai_alive:
            return
        snapshot = self._snapshot_answers()
        if not any(snapshot.values()):
            self._ai_timer.stop()
            self.ai_status.setText("Choose an answer to request an estimate from the local model.")
            return
        self.ai_status.setText("Local AI analysis pending · selections are self-reported, not measured. No settings change automatically.")
        self._ai_timer.start()

    def _dispatch_ai(self):
        if not self._ai_alive or not self.ai_enabled.isChecked() or self._ai_running:
            return
        answers = self._snapshot_answers()
        if not any(answers.values()):
            self.ai_status.setText("Choose an answer to request an estimate from the local model.")
            return
        revision = self._ai_revision
        settings = _settings()
        provider = str(settings.value("ai/provider", "ollama"))
        base_url = str(settings.value("ai/base_url", "http://127.0.0.1:11434"))
        model = str(settings.value("ai/model", "qwen3:1.7b"))
        summary = dict(self.measured_summary)
        signals = self._ai_signals
        self._ai_running = True
        self.ai_status.setText("Local AI is analyzing the latest selections. This may take time on your device.")

        def work():
            try:
                result = analyze_guide(provider, base_url, model, answers, summary)
                message = ""
            except Exception as exc:  # noqa: BLE001 - model and service errors are nonfatal
                result, message = None, str(exc) or "Local AI analysis failed."
            try:
                signals.finished.emit(revision, (answers, result), message)
            except RuntimeError:
                pass  # Dialog was destroyed; no UI object is touched from this thread.

        threading.Thread(target=work, daemon=True, name="guide-local-ai").start()

    def _on_ai_finished(self, revision, payload, error):
        self._ai_running = False
        if not self._ai_alive or not self.ai_enabled.isChecked():
            return
        if revision != self._ai_revision:
            self._ai_timer.start(0)
            return
        answers, proposal = payload
        if error:
            self.ai_status.setText(f"Local AI unavailable: {error} Toggle local analysis off and on to retry.")
            return
        self.ai_proposal, self.ai_proposal_answers = proposal, answers
        factor = float(proposal["factor"])
        limited = " · limited to ±10%" if proposal.get("limited") else ""
        self.ai_status.setText(
            f"Local AI estimate: sensitivity ×{factor:.3f}{limited} relative to the current curve. "
            f"{proposal['guidance']} Self-report-based estimate, not an optimality guarantee."
        )
        self.preview_btn.setEnabled(self.step == self.REVIEW_STEP)

    def _open_ai_preview(self):
        if self.step != self.REVIEW_STEP or self.ai_proposal is None or self._snapshot_answers() != self.ai_proposal_answers:
            return
        if not self._validate():
            return
        self.answers = clone_answers(self._snapshot_answers())
        self.preview_requested = True
        self.accept()

    def _stop_ai(self, *_args):
        self._ai_alive = False
        self._ai_timer.stop()
        self._ai_revision += 1

    def _read_numeric(
        self, field: QLineEdit, label: str, minimum: float, maximum: float, integer=False
    ):
        text = field.text().strip()
        if not text:
            return 0
        try:
            number = float(text.replace(",", "."))
            if (
                not math.isfinite(number)
                or not minimum <= number <= maximum
                or (integer and not number.is_integer())
            ):
                raise ValueError
        except ValueError:
            self.error.setText(
                f"{label} must be between {minimum:g} and {maximum:g}"
                + (" and a whole number." if integer else ".")
            )
            field.setFocus()
            return None
        return int(number) if integer else number

    def _validate(self):
        if self.step == 3 or self.step == 4:
            width = self._read_numeric(self.pad_width, "Usable width", 0.1, 5000)
            if width is None:
                return False
            self.answers["pad_width_mm"] = width
        if self.step == 4:
            dpi = self._read_numeric(self.dpi, "DPI", 1, 64000, integer=True)
            if dpi is None:
                return False
            sens = self._read_numeric(self.sensitivity, "Sensitivity", 0.00001, 10000)
            if sens is None:
                return False
            self.answers.update(
                mouse_model=self.mouse_model.text(),
                game=self.game.text(),
                dpi=dpi,
                sensitivity=sens,
            )
        self.error.clear()
        return True

    def _refresh(self):
        review = self.step == self.REVIEW_STEP
        self.counter.setText("SETUP PREVIEW" if review else f"SECTION {self.step + 1} / 5")
        self.title.setText(self._TITLES[self.step])
        self.subtitle.setText(self._SUBTITLES[self.step])
        self.stack.setCurrentIndex(self.step)
        self.evidence.setVisible(self.step < 5)
        if self.step < 5:
            link_color = active_palette()["accent"]
            evidence = self._EVIDENCE[self.step].replace("<a ", f'<a style="color:{link_color}" ')
            self.evidence.setText("<b>Research note</b> · " + evidence)
        elif review:
            self.setup_preview.set_answers(self._snapshot_answers())
            settings = _settings()
            animations = bool(settings.value("ui/animations", True, type=bool))
            mode = str(settings.value("ui/mode", "luxurious"))
            self.setup_preview.start_flow(animations and fx_enabled(mode))
        self.back_btn.setEnabled(self.step > 0)
        self.next_btn.setText("Finish" if review else "Continue")
        self.skip_btn.setVisible(not review)
        self.start_calibration_btn.setVisible(review)
        self.preview_btn.setVisible(review)
        self.preview_btn.setEnabled(review and self.ai_proposal is not None and self._snapshot_answers() == self.ai_proposal_answers)
        for index, label in enumerate(self.nav_labels):
            label.setProperty("current", index == self.step)
            label.style().unpolish(label)
            label.style().polish(label)
        self.error.clear()

    def skip_step(self):
        """Clear the current question, then continue to the next page."""
        if self.step == self.REVIEW_STEP:
            return
        if self.step == 0:
            self.answers["problems"] = []
            for card in self.cards[0].values():
                card.setChecked(False)
            self._update_problem_guidance()
        elif self.step in (1, 2, 3):
            key = {1: "grip", 2: "motion", 3: "pad"}[self.step]
            self.answers[key] = ""
            for card in self.cards[self.step].values():
                card.setChecked(False)
            if self.step == 3:
                self.pad_width.clear()
        else:
            self.dpi_unknown.setChecked(False)
            self.sensitivity_unknown.setChecked(False)
            for field in (self.mouse_model, self.game, self.dpi, self.sensitivity):
                field.clear()
        self._schedule_ai()
        self.go_next()

    def go_back(self):
        if self.step > 0:
            self.step -= 1
            self._refresh()

    def go_next(self):
        if not self._validate():
            return
        if self.step == self.REVIEW_STEP:
            self.answers = clone_answers(self._snapshot_answers())
            self.accept()
        else:
            if self.step == 4:
                self.answers = clone_answers(self.answers)
            self.step += 1
            self._refresh()
