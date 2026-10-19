"""Application styling / theming.

``build_style(mode, theme)`` returns the Qt stylesheet for a visual *mode* and a
color *theme*:

  mode:  "luxurious"   - gradients + soft shadows (shadows applied in code)
         "performance" - flat, lightweight painting for maximum FPS
  theme: "dark"        - the core, default appearance
         "light"       - adaptive light appearance (follows the OS when set to
                          "system" by the caller)

The whole UI is driven from one palette per theme, so dark mode is a first-class,
fully themed experience and light mode is a complete, consistent alternative
rather than an afterthought. ``APP_STYLE`` stays as the default (luxurious/dark)
for backward compatibility.
"""

from __future__ import annotations

# One palette per theme. Every themed color the UI uses is defined here, so
# switching theme restyles the entire app consistently. Dark values are the
# established look; light is a clean, low-chrome counterpart.
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0a0d12",
        "panel": "#10151d",
        "sidebar_border": "#1d2734",
        "card": "#111823",
        "card_border": "#222d3b",
        "banner_bg": "#142a26",
        "banner_border": "#1f5346",
        "ink": "#e8edf5",
        "ink_strong": "#f7fafc",
        "subtitle": "#92a0b3",
        "muted": "#8997aa",
        "accent": "#70e1ba",
        "accent_hi": "#8ff0cf",
        "accent_soft": "#5fd3ac",
        "accent_ink": "#07110e",
        "nav_text": "#9eabba",
        "nav_hover_bg": "#17202b",
        "nav_hover_text": "#ffffff",
        "nav_active_bg": "#16342e",
        "warn": "#ffcf70",
        "warn_bg": "#3a2e17",
        "status_good_bg": "#16342e",
        "disabled_bg": "#33433f",
        "disabled_ink": "#89948f",
        "input_bg": "#0d1219",
        "input_ink": "#e6ebf2",
        "input_border": "#2b3746",
        "input_sel": "#285447",
        "input_disabled_bg": "#0b0f15",
        "input_disabled_ink": "#667385",
        "menu_bg": "#111720",
        "menu_sel": "#25483f",
        "form_label": "#9aa8ba",
        "control_text": "#cdd6e3",
        "control_border": "#39485a",
        "chip_bg": "#1a2330",
        "chip_ink": "#dbe3ec",
        "chip_hover_bg": "#222e3d",
        "progress_border": "#273240",
        "scrollbar": "#2b3746",
        "scrollbar_hover": "#3a4a5d",
        "tooltip_bg": "#1b2430",
        "tooltip_ink": "#ffffff",
        "tooltip_border": "#344256",
    },
    "light": {
        # Soft, light gray surface with slightly lighter cards for gentle depth.
        "bg": "#e8eaef",
        "panel": "#f1f3f7",
        "sidebar_border": "#d9dee7",
        "card": "#f7f8fb",
        "card_border": "#dde2ea",
        "banner_bg": "#e3f4ee",
        "banner_border": "#a7e2cf",
        "ink": "#1b2330",
        "ink_strong": "#0d1522",
        "subtitle": "#5b6678",
        "muted": "#647081",
        "accent": "#14a883",
        "accent_hi": "#0e9a78",
        "accent_soft": "#17b890",
        "accent_ink": "#ffffff",
        "nav_text": "#556175",
        "nav_hover_bg": "#eef2f7",
        "nav_hover_text": "#0d1522",
        "nav_active_bg": "#e2f5ee",
        "warn": "#8a5a00",
        "warn_bg": "#fbe8c8",
        "status_good_bg": "#d8f3ea",
        "disabled_bg": "#dce2ec",
        "disabled_ink": "#9aa3b2",
        "input_bg": "#ffffff",
        "input_ink": "#1b2330",
        "input_border": "#d3dbe6",
        "input_sel": "#bdeadd",
        "input_disabled_bg": "#eef1f6",
        "input_disabled_ink": "#9aa3b2",
        "menu_bg": "#ffffff",
        "menu_sel": "#dff3ec",
        "form_label": "#556175",
        "control_text": "#28313f",
        "control_border": "#c2ccd9",
        "chip_bg": "#eef2f7",
        "chip_ink": "#28313f",
        "chip_hover_bg": "#e3eaf2",
        "progress_border": "#d3dbe6",
        "scrollbar": "#ccd4e0",
        "scrollbar_hover": "#b3bece",
        "tooltip_bg": "#1b2430",
        "tooltip_ink": "#ffffff",
        "tooltip_border": "#344256",
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
QLabel#brand {
    color: %(ink_strong)s;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 1px;
    padding: 8px 6px 20px 6px;
}
QLabel#eyebrow { color: %(accent_hi)s; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
QLabel#pageTitle { color: %(ink_strong)s; font-size: 29px; font-weight: 800; }
QLabel#pageSubtitle { color: %(subtitle)s; font-size: 14px; }
QLabel#cardTitle { color: %(ink_strong)s; font-size: 16px; font-weight: 700; }
QLabel#muted { color: %(muted)s; }
QLabel#metric { color: %(accent_hi)s; font-size: 24px; font-weight: 800; }

QPushButton#navButton {
    background: transparent;
    color: %(nav_text)s;
    border: 0;
    border-radius: 12px;
    text-align: left;
    padding: 12px 14px;
    font-weight: 600;
}
QPushButton#navButton:hover { background: %(nav_hover_bg)s; color: %(nav_hover_text)s; }
QPushButton#navButton:checked { background: %(nav_active_bg)s; color: %(accent_hi)s; }

QFrame#card {
    background: %(card)s;
    border: 1px solid %(card_border)s;
    border-radius: 18px;
}
QFrame#banner {
    background: %(banner_bg)s;
    border: 1px solid %(banner_border)s;
    border-radius: 16px;
}

QLabel#statusGood { color: %(accent_hi)s; background: %(status_good_bg)s; border-radius: 11px; padding: 6px 12px; }
QLabel#statusWarn { color: %(warn)s; background: %(warn_bg)s; border-radius: 11px; padding: 6px 12px; }

QPushButton#primary {
    color: %(accent_ink)s;
    border: 0;
    border-radius: 12px;
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
    border-radius: 12px;
    padding: 10px 17px;
    font-weight: 600;
}
QPushButton#secondary:hover { background: %(chip_hover_bg)s; border-color: %(control_border)s; }

QComboBox, QLineEdit, QPlainTextEdit, QTextBrowser, QDoubleSpinBox {
    background: %(input_bg)s;
    color: %(input_ink)s;
    border: 1px solid %(input_border)s;
    border-radius: 11px;
    padding: 9px 11px;
    min-height: 18px;
    selection-background-color: %(input_sel)s;
}
QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus {
    border-color: %(accent)s;
}
QPlainTextEdit, QTextBrowser { padding: 12px; border-radius: 14px; }
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
    width: 20px; height: 20px;
    border-radius: 7px; border: 1px solid %(control_border)s; background: %(input_bg)s;
}
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }

QRadioButton { color: %(control_text)s; spacing: 9px; padding: 4px 0; }
QRadioButton::indicator {
    width: 20px; height: 20px; border-radius: 10px;
    border: 1px solid %(control_border)s; background: %(input_bg)s;
}
QRadioButton::indicator:checked { background: %(accent)s; border: 5px solid %(input_bg)s; }

QProgressBar {
    background: %(input_bg)s; border: 1px solid %(progress_border)s;
    border-radius: 10px; text-align: center; color: %(ink)s;
}
QProgressBar::chunk { background: %(accent)s; border-radius: 9px; }

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


# Default stylesheet (luxurious / dark) for backward compatibility.
APP_STYLE = build_style("luxurious", "dark")
