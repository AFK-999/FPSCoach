"""Application styling.

``build_style(mode)`` returns the Qt stylesheet for the chosen visual mode:
  - "luxurious"   : richer accents, gradients, larger radii (paired with
                    animations + soft shadows applied in code).
  - "performance" : flatter, lighter-weight painting for maximum FPS.

Both modes share a clean, modern, fully rounded design (no sharp corners).
``APP_STYLE`` is kept as the default (luxurious) for backward compatibility.
"""

from __future__ import annotations

# Shared palette ------------------------------------------------------------
_BG = "#0a0d12"
_PANEL = "#10151d"
_CARD = "#111823"
_CARD_BORDER = "#222d3b"
_INK = "#e8edf5"
_MUTED = "#8997aa"
_ACCENT = "#70e1ba"
_ACCENT_HI = "#8ff0cf"

_BASE = r"""
QMainWindow, QWidget#root {
    background: %(bg)s;
    color: %(ink)s;
    font-family: "Segoe UI";
    font-size: 14px;
}
QFrame#sidebar {
    background: %(panel)s;
    border: 0;
    border-right: 1px solid #1d2734;
}
QLabel#brand {
    color: #f7fafc;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 1px;
    padding: 8px 6px 20px 6px;
}
QLabel#eyebrow { color: %(accent_hi)s; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
QLabel#pageTitle { color: #f7fafc; font-size: 29px; font-weight: 800; }
QLabel#pageSubtitle { color: #92a0b3; font-size: 14px; }
QLabel#cardTitle { color: #f2f5fa; font-size: 16px; font-weight: 700; }
QLabel#muted { color: %(muted)s; }
QLabel#metric { color: %(accent_hi)s; font-size: 24px; font-weight: 800; }

QPushButton#navButton {
    background: transparent;
    color: #9eabba;
    border: 0;
    border-radius: 12px;
    text-align: left;
    padding: 12px 14px;
    font-weight: 600;
}
QPushButton#navButton:hover { background: #17202b; color: #ffffff; }
QPushButton#navButton:checked { background: #16342e; color: %(accent_hi)s; }

QFrame#card {
    background: %(card)s;
    border: 1px solid %(card_border)s;
    border-radius: 18px;
}
QFrame#banner {
    background: #142a26;
    border: 1px solid #1f5346;
    border-radius: 16px;
}

QLabel#statusGood { color: %(accent_hi)s; background: #16342e; border-radius: 11px; padding: 6px 12px; }
QLabel#statusWarn { color: #ffcf70; background: #3a2e17; border-radius: 11px; padding: 6px 12px; }

QPushButton#primary {
    color: #07110e;
    border: 0;
    border-radius: 12px;
    padding: 11px 20px;
    font-weight: 800;
    %(primary_bg)s
}
QPushButton#primary:hover { %(primary_hover)s }
QPushButton#primary:disabled { background: #33433f; color: #89948f; }

QPushButton#secondary {
    background: #1a2330;
    color: #dbe3ec;
    border: 1px solid #2b3746;
    border-radius: 12px;
    padding: 10px 17px;
    font-weight: 600;
}
QPushButton#secondary:hover { background: #222e3d; border-color: #39485a; }

QComboBox, QLineEdit, QPlainTextEdit, QTextBrowser, QDoubleSpinBox {
    background: #0d1219;
    color: #e6ebf2;
    border: 1px solid #2b3746;
    border-radius: 11px;
    padding: 9px 11px;
    min-height: 18px;
    selection-background-color: #285447;
}
QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus {
    border-color: %(accent)s;
}
QPlainTextEdit, QTextBrowser { padding: 12px; border-radius: 14px; }
QComboBox:disabled, QLineEdit:disabled { color: #667385; background: #0b0f15; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView {
    background: #111720; color: #e6ebf2;
    border: 1px solid #2b3746; border-radius: 10px;
    selection-background-color: #25483f; outline: 0;
}
QFormLayout QLabel { color: #9aa8ba; }

QCheckBox { color: #cdd6e3; spacing: 9px; }
QCheckBox::indicator {
    width: 20px; height: 20px;
    border-radius: 7px; border: 1px solid #39485a; background: #0d1219;
}
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }

QRadioButton { color: #cdd6e3; spacing: 9px; padding: 4px 0; }
QRadioButton::indicator {
    width: 20px; height: 20px; border-radius: 10px;
    border: 1px solid #39485a; background: #0d1219;
}
QRadioButton::indicator:checked { background: %(accent)s; border: 5px solid #0d1219; }

QProgressBar {
    background: #0d1219; border: 1px solid #273240;
    border-radius: 10px; text-align: center; color: %(ink)s;
}
QProgressBar::chunk { background: %(accent)s; border-radius: 9px; }

QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 4px; }
QScrollBar::handle:vertical { background: #2b3746; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3a4a5d; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 4px; }
QScrollBar::handle:horizontal { background: #2b3746; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QToolTip { background: #1b2430; color: white; border: 1px solid #344256; border-radius: 8px; padding: 7px; }
"""

_LUX = {
    "primary_bg": "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    "stop:0 #8ff0cf, stop:1 #5fd3ac);",
    "primary_hover": "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    "stop:0 #a7f6db, stop:1 #70e1ba);",
}
_PERF = {
    "primary_bg": "background: #70e1ba;",
    "primary_hover": "background: #8ff0cf;",
}


def build_style(mode: str = "luxurious") -> str:
    accents = _PERF if mode == "performance" else _LUX
    return _BASE % {
        "bg": _BG,
        "panel": _PANEL,
        "card": _CARD,
        "card_border": _CARD_BORDER,
        "ink": _INK,
        "muted": _MUTED,
        "accent": _ACCENT,
        "accent_hi": _ACCENT_HI,
        **accents,
    }


# Default stylesheet (luxurious) for backward compatibility.
APP_STYLE = build_style("luxurious")
