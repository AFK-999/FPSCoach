"""Selectable, theme-aware chat messages with actual rounded Qt widgets."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .styles import active_palette


class ChatTranscript(QScrollArea):
    """Scrollable transcript: quiet automatic notes and rounded conversation bubbles.

    Individual labels retain plain-text selection; the small text document provides a
    plain-text transcript and a shared default font without rendering the bubbles as HTML.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transcript")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._document = QTextDocument(self)
        self._entries: list[tuple[QWidget, QLabel]] = []
        self._content = QWidget()
        self._content.setObjectName("chatContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(12)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._content)
        self.viewport().installEventFilter(self)

    def document(self) -> QTextDocument:
        return self._document

    def toPlainText(self) -> str:
        return self._document.toPlainText()

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self._entries.clear()
        self._document.clear()

    def set_message_font_size(self, px: int) -> None:
        font = self.font()
        font.setPixelSize(int(px))
        self.setFont(font)
        self._document.setDefaultFont(font)
        for _, label in self._entries:
            self._set_label_font(label, px)
        self._resize_messages()

    @staticmethod
    def _set_label_font(label: QLabel, px: int) -> None:
        label.setStyleSheet(label.property("messageColorStyle") + f" font-size: {int(px)}px;")

    def add_message(self, who: str, text: str) -> None:
        who, text = str(who), str(text)
        kind = who.strip().casefold()
        is_user = kind in {"you", "user"}
        is_auto = kind in {"coach (auto)", "coach (live)"}
        palette = active_palette()

        row = QWidget(self._content)
        row.setObjectName("chatMessageRow")
        row.setProperty("messageKind", "user" if is_user else "auto" if is_auto else "coach")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if is_user:
            row_layout.addStretch(1)

        frame = QFrame(row)
        frame.setObjectName("chatMessage")
        frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        if is_auto:
            frame.setStyleSheet("QFrame#chatMessage { background: transparent; border: none; }")
            color = palette["text2"]
        else:
            # Both themes use a genuinely dark user bubble with accessible light text.
            background = "#1f2c26" if QColor(palette["input"]).lightness() < 128 else "#2a3d30"
            if not is_user:
                background = palette["surface2"]
            border = "#425a4b" if is_user else palette["line2"]
            frame.setStyleSheet(
                f"QFrame#chatMessage {{ background: {background}; border: 1px solid {border}; "
                "border-radius: 16px; }"
            )
            color = "#f4f8f3" if is_user else palette["text"]

        contents = QVBoxLayout(frame)
        contents.setContentsMargins(14 if not is_auto else 3, 10 if not is_auto else 2,
                                    14 if not is_auto else 3, 10 if not is_auto else 2)
        contents.setSpacing(0)
        # Escape untrusted message content; only the speaker label is formatted.
        # pre-wrap preserves whitespace while still allowing long messages to wrap.
        safe_who = escape(who)
        safe_text = escape(text).replace("\n", "<br>")
        label = QLabel(f'<span style="white-space: pre-wrap"><b>{safe_who}</b>: {safe_text}</span>', frame)
        label.setProperty("messagePlainText", f"{who}: {text}")
        label.setObjectName("chatMessageText")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        label.setProperty("messageColorStyle", f"background: transparent; border: none; color: {color};")
        self._set_label_font(label, self._document.defaultFont().pixelSize() if self._document.defaultFont().pixelSize() > 0 else 14)
        contents.addWidget(label)
        row_layout.addWidget(frame)
        if not is_user:
            row_layout.addStretch(1)
        self._layout.addWidget(row)
        self._entries.append((frame, label))
        self._resize_messages()

        cursor = QTextCursor(self._document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self._document.isEmpty():
            cursor.insertText("\n")
        role_format = QTextCharFormat()
        role_format.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(who, role_format)
        cursor.insertText(f": {text}", QTextCharFormat())
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _resize_messages(self) -> None:
        maximum = max(120, int((self.viewport().width() - 28) * 0.78))
        for frame, label in self._entries:
            longest_line = max(label.property("messagePlainText").splitlines(), key=len, default="")
            # Extra room accounts for the bold speaker label as well as padding.
            natural = label.fontMetrics().horizontalAdvance(longest_line) + 44
            frame.setFixedWidth(min(maximum, max(190, natural)))

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            self._resize_messages()
        return super().eventFilter(obj, event)
