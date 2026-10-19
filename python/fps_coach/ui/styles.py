"""Application styling / theming - the "Vantage" visual language.

A tactical-optics aesthetic built around a single warm signal accent (burnt
amber/copper) on graphite surfaces, with a clearly separate "good" color for
status so the brand accent is never diluted into meaning "success". Dark is
the primary, fully-realized experience; light is a complete warm-paper
counterpart rather than an inverted afterthought.

``build_style(mode, theme)`` returns the Qt stylesheet for a visual *mode* and
color *theme*:

  mode:  "luxurious"   - soft gradients on primary actions + drop shadows
                          (shadows applied in code, see MainWindow)
         "performance" - flat, lightweight painting for maximum FPS
  theme: "dark"        - the core, default appearance
         "light"       - a complete warm, low-glare alternative
"""

from __future__ import annotations

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0b0c10",
        "panel": "#111318",
        "sidebar_border": "#1f2229",
        "card": "#15171d",
        "card_border": "#23262e",
        "banner_bg": "#201a12",
        "banner_border": "#4a3419",
        "ink": "#e7e5e0",
        "ink_strong": "#faf8f4",
        "subtitle": "#9b9893",
        "muted": "#86837d",
        "accent": "#d98f3b",
        "accent_hi": "#f0a94e",
        "accent_soft": "#c97f2c",
        "accent_ink": "#1a1106",
        "good": "#5fb88a",
        "good_bg": "#152420",
        "nav_text": "#8d8a84",
        "nav_group_text": "#5c5952",
        "nav_hover_bg": "#191b21",
        "nav_hover_text": "#f5f3ee",
        "nav_active_bg": "#241a0c",
        "header_accent": "#d98f3b",
        "warn": "#e8b65a",
        "warn_bg": "#2c2210",
        "status_good_bg": "#152420",
        "disabled_bg": "#2a2c32",
        "disabled_ink": "#6e6b66",
        "input_bg": "#0e1014",
        "input_ink": "#e5e3de",
        "input_border": "#2a2d34",
        "input_sel": "#3a2a15",
        "input_disabled_bg": "#0c0d10",
        "input_disabled_ink": "#5b5954",
        "menu_bg": "#14161b",
        "menu_sel": "#2a2112",
        "form_label": "#95928c",
        "control_text": "#cfccc5",
        "control_border": "#34373f",
        "chip_bg": "#191b21",
        "chip_ink": "#d9d6d0",
        "chip_hover_bg": "#20232a",
        "progress_border": "#262930",
        "scrollbar": "#2a2d34",
        "scrollbar_hover": "#3a3e47",
        "tooltip_bg": "#1b1d22",
        "tooltip_ink": "#f5f3ee",
        "tooltip_border": "#34373f",
    },
    "light": {
        "bg": "#f4f1ec",
        "panel": "#faf8f4",
        "sidebar_border": "#e3ddd2",
        "card": "#ffffff",
        "card_border": "#e7e1d6",
        "banner_bg": "#fbeedd",
        "banner_border": "#eccfa0",
        "ink": "#262320",
        "ink_strong": "#141210",
        "subtitle": "#6b6459",
        "muted": "#7a7367",
        "accent": "#b5701f",
        "accent_hi": "#935a16",
        "accent_soft": "#c97f2c",
        "accent_ink": "#fffaf3",
        "good": "#1f7a52",
        "good_bg": "#dcf1e6",
        "nav_text": "#6b6459",
        "nav_group_text": "#a79d8a",
        "nav_hover_bg": "#f1ece3",
        "nav_hover_text": "#141210",
        "nav_active_bg": "#f3e3c9",
        "header_accent": "#b5701f",
        "warn": "#8a5a00",
        "warn_bg": "#fbe8c8",
        "status_good_bg": "#dcf1e6",
        "disabled_bg": "#e7e2d8",
        "disabled_ink": "#a39c8e",
        "input_bg": "#ffffff",
        "input_ink": "#262320",
        "input_border": "#ddd5c7",
        "input_sel": "#f0dcb8",
        "input_disabled_bg": "#f1ece3",
        "input_disabled_ink": "#a39c8e",
        "menu_bg": "#ffffff",
        "menu_sel": "#f3e3c9",
        "form_label": "#6b6459",
        "control_text": "#312d27",
        "control_border": "#d8cfbf",
        "chip_bg": "#f1ece3",
        "chip_ink": "#312d27",
        "chip_hover_bg": "#e8e0d2",
        "progress_border": "#ddd5c7",
        "scrollbar": "#d8cfbf",
        "scrollbar_hover": "#c2b8a4",
        "tooltip_bg": "#262320",
        "tooltip_ink": "#faf8f4",
        "tooltip_border": "#3a352c",
    },
}

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
    border-right: 1px solid %(sidebar_border)s;
}
QFrame#navDivider {
    background: %(sidebar_border)s;
    max-height: 1px;
    min-height: 1px;
    border: 0;
    margin: 6px 10px;
}
QLabel#brand {
    color: %(ink_strong)s;
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 2px;
}
QLabel#navGroup {
    color: %(nav_group_text)s;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.6px;
    padding: 16px 10px 6px 10px;
}

QLabel#eyebrow { color: %(accent_hi)s; font-size: 11px; font-weight: 800; letter-spacing: 1.6px; }
QLabel#pageTitle { color: %(ink_strong)s; font-size: 28px; font-weight: 800; }
QLabel#pageSubtitle { color: %(subtitle)s; font-size: 14px; }
QLabel#cardTitle { color: %(ink_strong)s; font-size: 16px; font-weight: 700; }
QLabel#muted { color: %(muted)s; }
QLabel#metric {
    color: %(accent_hi)s;
    font-size: 25px;
    font-weight: 800;
    font-family: "Cascadia Mono", "Consolas", "Segoe UI";
}

QFrame#headerAccent { background: %(header_accent)s; border-radius: 2px; }

QPushButton#navButton {
    background: transparent;
    color: %(nav_text)s;
    border: 0;
    border-left: 3px solid transparent;
    border-radius: 10px;
    text-align: left;
    padding: 11px 14px 11px 11px;
    font-weight: 600;
}
QPushButton#navButton:hover { background: %(nav_hover_bg)s; color: %(nav_hover_text)s; }
QPushButton#navButton:checked {
    background: %(nav_active_bg)s;
    color: %(accent_hi)s;
    border-left: 3px solid %(accent)s;
}
QPushButton#collapseButton {
    background: transparent;
    color: %(nav_text)s;
    border: 0;
    border-radius: 9px;
    padding: 8px;
}
QPushButton#collapseButton:hover { background: %(nav_hover_bg)s; }

QFrame#card {
    background: %(card)s;
    border: 1px solid %(card_border)s;
    border-radius: 16px;
}
QFrame#banner {
    background: %(banner_bg)s;
    border: 1px solid %(banner_border)s;
    border-radius: 14px;
}

QLabel#statusGood { color: %(good)s; background: %(status_good_bg)s; border-radius: 10px; padding: 6px 12px; font-weight: 600; }
QLabel#statusWarn { color: %(warn)s; background: %(warn_bg)s; border-radius: 10px; padding: 6px 12px; font-weight: 600; }

QPushButton#primary {
    color: %(accent_ink)s;
    border: 0;
    border-radius: 11px;
    padding: 11px 20px;
    font-weight: 800;
    %(primary_bg)s
}
QPushButton#primary:hover { %(primary_hover)s }
QPushButton#primary:disabled { background: %(disabled_bg)s; color: %(disabled_ink)s; }

QPushButton#secondary {
    background: %(chip_bg)s;
    color: %(chip_ink)s;
    border: 1px solid %(input_border)s;
    border-radius: 11px;
    padding: 10px 17px;
    font-weight: 600;
}
QPushButton#secondary:hover { background: %(chip_hover_bg)s; border-color: %(control_border)s; }
QPushButton#secondary:checked { border-color: %(accent)s; color: %(accent_hi)s; }

QComboBox, QLineEdit, QPlainTextEdit, QTextBrowser, QDoubleSpinBox, QSpinBox {
    background: %(input_bg)s;
    color: %(input_ink)s;
    border: 1px solid %(input_border)s;
    border-radius: 10px;
    padding: 9px 11px;
    min-height: 18px;
    selection-background-color: %(input_sel)s;
}
QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border-color: %(accent)s;
}
QPlainTextEdit, QTextBrowser { padding: 12px; border-radius: 13px; }
QComboBox:disabled, QLineEdit:disabled { color: %(input_disabled_ink)s; background: %(input_disabled_bg)s; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView {
    background: %(menu_bg)s; color: %(input_ink)s;
    border: 1px solid %(input_border)s; border-radius: 10px;
    selection-background-color: %(menu_sel)s; outline: 0;
}
QFormLayout QLabel { color: %(form_label)s; }

QCheckBox { color: %(control_text)s; spacing: 9px; }
QCheckBox::indicator {
    width: 19px; height: 19px;
    border-radius: 6px; border: 1px solid %(control_border)s; background: %(input_bg)s;
}
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }

QRadioButton { color: %(control_text)s; spacing: 9px; padding: 4px 0; }
QRadioButton::indicator {
    width: 19px; height: 19px; border-radius: 10px;
    border: 1px solid %(control_border)s; background: %(input_bg)s;
}
QRadioButton::indicator:checked { background: %(accent)s; border: 5px solid %(input_bg)s; }

QProgressBar {
    background: %(input_bg)s; border: 1px solid %(progress_border)s;
    border-radius: 9px; text-align: center; color: %(ink)s;
}
QProgressBar::chunk { background: %(accent)s; border-radius: 8px; }

QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 4px; }
QScrollBar::handle:vertical { background: %(scrollbar)s; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(scrollbar_hover)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 4px; }
QScrollBar::handle:horizontal { background: %(scrollbar)s; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QToolTip { background: %(tooltip_bg)s; color: %(tooltip_ink)s; border: 1px solid %(tooltip_border)s; border-radius: 8px; padding: 7px; }
"""


def _accents(mode: str, palette: dict[str, str]) -> dict[str, str]:
    if mode == "performance":
        return {
            "primary_bg": f"background: {palette['accent']};",
            "primary_hover": f"background: {palette['accent_hi']};",
        }
    return {
        "primary_bg": (
            "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {palette['accent_hi']}, stop:1 {palette['accent_soft']});"
        ),
        "primary_hover": (
            "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {palette['accent']}, stop:1 {palette['accent_hi']});"
        ),
    }


def build_style(mode: str = "luxurious", theme: str = "dark") -> str:
    palette = PALETTES.get(theme, PALETTES["dark"])
    return _BASE % {**palette, **_accents(mode, palette)}


APP_STYLE = build_style("luxurious", "dark")